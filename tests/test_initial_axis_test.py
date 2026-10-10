import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from panda_alpha.data import DailyData
from scripts.initial_axis_test import (COSTS, END, START, calendar_gate, fixed_sample,
                                     run_initial, source_pool)


class FakeProvider:
    def __init__(self, count=40, gap=None):
        self.dates = pd.bdate_range(START, END)
        self.codes = [str(i).zfill(6) for i in range(count)]
        self.receipts, self.lifecycles, self.raw = [], [], []
        frame = []
        for i, code in enumerate(self.codes):
            self.receipts += [{"dataset": "stock_day", "code": code, "status": "complete", "start": START,
                               "through": END, "source": "synthetic"},
                              {"dataset": "stock_adj", "code": code, "status": "complete", "start": START,
                               "through": END, "source": "synthetic", "history_complete": True}]
            self.lifecycles.append({"code": code, "source": "synthetic", "sse": "sh" if i % 2 else "sz",
                                   "ipo_date": "1999-01-01", "delisted_date": None, "listing_status": "1"})
            for t, date in enumerate(self.dates):
                if gap == date.strftime("%Y-%m-%d"):
                    continue
                opening = 10 + i * .1 + .009 * t + .09 * np.sin(t * (i + 1) / 17)
                close = opening * (1 + .002 * np.sin(t / 4 + i))
                volume = 1000 + (i + 1) * 19 + 17 * np.cos(t * (i + 1) / 21)
                row = {"date": date.strftime("%Y-%m-%d"), "code": code, "open": opening,
                       "high": max(opening, close) + .12, "low": min(opening, close) - .13,
                       "close": close, "vol": volume, "amount": volume * opening * 100,
                       "trade_status": "1", "is_st": "0", "source": "synthetic"}
                self.raw.append(row)
                frame.append({**row, "date": date, "symbol": code, "volume": volume,
                              "adjustment": "hfq", "raw_open": opening, "raw_high": row["high"],
                              "raw_low": row["low"], "raw_close": close})
        self.frame = pd.DataFrame(frame)
        self.receipts.append({"dataset": "trade_calendar", "code": "SSE", "status": "complete", "source": "baostock",
                              "start": START, "through": END})
        self.last_adjustment = None

    def _records(self, name, query=None):
        records = {"panda_axis_sync": self.receipts, "stock_lifecycle": self.lifecycles,
                   "stock_day": self.raw,
                   "trade_calendar": [{"date": d.strftime("%Y-%m-%d"), "exchange": "SSE", "source": "baostock_trade_dates"} for d in self.dates]}.get(name, [])
        def matches(row):
            for key, value in (query or {}).items():
                if isinstance(value, dict):
                    actual = row.get(key)
                    if "$in" in value and actual not in value["$in"]:
                        return False
                    if "$gte" in value and (actual is None or actual < value["$gte"]):
                        return False
                    if "$lte" in value and (actual is None or actual > value["$lte"]):
                        return False
                elif row.get(key) != value:
                    return False
            return True
        return [dict(row) for row in records if matches(row)]

    def daily(self, codes, start, end, adjustment):
        self.last_adjustment = adjustment
        frame = self.frame[self.frame.symbol.isin(codes)].copy()
        return DailyData(frame, {"calendar": {"status": "verified", "sessions": len(self.dates)},
            "daily": {"status": "verified", "coverage": 1, "missing_by_code": {}},
            "all_a": {"status": "pending"}, "delisted": {"status": "pending"}})


