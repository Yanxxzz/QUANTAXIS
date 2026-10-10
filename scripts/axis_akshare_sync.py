#!/usr/bin/env python3
"""Bounded, explicit AKShare Tencent daily/HFQ ingestion into an isolated AXIS DB.

No account, source fallback, universe/calendar certification, or factor evaluation.
AKShare is imported only in a disposable network worker. The default batch is one
security; independently verified historical membership and execution states remain
required before this downloaded price scope can retire another provider.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import uuid
from contextlib import redirect_stderr, redirect_stdout
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.axis_sync import date_string, qa_date_stamp

SOURCE = "akshare_tencent"
DEFAULT_DATABASE = "quantaxis_akshare_tencent"
SUPPORTED_AKSHARE_VERSION = "1.19.1"
HTTP_HOSTS = {"web.ifzq.gtimg.cn", "proxy.finance.qq.com"}


class SourceUnavailable(RuntimeError):
    """Transport/remote refusal: stop the batch and persist the source circuit."""


class DataQualityError(ValueError):
    """A returned security window cannot yet be certified as valid price data."""


class DependencyError(ValueError):
    """A safe, actionable local dependency message, unrelated to source health."""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def tencent_symbol(code):
    # Require explicit six-digit SH/SZ A-share identities. The actual Beijing
    # probe failed; accepting a bj prefix in AKShare is not source availability.
    code = str(code)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("Use a six-digit A-share code")
    if code.startswith(("600", "601", "603", "605", "688")):
        return "sh" + code
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz" + code
    if code.startswith(("4", "8", "920")):
        raise ValueError("Beijing history is unverified/unavailable; this CLI supports SH/SZ only")
    raise ValueError("Unsupported SH/SZ A-share code; indices/B shares/CDRs are excluded")


def validate_database(database):
    if not re.fullmatch(r"quantaxis_akshare_tencent(?:_[A-Za-z0-9_]+)?", database):
        raise ValueError("The target must be an isolated quantaxis_akshare_tencent database")
    return database


def json_value(value):
    """Preserve interface fields; represent pandas missing scalars as JSON null."""
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def normalize_tencent_frame(frame, code, start, end, adjustment="none"):
    """AKShare 1.19.1 contract: volume in shares, amount in CNY.

    Check the raw traded average against OHLC to detect a 100/10000 unit
    mismatch. HFQ OHLC must not be compared to unadjusted turnover cash.
    """
    if adjustment not in {"none", "hfq"}:
        raise ValueError("Only raw and native HFQ ingestion is supported")
    symbol = tencent_symbol(code)
    if hasattr(frame, "to_dict"):
        frame = frame.to_dict("records")
    if not isinstance(frame, list) or not frame:
        raise DataQualityError("Empty security price window is not coverage evidence")
    rows, seen = [], set()
    required = {"date", "open", "high", "low", "close", "volume", "amount"}
    for original in frame:
        if not isinstance(original, dict) or not required <= original.keys():
            raise DataQualityError("Tencent output lacks date/OHLC/volume/amount fields")
        value = original["date"]
        day = value.date().isoformat() if isinstance(value, datetime) else value.isoformat() if isinstance(value, date) else str(value)
        try:
            day = date_string(day)
        except (ValueError, TypeError):
            raise DataQualityError("Invalid daily date; timestamps are not daily identities") from None
        if day in seen or not start <= day <= end:
            raise DataQualityError("Duplicate or out-of-window daily date")
        seen.add(day)
        try:
            values = {k: float(original[k]) for k in required - {"date"}}
        except (ValueError, TypeError):
            raise DataQualityError("Missing/non-numeric Tencent price or quantity") from None
        prices = [values[k] for k in ("open", "high", "low", "close")]
        if not all(math.isfinite(v) for v in values.values()) or min(prices) <= 0:
            raise DataQualityError("Nonfinite/nonpositive daily OHLC")
        if values["high"] < max(prices) or values["low"] > min(prices):
            raise DataQualityError("Daily OHLC ordering is invalid")
        shares, amount = values.pop("volume"), values["amount"]
        if shares < 0 or amount < 0 or (shares == 0 and amount != 0):
            raise DataQualityError("Invalid daily volume/amount")
        if adjustment == "none" and shares > 0:
            average = amount / shares
            if not values["low"] * 0.99 <= average <= values["high"] * 1.01:
                raise DataQualityError("Raw amount/share units disagree with traded price range")
        rows.append({"code": code, "date": day, "date_stamp": qa_date_stamp(day), **values,
                     "vol": shares / 100.0, "volume_shares": shares,
                     "volume_unit": "lots_100_shares", "source_volume_unit": "shares",
                     "amount_unit": "CNY", "exchange": symbol[:2].upper(), "source": SOURCE,
                     "adjustment": adjustment, "source_adjustment": "" if adjustment == "none" else "hfq",
                     "trade_status": None, "is_st": None,
                     "execution_state": "historical_suspension_and_ST_evidence_required",
                     "source_original": json_value(original)})
    return sorted(rows, key=lambda r: r["date"])


def audit_pair(raw, hfq):
    raw_by_date, hfq_by_date = ({r["date"]: r for r in rows} for rows in (raw, hfq))
    if not raw_by_date or raw_by_date.keys() != hfq_by_date.keys():
        raise DataQualityError("Raw and same-source HFQ daily identities disagree")
    for day, row in raw_by_date.items():
        adjusted = hfq_by_date[day]
        if row["source"] != SOURCE or adjusted["source"] != SOURCE or row["code"] != adjusted["code"]:
            raise DataQualityError("Raw/HFQ source or security identity disagrees")
        for field in ("volume_shares", "amount"):
            if not math.isclose(row[field], adjusted[field], rel_tol=1e-9, abs_tol=0.01):
                raise DataQualityError("HFQ output changed raw traded volume/amount")
    return {"status": "complete", "raw_hfq_dates": len(raw),
            "first_date": raw[0]["date"], "last_date": raw[-1]["date"],
            "scope": "successful_requested_query_only",
            "adjustment_scope": "same_source_native_hfq_price_window_relative",
            "absolute_ipo_anchor": "pending", "qfq_anchor": "sample_end_only",
            "calendar": "pending", "suspension_and_st": "pending", "all_a": "pending",
            "delisted": "pending", "beijing": "unverified", "pit_financial": "pending"}


def install_http_guard(timeout, requests_module):
    """Process-local guard, including AKShare's unbounded start-year query."""
    session = requests_module.sessions.Session
    original_request, original_init = session.request, session.__init__
    evidence = []
    def init(instance, *args, **kwargs):
        original_init(instance, *args, **kwargs)
        for scheme in ("https://", "http://"):
            instance.mount(scheme, requests_module.adapters.HTTPAdapter(max_retries=0))
    def request(instance, method, url, **kwargs):
        parsed = urlparse(url)
        if method.upper() != "GET" or parsed.scheme != "https" or parsed.hostname not in HTTP_HOSTS:
            raise SourceUnavailable("Unexpected endpoint outside the public Tencent backend")
        kwargs["timeout"] = timeout
        kwargs["allow_redirects"] = False
        try:
            response = original_request(instance, method, url, **kwargs)
        except requests_module.exceptions.RequestException as exc:
            # Exception URLs/proxy messages may contain environment credentials.
            raise SourceUnavailable("Tencent transport failed: " + type(exc).__name__) from None
        evidence.append({"url": url, "params": json_value(kwargs.get("params", {})),
                         "http_status": response.status_code, "bytes": len(response.content),
                         "response_sha256": hashlib.sha256(response.content).hexdigest()})
        if not 200 <= response.status_code < 300:
            raise SourceUnavailable("Tencent HTTP refused/unavailable: " + str(response.status_code))
        return response
    session.__init__, session.request = init, request
    return evidence


