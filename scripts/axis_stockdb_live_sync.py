#!/usr/bin/env python3
"""Collect one bounded local StockDB daily query into an isolated candidate DB.

Example: python scripts/axis_stockdb_live_sync.py --code 600000 \
    --start 2024-01-02 --end 2024-01-10 --apply

The native table is read without SDK adjustment or factor prefetch. Its returned
values are evidence, not independently certified unadjusted exchange prices.
No market enumeration, source updates, research acceptance, or fallback occurs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse


DATABASE = "quantaxis_stockdb_live"
SOURCE = "stockdb_live"
MAX_WINDOW_DAYS = 31
WINDOW_START, WINDOW_END = "2019-09-20", "2026-09-18"
SDK_DIR = Path(r"D:\Quant-research\free-stockdb-windows-v0.3.5-more-power\stockdb\pybao")
FIELDS = ("date", "code", "name", "open", "high", "low", "close", "pre_close",
          "volume", "amount", "turnover", "pct_chg", "is_st", "total_mv", "pb", "pe_ttm")
RESULT_MARKER = "AXIS_STOCKDB_RESULT="


class ProbeError(RuntimeError):
    """Bounded source probe failed; caller must not retry automatically."""


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def portable_value(value):
    """Retain nonfinite source values as explicit tags, never as fabricated zero."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"stockdb_source_nonfinite": "NaN" if math.isnan(value) else
                "+Infinity" if value > 0 else "-Infinity"}
    if isinstance(value, dict):
        return {key: portable_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [portable_value(item) for item in value]
    return value


def validate_scope(code, start, end):
    if not isinstance(code, str) or not re.fullmatch(
            r"(?:(?:000|001|002|003|300|301|600|601|603|605|688|689|920)\d{3}|43\d{4}|8\d{5})", code):
        raise ValueError("Exactly one six-digit SH/SZ or explicit BJ/NEEQ-prefix candidate is required; wildcards forbidden")
    for value in (start, end):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Dates must be YYYY-MM-DD")
    first, last = (datetime.strptime(value, "%Y-%m-%d").date() for value in (start, end))
    if first > last or (last - first).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError("Only ordered windows of at most 31 calendar days are permitted")
    if start < WINDOW_START or end > WINDOW_END:
        raise ValueError("Query must remain inside the declared migration window")
    return {"table": "日k", "code": code, "start": start, "end": end,
            "date_filter": start.replace("-", "") + "<" + end.replace("-", ""),
            "fields": list(FIELDS), "host": "127.0.0.1", "port": 7899,
            "calls": 1, "adjustment_operation": "none; native table projection only",
            "identity_scope": "BJ/NEEQ-prefix historical identity pending" if code.startswith(("92", "43", "8"))
                              else "SH/SZ-prefix historical identity pending"}


def validate_deadline(seconds):
    if isinstance(seconds, bool) or not math.isfinite(seconds) or not 1 <= seconds <= 30:
        raise ValueError("Native subprocess deadline must be 1..30 seconds")


def native_query(query, sdk_dir, deadline):
    """Worker-only entry: import the pyd only after explicit scope validation."""
    validate_scope(query["code"], query["start"], query["end"])
    sys.path.insert(0, str(Path(sdk_dir).resolve()))
    import stockdb
    rd = stockdb.init(host="127.0.0.1", port=7899,
                      socket_timeout=max(1, int(deadline) - 1))
    rows = rd.vals("日k", query["code"], query["date_filter"]).get(",".join(FIELDS)).do()
    if rows is None:
        raise ProbeError("Source returned no response")
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_WINDOW_DAYS:
        raise ProbeError("Source row count/type exceeded the single-code daily bound")
    records = []
    for row in rows:
        if isinstance(row, dict):
            if set(row) != set(FIELDS):
                raise ProbeError("Source projection changed its fields")
            records.append({field: row[field] for field in FIELDS})
        elif isinstance(row, (list, tuple)) and len(row) == len(FIELDS):
            records.append(dict(zip(FIELDS, row)))
        else:
            raise ProbeError("Source projection shape mismatch")
    # Round-trip enforces a portable snapshot without silently coercing values.
    return json.loads(canonical_bytes(portable_value(records)))


def bounded_probe(query, sdk_dir=SDK_DIR, deadline=20, *, command=None):
    """Kill the native worker at its outer deadline; never retry a failed probe."""
    validate_scope(query["code"], query["start"], query["end"])
    validate_deadline(deadline)
    args = command or [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                       "--worker", "--code", query["code"], "--start", query["start"],
                       "--end", query["end"], "--sdk-dir", str(sdk_dir),
                       "--deadline", str(deadline)]
    try:
        result = subprocess.run(args, capture_output=True, timeout=deadline, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("native_subprocess_deadline_exceeded; no retry") from exc
    if result.returncode != 0:
        raise ProbeError("native_subprocess_failed; no retry")
    try:
        encoded = result.stdout.rsplit(RESULT_MARKER.encode("ascii"), 1)[1]
        payload = json.loads(encoded.decode("utf-8", errors="strict").strip())
    except (UnicodeError, ValueError, IndexError, AttributeError) as exc:
        raise ProbeError("native_subprocess_invalid_response; no retry") from exc
    if payload.get("status") != "ok" or not isinstance(payload.get("records"), list):
        raise ProbeError("native_source_query_failed; no retry")
    return payload["records"]


def normalize_records(records, query, *, snapshot_sha256, observed_at):
    """Validate returned identities, OHLC bounds and CNY/share plausibility.

    VWAP range checks can expose lot/CNY-unit mismatches. They cannot establish
    an adjustment history, historical security status, or calendar completeness.
    Missing fields, duplicate dates and out-of-window responses fail closed.
    """
    validate_scope(query["code"], query["start"], query["end"])
    if not isinstance(records, list) or len(records) > MAX_WINDOW_DAYS:
        raise ValueError("Source row count/type exceeded the daily bound")
    bars, seen, traded, zero_activity = [], set(), 0, 0
    for index, raw in enumerate(records):
        if not isinstance(raw, dict) or set(raw) != set(FIELDS):
            raise ValueError("Source projection changed its fields")
        code = str(raw["code"]).strip()
        day = str(raw["date"])
        if not re.fullmatch(r"\d{8}", day):
            raise ValueError("Source date must be YYYYMMDD")
        date = datetime.strptime(day, "%Y%m%d").date().isoformat()
        if code != query["code"] or not query["start"] <= date <= query["end"]:
            raise ValueError("Stale/misaligned source identity or date")
        if date in seen:
            raise ValueError("Duplicate code/date source records")
        seen.add(date)
        values = {}
        for field in ("open", "high", "low", "close", "pre_close", "volume", "amount"):
            value = raw[field]
            if (value is None or isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError("Missing/nonfinite/negative required OHLCV/amount")
            values[field] = float(value)
        low, high = values["low"], values["high"]
        if not low <= min(values["open"], values["close"]) <= max(
                values["open"], values["close"]) <= high:
            raise ValueError("Source OHLC bounds mismatch")
        volume, amount = values["volume"], values["amount"]
        if not volume.is_integer():
            raise ValueError("Share volume must be integral")
        if volume == 0 or amount == 0:
            if volume != 0 or amount != 0:
                raise ValueError("Source volume/amount activity mismatch")
            zero_activity += 1
        else:
            if low <= 0:
                raise ValueError("Traded source OHLC must be positive")
            vwap = amount / volume
            if not low * 0.95 - 0.02 <= vwap <= high * 1.05 + 0.02:
                raise ValueError("CNY/share units or untransformed OHLC plausibility mismatch")
            traded += 1
        row_sha = digest(raw)
        row_id = snapshot_sha256 + ":" + str(index)
        bars.append({"code": code, "date": date,
                     "date_stamp": datetime.strptime(date, "%Y-%m-%d").timestamp(),
                     **{field: raw[field] for field in ("open", "high", "low", "close", "amount")},
                     "vol": volume / 100, "volume_shares": raw["volume"],
                     "preclose": raw["pre_close"], "source": SOURCE,
                     "adjustment": "source_table_untransformed",
                     "price_adjustment_status": "pending_independent_raw_validation",
                     "volume_unit": "shares; QA vol in 100-share lots",
                     "amount_unit": "CNY; arithmetic_plausibility_checked",
                     "unit_status": "scoped_arithmetic_checks_only",
                     "research_eligibility": "candidate_only",
                     "source_observed_at": observed_at,
                     "source_snapshot_sha256": snapshot_sha256,
                     "source_row_sha256": row_sha, "source_row_id": row_id,
                     "source_record": raw,
                     "source_nonfinite_fields": [field for field, value in raw.items()
                         if isinstance(value, dict) and "stockdb_source_nonfinite" in value],
                     "trade_status": None, "is_st": None,
                     "security_status_verified": False,
                     "historical_membership_verified": False})
    bars.sort(key=lambda row: row["date"])
    return bars, {"status": "scoped_arithmetic_checks_only", "traded_rows_checked": traded,
                  "zero_activity_rows": zero_activity, "volume_shares_to_qa_lots": 100,
                  "amount": "CNY", "independent_provider_validation": "pending",
                  "adjustment_validation": "pending", "zero_activity_status": "unverified"}


def import_candidate(db, bars, receipt):
    """Only the isolated DB may accept candidates; existing values are immutable."""
    if getattr(db, "name", None) != DATABASE:
        raise ValueError("StockDB live candidates may only enter quantaxis_stockdb_live")
    from pymongo.errors import DuplicateKeyError
    # Preflight every collision before any writes from this query.
    existing_by_key = {}
    for row in bars:
        key = {"code": row["code"], "date_stamp": row["date_stamp"]}
        prior = db.stock_day.find_one(key)
        if prior and (prior.get("source") != SOURCE or
                      prior.get("source_row_sha256") != row["source_row_sha256"]):
            raise ValueError("Existing candidate code/date conflict; no overwrite")
        existing_by_key[(row["code"], row["date_stamp"])] = prior
    db.stock_day.create_index([("code", 1), ("date_stamp", 1)], unique=True)
    inserted, duplicates = 0, 0
    for row in bars:
        key = {"code": row["code"], "date_stamp": row["date_stamp"]}
        if existing_by_key[(row["code"], row["date_stamp"])]:
            duplicates += 1
        else:
            try:
                db.stock_day.insert_one(dict(row))
                inserted += 1
            except DuplicateKeyError:
                prior = db.stock_day.find_one(key)
                if not prior or prior.get("source") != SOURCE or prior.get("source_row_sha256") != row["source_row_sha256"]:
                    raise ValueError("Concurrent candidate conflict; no overwrite")
                duplicates += 1
        db.stockdb_row_receipt.update_one({"_id": row["source_row_id"]}, {"$setOnInsert": {
            **key, "date": row["date"], "source": SOURCE,
            "source_snapshot_sha256": row["source_snapshot_sha256"],
            "source_row_sha256": row["source_row_sha256"], "source_record": row["source_record"],
            "observed_at": row["source_observed_at"], "research_accepted": False}}, upsert=True)
    outcome = {"inserted": inserted, "duplicates": duplicates}
    db.panda_axis_sync.update_one({"_id": receipt["receipt_id"]},
        {"$setOnInsert": {**receipt, **outcome, "status": "candidate_imported", "database": DATABASE}}, upsert=True)
    return outcome


def save_json(path, value):
    path = Path(path)
    encoded = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Immutable source files cannot be silently replaced by a later attempt.
    if path.exists() and path.read_bytes() != encoded:
        raise ValueError("Refusing to overwrite an existing source/receipt artifact")
    if not path.exists():
        path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--deadline", type=float, default=20)
    parser.add_argument("--sdk-dir", type=Path, default=SDK_DIR)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--output-dir", type=Path, default=Path("research_runs/stockdb_live"))
    parser.add_argument("--apply", action="store_true", help="Import only into the fixed isolated candidate DB")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        query = validate_scope(args.code, args.start, args.end)
        validate_deadline(args.deadline)
        uri = urlparse(args.uri)
        if (uri.scheme != "mongodb" or uri.hostname not in ("127.0.0.1", "localhost", "::1")
                or uri.username is not None or uri.password is not None
                or uri.path not in ("", "/")):
            raise ValueError("Only a credential-free local Mongo URI without a database path is permitted")
    except ValueError as exc:
        parser.error(str(exc))
    if args.worker:
        try:
            records = native_query(query, args.sdk_dir, args.deadline)
            print(RESULT_MARKER + canonical_bytes({"status": "ok", "records": records}).decode("ascii"), flush=True)
            return 0
        except Exception:
            # Source error text/SDK logs may contain local configuration; never expose it.
            print(RESULT_MARKER + '{"status":"failed"}', flush=True)
            return 1
    observed = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = args.output_dir / (args.code + "_" + args.start + "_" + args.end + "_" + run_id)
    receipt = {"schema_version": 1, "source": SOURCE, "query": query,
               "observed_at": observed, "deadline_seconds": args.deadline,
               "database": DATABASE, "dataset": "stock_day", "status": "pending",
               "research_accepted": False, "requested_window_complete": False,
               "market_scope_complete": False, "historical_membership_verified": False,
               "adjustment_verified": False, "automatic_fallback": False,
               "pending": ["independent_raw_adjustment", "source_calendar_and_suspension",
                           "historical_security_state", "research_acceptance"]}
    try:
        records = bounded_probe(query, args.sdk_dir, args.deadline)
        sdk_file = args.sdk_dir / "stockdb.pyd"
        snapshot = {"schema_version": 1, "source": SOURCE, "query": query,
                    "observed_at": observed, "records": records,
                    "nonfinite_encoding": "explicit stockdb_source_nonfinite tag; no zero filling",
                    "sdk_sha256": hashlib.sha256(sdk_file.read_bytes()).hexdigest() if sdk_file.exists() else None}
        sha = save_json(directory / "source_snapshot.json", snapshot)
        receipt.update(snapshot_sha256=sha, snapshot_path=str((directory / "source_snapshot.json").resolve()))
        bars, units = normalize_records(records, query, snapshot_sha256=sha, observed_at=observed)
        receipt.update(rows=len(bars), first=bars[0]["date"] if bars else None,
                       last=bars[-1]["date"] if bars else None, units=units,
                       status="candidate_collected" if bars else "empty_source_response")
        receipt["receipt_id"] = SOURCE + ":" + sha
        save_json(directory / "candidate_rows.json", bars)
        if args.apply and bars:
            from pymongo import MongoClient
            with MongoClient(args.uri, serverSelectionTimeoutMS=3000, socketTimeoutMS=5000) as client:
                receipt.update(import_candidate(client[DATABASE], bars, receipt))
            receipt["status"] = "candidate_imported"
    except (ProbeError, ValueError) as exc:
        receipt.update(status="failed", error=str(exc))
    except Exception as exc:
        receipt.update(status="failed", error_type=type(exc).__name__)
    save_json(directory / "receipt.json", receipt)
    print(json.dumps({key: receipt.get(key) for key in (
        "status", "source", "database", "rows", "inserted", "duplicates", "error", "error_type")},
        ensure_ascii=True))
    print(str((directory / "receipt.json").resolve()))
    return 0 if receipt["status"] in ("candidate_collected", "candidate_imported", "empty_source_response") else 1


if __name__ == "__main__":
    raise SystemExit(main())
