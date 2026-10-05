from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from panda_alpha.execution import (ExecutionPolicy, TradingRuleBook, assess_next_open_order,
                                  audit_next_open, mark_position, reviewed_2026_rulebook)


def bar(**changes):
    return dict({"date": "2026-07-07", "symbol": "600000", "board": "sse_main",
                 "raw_open": 10, "raw_high": 10.5, "raw_low": 9.5, "raw_close": 10,
                 "raw_preclose": 10, "volume": 10000, "trade_status": "1", "is_st": "0",
                 "listing_state": "seasoned", "security_status_verified": True,
                 "limit_reference_verified": True}, **changes)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.rules = reviewed_2026_rulebook()

    def test_main_board_st_change_is_effective_date_specific(self):
        for board in ("sse_main", "szse_main"):
            before = self.rules.resolve(bar(date="2026-07-03", board=board, is_st="1"))
            after = self.rules.resolve(bar(date="2026-07-06", board=board, is_st="1"))
            self.assertEqual(before["upper_limit"], 10.5)
            self.assertEqual(after["upper_limit"], 11)
            self.assertNotEqual(before["rule"]["rule_id"], after["rule"]["rule_id"])

    def test_star_chinext_and_bse_have_independent_reviewed_limits(self):
        for board, upper in (("star", 12), ("chinext", 12), ("bse", 13)):
            result = self.rules.resolve(bar(board=board, is_st="1"))
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["upper_limit"], upper)

    def test_unknown_board_st_or_listing_state_never_uses_code_prefix_heuristics(self):
        for changes in ({"board": None}, {"is_st": None}, {"listing_state": None},
                        {"security_status_verified": False}, {"date": "2025-09-01"}):
            self.assertEqual(self.rules.resolve(bar(**changes))["status"], "pending")
        rule = replace(self.rules.rules[1], source_url="https://example.com/rules")
        self.assertEqual(TradingRuleBook([rule]).resolve(bar())["status"], "pending")

    def test_ipo_session_and_special_listing_exceptions_are_explicit(self):
        self.assertTrue(self.rules.resolve(bar(board="star", listing_state="ipo", listing_session_number=5))["unlimited"])
        self.assertFalse(self.rules.resolve(bar(board="star", listing_state="ipo", listing_session_number=6))["unlimited"])
        self.assertEqual(self.rules.resolve(bar(board="star", listing_state="ipo"))["status"], "pending")
        self.assertTrue(self.rules.resolve(bar(board="bse", listing_state="ipo", listing_session_number=1))["unlimited"])
        self.assertFalse(self.rules.resolve(bar(board="bse", listing_state="ipo", listing_session_number=2))["unlimited"])
        self.assertEqual(self.rules.resolve(bar(board="bse", listing_state="relisting_first_day"))["status"], "pending")

    def test_bse_fractional_tick_rounding_is_not_borrowed_from_other_exchanges(self):
        self.assertEqual(self.rules.resolve(bar(board="bse", raw_preclose=10.01))["status"], "pending")
        self.assertEqual(self.rules.resolve(bar(board="bse", raw_preclose=10))["upper_limit"], 13)

    def test_adjusted_price_cannot_become_exchange_limit_reference(self):
        self.assertEqual(self.rules.resolve(bar(raw_preclose=None, close=9.8))["status"], "pending")
        self.assertEqual(self.rules.resolve(bar(limit_reference_verified=False))["status"], "pending")

    def test_ordinary_daily_bar_does_not_prove_auction_fill(self):
        result = assess_next_open_order(bar(), "buy", self.rules)
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["daily_bar_price_admissible"])
        self.assertIsNone(result["fill_price"])

    def test_limit_open_queue_is_pending_despite_later_intraday_range(self):
        buying = assess_next_open_order(bar(raw_open=11, raw_high=11, raw_close=10.2), "buy", self.rules)
        selling = assess_next_open_order(bar(raw_open=9, raw_low=9, raw_close=9.8), "sell", self.rules)
        self.assertTrue(buying["queue_constrained"])
        self.assertTrue(selling["queue_constrained"])
        self.assertEqual(buying["status"], "pending")
        self.assertIn("queue", selling["reason"])

    def test_price_outside_verified_limit_is_blocked(self):
        result = assess_next_open_order(bar(raw_open=11.01, raw_high=11.02), "buy", self.rules)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["fill_price"])

    def test_suspension_blocks_exit_but_allows_only_prior_known_valuation_mark(self):
        suspended = bar(trade_status="0", raw_open=np.nan, raw_close=np.nan)
        self.assertEqual(assess_next_open_order(suspended, "sell", self.rules)["status"], "blocked")
        marked = mark_position(suspended, "2026-07-07", 9.9, "2026-07-06")
        self.assertEqual(marked["status"], "marked_stale")
        self.assertEqual(marked["mark"], 9.9)
        self.assertFalse(marked["executable_exit"])
        self.assertEqual(mark_position(suspended, "2026-07-07", 9.9, "2026-07-08")["status"], "pending")

    def test_missing_or_delisted_stock_never_gets_a_zero_return_or_fake_exit(self):
        for row in (None, bar(listing_state="delisted", trade_status="0")):
            self.assertEqual(assess_next_open_order(row, "sell", self.rules)["status"], "pending")
            marked = mark_position(row, "2026-07-07", 10, "2026-07-06")
            self.assertEqual(marked["status"], "pending")
            self.assertNotIn("mark", marked)

    def test_receipt_hash_and_claimed_fill_must_match_for_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / "fills.json"
            fill = {"symbol": "600000", "date": "2026-07-07", "side": "buy", "price": 10,
                    "quantity": 100, "phase": "opening_auction"}
            evidence.write_text(json.dumps({"fills": [fill]}), encoding="utf-8")
            receipt = {**fill, "verified": True, "source_kind": "broker_execution", "evidence_path": str(evidence),
                       "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
            result = assess_next_open_order(bar(), "buy", self.rules, receipt, quantity=100, one_way_cost=.005)
            self.assertEqual(result["status"], "confirmed")
            self.assertEqual(result["cost_stress_estimate"], 5)
            self.assertEqual(assess_next_open_order(bar(), "buy", self.rules, receipt, quantity=200)["status"], "pending")
            self.assertEqual(assess_next_open_order(bar(), "buy", self.rules, {**receipt, "quantity": 200})["status"], "pending")
            evidence.write_text("{}");
            self.assertEqual(assess_next_open_order(bar(), "buy", self.rules, receipt)["status"], "pending")

    def test_explicit_calendar_preserves_missing_execution_session(self):
        dates = pd.bdate_range("2026-07-06", periods=7)
        columns = [str(i) for i in range(10)]
        scores = pd.DataFrame(np.tile(np.arange(10), (7, 1)), index=dates, columns=columns)
        # Highest-ranked stock is missing on t+1; it must not be shifted to t+2.
        frame = pd.DataFrame([bar(date=date, symbol=symbol) for date in dates for symbol in columns
                              if not (date == dates[1] and symbol == "9")])
        result = audit_next_open(frame, scores, self.rules, ExecutionPolicy(min_assets=10), calendar=dates)
        first = result["orders"][0]
        self.assertEqual(first["execution_date"], dates[1].strftime("%Y-%m-%d"))
        self.assertEqual(first["status"], "pending")
        self.assertNotIn("nav", result)
        self.assertNotIn("sharpe", result)
        self.assertEqual(result["status"], "pending")

    def test_calendar_is_required_and_future_marks_are_rejected(self):
        frame = pd.DataFrame([bar()])
        signal = pd.DataFrame([[1]], index=[pd.Timestamp("2026-07-06")], columns=["600000"])
        self.assertEqual(audit_next_open(frame, signal, self.rules)["status"], "pending")
        self.assertEqual(mark_position(bar(date="2026-07-08"), "2026-07-07")["status"], "pending")


if __name__ == "__main__":
    unittest.main()
