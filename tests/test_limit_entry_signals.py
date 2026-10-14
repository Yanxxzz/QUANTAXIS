"""Synthetic clock, missing-source, raw-limit and split-basis checks only."""
import unittest

import numpy as np
import pandas as pd

from panda_alpha.limit_entry_signals import build_limit_entry_signals, LimitEntrySignalError


DAYS = pd.bdate_range("2024-01-02", periods=35).strftime("%Y-%m-%d").tolist()
B, T = 20, 23
ROLE = "total_return_factor"


def panel(calendar=DAYS, symbols=("A", "B", "C")):
    defaults = {"close": 10., "high": 10.5, "low": 9.5, "open": 10.,
                "up_limit": 11., "down_limit": 9., "amount": 1e6,
                "volume_shares": 1e5, "adjustment_factor": 1.,
                "eligible": True, "trade_status": "1"}
    return {k: pd.DataFrame(v, index=calendar, columns=symbols) for k, v in defaults.items()}


def put(p, t, symbol="A", **changes):
    for k, v in changes.items():
        p[k].loc[p[k].index[t], symbol] = v


def fixture():
    p = panel()
    put(p, B, close=11., high=11., low=10., open=10.5)
    put(p, B + 1, close=10.8, open=10.7, high=10.9, low=10.2,
        up_limit=12.1, down_limit=9.9)
    put(p, T, close=9.7, high=10., low=9., open=9.5)
    return p


def run(p=None, calendar=DAYS, base=None, role=ROLE):
    return build_limit_entry_signals(fixture() if p is None else p, calendar,
                                    {(calendar[T], "A"): True} if base is None else base,
                                    adjustment_factor_role=role)


