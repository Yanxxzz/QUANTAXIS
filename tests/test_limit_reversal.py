"""Synthetic daily bars only: no account calls, market sources or real returns."""
import unittest

import pandas as pd

from panda_alpha.limit_reversal import Policy, simulate, LimitStrategyInputError


DAYS = pd.bdate_range("2024-01-02", periods=16).strftime("%Y-%m-%d").tolist()


def bar(day, code="A", *, open=10., high=10., low=10., close=10., up=20., down=1.,
        volume=1000000., status=1, factor=1., eligible=True, **extra):
    return {"date": day, "symbol": code, "raw_open": open, "raw_high": high,
            "raw_low": low, "raw_close": close, "up_limit": up, "down_limit": down,
            "volume": volume, "trade_status": status, "adjustment_factor": factor,
            "entry_eligible": eligible, **extra}


def setup(codes=("A",), n=6):
    rows = []
    for code in codes:
        rows += [bar(DAYS[0], code, open=11., high=11., low=10., close=11., up=11., down=9.),
                 bar(DAYS[1], code, open=12., high=12., low=11., close=12., up=12., down=10.),
                 bar(DAYS[2], code, open=10.5, high=11., low=10., close=10.5, up=13., down=10.)]
        rows += [bar(day, code, open=10.5, high=10.7, low=10.3, close=10.5)
                 for day in DAYS[3:n]]
    return rows


def put(rows, day, code="A", **changes):
    row = next(row for row in rows if row["date"] == day and row["symbol"] == code)
    aliases = {"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close",
               "up": "up_limit", "down": "down_limit", "status": "trade_status", "factor": "adjustment_factor"}
    row.update({aliases.get(key, key): value for key, value in changes.items()})


