#!/usr/bin/env python3
"""Resume all source-supported SH/SZ lifecycle raw histories, including delisted.

This downloads prices/factors and audits dates. It never evaluates factor values,
returns, or holdout labels. Beijing, minutes and financial PIT remain separate.
BaoStock owns process-global sockets: workers are independent processes.
"""
from __future__ import annotations

import argparse
import atexit
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panda_alpha.universe import normalize_lifecycles, window_lifecycles, lifecycle_sessions, lifecycle_window
from scripts.axis_sync import (baostock_records, date_string, fetch_baostock_bars,
                              fetch_baostock_calendar, fetch_baostock_factors,
                              merge_calendar_receipt, upsert_many, baostock_code)

_bs = _db = _client = _context = None
_login_verified = False


class SourceBlockedError(RuntimeError):
    error_code = "10001011"
    official_name = "BSERR_BLACKLIST_USER"


def source_blocked_error(error) -> bool:
    import re
    return isinstance(error, SourceBlockedError) or bool(re.search(r"\b10001011\b", str(error)))


def record_source_block(db, run_id, error):
    doc = {"source": "baostock", "status": "blocked", "error_code": "10001011",
           "official_name": "BSERR_BLACKLIST_USER", "run_id": run_id, "observed_at": utcnow(),
           "error": str(error), "resume": "Wait for external restoration; explicitly pass --resume-after-source-unblock"}
    db.panda_axis_source_state.update_one({"source": "baostock"}, {"$set": doc}, upsert=True)
    return doc


def ensure_source_available(db):
    state = db.panda_axis_source_state.find_one({"source": "baostock", "status": "blocked"})
    if state:
        raise SourceBlockedError("BaoStock 10001011 BSERR_BLACKLIST_USER; persisted source circuit is open")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def storage_identity(uri: str, database: str) -> str:
    # The manifest identifies its durable store without copying connection secrets.
    return hashlib.sha256(f"{uri}|{database}".encode()).hexdigest()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def resumable_code(result: dict) -> bool:
    # Known suspensions and unavailable factors are finished transport evidence,
    # not a reason to retry the same successful empty/source-partial response.
    return bool(result.get("transport_complete") and result.get("source") == "baostock")


def receipt_covers(receipt: dict | None, start: str, end: str, dataset: str) -> bool:
    if not receipt or receipt.get("source") != "baostock" or receipt.get("through", "") < end:
        return False
    if receipt.get("status") not in {"complete", "partial"}:
        return False
    if dataset == "stock_adj":
        return receipt.get("history_complete") is True
    return receipt.get("start", "9999") <= start and receipt.get("scope", "").startswith("successful_requested_query")


def audit_raw_rows(rows: list[dict], expected: list[str]) -> dict:
    dates = [r["date"] for r in rows]
    suspended = [r["date"] for r in rows if r.get("trade_status") == "0"]
    unknown_missing = [r["date"] for r in rows if r.get("missing_numeric_fields") and r.get("trade_status") != "0"]
    priced = {r["date"] for r in rows if r.get("trade_status") != "0" and not r.get("missing_numeric_fields")}
    return {"raw_rows": len(rows), "expected_sessions": len(expected), "priced_tradable_rows": len(priced),
            "source_missing_dates": sorted(set(expected) - set(dates)), "suspended_dates": suspended,
            "unknown_missing_dates": unknown_missing, "off_lifecycle_dates": sorted(set(dates) - set(expected)),
            "duplicate_dates": len(dates) - len(set(dates)),
            "raw_coverage": len(set(expected) & set(dates)) / len(expected) if expected else None,
            "tradable_coverage": len(set(expected) & priced) / len(expected) if expected else None}


