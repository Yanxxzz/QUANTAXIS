from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from panda_alpha.minute_board_quality import REGULAR_POINTS, measure_minute_upper_close_fraction


def rows_at_upper():
    return [{"date": "2025-01-02 " + point.strftime("%H:%M:%S"),
             "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0}
            for point in REGULAR_POINTS]


def measure(rows, **change):
    args = dict(source_date="2025-01-02", upper_price=11.0,
                next_entry_date="2025-01-03", price_basis="none")
    args.update(change)
    return measure_minute_upper_close_fraction(rows, **args)


def test_full_grid_boundaries_and_all_upper_fraction():
    assert len(REGULAR_POINTS) == len(set(REGULAR_POINTS)) == 240
    assert [str(REGULAR_POINTS[i]) for i in (0, 119, 120, 239)] == ["09:31:00", "11:30:00", "13:01:00", "15:00:00"]
    result = measure(rows_at_upper())
    assert result["score"] == 1.0
    assert result["upper_close_points"] == 240
    assert result["regular_unique_points"] == 240
    assert result["reason"] is None
    assert not result["continuous_seal_duration_order_queue_or_execution_certified"]


def test_half_upper_with_auction_excluded():
    rows = rows_at_upper()
    for row in rows[:120]: row.update(open=10.0, low=10.0, close=10.0)
    rows.insert(0, {"date": "2025-01-02 09:30:00", "open": 11, "high": 11, "low": 11, "close": 11})
    result = measure(rows)
    assert result["score"] == 0.5
    assert result["upper_close_points"] == 120
    assert result["auction_0930_records_excluded"] == 1
    assert result["regular_observed_points"] == 240


def test_same_daily_extrema_open_close_but_distinct_minute_paths():
    first, second = rows_at_upper(), rows_at_upper()
    for rows, n_upper in ((first, 120), (second, 60)):
        for i, row in enumerate(rows):
            row.update(open=10.0, high=11.0, low=9.0, close=11.0 if i >= 240-n_upper else 10.0)
    def daily(rows):
        return rows[0]["open"], max(r["high"] for r in rows), min(r["low"] for r in rows), rows[-1]["close"]
    assert daily(first) == daily(second) == (10.0, 11.0, 9.0, 11.0)
    assert measure(first)["score"] == 0.5
    assert measure(second)["score"] == 0.25


def test_missing_minute_is_not_filled_or_compressed():
    rows = rows_at_upper()
    del rows[17]
    result = measure(rows)
    assert result["score"] is None
    assert result["reason"] == "incomplete_regular_240_point_grid"
    assert result["regular_observed_points"] == 239


def test_duplicate_and_unsorted_records_are_rejected_not_repaired():
    rows = rows_at_upper()
    rows.insert(12, deepcopy(rows[11]))
    assert measure(rows)["reason"] == "duplicate_minute_timestamp"
    rows = rows_at_upper()
    rows[11], rows[12] = rows[12], rows[11]
    assert measure(rows)["reason"] == "minute_records_not_sorted"


@pytest.mark.parametrize("badtime", ["12:00:00", "13:00:00", "09:29:00", "15:01:00"])
def test_lunch_or_outside_session_points_cannot_replace_regular_grid(badtime):
    rows = rows_at_upper()
    rows[120]["date"] = "2025-01-02 " + badtime
    # Keep source order where applicable; either bad session or unsorted is invalid.
    result = measure(rows)
    assert result["score"] is None
    assert result["reason"] in {"unexpected_regular_session_time", "minute_records_not_sorted"}


def test_wrong_row_date_and_future_source_date_are_rejected():
    rows = rows_at_upper()
    rows[0]["date"] = "2025-01-03 09:31:00"
    assert measure(rows)["reason"] == "minute_date_differs_from_source_date"
    assert measure(rows_at_upper(), next_entry_date="2025-01-02")["reason"] == "source_date_not_before_next_entry"
    assert measure(rows_at_upper(), next_entry_date="2025-01-01")["score"] is None


@pytest.mark.parametrize("basis", ["qfq", "hfq", None, False])
def test_adjusted_or_unknown_price_basis_is_not_raw(basis):
    assert measure(rows_at_upper(), price_basis=basis)["reason"] == "raw_price_basis_none_required"


@pytest.mark.parametrize("change,reason", [
    ({"low": 12.0}, "invalid_raw_OHLC_geometry"),
    ({"open": 12.0}, "invalid_raw_OHLC_geometry"),
    ({"close": 0.0}, "positive_finite_raw_OHLC_required"),
    ({"high": float("nan")}, "positive_finite_raw_OHLC_required"),
    ({"low": float("inf")}, "positive_finite_raw_OHLC_required"),
    ({"close": True}, "positive_finite_raw_OHLC_required"),
    ({"high": 11.000000000000002}, "raw_high_exceeds_known_upper_band"),
])
def test_raw_OHLC_and_upper_band_are_strict(change, reason):
    rows = rows_at_upper()
    rows[100].update(change)
    result = measure(rows)
    assert result["score"] is None
    assert result["reason"] == reason


def test_close_near_upper_is_not_rounded_to_upper():
    rows = rows_at_upper()
    rows[0].update(open=10.0, low=10.0, close=10.999999999999998)
    result = measure(rows, upper_price=Decimal("11.00"))
    assert result["upper_close_points"] == 239
    assert result["score"] == 239/240


def test_volume_amount_and_zero_trade_fields_are_unused():
    rows = rows_at_upper()
    for row in rows: row.update(volume=0, amount=None)
    result = measure(rows)
    assert result["score"] == 1.0
    assert not result["volume_or_amount_used"]
    assert result["zero_trade_bars_are_only_recorded_price_states"]


def test_naive_datetime_is_allowed_but_timezone_or_subminute_is_unknown():
    rows = rows_at_upper()
    rows[0]["date"] = datetime(2025, 1, 2, 9, 31)
    assert measure(rows)["score"] == 1.0
    rows[0]["date"] = datetime(2025, 1, 2, 9, 31, tzinfo=timezone.utc)
    assert measure(rows)["reason"] == "naive_exact_minute_timestamp_required"
    rows[0]["date"] = "2025-01-02 09:31:00.000001"
    assert measure(rows)["reason"] == "naive_exact_minute_timestamp_required"


def test_unknown_upper_dates_and_schema_fail_without_score():
    for upper in [None, 0, False, float("inf")]:
        assert measure(rows_at_upper(), upper_price=upper)["reason"] == "positive_known_legal_upper_price_required"
    assert measure(rows_at_upper(), source_date="2025/01/02")["reason"] == "invalid_source_or_entry_date"
    rows = rows_at_upper()
    del rows[0]["close"]
    assert measure(rows)["reason"] == "positive_finite_raw_OHLC_required"


def test_auction_stays_excluded_but_is_not_exempt_from_source_sanity():
    rows = rows_at_upper()
    rows.insert(0, {"date": "2025-01-02 09:30:00", "open": 11, "high": 12, "low": 11, "close": 11})
    assert measure(rows)["reason"] == "raw_high_exceeds_known_upper_band"
