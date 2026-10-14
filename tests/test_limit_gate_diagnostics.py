"""Synthetic supplied panels only; no event detection or observed market labels."""
import unittest

import numpy as np
import pandas as pd

from panda_alpha.limit_gate_diagnostics import (
    LimitGateDiagnosticInputError, build_limit_gate_diagnostics,
)


DAYS = pd.bdate_range("2024-01-02", periods=34).strftime("%Y-%m-%d").tolist()
T = 25
ROLE = "total_return_factor"
MEASURES = ["prior_extension20", "downside_semideviation20", "median_amount20", "liquidity_ratio"]


def fixture():
    close = np.full(len(DAYS), 10.)
    for i in range(T - 22, T - 2):
        close[i] = close[i - 1] * (.99 if (i - (T - 22)) % 2 == 0 else 1.02)
    panel = {"close": pd.DataFrame({"A": close}, index=DAYS),
             "adjustment_factor": pd.DataFrame({"A": np.ones(len(DAYS))}, index=DAYS),
             "amount": pd.DataFrame({"A": 100. + np.arange(len(DAYS))}, index=DAYS)}
    entries = pd.DataFrame([{"date": DAYS[T], "symbol": "A", "planned_slot_cash": 20000.}])
    return panel, entries


def calc(panel=None, entries=None, calendar=None):
    source, fixed = fixture()
    return build_limit_gate_diagnostics(source if panel is None else panel,
                                         DAYS if calendar is None else calendar,
                                         fixed if entries is None else entries,
                                         adjustment_factor_role=ROLE)