def fetch_native_hfq_panel(bs, code: str, start: str, end: str) -> list[dict]:
    """Native adjusted OHLC evidence, without claiming an absolute IPO origin."""
    import math
    source_rows = baostock_records(bs.query_history_k_data_plus(baostock_code(code),
        "date,code,open,high,low,close,tradestatus", start_date=start, end_date=end,
        frequency="d", adjustflag="1"))
    out = []
    for row in source_rows:
        day = date_string(row["date"])
        if row["code"] != baostock_code(code) or not start <= day <= end:
            raise ValueError("Native HFQ code/date differs from requested window")
        prices = {k: float(row[k]) if row[k].strip() else None for k in ["open", "high", "low", "close"]}
        valid = all(v is not None and math.isfinite(v) and v > 0 for v in prices.values())
        if valid:
            valid = prices["high"] >= max(prices.values()) and prices["low"] <= min(prices.values())
        out.append({"code": code, "date": day, **prices, "source": "baostock", "adjustment": "hfq",
                    "source_adjustflag": "1", "trade_status": row["tradestatus"], "valid_ohlc": valid,
                    "evidence_scope": "native_hfq_price_window_relative", "absolute_ipo_anchor": "pending",
                    "observed_through": end})
    if len({r["date"] for r in out}) != len(out):
        raise ValueError("Duplicate source native HFQ dates")
    return out


def read_baostock_packet(connection, *, max_bytes=128 * 1024 * 1024, deadline_seconds=120):
    """SDK 0.9.4 loops forever on recv(b''); require a complete real packet."""
    packet = bytearray()
    deadline = time.monotonic() + deadline_seconds
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("BaoStock packet did not finish before deadline")
        chunk = connection.recv(8192)
        if not chunk:
            raise ConnectionError("BaoStock peer closed before packet terminator")
        packet.extend(chunk)
        if len(packet) > max_bytes:
            raise ValueError("BaoStock packet exceeds bounded receiver size")
        if packet.endswith(b"<![CDATA[]]>\n"):
            return bytes(packet)


def install_baostock_transport(timeout=30):
    """Process-local official framing with bounded sockets and explicit EOF errors."""
    import zlib
    import baostock.util.socketutil as transport
    import baostock.common.context as context
    import baostock.common.contants as constants
    socket.setdefaulttimeout(timeout)
    def send_msg(message):
        connection = getattr(context, "default_socket", None)
        if connection is None:
            raise ConnectionError("BaoStock has no logged-in socket")
        connection.settimeout(timeout)
        connection.sendall((message + "\n").encode("utf-8"))
        packet = read_baostock_packet(connection)
        if len(packet) < constants.MESSAGE_HEADER_LENGTH:
            raise ValueError("Truncated BaoStock response header")
        header = packet[:constants.MESSAGE_HEADER_LENGTH].decode("utf-8")
        fields = header.split(constants.MESSAGE_SPLIT)
        if len(fields) < 3:
            raise ValueError("Malformed BaoStock response header")
        if fields[1] in constants.COMPRESSED_MESSAGE_TYPE_TUPLE:
            length = int(fields[2])
            body = packet[constants.MESSAGE_HEADER_LENGTH:constants.MESSAGE_HEADER_LENGTH + length]
            if len(body) != length:
                raise ValueError("Truncated compressed BaoStock body")
            return header + zlib.decompress(body).decode("utf-8")
        return packet.decode("utf-8")
    transport.send_msg = send_msg


def _worker_close():
    try:
        if _bs and _login_verified and not _db.panda_axis_source_state.find_one({"source": "baostock", "status": "blocked"}):
            _bs.logout()
    finally:
        if _client:
            _client.close()


def login_source(bs, attempts=3, sleep=time.sleep):
    """A transient anonymous login timeout must not poison the whole process pool."""
    last_error = None
    for attempt in range(attempts):
        try:
            result = bs.login()
            if str(result.error_code) == "0":
                return
            if str(result.error_code) == "10001011":
                raise SourceBlockedError("BaoStock 10001011 BSERR_BLACKLIST_USER; anonymous source access rejected")
            last_error = RuntimeError(f"BaoStock login {result.error_code}: {result.error_msg}")
        except SourceBlockedError:
            raise
        except Exception as exc:
            last_error = exc
        try:
            bs.logout()
        except Exception:
            pass
        if attempt + 1 < attempts:
            sleep(min(2 ** attempt, 4))
    raise last_error


