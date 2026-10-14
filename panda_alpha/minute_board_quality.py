"""Recorded raw minute close-at-upper-band point count, not queue/seal quality.

The 240 supplied naive wall-clock points are 09:31..11:30 and 13:01..15:00.
One 09:30 record may be reported separately and excluded from the denominator.
No timezone, bar start/end, publication delay, continuously elapsed seal time,
actual trading, depth, order queue or execution certification is inferred.
Volume/amount are deliberately unused, including zero-trade provided bars.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
import re


def _minute_range(start_hour: int, start_minute: int, count: int) -> tuple[time, ...]:
    start = start_hour * 60 + start_minute
    return tuple(time((start + i) // 60, (start + i) % 60) for i in range(count))


REGULAR_POINTS = _minute_range(9, 31, 120) + _minute_range(13, 1, 120)
AUCTION_POINT = time(9, 30)
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?\Z")


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return day if day.isoformat() == value else None


def _stamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and _STAMP.fullmatch(value):
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is not None or stamp.second or stamp.microsecond or getattr(stamp, "nanosecond", 0):
        return None
    return stamp


def _positive_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() and number > 0 else None


def measure_minute_upper_close_fraction(
    rows: Sequence[Mapping[str, Any]], *, source_date: str,
    upper_price: Any, next_entry_date: str, price_basis: str,
) -> dict[str, Any]:
    """Strict supplied-point measurement; caller must bind the legal same-day U.

    A failure never sorts, forward-fills, compresses missing minutes, changes
    price basis, rounds to an approximately upper close or infers an upper band
    from a percentage return. Zero volume is not interpreted as an executable
    trade; volume and amount do not enter this price-state measurement.
    """
    result: dict[str, Any] = {
        "score": None, "reason": None,
        "measurement": "recorded_regular_minute_close_exactly_at_known_upper_fraction",
        "regular_expected_points": 240, "regular_observed_points": 0,
        "regular_unique_points": 0, "auction_0930_records_excluded": 0,
        "upper_close_points": None, "source_date": source_date,
        "next_entry_date": next_entry_date, "price_basis": price_basis,
        "timezone_bar_interval_or_publication_latency_certified": False,
        "continuous_seal_duration_order_queue_or_execution_certified": False,
        "volume_or_amount_used": False,
        "zero_trade_bars_are_only_recorded_price_states": True,
    }
    def fail(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if not isinstance(price_basis, str) or price_basis != "none":
        return fail("raw_price_basis_none_required")
    day, entry = _day(source_date), _day(next_entry_date)
    if day is None or entry is None:
        return fail("invalid_source_or_entry_date")
    if day >= entry:
        return fail("source_date_not_before_next_entry")
    upper = _positive_decimal(upper_price)
    if upper is None:
        return fail("positive_known_legal_upper_price_required")
    try:
        records = list(rows)
    except TypeError:
        return fail("minute_records_required")
    allowed = set(REGULAR_POINTS)
    parsed: list[tuple[datetime, Mapping[str, Any]]] = []
    seen: set[datetime] = set()
    previous: datetime | None = None
    regular: list[time] = []
    for row in records:
        if not isinstance(row, Mapping):
            return fail("minute_record_mapping_required")
        stamp = _stamp(row.get("date"))
        if stamp is None:
            return fail("naive_exact_minute_timestamp_required")
        if stamp.date() != day:
            return fail("minute_date_differs_from_source_date")
        if stamp in seen:
            return fail("duplicate_minute_timestamp")
        if previous is not None and stamp < previous:
            return fail("minute_records_not_sorted")
        point = stamp.time()
        if point == AUCTION_POINT:
            result["auction_0930_records_excluded"] += 1
        elif point in allowed:
            regular.append(point)
        else:
            return fail("unexpected_regular_session_time")
        seen.add(stamp)
        previous = stamp
        parsed.append((stamp, row))
    result["regular_observed_points"] = len(regular)
    result["regular_unique_points"] = len(set(regular))
    if tuple(regular) != REGULAR_POINTS:
        return fail("incomplete_regular_240_point_grid")
    upper_count = 0
    for stamp, row in parsed:
        values = {name: _positive_decimal(row.get(name)) for name in ("open", "high", "low", "close")}
        if any(value is None for value in values.values()):
            return fail("positive_finite_raw_OHLC_required")
        opening, high, low, close = (values[k] for k in ("open", "high", "low", "close"))
        if not (low <= min(opening, close) <= max(opening, close) <= high):
            return fail("invalid_raw_OHLC_geometry")
        if high > upper:
            return fail("raw_high_exceeds_known_upper_band")
        if stamp.time() != AUCTION_POINT and close == upper:
            upper_count += 1
    result["upper_close_points"] = upper_count
    result["score"] = float(Decimal(upper_count) / Decimal(240))
    return result
