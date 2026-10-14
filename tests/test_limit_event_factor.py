"""Synthetic sparse-factor interface tests; no market sources or real labels."""
import unittest

import numpy as np
import pandas as pd

from panda_alpha.limit_event_features import FEATURES, FIELDS, LimitEventInputError
from panda_alpha.limit_event_factor import LimitEventFactor, native_adapter_notes


DAYS = pd.bdate_range("2024-01-02", periods=34).strftime("%Y-%m-%d").tolist()
ROLE = "total_return_factor"
T = 25


def fixture():
    rows = []
    for symbol in ["A", "B"]:
        stock = [{"date": day, "symbol": symbol, "raw_close": 10. + i * .1,
                  "raw_high": 10.5 + i * .1, "raw_low": 9.5 + i * .1,
                  "up_limit": 20., "down_limit": 8., "amount": 100.,
                  "adjustment_factor": 1., "eligible": True}
                 for i, day in enumerate(DAYS)]
        stock[T - 2].update(raw_close=14., raw_high=14., raw_low=14., up_limit=14.)
        stock[T - 1].update(raw_close=15., raw_high=15., raw_low=14., up_limit=15., amount=400.)
        stock[T].update(raw_close=13., raw_high=13.5, raw_low=12., down_limit=12.)
        rows += stock
    bars = pd.DataFrame(rows).set_index(["date", "symbol"])
    return {field: bars[field] for field in FIELDS}


def calculate(feature="prior_extension_20", factors=None, calendar=None, role=ROLE):
    factor = LimitEventFactor(feature)
    value = factor.calculate(fixture() if factors is None else factors,
                             DAYS if calendar is None else calendar, role)
    return value, factor.coverage_counts()


class LimitEventFactorTests(unittest.TestCase):
    def test_full_raw_close_identity_order_name_and_structural_nan(self):
        factors = fixture()
        factors = {field: values.iloc[::-1] for field, values in factors.items()}
        values, counts = calculate(factors=factors)
        pd.testing.assert_index_equal(factors["raw_close"].index, values.index)
        self.assertEqual("value", values.name)
        self.assertEqual(68, len(values))
        self.assertEqual(2, values.notna().sum())
        self.assertEqual(2, counts["known_touch_events"])
        self.assertEqual(66, counts["nan_factor_values"])
        self.assertTrue(values.loc[(DAYS[T - 1], "A")] != values.loc[(DAYS[T - 1], "A")])

    def test_all_three_features_are_negative_on_same_touch_domain(self):
        expected = {"prior_extension_20": -(.122e2 / .102e2 - 1.),
                    "locked_board_fraction": -.5, "board_amount_surge": -np.log(4.)}
        for feature in FEATURES:
            values, _ = calculate(feature)
            self.assertAlmostEqual(expected[feature], values.loc[(DAYS[T], "A")])
            self.assertEqual(2, values.notna().sum())

    def test_prefix_stable_and_later_input_cannot_change_past_values(self):
        factors = fixture()
        full, _ = calculate(factors=factors)
        prefix = {field: values.loc[values.index.get_level_values("date") <= DAYS[T]]
                  for field, values in factors.items()}
        actual, _ = calculate(factors=prefix, calendar=DAYS[:T + 1])
        pd.testing.assert_series_equal(full.reindex(actual.index), actual)
        changed = {field: values.copy() for field, values in factors.items()}
        later = changed["raw_close"].index.get_level_values("date") > DAYS[T]
        for field in ["raw_close", "raw_high", "raw_low", "amount", "adjustment_factor"]:
            changed[field].loc[later] *= .25
        after, _ = calculate(factors=changed)
        pd.testing.assert_series_equal(full.loc[~later], after.loc[~later])

    def test_current_touch_is_only_close_formed_event_requirement(self):
        factors = fixture()
        factors["raw_low"] = factors["raw_low"].copy()
        factors["raw_low"].loc[(DAYS[T], "A")] = 12.5
        values, counts = calculate(factors=factors)
        self.assertTrue(np.isnan(values.loc[(DAYS[T], "A")]))
        self.assertEqual(1, counts["known_touch_events"])
        self.assertEqual("after_close_t", native_adapter_notes["formation"])
        self.assertEqual("next_reference_session_t_plus_1", native_adapter_notes["earliest_use"])

    def test_missing_touch_and_feature_and_role_never_fill_zero(self):
        for field, date in [("raw_low", DAYS[T]), ("adjustment_factor", DAYS[T - 10])]:
            factors = fixture()
            factors[field] = factors[field].drop((date, "A"))
            values, _ = calculate(factors=factors)
            self.assertTrue(np.isnan(values.loc[(DAYS[T], "A")]))
            self.assertTrue(np.isfinite(values.loc[(DAYS[T], "B")]))
        values, counts = calculate(role=None)
        self.assertTrue(values.isna().all())
        self.assertEqual(2, counts["touch_events_feature_unknown"])

    def test_locked_fraction_ties_are_retained_without_jitter(self):
        values, counts = calculate("locked_board_fraction")
        self.assertEqual([-.5, -.5], values.dropna().tolist())
        self.assertEqual(2, counts["finite_factor_values"])
        factors = fixture()
        factors["raw_low"] = factors["raw_low"].copy()
        factors["raw_low"].loc[(DAYS[T - 2], "A")] = 13.
        value, _ = calculate("locked_board_fraction", factors)
        self.assertEqual(0., value.loc[(DAYS[T], "A")])  # A known event feature may be zero.
        self.assertTrue(np.isnan(value.loc[(DAYS[T + 1], "A")]))

    def test_datetime_and_string_field_keys_align_without_date_compression(self):
        factors = fixture()
        source = factors["amount"].copy()
        source.index = pd.MultiIndex.from_arrays(
            [pd.to_datetime(source.index.get_level_values("date")),
             source.index.get_level_values("symbol")], names=["date", "symbol"])
        factors["amount"] = source
        values, _ = calculate("board_amount_surge", factors)
        self.assertAlmostEqual(-np.log(4.), values.loc[(DAYS[T], "A")])

    def test_unknown_feature_duplicate_identity_and_extra_grid_rejected(self):
        with self.assertRaises(LimitEventInputError):
            LimitEventFactor("fitted_other_feature")
        factors = fixture()
        factors["raw_close"] = pd.concat([factors["raw_close"], factors["raw_close"].iloc[[0]]])
        with self.assertRaises(LimitEventInputError):
            calculate(factors=factors)
        factors = fixture()
        extra = pd.Series([100.], index=pd.MultiIndex.from_tuples(
            [(DAYS[0], "C")], names=["date", "symbol"]))
        factors["amount"] = pd.concat([factors["amount"], extra])
        with self.assertRaises(LimitEventInputError):
            calculate(factors=factors)

    def test_no_events_all_nan_and_counts_are_detached(self):
        factors = fixture()
        factors["eligible"] = factors["eligible"].copy() * False
        factor = LimitEventFactor("locked_board_fraction")
        values = factor.calculate(factors, DAYS, ROLE)
        self.assertTrue(values.isna().all())
        counts = factor.coverage_counts()
        counts["finite_factor_values"] = 100
        self.assertEqual(0, factor.coverage_counts()["finite_factor_values"])
        self.assertEqual(0, factor.coverage_counts()["preopen_two_board_candidates"])


if __name__ == "__main__":
    unittest.main()
