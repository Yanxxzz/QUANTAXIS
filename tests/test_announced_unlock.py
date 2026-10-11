import unittest

import pandas as pd

from panda_alpha.announced_unlock import project_announced_quantity, render_notice_contract_probe


class AnnouncedUnlockTests(unittest.TestCase):
    def setUp(self):
        self.events = [{"symbol": "600548", "available_reference_date": "2026-09-23",
                        "announced_listing_date": "2026-09-28",
                        "announced_listing_shares": 75930144}]

    def test_no_prepublication_or_postplanned_date_inference(self):
        idx = pd.MultiIndex.from_product([["600548", "000001"],
                                          ["2026-09-22", "2026-09-23", "2026-09-28"]],
                                         names=["symbol", "date"])
        out = project_announced_quantity(idx, self.events)
        self.assertTrue(out.index.equals(idx))
        self.assertEqual(out.notna().sum(), 1)
        self.assertEqual(out.loc[("600548", "2026-09-23")], 75930144)
        self.assertTrue(out.loc[("000001", "2026-09-23")] != 0)

    def test_native_date_first_exchange_suffix_and_integer_symbol(self):
        idx = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-09-23"), "600548.XSHG"),
                                         (pd.Timestamp("2026-09-23"), 1)],
                                        names=["date", "symbol"])
        out = project_announced_quantity(idx, self.events)
        self.assertEqual(out.iloc[0], 75930144)
        self.assertTrue(pd.isna(out.iloc[1]))

    def test_ambiguous_same_symbol_overlap_is_not_summed(self):
        idx = pd.MultiIndex.from_tuples([("600548", "2026-09-23")], names=["symbol", "date"])
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            project_announced_quantity(idx, self.events * 2)

    def test_self_contained_probe_replays_both_native_level_orders(self):
        class FixtureFactor:
            pass
        for order in [("symbol", "date"), ("date", "symbol")]:
            pairs = [("600548", "2026-09-23"), ("000001", "2026-09-23")]
            idx = pd.MultiIndex.from_tuples(pairs if order[0] == "symbol" else
                                           [(b, a) for a, b in pairs], names=order)
            namespace = {"Factor": FixtureFactor}
            exec(compile(render_notice_contract_probe(self.events), '<source contract>', 'exec'), namespace)
            actual = namespace['AnnouncedUnlockSourceContractProbe']().calculate(
                {'close': pd.Series([9.0, 10.0], index=idx)})
            pd.testing.assert_series_equal(actual, project_announced_quantity(idx, self.events))


if __name__ == '__main__':
    unittest.main()