def worker_payload(code, start, end, timeout):
    import importlib.metadata
    import requests
    evidence = install_http_guard(timeout, requests)
    try:
        version = importlib.metadata.version("akshare")
    except importlib.metadata.PackageNotFoundError:
        raise DependencyError("Install AKShare " + SUPPORTED_AKSHARE_VERSION + " in this Python environment") from None
    if version != SUPPORTED_AKSHARE_VERSION:
        raise DependencyError("Install the audited AKShare version " + SUPPORTED_AKSHARE_VERSION)
    import akshare as ak
    # ISO dates are necessary for the audited Tencent implementation's year loop.
    frames = {}
    for name, adjustment in (("raw", ""), ("hfq", "hfq")):
        frame = ak.stock_zh_a_hist_tx(symbol=tencent_symbol(code), start_date=start,
                                     end_date=end, adjust=adjustment, timeout=timeout)
        if frame.empty:
            raise DataQualityError("Empty Tencent " + name + " price response")
        frames[name] = json_value(frame.to_dict("records"))
    return {"source": SOURCE, "akshare_version": version, "api": "stock_zh_a_hist_tx",
            "code": code, "start": start, "end": end, "observed_at": utcnow(),
            "http_evidence": evidence, **frames}


def fetch_with_deadline(code, start, end, *, deadline=45, http_timeout=8):
    command = [sys.executable, str(Path(__file__).resolve()), "--fetch-worker", code,
               start, end, str(http_timeout)]
    try:
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                   text=True, encoding="utf-8", timeout=deadline, check=False)
    except subprocess.TimeoutExpired:
        raise SourceUnavailable("Tencent security worker exceeded its outer deadline; no retry") from None
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("Worker exited without a structured result; stderr is withheld") from None
    if completed.returncode or payload.get("error"):
        kind = payload.get("error_kind", "local_failure")
        error = payload.get("error", "Unknown worker failure")
        if kind == "source_unavailable":
            raise SourceUnavailable(error)
        if kind == "data_quality":
            raise DataQualityError(error)
        if kind == "dependency_error":
            raise DependencyError(error)
        raise RuntimeError(error)
    return payload


