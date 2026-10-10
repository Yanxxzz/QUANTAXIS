"""Transport causes must not manufacture date-density child requests."""
from datetime import date

import json
import sqlite3

from scripts.collect_announcement_metadata import calendar_availability, exception_diagnostics, pending_window, window_failure_action


class PaginationUnstable(ValueError):
    pass


class WindowTooDense(ValueError):
    pass


def test_wrapped_transport_stops_even_multiday_window():
    error = PaginationUnstable("CNInfo page transport failed after three attempts: ConnectionError")
    error.__cause__ = ConnectionError("Failed public HTTPS connection [WinError 10013]")
    assert window_failure_action(error, date(2026, 9, 27), date(2026, 10, 3)) == "stop_source_pending"
    assert exception_diagnostics(error)["winerror_codes"] == [10013]


def test_true_dense_window_can_split():
    error = WindowTooDense("Window has 4000 announcements, above limit3000; split interval")
    assert window_failure_action(error, date(2026, 9, 27), date(2026, 10, 3)) == "split"


def test_content_pagination_anomaly_can_split_but_single_day_pending():
    error = PaginationUnstable("Duplicate announcement ID across pages:123")
    assert window_failure_action(error, date(2026, 9, 27), date(2026, 10, 3)) == "split"
    assert window_failure_action(error, date(2026, 9, 27), date(2026, 9, 27)) == "unresolved"


def test_source_frontier_never_selects_failed_history_pending_children():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE windows(id TEXT,query_json TEXT,status TEXT)")
    db.executemany("INSERT INTO windows VALUES(?,?,'PENDING')", [("old", json.dumps({"category": "一季报"})),
                     ("fresh", json.dumps({"category": "", "repair_namespace": "FRESH"}))])
    assert pending_window(db, "FRESH")[0] == "fresh"
    db.close()


def test_calendar_weekend_uses_verified_coverage_not_first_session():
    value = {"dates": ["2026-08-31", "2026-09-01", "2026-10-12"],
             "coverage_window": {"start": "2026-08-30", "end": "2026-10-12"},
             "verified_reference_windows": [{"start": "2026-08-30", "end": "2026-09-18"}],
             "actual_verified": False}
    assert calendar_availability("2026-08-30", value) == ("2026-08-31", "next_verified_reference_session")
    assert calendar_availability("2026-10-09", value) == (None, "next_planned_session:2026-10-12:actual_pending")
    value.update(dates=["2026-08-31", "2026-09-18", "2026-09-21", "2026-10-12"],
                 historical_vendor_reference_verified=True,
                 actual_verified_window={"start": "2026-08-30", "end": "2026-10-09"})
    assert calendar_availability("2026-09-18", value) == ("2026-09-21", "next_verified_reference_session")
    assert calendar_availability("2026-10-09", value) == (None, "next_planned_session:2026-10-12:actual_pending")