def _worker_init(context):
    global _bs, _db, _client, _context, _login_verified
    import baostock
    from pymongo import MongoClient
    _bs, _context = baostock, context
    install_baostock_transport()
    _client = MongoClient(context["uri"], serverSelectionTimeoutMS=5000)
    _client.admin.command("ping")
    _db = _client[context["database"]]
    try:
        ensure_source_available(_db)
        login_source(_bs)
        _login_verified = True
    except Exception as exc:
        # Keep the worker alive. Its first necessary network task retries and
        # reports a per-dataset transport failure if the source stays unavailable.
        _login_verified = False
        if source_blocked_error(exc):
            record_source_block(_db, context["run_id"], exc)
    atexit.register(_worker_close)


def _receipt(dataset, code, rows, status, **extra):
    doc = {"dataset": dataset, "code": code, "source": "baostock", "status": status,
           "start": _context["start"], "through": _context["end"], "rows": rows,
           "synced_at": utcnow(), **extra}
    if status == "failed":
        _db.panda_axis_market_attempts.insert_one(dict(doc))
        previous = _db.panda_axis_sync.find_one({"dataset": dataset, "code": code}, {"_id": 0})
        doc = receipt_after_failure(previous, doc)
    _db.panda_axis_sync.update_one({"dataset": dataset, "code": code}, {"$set": doc}, upsert=True)
    return doc


def receipt_after_failure(previous: dict | None, failure: dict) -> dict:
    """An unsuccessful retry never overwrites successful historical evidence."""
    if previous and previous.get("status") in {"complete", "partial"}:
        return {**previous, "latest_attempt_failure": failure, "last_attempt_status": "failed",
                "last_attempt_at": failure["synced_at"]}
    return failure