class LimitReversalTests(unittest.TestCase):
    def run_strategy(self, rows=None, n=6, **kwargs):
        return simulate(pd.DataFrame(setup(n=n) if rows is None else rows), DAYS[:n], **kwargs)

    def test_preopen_two_closes_then_limit_bid_no_third_prior_constraint(self):
        result = self.run_strategy()
        buy = result["trades"][0]
        self.assertEqual(DAYS[2], buy["date"])
        self.assertEqual(10., buy["price"])
        self.assertEqual("potential_limit_fill", buy["state"])
        self.assertFalse(buy["fill_certified"])
        self.assertEqual(DAYS[:2], result["signals"][0]["evidence_dates"])
        # A third preceding limit-up is allowed and not quietly excluded.
        rows = setup()
        prior = "2024-01-01"
        rows.append(bar(prior, close=10., up=10., low=9., down=8.))
        actual = simulate(rows, [prior] + DAYS[:6])
        self.assertTrue(any(t["side"] == "buy" and t["date"] == DAYS[2] for t in actual["trades"]))

    def test_missing_or_suspended_reference_day_breaks_signal_no_compress(self):
        rows = setup()
        rows = [row for row in rows if row["date"] != DAYS[1]]
        self.assertEqual([], self.run_strategy(rows)["trades"])
        rows = setup()
        put(rows, DAYS[1], status="suspended", volume=0.)
        self.assertEqual([], self.run_strategy(rows)["trades"])

    def test_current_st_or_ipo_gate_does_not_remove_existing_position(self):
        rows = setup()
        put(rows, DAYS[2], entry_eligible=False)
        self.assertEqual([], self.run_strategy(rows)["trades"])
        rows = setup()
        put(rows, DAYS[3], entry_eligible=False, open=11.1, high=11.3, low=11., close=11.2)
        self.assertEqual("sell", self.run_strategy(rows)["trades"][-1]["side"])

    def test_no_entry_day_exit_and_strict_profit_tick(self):
        rows = setup()
        put(rows, DAYS[2], high=12.5, close=11.7)
        put(rows, DAYS[3], open=11., high=11., low=10.9, close=11.)
        put(rows, DAYS[4], open=11., high=11.01, low=10.9, close=11.)
        result = self.run_strategy(rows, cost=0.)
        sells = [trade for trade in result["trades"] if trade["side"] == "sell"]
        self.assertEqual([DAYS[4]], [trade["date"] for trade in sells])
        self.assertEqual(11.01, sells[0]["price"])
        self.assertFalse(any(o["side"] == "sell" and o["date"] == DAYS[2] for o in result["orders"]))

    def test_take_profit_open_gap_uses_open(self):
        rows = setup()
        put(rows, DAYS[3], open=11.5, high=11.7, low=11.2, close=11.6)
        result = self.run_strategy(rows)
        self.assertEqual(11.5, result["trades"][-1]["price"])

    def test_gap_stop_uses_worse_open_and_same_day_double_hit_prefers_stop(self):
        for opening, expected in [(8.7, 8.7), (10., 9.2)]:
            rows = setup()
            put(rows, DAYS[3], open=opening, high=12., low=min(8.5, opening), close=10.)
            sell = self.run_strategy(rows, policy="risk_guard")["trades"][-1]
            self.assertEqual("stop_loss", sell["reason"])
            self.assertAlmostEqual(expected, sell["price"], places=12)

    def test_one_price_down_blocks_selling_and_stop_retries_next_trade(self):
        rows = setup()
        put(rows, DAYS[3], open=9., high=9., low=9., close=9., down=9., up=11.)
        put(rows, DAYS[4], open=9.5, high=10., low=9.4, close=9.8)
        result = self.run_strategy(rows, policy="risk_guard")
        sell = result["trades"][-1]
        self.assertEqual(DAYS[4], sell["date"])
        self.assertEqual(9.5, sell["price"])
        self.assertEqual("stop_loss", sell["reason"])
        self.assertEqual(1, result["blocked"]["sell_blocked_one_price_limit_down"])

    def test_one_price_down_buy_potential_but_zero_volume_never_fills(self):
        rows = setup()
        put(rows, DAYS[2], open=10., high=10., low=10., close=10.)
        self.assertEqual("buy", self.run_strategy(rows)["trades"][0]["side"])
        put(rows, DAYS[2], volume=0.)
        result = self.run_strategy(rows)
        self.assertEqual([], result["trades"])
        self.assertEqual(1, result["blocked"]["buy_blocked_no_trade"])

    def test_baseline_terminal_not_forced_risk_session_ten_open(self):
        rows = setup(n=14)
        baseline = self.run_strategy(rows, n=14)
        self.assertIn("A", baseline["terminal"])
        self.assertFalse(baseline["summary"]["terminal_liquidation_forced"])
        risk = self.run_strategy(rows, n=14, policy="risk_guard")
        sell = risk["trades"][-1]
        self.assertEqual(DAYS[11], sell["date"])
        self.assertEqual(10, sell["holding_sessions"])
        self.assertEqual("max_holding_sessions", sell["reason"])

    def test_unknown_held_price_or_delisting_source_pending_not_cash_redemption(self):
        for changes in [dict(raw_close=float("nan")), dict(status="delisted")]:
            rows = setup()
            put(rows, DAYS[3], **changes)
            result = self.run_strategy(rows)
            self.assertEqual("SOURCE_PENDING", result["summary"]["status"])
            self.assertIsNone(result["summary"]["metrics"])
            self.assertEqual(3, len(result["daily"]))
            self.assertIn("A", result["terminal"])
        rows = [row for row in setup() if row["date"] != DAYS[3]]
        self.assertEqual("SOURCE_PENDING", self.run_strategy(rows)["summary"]["status"])

    def test_unknown_held_execution_metadata_pending_before_any_cash_change(self):
        rows = setup()
        put(rows, DAYS[3], down=float("nan"), up=float("nan"))
        result = self.run_strategy(rows)
        self.assertEqual("SOURCE_PENDING", result["summary"]["status"])
        self.assertEqual(result["daily"][-1]["cash"], result["summary"]["terminal_cash"])

    def test_explicit_suspension_previous_mark_and_unknown_zero_status_distinct(self):
        rows = setup()
        put(rows, DAYS[3], status="suspended", raw_open=float("nan"), raw_high=float("nan"),
            raw_low=float("nan"), raw_close=float("nan"), prevclose=10.5, volume=0.)
        result = self.run_strategy(rows)
        self.assertEqual(["A"], result["daily"][3]["known_suspension_marks"])
        self.assertEqual(10.5, result["daily"][3]["positions"][0]["close"])
        put(rows, DAYS[3], status=0)
        self.assertEqual("SOURCE_PENDING", self.run_strategy(rows)["summary"]["status"])

    def test_finite_cash_round_lots_code_order_no_volume_selection(self):
        rows = setup(codes=("F", "E", "D", "C", "B", "A"))
        put(rows, DAYS[2], "A", volume=1.)
        put(rows, DAYS[2], "F", volume=1e9)
        result = self.run_strategy(rows, cost=.005)
        buys = [t for t in result["trades"] if t["side"] == "buy"]
        self.assertEqual(list("BCDE"), [trade["symbol"] for trade in buys])
        self.assertFalse(any(t["symbol"] == "F" for t in buys))
        self.assertTrue(all(t["quantity"] % 100 == 0 for t in buys))
        self.assertTrue(all(d["cash"] >= 0 for d in result["daily"]))
        self.assertAlmostEqual(sum(t["fee"] for t in buys), result["summary"]["total_fees"])
        self.assertAlmostEqual(100000. - sum(t["notional"] + t["fee"] for t in buys), result["daily"][2]["cash"])

    def test_unfilled_slot_not_recycled_to_higher_code(self):
        rows = setup(codes=("A", "B", "C", "D", "E", "F"))
        put(rows, DAYS[2], "A", low=10.2)
        result = self.run_strategy(rows)
        self.assertEqual(list("BCDE"), [t["symbol"] for t in result["trades"] if t["side"] == "buy"])

    def test_future_suffix_changes_cannot_change_prior_signals_orders_cash(self):
        rows = setup(n=10)
        original = self.run_strategy(rows, n=10)
        altered = [dict(row) for row in rows]
        for row in altered:
            if row["date"] >= DAYS[6]:
                row.update(raw_open=1., raw_high=20., raw_low=1., raw_close=15., volume=1e20)
        changed = self.run_strategy(altered, n=10)
        for key in ["signals", "orders", "trades", "daily"]:
            self.assertEqual([row for row in original[key] if row["date"] < DAYS[6]],
                             [row for row in changed[key] if row["date"] < DAYS[6]])

    def test_factor_jump_without_exact_role_pending_total_return_proxy_works(self):
        rows = setup()
        put(rows, DAYS[3], factor=2., open=5.3, high=5.5, low=5.2, close=5.4)
        put(rows, DAYS[4], factor=2., open=5.6, high=5.8, low=5.4, close=5.7)
        result = self.run_strategy(rows)
        self.assertEqual("SOURCE_PENDING", result["summary"]["status"])
        proxy = self.run_strategy(rows, adjustment_factor_role="total_return_factor", cost=0.)
        sell = proxy["trades"][-1]
        self.assertEqual(DAYS[4], sell["date"])
        self.assertEqual(5.6, sell["price"])
        self.assertEqual(4000., sell["quantity"])
        self.assertEqual("total_return_equivalent_units_not_actual_post_action_shares", proxy["summary"]["quantity_model"])
        self.assertAlmostEqual(102400., proxy["summary"]["terminal_nav"])

    def test_unknown_factor_role_not_accepted(self):
        with self.assertRaises(LimitStrategyInputError):
            self.run_strategy(adjustment_factor_role="qfq")

    def test_evaluation_warmup_used_for_signal_not_counted_daily_metrics(self):
        result = self.run_strategy(evaluation_start=DAYS[2])
        self.assertEqual(DAYS[2], result["daily"][0]["date"])
        self.assertEqual(4, result["summary"]["metrics"]["days"])
        self.assertEqual(DAYS[2], result["trades"][0]["date"])

    def test_invalid_duplicate_rows_or_compressed_calendar_rejected(self):
        rows = setup()
        with self.assertRaises(LimitStrategyInputError):
            self.run_strategy(rows + [rows[0]])
        with self.assertRaises(LimitStrategyInputError):
            simulate(rows, [DAYS[1], DAYS[0]])


if __name__ == "__main__":
    unittest.main()


def test_checkpoint_resume_preserves_account_and_calendar_indices():
    rows=setup(n=6)
    full=simulate(rows,DAYS[:6],cost=.003)
    prefix=simulate([r for r in rows if r["date"]<=DAYS[2]],DAYS[:3],cost=.003)
    tail=simulate(rows,DAYS[:6],cost=.003,evaluation_start=DAYS[3],resume_state={"cash":prefix["summary"]["terminal_cash"],"nav":prefix["summary"]["last_completed_nav"],"holdings":prefix["terminal"]})
    assert tail["daily"] == full["daily"][3:]
    assert tail["terminal"] == full["terminal"]