class LimitEntrySignalsTests(unittest.TestCase):
    def test_recovery_parent_and_target_are_next_reference_open(self):
        result = run()
        self.assertEqual({(DAYS[T + 1], "A"): True}, result["recovery_mask"])
        self.assertEqual(result["recovery_mask"], result["recovery_parent_mask"])
        self.assertNotIn((DAYS[T], "A"), result["recovery_mask"])

    def test_closed_lower_limit_remains_parent_but_not_recovery(self):
        p = fixture()
        put(p, T, close=9.)
        r = run(p)
        self.assertEqual({}, r["recovery_mask"])
        self.assertIn((DAYS[T + 1], "A"), r["recovery_parent_mask"])

    def test_raw_limit_validity_status_and_share_unknown_block_recovery(self):
        for change in [{"low": 8.9}, {"trade_status": "suspended"},
                       {"trade_status": None}, {"volume_shares": 0.},
                       {"volume_shares": np.nan}, {"up_limit": np.nan}]:
            with self.subTest(change=change):
                p = fixture()
                put(p, T, **change)
                self.assertEqual({}, run(p)["recovery_parent_mask"])

    def test_entry_session_price_and_volume_are_not_selection_inputs(self):
        expected = run()
        p = fixture()
        put(p, T + 1, close=np.nan, open=np.nan, high=np.nan, low=np.nan,
            volume_shares=0., trade_status=None)
        put(p, B + 2, close=np.nan, open=np.nan, high=np.nan, low=np.nan,
            volume_shares=0., trade_status=None)
        got = run(p)
        self.assertEqual(expected["recovery_mask"], got["recovery_mask"])
        self.assertEqual(expected["first_board_mask"], got["first_board_mask"])

    def test_entry_eligibility_never_changes_previous_close_candidates(self):
        expected = run()
        for val in [False, None, "True", 1]:
            p = fixture()
            p["eligible"] = p["eligible"].astype(object)
            put(p, T + 1, eligible=val)
            put(p, B + 2, eligible=val)
            result = run(p)
            for key in ["recovery_mask", "recovery_parent_mask", "first_board_mask", "first_board_parent_mask"]:
                self.assertEqual(expected[key], result[key])

    def test_every_entry_session_execution_role_is_deferred_for_parent_and_target(self):
        changes = [{"eligible": False}, {"eligible": None},
                   {"trade_status": "suspended"}, {"trade_status": None},
                   {"open": np.nan, "close": np.nan, "high": np.nan, "low": np.nan},
                   {"volume_shares": 0.}, {"volume_shares": np.nan}]
        expected = run()
        for entry, keys in [(T + 1, ["recovery_mask", "recovery_parent_mask"]),
                            (B + 2, ["first_board_mask", "first_board_parent_mask"])]:
            for change in changes:
                with self.subTest(entry=entry, change=change):
                    p = fixture()
                    p["eligible"] = p["eligible"].astype(object)
                    put(p, entry, **change)
                    result = run(p)
                    for key in keys:
                        self.assertEqual(expected[key], result[key])

    def test_formation_day_eligibility_remains_required(self):
        for formation in [T, B, B + 1]:
            p = fixture()
            put(p, formation, eligible=False)
            r = run(p)
            if formation == T:
                self.assertFalse(r["recovery_mask"])
                self.assertFalse(r["recovery_parent_mask"])
            else:
                self.assertFalse(r["first_board_mask"])
                self.assertFalse(r["first_board_parent_mask"])

    def test_holiday_gap_uses_supplied_next_day_not_business_day(self):
        days = ["2024-09-27", "2024-09-30", "2024-10-08"]
        p = panel(days)
        put(p, 1, close=9.7, open=9.5, high=10., low=9.)
        r = run(p, days, {(days[1], "A"): True})
        self.assertEqual({("2024-10-08", "A"): True}, r["recovery_mask"])

    def test_market_uses_only_previous_day_and_counts_partial_cohort(self):
        p = fixture()
        put(p, T - 1, "A", close=9., low=9., open=9.5)
        put(p, T - 1, "B", close=9., low=9., open=9.5)
        put(p, T - 1, "C", close=np.nan, trade_status=None)
        r = run(p)
        row = r["market_coverage"][T]
        self.assertEqual(DAYS[T - 1], row["source_date"])
        self.assertEqual(3, row["source_identity_count"])
        self.assertEqual(2, row["observed_cohort_count"])
        self.assertEqual(2, row["known_lower_closes"])
        self.assertEqual(0, row["known_upper_closes"])
        self.assertEqual(1, row["unknown_status_count"])
        self.assertEqual(1, row["missing_or_invalid_raw_price_limit_count"])
        self.assertEqual({}, r["market_mask"])
        # Changing today's/future breadth cannot affect today's preopen decision.
        put(p, T, "B", close=11., high=11.)
        self.assertEqual(r["market_mask"], run(p)["market_mask"])

    def test_market_tie_permits_and_missing_amount_is_not_gate(self):
        p = fixture()
        put(p, T - 1, "A", close=9., low=9., open=9.5)
        put(p, T - 1, "B", close=11., high=11.)
        p["amount"].iloc[T - 1, :] = np.nan
        r = run(p)
        self.assertIn((DAYS[T], "A"), r["market_mask"])
        row = r["market_coverage"][T]
        self.assertEqual(3, row["observed_cohort_count"])
        self.assertEqual(3, row["missing_or_nonpositive_amount_count"])

    def test_market_fixed_base_never_reads_entry_session_execution_roles(self):
        p = fixture()
        expected = run(p)["market_mask"]
        self.assertIn((DAYS[T], "A"), expected)
        p["eligible"] = p["eligible"].astype(object)
        put(p, T, eligible=None, trade_status=None, open=np.nan, close=np.nan,
            high=np.nan, low=np.nan, up_limit=np.nan, down_limit=np.nan,
            volume_shares=0., amount=np.nan)
        self.assertEqual(expected, run(p)["market_mask"])

    def test_no_valid_market_cohort_is_unknown_not_zero_limit_counts(self):
        p = fixture()
        p["trade_status"].iloc[T - 1, :] = None
        r = run(p)
        row = r["market_coverage"][T]
        self.assertIsNone(row["known_upper_closes"])
        self.assertIsNone(row["known_lower_closes"])
        self.assertFalse(r["market_allowed_by_day"][DAYS[T]])
        self.assertEqual({}, r["market_mask"])

    def test_known_nontrading_is_not_traded_upper_board(self):
        p = fixture()
        put(p, T - 1, "A", close=11., high=11., trade_status="0")
        r = run(p)
        row = r["market_coverage"][T]
        self.assertEqual(1, row["known_nontrading_count"])
        self.assertEqual(2, row["observed_cohort_count"])
        self.assertEqual(0, row["known_upper_closes"])

    def test_market_records_unknown_eligible_across_entire_identity_grid(self):
        p = fixture()
        p["eligible"] = p["eligible"].astype(object)
        put(p, T - 1, "C", eligible=None, trade_status=None, close=np.nan)
        row = run(p)["market_coverage"][T]
        self.assertEqual(1, row["unknown_eligible_count"])
        self.assertEqual(1, row["unknown_status_count"])
        self.assertEqual(1, row["missing_or_invalid_raw_price_limit_count"])

    def test_first_board_parent_clock_and_target_do_not_use_old_base(self):
        r = run(base={})
        key = (DAYS[B + 2], "A")
        self.assertIn((DAYS[B], "A"), r["first_board_formation_mask"])
        self.assertEqual({key: True}, r["first_board_parent_mask"])
        self.assertEqual({key: True}, r["first_board_mask"])
        self.assertEqual({}, r["recovery_mask"])

    def test_missing_twenty_day_history_is_not_filled_as_no_upper_board(self):
        for change in [{"close": np.nan}, {"up_limit": np.nan},
                       {"trade_status": None}, {"trade_status": "suspended"}]:
            p = fixture()
            put(p, B - 5, **change)
            r = run(p)
            self.assertNotIn((DAYS[B], "A"), r["first_board_formation_mask"])
            self.assertEqual({}, r["first_board_mask"])

    def test_any_prior_upper_close_in_exact_twenty_days_prevents_first_board(self):
        for prior in [B - 20, B - 1]:
            p = fixture()
            put(p, prior, close=11., high=11.)
            self.assertNotIn((DAYS[B], "A"), run(p)["first_board_formation_mask"])

    def test_continued_upper_board_on_pullback_day_is_not_parent(self):
        p = fixture()
        put(p, B + 1, close=12.1, high=12.1)
        self.assertEqual({}, run(p)["first_board_parent_mask"])

    def test_pullback_filters_preserve_common_parent(self):
        for change in [{"low": 9.9}, {"close": 10., "low": 9.95}]:
            p = fixture()
            put(p, B + 1, **change)
            r = run(p)
            self.assertEqual({(DAYS[B + 2], "A"): True}, r["first_board_parent_mask"])
            self.assertEqual({}, r["first_board_mask"])

    def test_split_strength_uses_economic_close_but_raw_limits(self):
        p = fixture()
        put(p, B + 1, close=5.4, open=5.3, high=5.45, low=5.1,
            up_limit=6.05, down_limit=4.95, adjustment_factor=2.)
        r = run(p)
        self.assertIn((DAYS[B + 2], "A"), r["first_board_mask"])
        put(p, B + 1, close=5., open=5., high=5.2, low=4.96)
        self.assertEqual({}, run(p)["first_board_mask"])

    def test_unknown_factor_role_or_pair_does_not_forge_strength(self):
        p = fixture()
        r = run(p, role=None)
        self.assertFalse(r["first_board_parent_mask"])
        self.assertFalse(r["first_board_mask"])
        self.assertEqual("ECONOMIC_COMPARISON_SOURCE_UNKNOWN", r["first_board_parent_rows"][0]["reason"])
        for date in [B - 1, B + 1]:
            p = fixture()
            put(p, date, adjustment_factor=np.nan)
            r = run(p)
            self.assertFalse(r["first_board_mask"])
            self.assertFalse(r["first_board_parent_mask"])
            self.assertEqual(1, r["coverage"]["first_board_parent_economic_pair_unknown"])
        with self.assertRaises(LimitEntrySignalError):
            run(p, role="qfq")

    def test_first_board_prior_twenty_current_and_pullback_need_positive_shares(self):
        for date in [B - 20, B - 1, B, B + 1]:
            for shares in [0., np.nan, -1.]:
                with self.subTest(date=date, shares=shares):
                    p = fixture()
                    put(p, date, volume_shares=shares)
                    r = run(p)
                    self.assertFalse(r["first_board_mask"])
                    self.assertFalse(r["first_board_parent_mask"])
                    if date == B + 1:
                        self.assertEqual("PULLBACK_SOURCE_OR_FORMATION_ELIGIBILITY_UNKNOWN_OR_INELIGIBLE",
                                         r["first_board_parent_rows"][0]["reason"])

    def test_future_suffix_cannot_change_earlier_signals(self):
        p = fixture()
        before = run(p)
        for name in ["open", "high", "low", "close", "up_limit", "down_limit", "volume_shares"]:
            p[name].iloc[T + 2:, :] = np.nan
        p["eligible"].iloc[T + 2:, :] = False
        p["trade_status"].iloc[T + 2:, :] = None
        after = run(p)
        for name in ["recovery_mask", "recovery_parent_mask", "market_mask", "first_board_mask", "first_board_parent_mask"]:
            a = {k: v for k, v in before[name].items() if k[0] <= DAYS[T + 1]}
            b = {k: v for k, v in after[name].items() if k[0] <= DAYS[T + 1]}
            self.assertEqual(a, b)

    def test_inconsistent_grid_and_unknown_mask_identity_reject(self):
        p = fixture()
        p["close"] = p["close"].drop(DAYS[B - 2])
        with self.assertRaises(LimitEntrySignalError):
            run(p)
        with self.assertRaises(LimitEntrySignalError):
            run(base={(DAYS[T], "OUTSIDE"): True})
        p = fixture()
        del p["trade_status"]
        with self.assertRaises(LimitEntrySignalError):
            run(p)


if __name__ == "__main__":
    unittest.main()