def _sync_code(record):
    global _login_verified
    code = record["code"]
    expected = lifecycle_sessions(record, _context["sessions"], _context["start"], _context["end"])
    window = lifecycle_window(record, _context["start"], _context["end"])
    result = {"code": code, "source": "baostock", "run_id": _context["run_id"],
              "ipo_date": record.get("ipo_date"), "delisted_date": record.get("delisted_date"),
              "listing_status": record.get("listing_status"), "lifecycle_issues": record.get("lifecycle_issues", []),
              "transport_complete": False, "datasets": {}, "failures": []}
    for dataset in ["stock_day", "stock_adj"]:
        last_error = None
        for attempt in range(_context["retries"] + 1):
            try:
                ensure_source_available(_db)
                old = _db.panda_axis_sync.find_one({"dataset": dataset, "code": code}, {"_id": 0})
                if dataset == "stock_day":
                    reused = receipt_covers(old, window[0], window[1], dataset) if window else False
                    if not reused and not _login_verified:
                        login_source(_bs)
                        _login_verified = True
                    rows = list(_db.stock_day.find({"code": code, "source": "baostock", "date": {"$gte": window[0], "$lte": window[1]}}, {"_id": 0})) if reused else fetch_baostock_bars(_bs, code, *window) if window else []
                    if not reused:
                        upsert_many(_db.stock_day, rows, ["code", "date_stamp"])
                    audit = audit_raw_rows(rows, expected)
                    partial = audit["source_missing_dates"] or audit["unknown_missing_dates"] or audit["off_lifecycle_dates"] or audit["duplicate_dates"] or audit["suspended_dates"] or record.get("lifecycle_issues")
                    _receipt(dataset, code, len(rows), "partial" if partial else "complete", **audit,
                             scope="successful_requested_query; lifecycle_calendar_coverage_audited",
                             query_start=window[0] if window else None, query_end=window[1] if window else None,
                             reused_same_source_receipt=reused)
                    result["datasets"][dataset] = {**audit, "reused": reused}
                else:
                    reused = receipt_covers(old, _context["start"], _context["end"], dataset)
                    if reused:
                        rows = list(_db.stock_adj.find({"code": code, "source": "baostock", "date": {"$lte": _context["end"]}}, {"_id": 0}))
                        evidence = {k: old.get(k) for k in ["history_complete", "factor_origin", "ipo_date", "factor_method"]}
                    else:
                        if not _login_verified:
                            login_source(_bs)
                            _login_verified = True
                        rows, evidence = fetch_baostock_factors(_bs, code, _context["end"], record)
                        upsert_many(_db.stock_adj, rows, ["code", "date", "source"])
                    _receipt(dataset, code, len(rows), "complete" if evidence["history_complete"] else "partial", **evidence)
                    result["datasets"][dataset] = {"rows": len(rows), **evidence, "reused": reused}
                last_error = None
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if source_blocked_error(exc):
                    record_source_block(_db, _context["run_id"], exc)
                    result["source_blocked"] = True
                    result["failures"].append({"dataset": "source", "error_code": "10001011",
                                               "official_name": "BSERR_BLACKLIST_USER", "error": last_error})
                    _receipt(dataset, code, 0, "failed", error=last_error, source_blocked=True,
                             error_code="10001011", official_name="BSERR_BLACKLIST_USER")
                    break
                if attempt < _context["retries"]:
                    try:
                        _bs.logout()
                        login_source(_bs)
                        _login_verified = True
                    except Exception as login_exc:
                        _login_verified = False
                        if source_blocked_error(login_exc):
                            record_source_block(_db, _context["run_id"], login_exc)
                            result["source_blocked"] = True
                            result["failures"].append({"dataset": "source", "error_code": "10001011",
                                "official_name": "BSERR_BLACKLIST_USER", "error": str(login_exc)})
                            _receipt(dataset, code, 0, "failed", error=str(login_exc), source_blocked=True,
                                     error_code="10001011", official_name="BSERR_BLACKLIST_USER")
                            break
                    time.sleep(min(2 ** attempt, 4))
        if last_error:
            if result.get("source_blocked"):
                break
            result["failures"].append({"dataset": dataset, "error": last_error})
            _receipt(dataset, code, 0, "failed", error=last_error)
    result["transport_complete"] = not result["failures"]
    day = result["datasets"].get("stock_day", {})
    adj = result["datasets"].get("stock_adj", {})
    result["data_acceptance"] = "pending" if result["failures"] or record.get("lifecycle_issues") or day.get("source_missing_dates") or day.get("unknown_missing_dates") or day.get("off_lifecycle_dates") or day.get("duplicate_dates") or not adj.get("history_complete") else "source_raw_scope_complete"
    result["completed_at"] = utcnow()
    _db.panda_axis_market_sync.update_one({"run_id": _context["run_id"], "code": code}, {"$set": result}, upsert=True)
    return result


def summarize(results: dict, total: int, started_at: str, run_id: str) -> dict:
    values = list(results.values())
    raw = sum(r.get("datasets", {}).get("stock_day", {}).get("raw_rows", 0) for r in values)
    complete = sum(resumable_code(r) for r in values)
    return {"run_id": run_id, "started_at": started_at, "updated_at": utcnow(),
            "total_codes": total, "attempted_codes": len(values), "transport_complete_codes": complete,
            "remaining_codes": total - complete, "failed_codes": sum(bool(r.get("failures")) and not r.get("source_blocked") for r in values),
            "source_blocked_attempts": sum(bool(r.get("source_blocked")) for r in values),
            "raw_day_rows": raw, "factor_rows": sum(r.get("datasets", {}).get("stock_adj", {}).get("rows", 0) for r in values),
            "inactive_codes_completed": sum(resumable_code(r) and r.get("listing_status") == "0" for r in values),
            "source_missing_sessions": sum(len(r.get("datasets", {}).get("stock_day", {}).get("source_missing_dates", [])) for r in values),
            "suspended_sessions": sum(len(r.get("datasets", {}).get("stock_day", {}).get("suspended_dates", [])) for r in values),
            "unknown_missing_sessions": sum(len(r.get("datasets", {}).get("stock_day", {}).get("unknown_missing_dates", [])) for r in values),
            "unverified_adjustment_codes": sum(resumable_code(r) and not r.get("datasets", {}).get("stock_adj", {}).get("history_complete") for r in values),
            "adjustment_not_downloaded_codes": sum("stock_adj" not in r.get("datasets", {}) for r in values),
            "status": "running" if complete < total else "downloaded_source_scope_requires_acceptance",
            "scope": "baostock_shsz_a_lifecycle", "beijing": "unsupported_live_probe",
            "migration": {"can_retire_legacy": False, "reason": "Full exchange/BJ, minute and PIT acceptance remain separate"}}


