import unittest

from panda_alpha.attribution import AttributionError, BetaExposure, Trade, account_day, summarize


class AttributionTests(unittest.TestCase):
    def day(self, **changes):
        kwargs = dict(day="2026-01-06", previous_nav=100, cash_before=0,
                      quantities_before={"A": 10}, previous_close={"A": 10},
                      open_marks={"A": 11}, close_marks={"A": 12}, trades=[], cash_after=0)
        kwargs.update(changes)
        return account_day(**kwargs)

    def test_unchanged_holdings_have_no_fee_and_exact_mark_pnl(self):
        r = self.day()
        self.assertEqual(r["price_pnl"], 20)
        self.assertEqual(r["fees"], 0)
        self.assertEqual(r["nav"], 120)

    def test_open_rotation_does_not_assign_new_buy_overnight_gain(self):
        r = self.day(open_marks={"A": 11, "B": 20}, close_marks={"A": 12, "B": 22},
                     trades=[Trade("A", -10, 11, 1.1), Trade("B", 5, 20, 1)], cash_after=7.9)
        rows = {x["code"]: x for x in r["security_rows"]}
        self.assertEqual(rows["A"]["overnight_price_pnl"], 10)
        self.assertEqual(rows["A"]["intraday_price_pnl"], 0)
        self.assertEqual(rows["B"]["overnight_price_pnl"], 0)
        self.assertEqual(rows["B"]["intraday_price_pnl"], 10)
        self.assertAlmostEqual(r["nav_change"], 17.9)
        self.assertAlmostEqual(r["fees"], 2.1)

    def test_blocked_sale_retains_quantity_cash_and_marked_pnl(self):
        r = self.day(open_marks={"A": 10}, close_marks={"A": 10})
        self.assertEqual(r["positions_after"], {"A": 10})
        self.assertEqual(r["cash_after"], 0)
        self.assertEqual(r["nav_change"], 0)

    def test_final_sale_earns_only_overnight_pnl_and_actual_fee(self):
        r = self.day(trades=[Trade("A", -10, 11, 0.55)], cash_after=109.45)
        self.assertEqual(r["positions_after"], {})
        self.assertEqual(r["price_pnl"], 10)
        self.assertAlmostEqual(r["nav_change"], 9.45)

    def test_two_leg_projection_handles_beta_refresh_at_open(self):
        r = self.day(overnight_beta={"A": BetaExposure(2, "2026-01-02")},
                     intraday_beta={"A": BetaExposure(1, "2026-01-05")},
                     overnight_market={"A": .01}, intraday_market={"A": .02})
        self.assertAlmostEqual(r["market_projection"], 100*2*.01+110*.02)
        self.assertAlmostEqual(r["statistical_residual_pnl"] + r["market_projection"], 20)
        self.assertEqual(r["unprojected_price_pnl"], 0)

    def test_missing_market_or_beta_is_explicit_unknown_pnl(self):
        r = self.day(overnight_beta={"A": BetaExposure(1, "2026-01-02")}, overnight_market={"A": .01})
        self.assertEqual(r["unprojected_price_pnl"], 10)
        self.assertEqual(r["market_projection"], 1)
        self.assertEqual(r["statistical_residual_pnl"], 9)
        self.assertFalse(r["security_rows"][0]["intraday_projection_known"])

    def test_same_day_beta_rejected(self):
        with self.assertRaises(AttributionError):
            self.day(overnight_beta={"A": BetaExposure(1, "2026-01-06")}, overnight_market={"A": .01})

    def test_invalid_cash_or_short_or_missing_mark_rejected(self):
        for changes in ({"cash_after": 1}, {"trades": [Trade("A", -11, 11, 0)], "cash_after": 121}, {"close_marks": {}}):
            with self.subTest(changes=changes), self.assertRaises(AttributionError):
                self.day(**changes)

    def test_fee_assigned_to_security_and_fold_contributions_add(self):
        first = self.day()
        second = self.day(day="2026-01-07", previous_nav=120, previous_close={"A": 12},
                          open_marks={"A": 12}, close_marks={"A": 11},
                          trades=[Trade("A", -5, 12, .6)], cash_after=59.4)
        result = summarize([first, second], folds=2)
        self.assertAlmostEqual(result["nav_change"], 14.4)
        self.assertAlmostEqual(result["securities"][0]["net_contribution"], 14.4)
        self.assertAlmostEqual(sum(x["nav_change"] for x in result["folds"]), result["nav_change"])
        self.assertEqual(result["concentration"]["top_1_share_of_positive_contributions"], 1)

    def test_broken_nav_chain_rejected(self):
        with self.assertRaises(AttributionError):
            summarize([self.day(), self.day(day="2026-01-07")], folds=2)


if __name__ == "__main__":
    unittest.main()