class LimitGateDiagnosticTests(unittest.TestCase):
    def test_exact21_prices20_returns_and20_amount_reference_windows(self):
        row = calc().iloc[0]
        self.assertAlmostEqual((.99 * 1.02) ** 10 - 1., row.prior_extension20)
        self.assertAlmostEqual(np.sqrt((10 * .01 ** 2) / 20.) * np.sqrt(252.),
                               row.downside_semideviation20)
        self.assertEqual(112.5, row.median_amount20)
        self.assertAlmostEqual(20000. / 112.5, row.liquidity_ratio)
        self.assertEqual(DAYS[T - 23], row.price_start)
        self.assertEqual(DAYS[T - 3], row.price_end)
        self.assertEqual(DAYS[T - 22], row.amount_start)
        self.assertEqual(21, row.known_price_slots21)
        self.assertEqual(20, row.known_amount_slots20)
        self.assertFalse(row.fillable_size_certified)

    def test_two_boards_current_day_and_future_suffix_excluded(self):
        panel, entries = fixture()
        original = calc(panel, entries)
        for field in panel:
            panel[field].iloc[T - 2:] *= 1000.
        actual = calc(panel, entries)
        pd.testing.assert_frame_equal(original, actual)
        prefix = {field: source.iloc[:T + 1] for field, source in panel.items()}
        pd.testing.assert_frame_equal(actual, calc(prefix, entries, DAYS[:T + 1]))

    def test_missing_price_not_compressed_but_amount_independently_valid(self):
        panel, _ = fixture()
        panel["close"] = panel["close"].drop(DAYS[T - 10])
        row = calc(panel).iloc[0]
        self.assertTrue(np.isnan(row.prior_extension20))
        self.assertTrue(np.isnan(row.downside_semideviation20))
        self.assertEqual(20, row.known_price_slots21)
        self.assertEqual(112.5, row.median_amount20)
        self.assertTrue(np.isfinite(row.liquidity_ratio))

    def test_missing_factor_endpoint_or_interior_blocks_continuous_price_math(self):
        for index, value in [(T - 23, np.nan), (T - 10, 0.), (T - 3, np.inf)]:
            panel, _ = fixture()
            panel["adjustment_factor"].iloc[index, 0] = value
            row = calc(panel).iloc[0]
            self.assertTrue(np.isnan(row.prior_extension20))
            self.assertTrue(np.isnan(row.downside_semideviation20))
            self.assertTrue(np.isfinite(row.median_amount20))

    def test_amount_missing_zero_and_negative_never_zero_filled(self):
        for value in [np.nan, 0., -1.]:
            panel, _ = fixture()
            panel["amount"].iloc[T - 10, 0] = value
            row = calc(panel).iloc[0]
            self.assertTrue(np.isnan(row.median_amount20))
            self.assertTrue(np.isnan(row.liquidity_ratio))
            self.assertTrue(np.isfinite(row.prior_extension20))
            self.assertTrue(np.isfinite(row.downside_semideviation20))

    def test_unknown_factor_role_rejected_before_math(self):
        panel, entries = fixture()
        for role in [None, "qfq", "some_factor"]:
            with self.assertRaises(LimitGateDiagnosticInputError):
                build_limit_gate_diagnostics(panel, DAYS, entries, adjustment_factor_role=role)

    def test_entry_needs23_prior_reference_sessions_and_canonical_identity(self):
        panel, entries = fixture()
        for date in [DAYS[22], "2024-01-01", DAYS[T] + " 12:00:00"]:
            modified = entries.assign(date=date)
            with self.assertRaises(LimitGateDiagnosticInputError):
                calc(panel, modified)
        with self.assertRaises(LimitGateDiagnosticInputError):
            calc(panel, pd.concat([entries, entries]))
        with self.assertRaises(LimitGateDiagnosticInputError):
            calc(panel, entries.assign(symbol="UNKNOWN"))

    def test_scaling_same_source_units_does_not_change_dimensionless_values(self):
        panel, entries = fixture()
        original = calc(panel, entries)
        panel["close"] *= 100.
        panel["adjustment_factor"] *= 5.
        panel["amount"] *= 1000.
        entries.planned_slot_cash *= 1000.
        actual = calc(panel, entries)
        np.testing.assert_allclose(original[["prior_extension20", "downside_semideviation20", "liquidity_ratio"]],
                                   actual[["prior_extension20", "downside_semideviation20", "liquidity_ratio"]])
        self.assertEqual(original.median_amount20.iloc[0] * 1000., actual.median_amount20.iloc[0])

    def test_factors_use_supplied_asof_values_and_zero_downside_is_real_zero(self):
        panel, _ = fixture()
        panel["close"].iloc[:T - 2, 0] = 10.
        panel["adjustment_factor"].iloc[:T - 2, 0] = np.linspace(1., 2., T - 2)
        row = calc(panel).iloc[0]
        expected = panel["adjustment_factor"].iloc[T - 3, 0] / panel["adjustment_factor"].iloc[T - 23, 0] - 1.
        self.assertAlmostEqual(expected, row.prior_extension20)
        self.assertEqual(0., row.downside_semideviation20)

    def test_unknown_budget_keeps_amount_and_price_facts_separate(self):
        panel, entries = fixture()
        for budget in [np.nan, pd.NA, 0., -100.]:
            row = calc(panel, entries.assign(planned_slot_cash=budget)).iloc[0]
            self.assertTrue(np.isnan(row.liquidity_ratio))
            self.assertEqual("planned_slot_cash_unknown_or_nonpositive", row.liquidity_ratio_reason)
            self.assertTrue(np.isfinite(row.median_amount20))
            self.assertTrue(np.isfinite(row.prior_extension20))

    def test_empty_fixed_entries_stays_empty_and_calendar_not_reordered(self):
        panel, entries = fixture()
        self.assertTrue(calc(panel, entries.iloc[:0]).empty)
        with self.assertRaises(LimitGateDiagnosticInputError):
            calc(panel, entries, DAYS[::-1])

    def test_capacity_min20_uses_same_amount_dates_excludes_two_boards_and_future(self):
        panel, entries = fixture()
        panel["volume_shares"] = pd.DataFrame({"A": np.full(len(DAYS), 100000.)}, index=DAYS)
        panel["volume_shares"].iloc[T - 10, 0] = 20000.
        panel["volume_shares"].iloc[T - 23, 0] = 1.  # Before the20-volume window.
        panel["volume_shares"].iloc[T - 2:, 0] = 0.  # Boards/current/future are excluded.
        entries["recorded_order_quantity"] = 100.
        row = calc(panel, entries).iloc[0]
        self.assertEqual(20000., row.min_shares20)
        self.assertEqual(.005, row.reserved_quantity_over_min_shares20)
        self.assertTrue(row.liquidity_capacity_pass_1pct)
        self.assertEqual(20, row.known_volume_slots20)
        self.assertFalse(row.fillable_size_certified)

    def test_capacity_fixed_one_percent_boundary_no_threshold_or_epsilon_relaxation(self):
        panel, entries = fixture()
        panel["volume_shares"] = pd.DataFrame({"A": np.full(len(DAYS), 20000.)}, index=DAYS)
        for quantity, expected in [(100., True), (200., True), (200.000000001, False), (201., False)]:
            entries["recorded_order_quantity"] = quantity
            row = calc(panel, entries).iloc[0]
            self.assertEqual(expected, bool(row.liquidity_capacity_pass_1pct))
            self.assertAlmostEqual(quantity / 20000., row.reserved_quantity_over_min_shares20)
        self.assertEqual(112.5, row.median_amount20)  # Existing cash/amount facts unchanged.

    def test_capacity_missing_zero_volume_or_quantity_is_unknown_not_ignored_or_zero(self):
        for value in [np.nan, 0., -1.]:
            panel, entries = fixture()
            panel["volume_shares"] = pd.DataFrame({"A": np.full(len(DAYS), 20000.)}, index=DAYS)
            panel["volume_shares"].iloc[T - 10, 0] = value
            entries["recorded_order_quantity"] = 100.
            row = calc(panel, entries).iloc[0]
            self.assertTrue(np.isnan(row.min_shares20))
            self.assertTrue(np.isnan(row.reserved_quantity_over_min_shares20))
            self.assertTrue(pd.isna(row.liquidity_capacity_pass_1pct))
            self.assertEqual(19, row.known_volume_slots20)
            self.assertTrue(np.isfinite(row.prior_extension20))
        panel["volume_shares"].iloc[T - 10, 0] = 20000.
        for quantity in [pd.NA, 0.]:
            entries["recorded_order_quantity"] = quantity
            row = calc(panel, entries).iloc[0]
            self.assertEqual(20000., row.min_shares20)
            self.assertTrue(np.isnan(row.reserved_quantity_over_min_shares20))
            self.assertTrue(pd.isna(row.liquidity_capacity_pass_1pct))
        row = calc(panel, entries.drop(columns="recorded_order_quantity")).iloc[0]
        self.assertTrue(pd.isna(row.liquidity_capacity_pass_1pct))


if __name__ == "__main__":
    unittest.main()