def receipt_covers(receipt, start, end):
    return bool(receipt and receipt.get("source") == SOURCE and receipt.get("status") == "complete"
                and receipt.get("start", "9999") <= start and receipt.get("through", "") >= end
                and receipt.get("rows", 0) > 0 and receipt.get("artifact_sha256"))


def preserve_failed_receipt(previous, failure):
    if previous and previous.get("status") in {"complete", "partial"}:
        return {**previous, "latest_attempt_failure": failure, "last_attempt_status": "failed"}
    return failure


def find_receipt(db, dataset, code, start, end):
    query = {"dataset": dataset, "code": code, "source": SOURCE}
    for receipt in db.panda_axis_window_receipts.find(query, {"_id": 0}):
        if receipt_covers(receipt, start, end):
            return receipt
    receipt = db.panda_axis_sync.find_one(query, {"_id": 0})
    return receipt if receipt_covers(receipt, start, end) else None


def resumable_code(db, code, start, end):
    receipts = [find_receipt(db, name, code, start, end) for name in ("stock_day", "stock_adjusted_day")]
    if not all(receipts):
        return False
    for receipt in receipts:
        artifact = Path(receipt.get("artifact_path", ""))
        if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != receipt["artifact_sha256"]:
            return False
        count = db[receipt["dataset"]].count_documents({"code": code, "source": SOURCE,
            "date": {"$gte": receipt["start"], "$lte": receipt["through"]}})
        if count != receipt["rows"]:
            return False
    return True


def new_rows_without_conflicts(collection, rows):
    """A failed attempt cannot alter earlier accepted prices or quantities."""
    compare = ("source", "open", "high", "low", "close", "vol", "volume_shares", "amount")
    new = []
    for row in rows:
        previous = collection.find_one({"code": row["code"], "date": row["date"]}, {"_id": 0})
        if previous:
            if any(previous.get(k) != row[k] for k in compare):
                raise DataQualityError("Stored source prices changed; explicit revision reconciliation is required")
        else:
            new.append(row)
    return new


def insert_only(collection, rows):
    from pymongo import UpdateOne
    if rows:
        collection.bulk_write([UpdateOne({"code": r["code"], "date": r["date"]},
                                         {"$setOnInsert": r}, upsert=True) for r in rows], ordered=True)


