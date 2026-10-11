import unittest
import pandas as pd
from panda_alpha.event_study import evaluate_event_windows


class EventStudyTests(unittest.TestCase):
    def fixture(self):
        days = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
        quotes = pd.DataFrame([{"date": d, "symbol": s, "open": 100.0,
                               "close": 110.0 if d == days[-1] and s == "600099" else 100.0}
                              for s in ["600099", "600001", "600002"] for d in days])
        return days, quotes, [{"symbol": "600099", "formation_day": days[0], "raw_supply_ratio": 0.1}]

    def test_five_sessions_and_two_sided_fee(self):
        days, q, events = self.fixture()
        out = evaluate_event_windows(q, events, days, ["600001", "600002"])
        row = out["events"][0]["event_path"]
        self.assertEqual((row["entry_date"], row["exit_date"]), ("2026-09-21", "2026-09-25"))
        self.assertAlmostEqual(row["round_trip_cost_proxy"]["0.005"], 1.1*0.995/1.005-1)
        self.assertIsNone(out["annual_Sharpe"])

    def test_missing_peer_keeps_fixed_reference_pending(self):
        days, q, events = self.fixture()
        q = q.loc[~((q.symbol == "600002") & (q.date == days[2]))]
        out = evaluate_event_windows(q, events, days, ["600001", "600002"])
        self.assertEqual(out["status"], "SOURCE_PENDING_EVENT_DIAGNOSTIC")
        self.assertEqual(out["events"][0]["missing_reference_peers"], ["600002"])
        self.assertNotIn("direction_aligned_event_RankIC", out)

    def test_missing_interior_held_day_blocks_event(self):
        days, q, events = self.fixture()
        q = q.loc[~((q.symbol == "600099") & (q.date == days[3]))]
        out = evaluate_event_windows(q, events, days, ["600001", "600002"])
        self.assertEqual(out["complete_event_paths"], 0)
        self.assertNotIn("fixed_median_groups", out)


if __name__ == "__main__":
    unittest.main()
