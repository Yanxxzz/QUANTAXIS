"""Only synthetic notices/prices; no real factor research or account calls."""
import unittest

import pandas as pd

from panda_alpha import study
from panda_alpha.lifecycle_replay import replay_portfolio


def fixture():
    days = pd.bdate_range("2024-01-02", periods=8).strftime("%Y-%m-%d").tolist()
    rows = []
    for n, day in enumerate(days):
        for code, speed in [("A", .02), ("B", -.01), ("C", .005)]:
            opening = 10 * (1 + speed) ** n
            closing = opening * 1.003
            rows.append({"date": day, "symbol": code, "open": opening, "close": closing,
                         "raw_open": opening, "raw_close": closing,
                         "raw_low": opening * .99, "raw_high": closing * 1.01,
                         "volume": 1000., "amount": 20000., "trade_status": 1})
    schedule = {days[0]: {"codes": ["A", "B"]}, days[5]: {"codes": ["A", "B", "C"]}}
    return days, schedule, pd.DataFrame(rows)


def event(code, day):
    return {"symbol": code, "available_reference_date": day,
            "source": {"notice_sha256": "synthetic-notice", "availability": "synthetic-reference"}}


class LifecycleReplayTests(unittest.TestCase):
    def test_default_and_untriggered_clone_equal_original_every_accounting_field(self):
        days, schedule, frame = fixture()
        for fee in [.003, .005]:
            expected = study.replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), fee)
            for events in [None, []]:
                actual = replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), fee, events=events)
                self.assertEqual(expected, actual)
            actual = replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), fee,
                                      events=[event("A", "2024-02-01")])
            actual.pop("lifecycle_policy")
            self.assertEqual(expected, actual)

    def test_off_grid_flat_exit_does_not_rebalance_survivor_or_rebuy(self):
        days, schedule, frame = fixture()
        actual = replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), .003,
                                  events=[event("A", days[2])])
        off_grid = [trade for trade in actual["trades"] if trade["date"] == days[2]]
        self.assertEqual(["A"], [trade["code"] for trade in off_grid])
        self.assertLess(off_grid[0]["quantity_change"], 0)
        self.assertFalse(any(t["code"] == "A" and t["quantity_change"] > 0
                             and t["date"] >= days[2] for t in actual["trades"]))
        positions = actual["exposures"]["daily"]
        b_before = next(p["quantity"] for p in positions[1]["positions"] if p["symbol"] == "B")
        b_after = next(p["quantity"] for p in positions[2]["positions"] if p["symbol"] == "B")
        self.assertEqual(b_before, b_after)
        self.assertGreater(positions[2]["cash_fraction"], .4)
        self.assertLess(actual["accounting"]["max_absolute_accounting_error"], 1e-12)
        self.assertFalse(actual["terminal_liquidation_pending"])

    def test_pre_entry_prohibition_retains_original_slot_as_cash_without_quote(self):
        days, schedule, frame = fixture()
        frame = frame[frame.symbol.ne("A")]
        actual = replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), 0.,
                                  events=[event("A", days[0])])
        first = actual["daily"][0]
        self.assertAlmostEqual(.5, first["cash_after"], 14)
        self.assertFalse(any(t["code"] == "A" for t in actual["trades"]))

    def test_suspension_and_one_price_retry_then_first_potential_fill(self):
        days, schedule, frame = fixture()
        suspended = frame.symbol.eq("A") & frame.date.eq(days[2])
        frame.loc[suspended, ["open", "close", "raw_open", "raw_close", "raw_high", "raw_low"]] = float("nan")
        frame.loc[suspended, "trade_status"] = 0
        frame.loc[suspended, "source_status"] = "source_explicit_suspension"
        one_price = frame.symbol.eq("A") & frame.date.eq(days[3])
        frame.loc[one_price, ["open", "close", "raw_open", "raw_close", "raw_high", "raw_low"]] = 9.
        actual = replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), .003,
                                  events=[event("A", days[2])])
        attempts = [o for o in actual["orders"] if o.get("lifecycle_exit")]
        self.assertEqual(days[2:5], [o["date"] for o in attempts])
        self.assertEqual(["blocked_suspension", "blocked_one_price", "potential_fill"],
                         [o["state"] for o in attempts])
        exits = [t for t in actual["trades"] if t["code"] == "A" and t["quantity_change"] < 0]
        self.assertEqual([days[4]], [t["date"] for t in exits])
        self.assertTrue(actual["daily"][2]["known_suspension_mark"])
        self.assertLess(actual["accounting"]["max_absolute_accounting_error"], 1e-12)

    def test_delisting_never_uses_suspension_stale_price_or_zero_redemption(self):
        days, schedule, frame = fixture()
        bad = frame.symbol.eq("A") & frame.date.eq(days[2])
        frame.loc[bad, ["open", "close"]] = float("nan")
        frame.loc[bad, "trade_status"] = 0
        frame.loc[bad, "source_status"] = "source_explicit_delisted"
        with self.assertRaisesRegex(study.StudyExecutionError, "delisted_valuation_pending:A"):
            replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), .003,
                             events=[event("A", days[2])])

    def test_still_held_without_legal_mark_is_pending_even_with_event(self):
        days, schedule, frame = fixture()
        frame = frame[~(frame.symbol.eq("A") & frame.date.eq(days[2]))]
        with self.assertRaisesRegex(study.StudyExecutionError, "unknown_quote:A"):
            replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), .003,
                             events=[event("A", days[2])])

    def test_blocked_terminal_claim_remains_held_with_single_daily_attempt(self):
        days, schedule, frame = fixture()
        for day in days[2:]:
            bad = frame.symbol.eq("A") & frame.date.eq(day)
            frame.loc[bad, ["open", "close", "raw_open", "raw_close", "raw_high", "raw_low"]] = float("nan")
            frame.loc[bad, "trade_status"] = 0
            frame.loc[bad, "source_status"] = "source_explicit_suspension"
        actual = replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), .003,
                                  events=[event("A", days[2])])
        self.assertTrue(actual["terminal_liquidation_pending"])
        self.assertIn("A", actual["final_open_positions"])
        attempts = [o for o in actual["orders"] if o["symbol"] == "A" and o["side"] == "sell"]
        self.assertEqual(days[2:], [o["date"] for o in attempts])

    def test_unknown_availability_or_source_cannot_trigger_retrospective_exit(self):
        days, schedule, frame = fixture()
        for notice in [{"symbol": "A", "available_reference_date": None, "source": "synthetic"},
                       {"symbol": "A", "available_reference_date": days[2]},
                       event("A", "2024-01-06")]:
            with self.assertRaises(study.StudyInputError):
                replay_portfolio("X", schedule, days, study.PotentialQuoteBook(frame), .003,
                                 events=[notice])


if __name__ == "__main__":
    unittest.main()