def sync_code(db, code, start, end, run_id, output_dir, fetch=fetch_with_deadline, **fetch_options):
    try:
        payload = fetch(code, start, end, **fetch_options)
        if payload.get("source") != SOURCE or payload.get("code") != code or payload.get("start") != start or payload.get("end") != end:
            raise DataQualityError("Worker provenance differs from the requested source/window")
        raw = normalize_tencent_frame(payload["raw"], code, start, end)
        hfq = normalize_tencent_frame(payload["hfq"], code, start, end, "hfq")
        audit = audit_pair(raw, hfq)
        for rows in (raw, hfq):
            for row in rows:
                row.update(run_id=run_id, akshare_version=payload["akshare_version"],
                           source_api=payload["api"], source_observed_at=payload["observed_at"])
        # Inspect both datasets before writing either. Never overwrite an
        # accepted overlap merely because the source's latest revision changed.
        new_raw = new_rows_without_conflicts(db.stock_day, raw)
        new_hfq = new_rows_without_conflicts(db.stock_adjusted_day, hfq)
        artifact = Path(output_dir) / "source_windows" / (code + "_" + uuid.uuid4().hex + ".json")
        write_json(artifact, {"run_id": run_id, "interface_output": payload, "audit": audit})
        sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        insert_only(db.stock_day, new_raw)
        insert_only(db.stock_adjusted_day, new_hfq)
        if new_rows_without_conflicts(db.stock_day, raw) or new_rows_without_conflicts(db.stock_adjusted_day, hfq):
            raise RuntimeError("Stored datasets do not match the verified interface window")
        for name, rows in (("stock_day", raw), ("stock_adjusted_day", hfq)):
            receipt = {"dataset": name, "code": code, "source": SOURCE, "run_id": run_id,
                       "start": start, "through": end, "rows": len(rows), "synced_at": utcnow(),
                       "artifact_path": str(artifact.resolve()), "artifact_sha256": sha,
                       "akshare_version": payload["akshare_version"], **audit}
            key = {"dataset": name, "code": code, "source": SOURCE, "start": start, "through": end}
            db.panda_axis_window_receipts.update_one(key, {"$set": receipt}, upsert=True)
            key = {"dataset": name, "code": code}
            previous = db.panda_axis_sync.find_one(key, {"_id": 0})
            if not receipt_covers(previous, start, end):
                db.panda_axis_sync.update_one(key, {"$set": receipt}, upsert=True)
        result = {"code": code, "source": SOURCE, "rows": len(raw), "new_raw_rows": len(new_raw),
                  "new_hfq_rows": len(new_hfq), "artifact_sha256": sha, **audit}
    except Exception as exc:
        # Known errors contain only our messages. Local DB/URI errors can embed
        # credentials, so the report never copies an arbitrary exception string.
        error = str(exc) if isinstance(exc, (SourceUnavailable, DataQualityError, DependencyError)) else "Local failure: " + type(exc).__name__
        result = {"code": code, "source": SOURCE, "status": "failed", "rows": 0,
                  "run_id": run_id, "start": start, "through": end, "synced_at": utcnow(),
                  "error": error, "source_unavailable": isinstance(exc, SourceUnavailable)}
        for name in ("stock_day", "stock_adjusted_day"):
            failure = {**result, "dataset": name}
            key = {"dataset": name, "code": code}
            previous = db.panda_axis_sync.find_one(key, {"_id": 0})
            db.panda_axis_sync.update_one(key, {"$set": preserve_failed_receipt(previous, failure)}, upsert=True)
    db.panda_axis_akshare_attempts.insert_one({**result, "run_id": run_id, "observed_at": utcnow()})
    return result


