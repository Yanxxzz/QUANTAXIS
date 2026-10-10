import unittest
import numpy as np
import pandas as pd

from panda_alpha.residual_commonality import residual_commonality


class CommonalityTests(unittest.TestCase):
    def frame(self, seed=24):
        rng = np.random.default_rng(seed)
        r = rng.normal(0, .012, (80, 24))
        return pd.DataFrame(100 * np.cumprod(1 + r, axis=0))

    def test_dual_identity_matches_direct_pairwise_residual_correlations(self):
        prices = self.frame()
        actual = residual_commonality(prices, 30, 1, 10, 5).iloc[-1]
        r = (prices / prices.shift() - 1).iloc[-30:].to_numpy()
        m = (r.sum(axis=1, keepdims=True) - r) / (r.shape[1] - 1)
        r -= r.mean(axis=0)
        m -= m.mean(axis=0)
        e = r - m * (np.sum(r * m, axis=0) / np.sum(m * m, axis=0))
        corr = np.corrcoef(e.T)
        expected = (np.sum(corr ** 2, axis=1) - 1) / (len(actual) - 1)
        np.testing.assert_allclose(actual, expected, atol=1e-13)

    def test_future_changes_do_not_change_history(self):
        p = self.frame()
        before = residual_commonality(p, 20, 3, 10, 5)
        p.iloc[60:] *= np.linspace(1, 2, 20)[:, None]
        after = residual_commonality(p, 20, 3, 10, 5)
        pd.testing.assert_frame_equal(before.iloc[:60], after.iloc[:60])

    def test_missing_calendar_close_invalidates_both_adjacent_returns(self):
        p = self.frame()
        p.iloc[50, 0] = np.nan
        s = residual_commonality(p, 20, 1, 10, 5)
        self.assertTrue(s.iloc[50:71, 0].isna().all())
        self.assertTrue(np.isfinite(s.iloc[71, 0]))

    def test_insufficient_assets_fail_closed(self):
        self.assertTrue(residual_commonality(self.frame(), 20, 1, 25, 5).isna().all().all())

    def test_common_residual_cluster_exceeds_independent_assets(self):
        rng = np.random.default_rng(13)
        r = rng.normal(0, .006, (140, 40))
        shock = rng.normal(0, .025, 140)
        r[:, :10] += shock[:, None]
        p = pd.DataFrame(100 * np.cumprod(1 + r, axis=0))
        s = residual_commonality(p, 120, 1, 20, 5).iloc[-1]
        self.assertGreater(s.iloc[:10].median(), s.iloc[10:].median())

    def test_column_reordering_preserves_values(self):
        p = self.frame()
        a = residual_commonality(p, 20, 3, 10, 5)
        b = residual_commonality(p.iloc[:, ::-1], 20, 3, 10, 5).reindex(columns=p.columns)
        np.testing.assert_allclose(a, b, atol=1e-13, equal_nan=True)


if __name__ == '__main__':
    unittest.main()
