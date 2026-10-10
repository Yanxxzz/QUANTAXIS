"""Retrospective source lifecycles, without assuming current listings are history.

BaoStock's ``query_stock_basic`` returns IPO/outDate/type/status for active and
delisted Shanghai/Shenzhen securities. This ledger describes that source's
coverage; it does not assert exchange-wide completeness or Beijing support.
An outDate is an effective delisting date, so membership ends before that date.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import Iterable


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def is_shsz_a(row: dict) -> bool:
    code = row.get("code", "")
    return row.get("type") == "1" and (code.startswith("sh.6") or code.startswith(("sz.00", "sz.30")))


def normalize_lifecycles(rows: Iterable[dict], observed_at: str | None = None) -> dict:
    """Preserve inactive stocks and report malformed lifecycle evidence explicitly."""
    rows = list(rows)
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    records, issues, seen = [], [], set()
    for row in rows:
        if not is_shsz_a(row):
            continue
        raw = row["code"]
        code = raw[3:]
        record_issues = []
        if len(code) != 6 or not code.isdigit():
            issues.append({"source_code": raw, "reason": "invalid_code"})
            continue
        if code in seen:
            issues.append({"code": code, "reason": "duplicate_lifecycle"})
            continue
        seen.add(code)
        dates = {}
        for source_field, field in [("ipoDate", "ipo_date"), ("outDate", "delisted_date")]:
            try:
                dates[field] = _iso(row.get(source_field))
            except (ValueError, TypeError):
                dates[field] = None
                record_issues.append(f"invalid_{source_field}")
        if not dates["ipo_date"]:
            record_issues.append("missing_ipo_date")
        if row.get("status") == "0" and not dates["delisted_date"]:
            record_issues.append("inactive_missing_delisted_date")
        if dates["ipo_date"] and dates["delisted_date"] and dates["delisted_date"] <= dates["ipo_date"]:
            record_issues.append("delisted_not_after_ipo")
        if row.get("status") not in {"0", "1"}:
            record_issues.append("unknown_listing_status")
        issues.extend({"code": code, "reason": reason} for reason in record_issues)
        records.append({"code": code, "source_code": raw, "name": row.get("code_name"),
                        "sse": raw[:2], **dates, "listing_status": row.get("status"),
                        "security_type": "1", "source": "baostock", "observed_at": observed_at,
                        "membership_basis": "retrospective_source_effective_lifecycle",
                        "membership_pit": False, "lifecycle_issues": record_issues})
    records.sort(key=lambda r: r["code"])
    canonical = [{k: v for k, v in r.items() if k != "observed_at"} for r in records]
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"records": records, "issues": issues, "source": "baostock_shsz_a_lifecycle",
            "universe_hash": digest, "observed_at": observed_at,
            "metadata_rows": len(rows), "lifecycle_rows": len(records),
            "inactive_rows": sum(r["listing_status"] == "0" for r in records),
            "beijing": {"status": "unsupported", "reason": "No bj metadata; live bj day probes return 10004011"},
            "exchange_completeness": "pending_independent_validation"}


def window_lifecycles(records: Iterable[dict], start: str, end: str) -> list[dict]:
    start, end = _iso(start), _iso(end)
    if not start or not end or start > end:
        raise ValueError("Invalid lifecycle window")
    # Unknown lifecycle dates remain potential members and carry their issues;
    # silently excluding them would make apparent source coverage look better.
    return [r for r in records if (not r.get("ipo_date") or r["ipo_date"] <= end)
            and (not r.get("delisted_date") or r["delisted_date"] > start)]


def members_on(records: Iterable[dict], day: str) -> list[str]:
    day = _iso(day)
    if not day:
        raise ValueError("A membership date is required")
    records = list(records)
    if any(r.get("lifecycle_issues") for r in records):
        raise ValueError("Unresolved source lifecycle issues prevent exact membership")
    return sorted(r["code"] for r in records if r["ipo_date"] <= day
                  and (not r.get("delisted_date") or day < r["delisted_date"]))


def lifecycle_window(record: dict, start: str, end: str) -> tuple[str, str] | None:
    """Bound raw queries to known effective dates; leave uncertainty in evidence."""
    query_start = max(start, record.get("ipo_date") or start)
    out = record.get("delisted_date")
    query_end = min(end, (date.fromisoformat(out) - timedelta(days=1)).isoformat()) if out else end
    return (query_start, query_end) if query_start <= query_end else None


def lifecycle_sessions(record: dict, sessions: Iterable[str], start: str, end: str) -> list[str]:
    window = lifecycle_window(record, start, end)
    return sorted(d for d in sessions if window and window[0] <= d <= window[1])