def run_batch(db, codes, start, end, output_dir, run_id, *, fetch=fetch_with_deadline,
              resume_after_source_unblock=False, **fetch_options):
    progress = {"run_id": run_id, "source": SOURCE, "start": start, "end": end,
                "codes": codes, "results": [], "status": "running", "started_at": utcnow(),
                "acceptance": "query transport only; universe/calendar/execution/PIT remain pending"}
    path = Path(output_dir) / "progress.json"
    state = db.panda_axis_source_state.find_one({"source": SOURCE, "status": "blocked"}, {"_id": 0})
    if state and not resume_after_source_unblock:
        progress.update(status="source_blocked_waiting", source_state=state, network_workers_started=0)
        write_json(path, progress)
        return progress, 2
    for code in codes:
        if resumable_code(db, code, start, end):
            progress["results"].append({"code": code, "status": "complete", "resumed": True})
        else:
            result = sync_code(db, code, start, end, run_id, output_dir, fetch=fetch, **fetch_options)
            progress["results"].append(result)
            if result.get("source_unavailable"):
                state = {"source": SOURCE, "status": "blocked", "reason": result["error"],
                         "observed_at": utcnow(), "run_id": run_id,
                         "resume": "After external restoration explicitly pass --resume-after-source-unblock"}
                db.panda_axis_source_state.update_one({"source": SOURCE}, {"$set": state}, upsert=True)
                progress.update(status="source_blocked_waiting", source_state=state)
                write_json(path, progress)
                break
            if result["status"] == "complete":
                db.panda_axis_source_state.update_one({"source": SOURCE}, {"$set": {
                    "source": SOURCE, "status": "reachable", "observed_at": utcnow()}}, upsert=True)
        write_json(path, progress)
    completed = {r["code"] for r in progress["results"] if r["status"] == "complete"}
    dispatched = {r["code"] for r in progress["results"]}
    progress.update(completed_at=utcnow(), completed_codes=len(completed),
                    remaining_codes=len(codes) - len(completed),
                    pending_codes=[c for c in codes if c not in completed],
                    undispatched_codes=[c for c in codes if c not in dispatched])
    if progress["status"] == "running":
        progress["status"] = "complete_query_scope" if progress["completed_codes"] == len(codes) else "partial"
    write_json(path, progress)
    return progress, 2 if progress["status"] == "source_blocked_waiting" else int(progress["status"] == "partial")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=[SOURCE], required=True)
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument("--start", type=date_string, required=True)
    parser.add_argument("--end", type=date_string, required=True)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", type=validate_database, default=DEFAULT_DATABASE)
    parser.add_argument("--max-codes", type=int, default=1, help="Explicit small-batch bound, at most 10; default 1")
    parser.add_argument("--deadline", type=float, default=45, help="Seconds for both raw+HFQ, including initialization")
    parser.add_argument("--http-timeout", type=float, default=8)
    parser.add_argument("--output-dir", type=Path, default=Path("research_runs/akshare_tencent_sync"))
    parser.add_argument("--resume-after-source-unblock", action="store_true")
    args = parser.parse_args(argv)
    if args.start > args.end or not 1 <= args.max_codes <= 10:
        parser.error("Invalid window or small-batch bound")
    if not 0 < args.http_timeout <= args.deadline <= 300:
        parser.error("Require 0 < http-timeout <= deadline <= 300")
    args.codes = sorted(set(args.codes))
    if len(args.codes) > args.max_codes:
        parser.error("Too many codes for the explicit --max-codes bound")
    for code in args.codes:
        try:
            tencent_symbol(code)
        except ValueError as exc:
            parser.error(str(exc))
    return args


def main(argv=None):
    args = parse_args(argv)
    from pymongo import MongoClient
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        db = client[args.database]
        for name in ("stock_day", "stock_adjusted_day"):
            db[name].create_index([("code", 1), ("date", 1)], unique=True)
        db.panda_axis_sync.create_index([("dataset", 1), ("code", 1)], unique=True)
        db.panda_axis_window_receipts.create_index([(k, 1) for k in ("dataset", "code", "source", "start", "through")], unique=True)
        identity = {"source": SOURCE, "database": args.database, "start": args.start,
                    "end": args.end, "codes": args.codes,
                    "store_sha256": hashlib.sha256(args.uri.encode()).hexdigest()}
        run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        output = args.output_dir / run_id
        write_json(output / "manifest.json", {**identity, "run_id": run_id})
        report, status = run_batch(db, args.codes, args.start, args.end, output, run_id,
                                  resume_after_source_unblock=args.resume_after_source_unblock,
                                  deadline=args.deadline, http_timeout=args.http_timeout)
        print(json.dumps({"status": report["status"], "source": SOURCE, "database": args.database,
                          "run_id": run_id, "results": report["results"]}, ensure_ascii=False), flush=True)
        return status
    finally:
        client.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fetch-worker":
        try:
            # Third-party progress must not corrupt the structured worker pipe.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                payload = worker_payload(sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]))
            print(json.dumps(payload, ensure_ascii=True, allow_nan=False))
        except Exception as exc:
            kind = "source_unavailable" if isinstance(exc, SourceUnavailable) else "dependency_error" if isinstance(exc, DependencyError) else "data_quality" if isinstance(exc, DataQualityError) else "local_failure"
            error = str(exc) if kind != "local_failure" else "Worker local failure: " + type(exc).__name__
            print(json.dumps({"error_kind": kind, "error": error}, ensure_ascii=True))
            raise SystemExit(1)
    else:
        raise SystemExit(main())
