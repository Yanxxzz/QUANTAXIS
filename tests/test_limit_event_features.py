"""Synthetic independent reference sessions only, with no market/account calls."""
import unittest

import numpy as np
import pandas as pd

from panda_alpha.limit_event_features import (
    FEATURES, LimitEventInputError, build_event_features, future_labels,
)


DAYS = pd.bdate_range("2024-01-02", periods=33).strftime("%Y-%m-%d").tolist()
ROLE = "total_return_factor"
T = 25


def fixture():
    rows = [{"date": day, "symbol": "A", "raw_close": 10. + i * .1,
             "raw_high": 10.5 + i * .1, "raw_low": 9.5 + i * .1,
             "up_limit": 20., "down_limit": 8., "amount": 100.,
             "adjustment_factor": 1., "eligible": True}
            for i, day in enumerate(DAYS)]
    rows[T - 2].update(raw_close=14., raw_high=14., raw_low=14., up_limit=14.)
    rows[T - 1].update(raw_close=15., raw_high=15., raw_low=14., up_limit=15., amount=400.)
    rows[T].update(raw_close=13., raw_high=13.5, raw_low=12., down_limit=12.)
    return pd.DataFrame(rows)


def features(bars=None, **kwargs):
    return build_event_features(fixture() if bars is None else bars, DAYS,
                                adjustment_factor_role=ROLE, **kwargs)


