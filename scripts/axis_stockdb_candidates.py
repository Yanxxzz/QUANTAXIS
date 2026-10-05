#!/usr/bin/env python3
"""Explicit read-only StockDB archive candidates; never an AXIS price fallback.

Only the execution cache's open window is selectable. Coverage is measured
against the cache's audited date union, not a trading calendar or lifecycle
ledger. Original price basis and research eligibility remain unverified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panda_alpha.legacy_archive import ARCHIVE_COLLECTION, file_sha256, utcnow, write_json


FORMAL_CACHE = "execution_bars_20210922_20260921.parquet"
OPEN_START = "2021-09-22"
OPEN_END = "2026-09-18"
DEFAULT_AUDIT = Path("research_runs/legacy_prices_archive/execution_bars_20210922_20260921.audit.json")
PRICE_FIELDS = ("open", "high", "low", "close", "pre_close", "volume", "amount", "pct_chg", "is_st")


def _date(value):
    text = str(value)
    pattern = "%Y%m%d" if re.fullmatch(r"\d{8}", text) else "%Y-%m-%d"
    day = datetime.strptime(text, pattern).strftime("%Y-%m-%d")
    if pattern == "%Y-%m-%d" and text != day:
        raise ValueError("Dates must use YYYY-MM-DD or YYYYMMDD")
    return day


def _code(value):
    text = str(value).strip()
    if not re.fullmatch(r"\d{6}", text):
        raise ValueError("Codes must be explicit six-digit archive identifiers")
    return text


def validate_audit(audit):
    """Check the existing audit's binding; do not certify its price semantics."""
    manifest = audit.get("manifest", {})
    # Reject the sealed warmup source even if a caller requests an open window.
    basename = str(audit.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]
    if basename != FORMAL_CACHE or manifest.get("path") != FORMAL_CACHE:
        raise ValueError("Only the formal execution cache is selectable; sealed warmup is excluded")
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported archive manifest schema")
    for field in ("file_sha256", "logical_hash", "rows", "codes", "start", "end"):
        if audit.get(field) != manifest.get(field):
            raise ValueError(f"Archive audit/manifest {field} mismatch")
    for field in ("source_id", "file_sha256", "manifest_sha256", "logical_hash"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(audit.get(field, ""))):
            raise ValueError(f"Missing or invalid archive {field}")
    if audit["source_id"] != audit["file_sha256"]:
        raise ValueError("Archive source_id must bind the original file SHA256")
    if _date(audit["start"]) != OPEN_START or _date(audit["end"]) < OPEN_END:
        raise ValueError("Formal cache audit does not describe the required open window")
    required = {"date", "code", "open", "high", "low", "close", "volume", "amount"}
    if not required <= set(manifest.get("columns", [])):
        raise ValueError("Formal cache price schema is incomplete")
    dates = [_date(day) for day in audit.get("source_dates", [])]
    if not dates or dates != sorted(set(dates)) or dates[0] != _date(audit["start"]) or dates[-1] != _date(audit["end"]):
        raise ValueError("Audited source date union is missing or inconsistent")
    by_code = audit.get("by_code", {})
    if not by_code or len(by_code) != audit["codes"]:
        raise ValueError("Audited source code inventory is missing or inconsistent")
    for code in by_code:
        _code(code)
    return dates


def load_audit(path):
    audit = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_audit(audit)
    manifest_path = Path(audit["manifest_path"])
    if file_sha256(manifest_path) != audit["manifest_sha256"]:
        raise ValueError("Existing source manifest SHA256 changed")
    if json.loads(manifest_path.read_text(encoding="utf-8")) != audit["manifest"]:
        raise ValueError("Existing source manifest differs from the archived audit")
    return audit


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _quality(original):
    issues = []
    prices = [original.get(field) for field in ("open", "high", "low", "close")]
    if not all(_finite(value) and value > 0 for value in prices):
        issues.append("invalid_ohlc")
    elif original["low"] > min(original["open"], original["close"]) or original["high"] < max(original["open"], original["close"]) or original["low"] > original["high"]:
        issues.append("invalid_ohlc_range")
    for field in ("volume", "amount"):
        value = original.get(field)
        if not _finite(value) or value < 0:
            issues.append("invalid_" + field)
    return issues


def _json_value(value):
    # Preserve NaN/Infinity as explicit tokens; never silently impute a price.
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    return value