def checkpoint_durable_store(args, db, state=None):
    """Merge branches from actual durable execution evidence, without source calls."""
    manifest = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    universe = manifest["universe"]
    targets = window_lifecycles(universe["records"], args.start, args.end)
    if args.code_prefix:
        targets = [r for r in targets if r["code"].startswith(tuple(args.code_prefix))]
    if args.max_codes:
        targets = targets[:args.max_codes]
    codes = [r["code"] for r in targets]
    run_id = hashlib.sha256(f"{args.start}|{args.end}|{universe['universe_hash']}".encode()).hexdigest()[:24]
    results = {}
    for row in db.panda_axis_market_sync.find({"run_id": run_id, "code": {"$in": codes}}, {"_id": 0}):
        if any(source_blocked_error(f.get("error", "")) for f in row.get("failures", [])):
            row["source_blocked"] = True
            row["failure_classification"] = "external_source_block; not missing_security_data"
        results[row["code"]] = row
    report = summarize(results, len(targets), manifest["created_at"], run_id)
    physical = list(db.stock_day.aggregate([
        {"$match": {"source": "baostock", "code": {"$in": codes}, "date": {"$gte": args.start, "$lte": args.end}}},
        {"$group": {"_id": "$code", "rows": {"$sum": 1}, "start": {"$min": "$date"}, "end": {"$max": "$date"}}}]))
    report["physical_raw_rows"] = sum(r["rows"] for r in physical)
    report["physical_raw_codes"] = len(physical)
    report["physical_factor_rows"] = db.stock_adj.count_documents({"source": "baostock", "code": {"$in": codes}, "date": {"$lte": args.end}})
    report["source_state"] = state or db.panda_axis_source_state.find_one({"source": "baostock"}, {"_id": 0})
    if report["source_state"] and report["source_state"].get("status") == "blocked":
        report["status"] = "source_blocked_checkpoint_preserved"
    report["unvisited_codes"] = sorted(set(codes) - set(results))
    report["unverified_adjustment_code_list"] = sorted(r["code"] for r in results.values()
        if r.get("transport_complete") and not r.get("datasets", {}).get("stock_adj", {}).get("history_complete"))
    report["sealed_policy"] = "Raw storage/date completeness only; no holdout returns or factor evaluation"
    write_json(args.output_dir / "results.json", results)
    write_json(args.output_dir / "progress.json", report)
    write_json(args.output_dir / "checkpoint.json", {"summary": report, "physical_by_code": physical, "results": results})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date_string, required=True)
    parser.add_argument("--end", type=date_string, required=True)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--chunk-size", type=int, default=32)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=Path("research_runs/market_sync"))
    parser.add_argument("--metadata-json", type=Path, help="previous real query_stock_basic raw snapshot")
    parser.add_argument("--max-codes", type=int, help="explicit bounded smoke run; omitted means all source lifecycles")
    parser.add_argument("--code-prefix", nargs="+", help="optional cohort, e.g. 6 for Shanghai; full scope is still retained in the manifest")
    parser.add_argument("--checkpoint-only", action="store_true", help="merge durable branches offline without contacting BaoStock")
    parser.add_argument("--resume-after-source-unblock", action="store_true", help="authorize one ordinary login probe only after the provider has restored access")
    args = parser.parse_args(argv)
    if args.start > args.end or not 1 <= args.workers <= 12 or args.chunk_size < 1 or args.retries < 0 or (args.max_codes is not None and args.max_codes < 1):
        parser.error("Invalid window, worker/chunk/retry or smoke limits")
    import baostock as bs
    from pymongo import MongoClient
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[args.database]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    state = db.panda_axis_source_state.find_one({"source": "baostock", "status": "blocked"}, {"_id": 0})
    if args.checkpoint_only or (state and not args.resume_after_source_unblock):
        report = checkpoint_durable_store(args, db, state)
        print(json.dumps({k: report[k] for k in ["status", "transport_complete_codes", "raw_day_rows", "remaining_codes", "source_blocked_attempts"]}), flush=True)
        client.close()
        return 2 if state else 0
    if state and args.resume_after_source_unblock:
        db.panda_axis_source_state.update_one({"source": "baostock"}, {"$set": {"status": "explicit_restore_probe_authorized", "authorized_at": utcnow()}})
    install_baostock_transport()
    try:
        login_source(bs)
    except SourceBlockedError as exc:
        state = record_source_block(db, None, exc)
        if manifest_path.exists():
            checkpoint_durable_store(args, db, state)
        client.close()
        return 2
    db.panda_axis_source_state.update_one({"source": "baostock"}, {"$set": {"source": "baostock", "status": "reachable", "observed_at": utcnow()}}, upsert=True)
    try:
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (manifest["start"], manifest["end"]) != (args.start, args.end):
                parser.error("Existing manifest uses a different window; choose another output-dir")
            if manifest.get("storage_identity") not in {None, storage_identity(args.uri, args.database)}:
                parser.error("Existing manifest uses a different Mongo store; choose another output-dir")
            universe = manifest["universe"]
        else:
            raw = json.loads(args.metadata_json.read_text(encoding="utf-8")) if args.metadata_json else baostock_records(bs.query_stock_basic())
            universe = normalize_lifecycles(raw)
            manifest = {"start": args.start, "end": args.end, "universe": universe,
                        "source_metadata_sha256": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                        "created_at": utcnow(), "holdout_policy": "raw synchronization only; no return/factor evaluation"}
        manifest["storage_identity"] = storage_identity(args.uri, args.database)
        write_json(manifest_path, manifest)
        window_targets = window_lifecycles(universe["records"], args.start, args.end)
        targets = [r for r in window_targets if not args.code_prefix or r["code"].startswith(tuple(args.code_prefix))]
        if args.max_codes:
            targets = targets[:args.max_codes]
        run_id = hashlib.sha256(f"{args.start}|{args.end}|{universe['universe_hash']}".encode()).hexdigest()[:24]
        for collection, keys in [(db.stock_day, [("code", 1), ("date_stamp", 1)]),
                (db.stock_adj, [("code", 1), ("date", 1), ("source", 1)]),
                (db.stock_list, [("code", 1)]), (db.stock_lifecycle, [("source", 1), ("code", 1)]),
                (db.trade_calendar, [("exchange", 1), ("date", 1)]),
                (db.panda_axis_sync, [("dataset", 1), ("code", 1)]),
                (db.panda_axis_market_sync, [("run_id", 1), ("code", 1)])]:
            collection.create_index(keys, unique=True)
        upsert_many(db.stock_lifecycle, universe["records"], ["source", "code"])
        upsert_many(db.stock_list, universe["records"], ["code"])
        calendar = fetch_baostock_calendar(bs, args.start, args.end)
        upsert_many(db.trade_calendar, calendar, ["exchange", "date"])
        cal_receipt = {"dataset": "trade_calendar", "code": "SSE", "status": "complete", "source": "baostock",
                       "start": args.start, "through": args.end, "rows": len(calendar), "synced_at": utcnow()}
        cal_receipt = merge_calendar_receipt(db.panda_axis_sync.find_one({"dataset": "trade_calendar", "code": "SSE"}, {"_id": 0}), cal_receipt)
        db.panda_axis_sync.update_one({"dataset": "trade_calendar", "code": "SSE"}, {"$set": cal_receipt}, upsert=True)
        db.panda_axis_sync.update_one({"dataset": "stock_lifecycle", "code": "SHSZ"}, {"$set": {
            "dataset": "stock_lifecycle", "code": "SHSZ", "source": "baostock", "status": "complete" if not universe["issues"] else "partial",
            "start": args.start, "through": args.end, "rows": len(universe["records"]), "window_codes": len(window_targets),
            "selected_codes": len(targets), "selected_prefixes": args.code_prefix,
            "universe_hash": universe["universe_hash"], "synced_at": utcnow(), "scope": universe["source"],
            "issues": universe["issues"], "exchange_completeness": "pending_independent_validation"}}, upsert=True)
    except Exception as exc:
        if source_blocked_error(exc):
            state = record_source_block(db, None, exc)
            if manifest_path.exists():
                checkpoint_durable_store(args, db, state)
            return 2
        raise
    finally:
        if not db.panda_axis_source_state.find_one({"source": "baostock", "status": "blocked"}):
            bs.logout()
        else:
            client.close()
    result_path = args.output_dir / "results.json"
    # A JSON report alone is not evidence that the chosen Mongo still has the
    # downloads. Resume from durable per-code Mongo execution records only.
    results = {}
    for r in db.panda_axis_market_sync.find({"run_id": run_id, "code": {"$in": [t["code"] for t in targets]}}, {"_id": 0}):
        results[r["code"]] = r
    results = {k: v for k, v in results.items() if k in {t["code"] for t in targets}}
    pending = [t for t in targets if not resumable_code(results.get(t["code"], {}))]
    started_at = utcnow()
    context = {"uri": args.uri, "database": args.database, "start": args.start, "end": args.end,
               "sessions": [r["date"] for r in calendar], "run_id": run_id, "retries": args.retries}
    progress_path = args.output_dir / "progress.json"
    journal = args.output_dir / "events.jsonl"
    write_json(progress_path, summarize(results, len(targets), started_at, run_id))
    print(json.dumps({"run_id": run_id, "total_codes": len(targets), "resume_pending": len(pending),
                      "window_delisted": sum(t["listing_status"] == "0" for t in targets), "workers": args.workers}), flush=True)
    interruption = None
    circuit_open = False
    try:
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_worker_init, initargs=(context,)) as pool:
            for offset in range(0, len(pending), args.chunk_size):
                futures = {pool.submit(_sync_code, t): t["code"] for t in pending[offset:offset + args.chunk_size]}
                for future in as_completed(futures):
                    code = futures[future]
                    if future.cancelled():
                        continue
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {"code": code, "source": "baostock", "run_id": run_id, "transport_complete": False,
                                  "failures": [{"dataset": "worker", "error": f"{type(exc).__name__}: {exc}"}]}
                    results[code] = result
                    if result.get("source_blocked"):
                        circuit_open = True
                        for queued in futures:
                            queued.cancel()
                    with journal.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
                    progress = summarize(results, len(targets), started_at, run_id)
                    write_json(progress_path, progress)
                    if len(results) % max(1, args.workers) == 0 or result.get("failures"):
                        print(json.dumps({k: progress[k] for k in ["attempted_codes", "total_codes", "raw_day_rows", "failed_codes", "remaining_codes"]}), flush=True)
                write_json(result_path, results)
                if circuit_open:
                    break
    except KeyboardInterrupt:
        write_json(result_path, results)
        raise
    except Exception as exc:
        interruption = f"{type(exc).__name__}: {exc}"
    final = summarize(results, len(targets), started_at, run_id)
    if circuit_open:
        final["status"] = "source_blocked_checkpoint_preserved"
        final["source_state"] = db.panda_axis_source_state.find_one({"source": "baostock"}, {"_id": 0})
    if final["failed_codes"]:
        final["status"] = "transport_failures_require_resume"
    if interruption:
        final["status"] = "transport_interruption_requires_resume"
        final["interruption_error"] = interruption
    if circuit_open:
        final["status"] = "source_blocked_checkpoint_preserved"
    write_json(progress_path, final)
    write_json(result_path, results)
    print(json.dumps(final, ensure_ascii=False), flush=True)
    client.close()
    return 2 if circuit_open else 1 if final["failed_codes"] or interruption else 0


if __name__ == "__main__":
    raise SystemExit(main())
