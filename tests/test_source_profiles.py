"""Research source choice is explicit and independent of record insertion order."""
import unittest

from panda_alpha.data import AxisProvider, PendingDataError
from panda_alpha.sources import resolve_source, resolve_sync_plan, factor_records_sha256
from tests.test_data_source_isolation import MemoryCollection, MemoryDatabase, action, database


class SourceProfileTests(unittest.TestCase):
    def test_overlapping_sources_are_rejected_before_keep_last_deduplication(self):
        db = database([action()])
        original = list(db["stock_day"].rows)
        foreign = [{**r, "source": "foreign", "close": r["close"] + .1} for r in original]
        for rows in (original + foreign, foreign + original):
            db["stock_day"].rows = rows
            with self.assertRaises(PendingDataError):
                AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07", "none")

    def test_disjoint_sources_for_one_security_are_also_rejected(self):
        db = database([action()])
        db["stock_day"].rows[1]["source"] = "foreign"
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07", "none")

    def test_source_selection_filters_prices_and_receipts_before_adjustment(self):
        db = database([action(), action(source="foreign", dividend=50)])
        db["stock_day"].rows.extend([{**r, "source": "foreign"} for r in list(db["stock_day"].rows)])
        foreign_receipt = {**db["panda_axis_sync"].rows[0], "source": "foreign"}
        for first in (True, False):
            own = {**foreign_receipt, "source": "tdx"}
            db["panda_axis_sync"].rows = [foreign_receipt, own] if first else [own, foreign_receipt]
            result = AxisProvider(db=db, price_source="tdx").daily(
                ["000001"], "2026-07-06", "2026-07-07", expected_dates=["2026-07-06", "2026-07-07"])
            self.assertEqual(result.frame.close.tolist(), [9, 9])
            self.assertEqual(result.frame.source.unique().tolist(), ["tdx"])

    def test_absent_selected_source_does_not_fall_back(self):
        result = AxisProvider(db=database([action()]), price_source="missing").daily(
            ["000001"], "2026-07-06", "2026-07-07", "none", ["2026-07-06", "2026-07-07"])
        self.assertTrue(result.frame.empty)
        self.assertEqual(result.coverage["daily"]["coverage"], 0)

    def test_short_receipt_cannot_certify_earlier_history(self):
        db = database([action()])
        db["panda_axis_sync"].rows[0]["start"] = "2026-07-07"
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07")

    def test_contradictory_same_scope_receipts_require_reconciliation(self):
        db = database([action()])
        db["panda_axis_sync"].rows.append({**db["panda_axis_sync"].rows[0], "rows": 0})
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07")

    def test_separate_reference_database_supplies_calendar_and_lifecycle(self):
        market = database([action()])
        reference = MemoryDatabase({
            "trade_calendar": MemoryCollection([{"date": d, "exchange": "SSE", "source": "baostock_trade_dates"}
                                                for d in ["2026-07-06", "2026-07-07"]]),
            "panda_axis_sync": MemoryCollection([{"dataset": "trade_calendar", "code": "SSE", "source": "baostock",
                "status": "complete", "start": "2026-07-06", "through": "2026-07-07"}]),
            "stock_lifecycle": MemoryCollection([{"code": "000001", "ipo_date": "1991-04-03"}])})
        result = AxisProvider(db=market, reference_db=reference, price_source="tdx",
                              calendar_source="baostock_trade_dates").daily(["000001"], "2026-07-06", "2026-07-07")
        self.assertEqual(result.coverage["calendar"]["sessions"], 2)
        self.assertEqual(result.coverage["daily"]["coverage"], 1)
        self.assertEqual(result.coverage["daily"]["membership_basis"], "source_effective_ipo_and_delisted_dates")

    def test_source_profile_unknown_or_incomplete_is_not_a_default(self):
        data = {"mongo_uri": "mongodb://127.0.0.1:27018", "database": "quantaxis", "source_profiles": {"bad": {}}}
        for source in ("missing", "bad"):
            with self.assertRaises(ValueError):
                resolve_source(data, source)

    def test_missing_factor_predecessor_cannot_become_an_ipo_baseline(self):
        db = database([])
        db["stock_adj"] = MemoryCollection([{"code": "000001", "date": "2026-07-07", "adj": 2., "source": "tdx"}])
        db["panda_axis_sync"].rows = [{"dataset": "stock_adj", "code": "000001", "source": "tdx",
            "start": "1990-01-01", "through": "2026-07-07", "rows": 2, "history_complete": True, "status": "complete"}]
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07", "hfq")

    def test_factor_receipt_full_horizon_is_checked_before_requested_end(self):
        db = database([])
        factors = [{"code": "000001", "date": d, "adj": a, "source": "tdx"} for d, a in
                   [("2026-07-06", 1.), ("2026-07-08", 2.)]]
        db["stock_adj"] = MemoryCollection(factors)
        db["panda_axis_sync"].rows = [{"dataset": "stock_adj", "code": "000001", "source": "tdx",
            "start": "1990-01-01", "through": "2026-07-08", "rows": 2, "history_complete": True,
            "status": "complete", "factor_records_sha256": factor_records_sha256(factors)}]
        result = AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07", "hfq")
        self.assertEqual(result.frame.close.tolist(), [10, 9])
        db["stock_adj"].rows[0]["adj"] = 1.5
        with self.assertRaises(PendingDataError):
            AxisProvider(db=db).daily(["000001"], "2026-07-06", "2026-07-07", "hfq")

    def test_declared_sync_plan_and_explicit_override(self):
        plan = {"codes_file": "codes.json", "start": "2025-01-01", "end": "2025-01-31",
                "sdk_dir": "sdk", "acceptance": "receipt.json", "output": "first"}
        cfg = {"data": {"mongo_uri": "mongodb://127.0.0.1:27018", "source_profiles": {"stockdb": {
            "database": "isolated", "price_source": "stockdb", "reference_database": "reference",
            "calendar_source": "calendar", "sync_plan": plan}}}}
        result = resolve_sync_plan(cfg, "stockdb", {"output": "second", "start": None})
        self.assertEqual(result["output"], "second")
        self.assertEqual(result["start"], plan["start"])


if __name__ == "__main__":
    unittest.main()