class LimitEventFeatureTests(unittest.TestCase):
    def test_exact_excluded_board_history_and_twenty_day_denominator(self):
        event = features().iloc[0]
        self.assertEqual(DAYS[T], event.date)
        self.assertAlmostEqual((10. + (T - 3) * .1) / (10. + (T - 23) * .1) - 1.,
                               event.prior_extension_20)
        self.assertEqual(.5, event.locked_board_fraction)
        self.assertAlmostEqual(np.log(4.), event.board_amount_surge)
        self.assertTrue(event.touch_event)
        self.assertFalse(event.fill_certified)

    def test_current_and_future_ohlc_cannot_change_preopen_features(self):
        original = features()
        bars = fixture()
        bars.loc[T:, ["raw_close", "raw_high", "raw_low", "amount"]] *= .25
        actual = features(bars)
        pd.testing.assert_frame_equal(original[["date", "symbol", *FEATURES]],
                                      actual[["date", "symbol", *FEATURES]])

    def test_missing_reference_bar_not_compressed_or_backfilled(self):
        bars = fixture().drop(T - 10)
        event = features(bars).iloc[0]
        self.assertTrue(np.isnan(event.prior_extension_20))
        self.assertTrue(np.isnan(event.board_amount_surge))
        self.assertEqual(.5, event.locked_board_fraction)
        self.assertEqual(0, len(features(fixture().drop(T - 1))))

    def test_missing_endpoint_and_factor_and_zero_amount_are_unknown(self):
        for column, index, value, output in [
            ("raw_close", T - 23, np.nan, "prior_extension_20"),
            ("adjustment_factor", T - 10, np.nan, "prior_extension_20"),
            ("amount", T - 3, 0., "board_amount_surge"),
            ("amount", T - 1, np.nan, "board_amount_surge"),
        ]:
            bars = fixture()
            bars.loc[index, column] = value
            self.assertTrue(np.isnan(features(bars).iloc[0][output]))

    def test_unit_scaling_and_constant_factor_anchor_do_not_change_ratios(self):
        bars = fixture()
        bars[["raw_close", "raw_high", "raw_low", "up_limit", "down_limit"]] *= 100.
        bars.amount *= 1000.
        bars.adjustment_factor *= 5.
        np.testing.assert_allclose(features()[list(FEATURES)].to_numpy(dtype=float),
                                   features(bars)[list(FEATURES)].to_numpy(dtype=float))

    def test_unknown_factor_role_no_default_assumption(self):
        event = build_event_features(fixture(), DAYS).iloc[0]
        self.assertTrue(np.isnan(event.prior_extension_20))
        self.assertEqual("adjustment_factor_role_unknown", event.prior_extension_reason)
        self.assertAlmostEqual(np.log(4.), event.board_amount_surge)
        labels = future_labels(fixture(), DAYS, features())
        self.assertFalse(labels.label_complete.iloc[0])

    def test_eligibility_needed_on_all_three_sessions_and_strings_unknown(self):
        for index, value in [(T - 2, False), (T - 1, np.nan), (T, "True")]:
            bars = fixture()
            bars["eligible"] = bars["eligible"].astype(object)
            bars.loc[index, "eligible"] = value
            self.assertTrue(features(bars).empty)

    def test_touch_is_separate_diagnostic_not_candidate_filter(self):
        bars = fixture()
        bars.loc[T, "raw_low"] = 12.5
        self.assertEqual(1, len(features(bars)))
        self.assertFalse(features(bars).touch_event.iloc[0])
        bars.loc[T, "raw_low"] = np.nan
        self.assertTrue(pd.isna(features(bars).touch_event.iloc[0]))

    def test_wide_input_equals_long(self):
        bars = fixture()
        wide = bars.set_index(["date", "symbol"]).unstack("symbol")
        pd.testing.assert_frame_equal(features(bars), features(wide))

    def test_duplicate_identity_and_bad_calendar_rejected(self):
        with self.assertRaises(LimitEventInputError):
            features(pd.concat([fixture(), fixture().iloc[[0]]]))
        with self.assertRaises(LimitEventInputError):
            build_event_features(fixture(), DAYS[::-1])

    def test_future_label_has_exact_reference_horizon_and_excludes_bid_day_low(self):
        bars = fixture()
        bars.loc[T, "raw_low"] = .01
        labels = future_labels(bars, DAYS, features(bars), adjustment_factor_role=ROLE)
        event = labels.iloc[0]
        self.assertTrue(event.label_complete)
        self.assertAlmostEqual(bars.raw_close.iloc[T + 5] / 12. - 1., event.future_return_5)
        self.assertAlmostEqual(bars.raw_low.iloc[T + 1:T + 6].min() / 12. - 1., event.future_mae_5)

    def test_future_missing_interior_quote_not_compressed_filled_or_shortened(self):
        for bars in [fixture().drop(T + 2), fixture().assign(
                raw_low=lambda frame: frame.raw_low.mask(frame.index == T + 2))]:
            event = future_labels(bars, DAYS, features(), adjustment_factor_role=ROLE).iloc[0]
            self.assertFalse(event.label_complete)
            self.assertTrue(np.isnan(event.future_return_5))
            self.assertTrue(np.isnan(event.future_mae_5))
        short = DAYS[:T + 5]
        bars = fixture().iloc[:T + 5]
        event = future_labels(bars, short, features(bars), adjustment_factor_role=ROLE).iloc[0]
        self.assertFalse(event.label_complete)
        self.assertEqual("future_reference_window_incomplete", event.label_reason)

    def test_factor_changes_apply_to_both_cost_and_future_prices(self):
        bars = fixture()
        bars.loc[T, "adjustment_factor"] = 2.
        bars.loc[T + 1:, "adjustment_factor"] = 4.
        event = future_labels(bars, DAYS, features(bars), adjustment_factor_role=ROLE).iloc[0]
        self.assertAlmostEqual(bars.raw_close.iloc[T + 5] * 4. / (12. * 2.) - 1.,
                               event.future_return_5)
        before = features(bars)[list(FEATURES)]
        bars.loc[T + 1:, ["raw_close", "raw_low", "adjustment_factor"]] *= 2.
        pd.testing.assert_frame_equal(before, features(bars)[list(FEATURES)])

    def test_no_candidates_gives_empty_label_table_without_synthetic_events(self):
        bars = fixture()
        bars.loc[T - 1, "raw_close"] = 14.9
        events = features(bars)
        self.assertTrue(events.empty)
        labels = future_labels(bars, DAYS, events, adjustment_factor_role=ROLE)
        self.assertTrue(labels.empty)
        self.assertIn("future_return_5", labels)


if __name__ == "__main__":
    unittest.main()