def query_candidates(db, audit, *, codes=None, start=OPEN_START, end=OPEN_END, sample_limit=20):
    """Stream only the selected archive source. No other collection is accessed."""
    source_dates = validate_audit(audit)
    start, end = _date(start), _date(end)
    if not OPEN_START <= start <= end <= OPEN_END:
        raise ValueError("Candidate dates must stay within 2021-09-22..2026-09-18")
    if isinstance(sample_limit, bool) or not isinstance(sample_limit, int) or not 0 <= sample_limit <= 1000:
        raise ValueError("sample_limit must be between 0 and 1000")
    selected = sorted(audit["by_code"] if codes is None else {_code(code) for code in codes})
    if not selected:
        raise ValueError("At least one explicit code is required")
    query = {"source_id": audit["source_id"], "date": {"$gte": start, "$lte": end}}
    if codes is not None:
        query["code"] = {"$in": selected}
    projection = {"_id": 0, "source_id": 1, "source_row": 1, "source": 1, "code": 1, "date": 1,
                  "adjustment": 1, "research_eligibility": 1, "original.code": 1, "original.date": 1,
                  **{"original." + field: 1 for field in PRICE_FIELDS}}
    grid = {day for day in source_dates if start <= day <= end}
    canonical_dates = {day: day for day in source_dates}
    observed = {code: set() for code in selected}
    valid = {code: set() for code in selected}
    counts, quality = Counter(), Counter()
    candidates = []
    # The existing archive index is (source_id, code, date). Avoid adding a
    # source_row sort that would force a large in-memory Mongo sort.
    cursor = db[ARCHIVE_COLLECTION].find(query, projection).sort([("code", 1), ("date", 1)])
    try:
        for row in cursor:
            code, day = _code(row.get("code")), _date(row.get("date"))
            day = canonical_dates.setdefault(day, day)
            if row.get("source_id") != audit["source_id"] or code not in observed or not start <= day <= end:
                raise ValueError("Archive query returned an unselected source/code/date")
            if row.get("source") != "legacy_stockdb_archive" or row.get("adjustment") != "unknown_unverified" or row.get("research_eligibility") != "archive_only":
                raise ValueError("Archive row has unexpected source or eligibility metadata")
            original = row.get("original", {})
            original_code = str(original.get("code", "")).strip()
            if original_code.endswith(".0"):
                original_code = original_code[:-2]
            if original_code.zfill(6) != code or _date(original.get("date")) != day:
                raise ValueError("Archive identity differs from its preserved original row")
            if day in observed[code]:
                quality["duplicate_code_date_rows"] += 1
            observed[code].add(day)
            counts[code] += 1
            issues = _quality(original)
            quality.update(issues)
            if issues:
                quality["invalid_observation_rows"] += 1
            else:
                valid[code].add(day)
            if original.get("volume") == 0:
                quality["zero_volume_rows_with_unknown_suspension_status"] += 1
            if len(candidates) < sample_limit:
                candidates.append({"code": code, "date": day, "source_id": row["source_id"],
                    "source_row": row.get("source_row"), "source": row["source"],
                    "price_basis": "unknown_unverified", "research_accepted": False,
                    "original": {field: _json_value(original[field]) for field in PRICE_FIELDS if field in original},
                    "quality_issues": issues})
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()
    by_code = []
    for code in selected:
        missing = sorted(grid - observed[code])
        by_code.append({"code": code, "audited_cache_code": code in audit["by_code"], "rows": counts[code],
            "observed_dates": len(observed[code]), "first": min(observed[code]) if observed[code] else None,
            "last": max(observed[code]) if observed[code] else None,
            "reference_grid_dates": len(grid), "observed_grid_dates": len(grid & observed[code]),
            "valid_price_grid_dates": len(grid & valid[code]),
            "observed_grid_coverage": len(grid & observed[code]) / len(grid) if grid else None,
            "reference_grid_missing_dates": missing, "outside_reference_grid_dates": sorted(observed[code] - grid)})
    denominator = len(grid) * len(selected)
    return {"schema_version": 1, "status": "archive_candidate_inventory_only", "observed_at": utcnow(),
        "collection": ARCHIVE_COLLECTION, "selection": {"source_id": audit["source_id"], "codes": selected,
            "start": start, "end": end, "formal_cache": FORMAL_CACHE, "automatic_fallback": False},
        "source": {"provenance": audit["manifest"].get("source"), "file_sha256": audit["file_sha256"],
            "manifest_sha256": audit["manifest_sha256"], "logical_hash": audit["logical_hash"],
            "audited_archive_rows": audit["rows"], "original_file_bytes_rechecked": False,
            "mongo_custody_reverified": False, "source_authentication": "unverified",
            "price_basis": "unknown_unverified", "qfq_hfq_supported": False},
        "coverage": {"reference": "audited_source_file_date_union", "independent_trading_calendar": False,
            "lifecycle_reconciled": False, "reference_dates": sorted(grid), "reference_code_date_cells": denominator,
            "rows": sum(counts.values()), "codes_with_rows": sum(bool(observed[code]) for code in selected),
            "observed_grid_cells": sum(item["observed_grid_dates"] for item in by_code),
            "valid_price_grid_cells": sum(item["valid_price_grid_dates"] for item in by_code),
            "reference_grid_missing_cells": sum(len(item["reference_grid_missing_dates"]) for item in by_code),
            "gap_interpretation": "Unobserved cache-grid cells; IPO/delisting/suspension/calendar/source reasons are unresolved",
            "quality_counts": dict(quality), "by_code": by_code},
        "candidates": candidates, "candidate_sample_limit": sample_limit,
        "candidate_sample_truncated": sum(counts.values()) > len(candidates),
        "acceptance": {"research_accepted": False, "can_retire_legacy": False,
            "all_a": "pending", "delisted": "pending", "pit_financial": "pending",
            "reason": "Archive custody and cache-grid coverage do not certify source, adjustment, calendar, lifecycle or research eligibility"},
        "permissions": {"data_mutations": 0, "formal_price_collection_reads": 0,
            "external_source_requests": 0, "sealed_oos_evaluations": 0, "platform_calls": 0, "recharge_spend": 0}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database", default="quantaxis")
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--codes", nargs="+", help="Explicit six-digit codes; omitted means the formal cache code inventory")
    parser.add_argument("--start", default=OPEN_START)
    parser.add_argument("--end", default=OPEN_END)
    parser.add_argument("--sample-limit", type=int, default=20, help="Candidate row sample size, 0..1000")
    parser.add_argument("--output", type=Path, default=Path("research_runs/axis_stockdb_candidates.json"))
    args = parser.parse_args(argv)
    audit = load_audit(args.audit)
    from pymongo import MongoClient
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    try:
        report = query_candidates(client[args.database], audit, codes=args.codes, start=args.start,
                                  end=args.end, sample_limit=args.sample_limit)
        write_json(args.output, report)
        print(json.dumps({"status": report["status"], "rows": report["coverage"]["rows"],
            "codes_with_rows": report["coverage"]["codes_with_rows"], "research_accepted": False,
            "price_basis": "unknown_unverified", "output": str(args.output)}, ensure_ascii=False), flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
