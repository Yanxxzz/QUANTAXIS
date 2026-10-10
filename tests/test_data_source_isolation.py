"""Corporate-action metadata cannot certify events from a different provider."""
import unittest

from panda_alpha.data import AxisProvider, PendingDataError


class MemoryCollection:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def find(self, query, projection=None):
        def matches(row):
            for key, condition in query.items():
                value = row.get(key)
                if isinstance(condition, dict):
                    if "$in" in condition and value not in condition["$in"]:
                        return False
                    if "$gte" in condition and (value is None or value < condition["$gte"]):
                        return False
                    if "$lte" in condition and (value is None or value > condition["$lte"]):
                        return False
                elif value != condition:
                    return False
            return True
        return [dict(row) for row in self.rows if matches(row)]


class MemoryDatabase(dict):
    def __getitem__(self, name):
        return self.get(name, MemoryCollection())


def action(source="tdx", dividend=10):
    row = {"date": "2026-07-07", "code": "000001", "category": 1,
           "fenhong": dividend, "peigu": 0, "peigujia": 0, "songzhuangu": 0}
    if source is not None:
        row["source"] = source
    return row


def database(events):
    bars = [{"date": date, "code": "000001", "source": "tdx", "open": close,
             "high": close + 1, "low": close - 1, "close": close, "vol": 100, "amount": 1000}
            for date, close in (("2026-07-06", 10), ("2026-07-07", 9))]
    return MemoryDatabase({
        "stock_day": MemoryCollection(bars), "stock_xdxr": MemoryCollection(events),
        "panda_axis_sync": MemoryCollection([{"dataset": "stock_xdxr", "code": "000001", "source": "tdx",
                                               "status": "complete", "start": "2000-01-01", "through": "2026-07-07",
                                               "rows": 1, "history_complete": True}]),
        "stock_list": MemoryCollection([{"code": "000001"}]),
    })


def daily(events):
    return AxisProvider(db=database(events)).daily(["000001"], "2026-07-06", "2026-07-07",
                                                  expected_dates=["2026-07-06", "2026-07-07"])


class DataSourceIsolationTests(unittest.TestCase):
    def test_different_provider_only_cannot_use_the_same_source_receipt(self):
        with self.assertRaises(PendingDataError):
            daily([action(source="different_provider", dividend=50)])

    def test_missing_source_tag_is_not_same_source_evidence(self):
        with self.assertRaises(PendingDataError):
            daily([action(source=None)])

    def test_missing_receipted_event_is_pending_not_a_verified_zero_event_history(self):
        # The durable receipt says one event exists; deleting its row cannot
        # silently certify an empty corporate-action history.
        with self.assertRaises(PendingDataError):
            daily([])

    def test_same_source_event_has_correct_adjustment_and_raw_prices_are_preserved(self):
        result = daily([action()])
        self.assertEqual(result.frame.close.tolist(), [9, 9])
        self.assertEqual(result.frame.raw_close.tolist(), [10, 9])
        self.assertEqual(result.coverage["adjustment_evidence"]["status"], "verified")

    def test_coexisting_foreign_event_is_excluded_before_duplicate_date_or_adjustment_checks(self):
        # Equal dates deliberately expose mixing: foreign dividend must neither
        # alter the price nor trigger the duplicate-event error for this source.
        baseline = daily([action()])
        for events in ([action(), action(source="different_provider", dividend=50)],
                       [action(source="different_provider", dividend=50), action()]):
            with self.subTest(foreign_first=events[0]["source"] != "tdx"):
                result = daily(events)
                self.assertEqual(result.frame.close.tolist(), baseline.frame.close.tolist())
                self.assertEqual(result.frame.raw_close.tolist(), [10, 9])
                self.assertEqual(result.coverage["adjustment_evidence"]["status"], "verified")

    def test_receipted_history_with_only_future_event_does_not_adjust_this_window(self):
        event = {**action(dividend=50), "date": "2026-07-08"}
        db = database([event])
        db["panda_axis_sync"].rows[0]["through"] = "2026-07-08"
        result = AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07",
                                           expected_dates=["2026-07-06", "2026-07-07"])
        self.assertEqual(result.frame.close.tolist(), [10, 9])
        self.assertEqual(result.frame.raw_close.tolist(), [10, 9])
        self.assertEqual(result.coverage["adjustment_evidence"]["status"], "verified")

    def test_empty_receipted_factor_history_cannot_manufacture_ipo_factor_one(self):
        db = database([action()])
        db["stock_adj"] = MemoryCollection()
        db["panda_axis_sync"].rows.append({"dataset": "stock_adj", "code": "000001", "source": "tdx",
                                         "status": "complete", "through": "2026-07-07", "rows": 1,
                                         "history_complete": True})
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07",
                                      expected_dates=["2026-07-06", "2026-07-07"])


if __name__ == "__main__":
    unittest.main()
