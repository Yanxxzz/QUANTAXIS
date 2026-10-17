import sys
import unittest

import numpy as np
import pandas as pd

from panda_alpha.diversity import (DiversityPolicy, as_factor_panel, assess_candidate,
                                  canonical_signature, cross_sectional_rank_correlation,
                                  select_diverse)
from panda_alpha.evolution import Candidate


class DiversityTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2025-01-01", periods=4)
        self.panel = pd.DataFrame(np.tile(np.arange(6), (4, 1)), index=self.dates,
                                  columns=list("ABCDEF"))
        self.policy = DiversityPolicy(min_assets=3, min_days=3,
                                      min_daily_coverage=0.6, min_valid_day_fraction=0.75)
        self.candidate = Candidate("resilience", "range", formula="HIGH-LOW")

    def test_ast_equivalence_and_parameter_grid_family(self):
        self.assertEqual(canonical_signature("rank(ma(close,20))+volume"),
                         canonical_signature("RANK(TS_MEAN(CLOSE,20))+VOLUME"))
        self.assertEqual(canonical_signature("MA(CLOSE,20) + VOLUME"),
                         canonical_signature("VOLUME+TS_MEAN(CLOSE,20.0) # comment"))
        self.assertNotEqual(canonical_signature("MA(CLOSE,20)"),
                            canonical_signature("MA(CLOSE,60)"))
        self.assertEqual(canonical_signature("MA(CLOSE,20)", True),
                         canonical_signature("MA(CLOSE,60)", True))
        self.assertNotEqual(canonical_signature("HIGH-LOW"), canonical_signature("LOW-HIGH"))

    def test_alternating_sign_cannot_hide_redundancy(self):
        opposite = self.panel.copy()
        opposite.iloc[::2] *= -1
        pair = cross_sectional_rank_correlation(self.panel, opposite, self.policy)
        self.assertEqual(pair.status, "complete")
        self.assertAlmostEqual(pair.signed_mean_correlation, 0)
        self.assertAlmostEqual(pair.mean_abs_correlation, 1)
        result = assess_candidate(self.candidate, self.panel, {"existing": opposite}, self.policy)
        self.assertEqual(result.status, "reject")
        self.assertEqual(result.economic_status, "pending")

    def test_python_definitions_keep_their_stored_python311_signatures(self):
        cases = [
            ("class Signal:\n    pass",
             "1ab42ceddf473e6f9f267a4288e6c580ee57a8ed0315d897e4fb75244b2042bb"),
            ('async def calculate(factors):\n    return factors["close"]',
             "d8e4fb753e5cacac120ee1d912aa8596e3ce714a8e0ce8352d329a4b847653de"),
            ('class Signal:\n    def calculate(self, factors):\n        return factors["close"]',
             "da9a59856292e0913db91739b115e3c5f6d732ab4d60342a0641a1c16aa24673"),
        ]
        for source, stored_signature in cases:
            with self.subTest(source=source):
                self.assertEqual(canonical_signature(source), stored_signature)

    @unittest.skipIf(sys.version_info < (3, 12), "PEP 695 syntax needs Python 3.12")
    def test_real_type_parameters_remain_part_of_definition_identity(self):
        for template in ("class Signal{params}:\n    pass",
                         "def calculate{params}(value):\n    return value",
                         "async def calculate{params}(value):\n    return value"):
            with self.subTest(template=template):
                signatures = {canonical_signature(template.format(params=params))
                              for params in ("", "[T]", "[T: int]")}
                self.assertEqual(len(signatures), 3)

    def test_constant_nan_and_insufficient_coverage_are_pending(self):
        constant = self.panel * 0 + 1
        self.assertEqual(cross_sectional_rank_correlation(self.panel, constant, self.policy).status, "pending")
        sparse = self.panel.astype(float)
        sparse.iloc[:, 2:] = np.nan
        self.assertEqual(cross_sectional_rank_correlation(self.panel, sparse, self.policy).status, "pending")
        self.assertEqual(assess_candidate(self.candidate, constant, {}, self.policy).status, "pending")
        nonfinite = self.panel * np.inf
        self.assertEqual(assess_candidate(self.candidate, nonfinite, {}, self.policy).status, "pending")

    def test_comparison_dates_include_missing_pool_dates(self):
        short = self.panel.iloc[:2]
        policy = DiversityPolicy(min_assets=3, min_days=2, min_valid_day_fraction=0.75)
        result = cross_sectional_rank_correlation(self.panel, short, policy)
        self.assertEqual(result.comparison_days, 4)
        self.assertEqual(result.valid_days, 2)
        self.assertEqual(result.status, "pending")

    def test_pool_without_values_cannot_pass(self):
        prior = Candidate("trend", "trend", formula="CLOSE/OPEN")
        result = assess_candidate(self.candidate, self.panel, {}, self.policy, [prior])
        self.assertEqual(result.status, "pending")
        self.assertIn(prior.candidate_id, result.reasons[0])

    def test_equivalent_formula_rejected_before_value_comparison(self):
        prior = Candidate("same range", "range", formula="HIGH - LOW")
        self.assertEqual(assess_candidate(self.candidate, None, {}, self.policy, [prior]).status, "reject")

    def test_low_correlated_values_pass_diversity_only(self):
        pool = self.panel.copy()
        pool.iloc[:, :] = [0, 3, 5, 2, 1, 4]
        result = assess_candidate(self.candidate, self.panel, {"existing": pool}, self.policy)
        self.assertEqual(result.status, "accept")
        self.assertEqual(result.economic_status, "pending")

    def test_factor_series_contract_and_long_table(self):
        series = self.panel.rename_axis(index="date", columns="symbol").stack()
        pd.testing.assert_frame_equal(as_factor_panel(series),
                                      self.panel.rename_axis(index="date", columns="symbol").astype(float))
        reversed_index = series.reorder_levels(["symbol", "date"])
        pd.testing.assert_frame_equal(as_factor_panel(reversed_index), as_factor_panel(series))
        long = series.rename("value").reset_index()
        pd.testing.assert_frame_equal(as_factor_panel(long), as_factor_panel(series), check_freq=False)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            as_factor_panel(pd.concat([long, long.iloc[:1]], ignore_index=True))

    def test_aggregate_metrics_and_numeric_date_index_rejected(self):
        result = assess_candidate(self.candidate, pd.DataFrame({"IC": [0.1], "Sharpe": [2]}), {}, self.policy)
        self.assertEqual(result.status, "pending")
        with self.assertRaises(ValueError):
            as_factor_panel(pd.Series([1, 2, 3]))

    def test_selection_prioritizes_new_axes_and_caps_numeric_grids(self):
        first = Candidate("reversion", "reversion", formula="MA(CLOSE,5)", fields=("CLOSE",))
        grid = Candidate("reversion", "reversion", formula="MA(CLOSE,10)", fields=("CLOSE",))
        different = Candidate("liquidity", "liquidity", formula="AMOUNT/VOLUME",
                              fields=("AMOUNT", "VOLUME"))
        selected = select_diverse([first, grid, different], 3)
        self.assertEqual(len(selected), 2)
        self.assertIn(different, selected)
        self.assertEqual(select_diverse([grid, different], 2, [first]), [different])


if __name__ == "__main__":
    unittest.main()