class InitialAxisTest(unittest.TestCase):
    def test_hash_sample_ignores_input_order_and_current_delisting_status(self):
        provider = FakeProvider()
        rows, _, _ = source_pool(provider)
        original = [r["code"] for r in fixed_sample(rows, 30)]
        provider.lifecycles[0].update(listing_status="0", delisted_date="2026-09-17")
        changed, retired, _ = source_pool(provider)
        self.assertIn(provider.codes[0], [r["code"] for r in changed])
        self.assertEqual(original, [r["code"] for r in fixed_sample(list(reversed(changed)), 30)])
        self.assertIn(provider.codes[0], [r["code"] for r in retired])

    def test_known_suspension_partial_receipt_is_distinct_from_unexplained_omission(self):
        provider = FakeProvider()
        receipt = provider.receipts[0]
        receipt.update(status="partial", suspended_dates=["2026-07-07"], source_missing_dates=[],
                       unknown_missing_dates=[], off_lifecycle_dates=[], duplicate_dates=0)
        self.assertIn(provider.codes[0], [r["code"] for r in source_pool(provider)[0]])
        receipt["unknown_missing_dates"] = ["2026-07-08"]
        self.assertNotIn(provider.codes[0], [r["code"] for r in source_pool(provider)[0]])

    def test_whole_session_gap_blocks_labels_even_with_verified_calendar(self):
        provider = FakeProvider(gap="2026-07-07")
        calendar = {"status": "verified", "sessions": [d.strftime("%Y-%m-%d") for d in provider.dates]}
        gate = calendar_gate(provider.frame, {"calendar": {"status": "verified"}}, calendar)
        self.assertEqual(gate["status"], "pending")
        self.assertEqual(gate["missing_whole_sessions"], ["2026-07-07"])
        with tempfile.TemporaryDirectory() as temp, patch("scripts.initial_axis_test.evaluate_signal") as evaluate:
            report = run_initial(provider, temp, sample_size=30)
            evaluate.assert_not_called()
            self.assertEqual(report["status"], "pending_calendar")
            self.assertEqual(report["trial_ledger"]["new_tested"], 0)

    def test_hfq_six_mechanisms_real_panels_cost_dedup_and_no_side_effects(self):
        provider = FakeProvider()
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "trials.sqlite3"
            report = run_initial(provider, Path(temp) / "one", sample_size=30, ledger_path=ledger)
            self.assertEqual(provider.last_adjustment, "hfq")
            self.assertEqual(len({r["mechanism"] for r in report["candidates"]}), 6)
            self.assertTrue(all(r["direction"] == 1 for r in report["candidates"]))
            self.assertEqual(report["trial_ledger"]["new_tested"], 6)
            self.assertEqual(report["trial_ledger"]["total"], 421)
            self.assertEqual(report["trial_ledger"]["batch_unique_trials"], 6)
            self.assertEqual(report["trial_ledger"]["batch_newly_registered"], 6)
            self.assertTrue(all(set(r["cost_results"]) == {str(c) for c in COSTS} for r in report["candidates"]))
            pairs = json.loads((Path(temp) / "one/factor_rank_correlations.json").read_text(encoding="utf-8"))
            self.assertEqual(len(pairs["pairs"]), 15)
            self.assertTrue(any(p["observed_mean_abs"] is not None for p in pairs["pairs"]))
            self.assertTrue(all(r["execution"]["status"] == "pending" for r in report["candidates"]))
            self.assertTrue(all(v == 0 for v in report["permissions"].values()))
            # Updating receipt timestamps does not create another hypothesis.
            for receipt in provider.receipts:
                receipt["synced_at"] = "2026-10-05T14:30:00+08:00"
            repeated = run_initial(provider, Path(temp) / "two", sample_size=30, ledger_path=ledger)
            self.assertEqual(repeated["trial_ledger"]["new_tested"], 6)
            self.assertEqual(repeated["trial_ledger"]["batch_newly_registered"], 0)
            self.assertTrue((Path(temp) / "two/summary.json").is_file())
            self.assertEqual(repeated["sample"]["definition_sha256"], report["sample"]["definition_sha256"])
            self.assertTrue(report["generated_at_asia_shanghai"].endswith("+08:00"))

    def test_saved_sample_cannot_be_changed_and_sealed_window_cannot_be_used(self):
        provider = FakeProvider()
        with tempfile.TemporaryDirectory() as temp:
            run_initial(provider, temp, sample_size=30)
            with self.assertRaisesRegex(ValueError, "Frozen sample identity"):
                run_initial(provider, temp, sample_size=31)
            with self.assertRaisesRegex(ValueError, "sealed OOS"):
                run_initial(provider, Path(temp) / "sealed", sample_size=30,
                            sealed=[{"start": START, "end": END}])


if __name__ == "__main__":
    unittest.main()
