"""Publication-bound industry support before a two-limit-up reversal bid.

This pure helper reads no source, labels or account. Its denominator is the
caller's fixed research universe, never a claim about the complete A market.
Missing or invalid observations are unknown, not zero limit-up occurrences.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence
import re


MIN_SECTOR_PEERS = 5
MIN_MARKET_PEERS = 30
_CODE = re.compile(r"\d{6}\Z")


def _day(value: Any, *, raw_row: bool = False) -> str | None:
    if raw_row and isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if raw_row and isinstance(value, str) and re.fullmatch(r"\d{8}", value):
        value = value[:4] + "-" + value[4:6] + "-" + value[6:]
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _positive(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() and number > 0 else None


def _explicit_false(value: Any) -> bool:
    # A dataframe may supply numpy.bool_; strings and numeric0 are not source
    # booleans and must not silently clear an unknown ST status.
    return value is False or (
        type(value).__module__ == "numpy" and type(value).__name__ == "bool_"
        and not bool(value)
    )


def _sector(row: Any) -> str | None:
    if not isinstance(row, Mapping):
        return None
    category, major = row.get("category_code"), row.get("major_code")
    if not isinstance(category, str) or not re.fullmatch(r"[A-S]", category):
        return None
    if not isinstance(major, str) or not re.fullmatch(r"\d{2}", major):
        return None
    return category + major


def _lifecycle_reason(row: Any, session: str, calendar: Sequence[str]) -> str | None:
    if not isinstance(row, Mapping) or "delisted_date" not in row:
        return "LIFECYCLE_UNKNOWN"
    ipo = _day(row.get("ipo_date"))
    if ipo is None:
        return "IPO_DATE_UNKNOWN"
    end = row.get("delisted_date")
    if end is not None and _day(end) is None:
        return "DELISTING_DATE_UNKNOWN"
    if session < ipo:
        return "PRE_IPO"
    if end is not None and session >= end:
        return "DELISTED"
    # Twenty actual independent reference sessions since the retained IPO
    # boundary, not twenty compressed stock observations or calendar days.
    first = bisect_left(calendar, ipo)
    current = bisect_left(calendar, session)
    if current - first < 20:
        return "IPO_FIRST20_REFERENCE_SESSIONS"
    return None


def _upper_close(row: Mapping[str, Any]) -> tuple[bool | None, str | None]:
    if not _explicit_false(row.get("is_st")):
        return None, "ST_OR_UNKNOWN_ST"
    # Explicit canonical aliases only. Equal numeric representations are
    # allowed, but one column must not hide a conflicting second source field.
    for canonical, raw in (("preclose", "pre_close"), ("volume_shares", "volume")):
        if canonical in row and raw in row:
            if _positive(row[canonical]) != _positive(row[raw]):
                return None, "CONFLICTING_RAW_CANONICAL_ALIAS"
    source = dict(row)
    if "preclose" in row:
        source["pre_close"] = row["preclose"]
    if "volume_shares" in row:
        source["volume"] = row["volume_shares"]
    numbers = {key: _positive(source.get(key)) for key in
               ("open", "high", "low", "close", "pre_close", "volume", "amount")}
    if any(value is None for value in numbers.values()):
        return None, "RAW_PRICE_OR_ACTIVITY_UNKNOWN"
    o, h, l, c = (numbers[key] for key in ("open", "high", "low", "close"))
    if not (l <= min(o, c) <= max(o, c) <= h):
        return None, "RAW_GEOMETRY_INVALID"
    upper = (numbers["pre_close"] * Decimal("1.10")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    lower = (numbers["pre_close"] * Decimal("0.90")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    if l < lower or h > upper:
        return None, "ORDINARY10_PERCENT_BAND_VIOLATION"
    return c == upper, None


def measure_sector_support(
    rows: Any, *, event_date: str, calendar: Sequence[str],
    universe: Sequence[str], target: str,
    industry_edition: Mapping[str, Any], lifecycle: Any,
    price_basis: str = "none",
) -> dict[str, Any]:
    """Measure sector-minus-market two-day limit-up participation at t open.

    Inputs use raw source columns code/date/OHLC/pre_close/volume/amount/is_st,
    or explicit canonical aliases preclose/volume_shares without conflicts.
    ``rows`` may be list-of-mappings or a dataframe. ``lifecycle`` accepts a
    code-keyed mapping or records with code. Industry is one already-public
    edition containing category_code+major_code records; it is not current bk.
    Caller must bind source acceptance, fixed universe and edition provenance.
    """
    result: dict[str, Any] = {
        "target_score": None, "control_score": None, "positive_support_gate": None,
        "common_supported": False, "reason": None, "event_date": event_date,
        "source_sessions": None, "target": target, "target_sector": None,
        "universe_count": len(universe), "peer_universe_count": None,
        "market_observed_peers": 0, "market_unknown_peers": None,
        "market_upper_hit_peers": 0, "sector_members_excluding_target": 0,
        "sector_observed_peers": 0, "sector_unknown_peers": 0,
        "sector_upper_hit_peers": 0, "industry_unknown_peers": 0,
        "peer_unknown_reasons": {}, "ignored_unneeded_rows": 0,
        "classification_vintage": industry_edition.get("vintage_id"),
        "classification_publication_date": industry_edition.get("publication_date"),
        "source_scope": "caller_fixed_research_universe_not_all_A_certificate",
        "future_labels_or_order_queue_certified": False,
    }

    def pending(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    cal = list(calendar)
    if (not cal or any(_day(x) is None for x in cal)
            or cal != sorted(set(cal)) or _day(event_date) is None
            or event_date not in cal):
        return pending("INVALID_REFERENCE_CALENDAR_OR_EVENT_CLOCK")
    i = cal.index(event_date)
    if i < 2:
        return pending("TWO_PRIOR_REFERENCE_SESSIONS_UNAVAILABLE")
    result["source_sessions"] = sessions = cal[i - 2:i]
    codes = list(universe)
    if (not codes or len(codes) != len(set(codes))
            or any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in codes)
            or target not in codes):
        return pending("INVALID_FIXED_UNIVERSE_OR_TARGET")
    result["peer_universe_count"] = len(codes) - 1
    if price_basis != "none":
        return pending("UNADJUSTED_RAW_PRICE_BASIS_REQUIRED")
    publication = _day(industry_edition.get("publication_date"))
    if publication is None or publication >= event_date:
        return pending("INDUSTRY_NOT_PUBLIC_BEFORE_EVENT")
    classifications = industry_edition.get("records")
    if not isinstance(classifications, Mapping):
        return pending("INDUSTRY_RECORDS_UNKNOWN")
    sector = _sector(classifications.get(target))
    result["target_sector"] = sector
    if sector is None:
        return pending("TARGET_INDUSTRY_UNKNOWN")
    if isinstance(lifecycle, Mapping):
        life = lifecycle
    else:
        life = {}
        for row in lifecycle:
            code = row.get("code") if isinstance(row, Mapping) else None
            if code in life:
                return pending("DUPLICATE_LIFECYCLE_IDENTITY")
            life[code] = row
    if hasattr(rows, "to_dict") and not isinstance(rows, Mapping):
        rows = rows.to_dict("records")
    by_key: dict[tuple[str, str], list] = defaultdict(list)
    code_set = set(codes)
    for row in rows:
        if not isinstance(row, Mapping):
            return pending("INVALID_RAW_RECORD_TYPE")
        day, code = _day(row.get("date"), raw_row=True), row.get("code")
        if day not in sessions or code not in code_set:
            result["ignored_unneeded_rows"] += 1
            continue
        by_key[(code, day)].append(row)

    observations: dict[str, tuple[bool | None, str | None]] = {}
    for code in codes:
        hits = []
        reason = None
        for session in sessions:
            reason = _lifecycle_reason(life.get(code), session, cal)
            if reason:
                break
            source = by_key.get((code, session), [])
            if len(source) != 1:
                reason = "RAW_ROW_MISSING" if not source else "DUPLICATE_RAW_IDENTITY"
                break
            hit, reason = _upper_close(source[0])
            if reason:
                break
            hits.append(hit)
        observations[code] = (any(hits), None) if reason is None else (None, reason)
    if observations[target][1] is not None:
        return pending("TARGET_" + observations[target][1])
    # Re-check both target days: peers use OR, but the target source must
    # agree with the selected opportunity's two consecutive upper closes.
    if not all(_upper_close(by_key[(target, session)][0])[0] for session in sessions):
        return pending("TARGET_NOT_TWO_UPPER_CLOSES")
    unknowns = Counter()
    for code in codes:
        if code == target:
            continue
        peer_sector = _sector(classifications.get(code))
        same_sector = peer_sector == sector
        result["industry_unknown_peers"] += peer_sector is None
        result["sector_members_excluding_target"] += same_sector
        hit, reason = observations[code]
        if reason is not None:
            unknowns[reason] += 1
            result["sector_unknown_peers"] += same_sector
            continue
        result["market_observed_peers"] += 1
        result["market_upper_hit_peers"] += bool(hit)
        if same_sector:
            result["sector_observed_peers"] += 1
            result["sector_upper_hit_peers"] += bool(hit)
    result["market_unknown_peers"] = len(codes) - 1 - result["market_observed_peers"]
    result["peer_unknown_reasons"] = dict(sorted(unknowns.items()))
    if result["sector_observed_peers"] < MIN_SECTOR_PEERS:
        return pending("FEWER_THAN5_OBSERVED_SECTOR_PEERS")
    if result["market_observed_peers"] < MIN_MARKET_PEERS:
        return pending("FEWER_THAN30_OBSERVED_MARKET_PEERS")
    market = result["market_upper_hit_peers"] / result["market_observed_peers"]
    sector_fraction = result["sector_upper_hit_peers"] / result["sector_observed_peers"]
    result.update(target_score=sector_fraction - market, control_score=market,
                  positive_support_gate=sector_fraction > market, common_supported=True)
    return result
