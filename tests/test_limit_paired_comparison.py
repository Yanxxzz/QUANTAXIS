"""Pure synthetic paired portfolio NAVs; no data, account or registry access."""
import unittest

import numpy as np
import pandas as pd

from panda_alpha.limit_paired_comparison import PairedNavInputError, compare_complete_nav


def nav(dates, returns):
    return pd.DataFrame({"date": list(dates),
                         "nav": 100000. * np.cumprod(1. + np.asarray(returns, dtype=float))})


class PairedMonthBlockTests(unittest.TestCase):
    def test_first_day_uses_fixed_initial_cash_and_one_month_boundary(self):
        result = compare_complete_nav([{"date": "2024-01-02", "nav": 101000.}],
                                      [{"date": "2024-01-02", "nav": 100000.}])
        self.assertAlmostEqual(2.52, result["annualized_mean_difference"])
        self.assertAlmostEqual(2.52, result["confidence_interval_95"][0])
        self.assertAlmostEqual(2.52, result["confidence_interval_95"][1])
        self.assertFalse(result["ci_contains_zero"])
        self.assertEqual(1, result["day_count"])
        self.assertEqual(1, result["block_count"])
        self.assertTrue(result["fewer_than_two_month_blocks"])

    def test_unequal_month_lengths_retain_daily_weights_when_repeated(self):
        dates = ["2024-01-29", "2024-01-30", "2024-01-31", "2024-02-01"]
        candidate = nav(dates, [.01, .01, .01, -.01])
        control = nav(dates, [0., 0., 0., 0.])
        result = compare_complete_nav(candidate, control)
        # Daily mean(.01,.01,.01,-.01)=.005; equal weighting monthly means would0.
        self.assertAlmostEqual(1.26, result["annualized_mean_difference"])
        self.assertEqual([3, 1], [block["days"] for block in result["month_blocks"]])
        # Entire-month draws can repeat all-positive or all-negative blocks.
        np.testing.assert_allclose([-2.52, 2.52], result["confidence_interval_95"], atol=1e-12)
        self.assertTrue(result["ci_contains_zero"])

    def test_identical_legs_cancel_common_shocks_and_are_reproducible(self):
        dates = ["2024-01-31", "2024-02-01", "2024-02-02", "2024-03-01"]
        series = nav(dates, [.1, -.2, .05, .01])
        result = compare_complete_nav(series, series)
        self.assertEqual(0., result["annualized_mean_difference"])
        self.assertEqual([0., 0.], result["confidence_interval_95"])
        self.assertAlmostEqual(1., result["paired_daily_return_correlation"])
        self.assertEqual(result, compare_complete_nav(series, series))
        self.assertEqual(20261014, result["bootstrap_seed"])
        self.assertEqual(2000, result["bootstrap_samples"])

    def test_candidate_minus_control_direction_is_preserved(self):
        dates = ["2024-01-31", "2024-02-01", "2024-03-01"]
        a, b = nav(dates, [.01, .02, .03]), nav(dates, [0., 0., 0.])
        forward, reverse = compare_complete_nav(a, b), compare_complete_nav(b, a)
        self.assertAlmostEqual(5.04, forward["annualized_mean_difference"])
        self.assertAlmostEqual(-forward["annualized_mean_difference"],
                               reverse["annualized_mean_difference"])
        np.testing.assert_allclose(reverse["confidence_interval_95"],
                                   [-forward["confidence_interval_95"][1],
                                    -forward["confidence_interval_95"][0]], atol=1e-12)

    def test_missing_date_in_one_leg_is_rejected_without_inner_selection(self):
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        a = nav(dates, [0., .01, -.01])
        b = nav(dates[:2], [0., .01])
        with self.assertRaises(PairedNavInputError):
            compare_complete_nav(a, b)

    def test_jointly_missing_date_is_rejected_with_independent_calendar(self):
        calendar = ["2024-01-02", "2024-01-03", "2024-01-04"]
        both = nav([calendar[0], calendar[2]], [0., .01])
        with self.assertRaises(PairedNavInputError):
            compare_complete_nav(both, both, expected_dates=calendar)
        self.assertFalse(compare_complete_nav(both, both)["expected_calendar_verified"])
        full = nav(calendar, [0., .01, -.01])
        self.assertTrue(compare_complete_nav(full, full,
            expected_dates=calendar)["expected_calendar_verified"])

    def test_unknown_or_nonpositive_nav_cannot_be_zero_filled(self):
        good = nav(["2024-01-02", "2024-01-03"], [0., .01])
        for value in (np.nan, np.inf, -np.inf, None, 0., -1., True, "not_money"):
            bad = good.astype(object).copy()
            bad.loc[1, "nav"] = value
            with self.subTest(value=value), self.assertRaises(PairedNavInputError):
                compare_complete_nav(bad, good)

    def test_unsorted_duplicate_or_invalid_daily_identity_is_rejected(self):
        good = nav(["2024-01-02", "2024-01-03"], [0., .01])
        for dates in (["2024-01-03", "2024-01-02"], ["2024-01-02", "2024-01-02"],
                      ["2024-01-02", None], ["2024-01-02", "2024-01-03T01:00:00"],
                      ["2024-01-02", "2024-01-03T00:00:00+08:00"]):
            bad = good.copy()
            bad["date"] = dates
            with self.subTest(dates=dates), self.assertRaises(PairedNavInputError):
                compare_complete_nav(bad, good)

    def test_empty_or_ambiguous_columns_are_rejected(self):
        good = nav(["2024-01-02"], [0.])
        for bad in (pd.DataFrame(columns=["date", "nav"]),
                    pd.DataFrame({"date": ["2024-01-02"], "value": [100000.]}),
                    pd.DataFrame([["2024-01-02", 100000., 100000.]],
                                 columns=["date", "nav", "nav"])):
            with self.assertRaises(PairedNavInputError):
                compare_complete_nav(bad, good)

    def test_no_economic_upgrade_claim_and_exposure_limits_are_explicit(self):
        a = nav(["2024-01-31", "2024-02-01"], [.01, -.01])
        result = compare_complete_nav(a, a)
        self.assertIsNone(result["economic_upgrade_decision"])
        text = " ".join(result["limitations"])
        for required in ("correlated", "exposed", "multiple", "drawdown", "equivalent", "CAGR"):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
