import numpy as np
import pandas as pd
import pytest

from panda_alpha.limit_episode_history import build_episode_history, economic_close


def fixture_panel(n=12, symbols=("000001",)):
    calendar = pd.bdate_range("2026-01-05", periods=n).strftime("%Y-%m-%d").tolist()
    def frame(value): return pd.DataFrame(value, index=calendar, columns=symbols)
    panel = {
        "raw_close": frame(100.0), "upper_close": frame(False),
        "trading": frame(True), "is_st": frame(False), "legal_price": frame(True),
        "adjustment_factor": frame(1.0), "factor_asof_date": frame("2020-01-01"),
        "factor_source_id": frame("same-source"),
    }
    panel["upper_close"].iloc[1:3, 0] = True
    panel["raw_close"].iloc[1:3, 0] = [110.0, 121.0]
    return panel, calendar


def inspect(panel, calendar, end=None, mode="count"):
    return build_episode_history(
        panel, calendar, formation_end=end or calendar[-1],
        factor_source_id="same-source", factor_role="total_return_factor", mode=mode,
    )


def test_maturity_at_exact_fifth_independent_close_and_no_self_history():
    panel, cal = fixture_panel()
    panel["raw_close"].iloc[7, 0] = 99.0
    result = inspect(panel, cal, mode="values")
    d = result["daily"]
    assert d["detected"].iloc[2, 0] == 1
    assert d["pending"].iloc[2, 0] == 1
    assert d["count"].iloc[6, 0] == 0
    assert np.isnan(d["risk"].iloc[6, 0])
    assert d["count"].iloc[7, 0] == 1
    assert d["risk"].iloc[7, 0] == 1
    assert result["episodes"][0]["maturity_date"] == cal[7]


def test_count_mode_contains_no_giveback_failure_or_ratio():
    panel, cal = fixture_panel()
    panel["raw_close"].iloc[3:8, 0] = 1.0
    result = inspect(panel, cal)
    assert result["daily"]["count"].iloc[-1, 0] == 1
    assert set(result["daily"]) == {"detected", "count", "unknown", "pending"}
    assert "giveback" not in result["episodes"][0]
    assert not result["receipt"]["failure_comparison_computed"]
    assert not result["receipt"]["risk_ratio_computed"]


def test_no_old_episode_means_no_risk_or_frequency_zero():
    panel, cal = fixture_panel()
    panel["upper_close"].iloc[:, :] = False
    result = inspect(panel, cal, mode="values")
    assert result["episodes"] == []
    assert result["daily"]["risk"].isna().all().all()
    assert result["daily"]["episode_frequency_count"].isna().all().all()


@pytest.mark.parametrize("field,value", [
    ("upper_close", None), ("trading", False), ("is_st", True),
    ("legal_price", False), ("raw_close", np.nan),
])
def test_unknown_or_nonordinary_anchor_is_not_known_nonboard(field, value):
    panel, cal = fixture_panel()
    panel[field] = panel[field].astype(object)
    panel[field].iloc[0, 0] = value
    assert inspect(panel, cal)["episodes"] == []


def test_entry_eligible_false_is_never_used_as_nonboard():
    panel, cal = fixture_panel()
    panel["upper_close"] = panel["upper_close"].astype(object)
    panel["upper_close"].iloc[0, 0] = None
    panel["eligible"] = pd.DataFrame(False, index=cal, columns=["000001"])
    result = inspect(panel, cal)
    assert not result["episodes"]
    assert result["receipt"]["possible_pair_start_unknown_count"] == 1


@pytest.mark.parametrize("field,value,reason", [
    ("trading", False, "known_nontrading_session"),
    ("trading", None, "trading_unknown"),
    ("raw_close", np.nan, "raw_close_missing_or_invalid"),
    ("adjustment_factor", True, "factor_missing_or_invalid"),
    ("factor_asof_date", None, "factor_effective_date_unknown"),
    ("factor_source_id", "another-source", "factor_source_unknown_or_mismatched"),
])
def test_halt_missing_or_bad_factor_is_not_a_success(field, value, reason):
    panel, cal = fixture_panel()
    panel[field] = panel[field].astype(object)
    panel[field].iloc[4, 0] = value
    result = inspect(panel, cal, mode="values")
    assert result["daily"]["unknown"].iloc[7, 0] == 1
    assert result["daily"]["count"].iloc[7, 0] == 0
    assert np.isnan(result["daily"]["risk"].iloc[7, 0])
    assert result["episodes"][0]["giveback"] is None
    assert result["receipt"]["mature_support_unknown_role_counts"][reason] == 1


def test_future_effective_factor_cannot_support_an_earlier_close():
    panel, cal = fixture_panel()
    panel["factor_asof_date"].iloc[4, 0] = cal[5]
    result = inspect(panel, cal, mode="values")
    assert result["daily"]["unknown"].iloc[7, 0] == 1
    assert result["receipt"]["mature_support_unknown_role_counts"]["factor_effective_after_session"] == 1


def test_split_uses_raw_close_times_factor_and_strict_anchor_boundary():
    panel, cal = fixture_panel()
    panel["raw_close"].iloc[3:8, 0] = 50.0
    panel["adjustment_factor"].iloc[3:8, 0] = 2.0
    panel["factor_asof_date"].iloc[3:8, 0] = cal[3]
    result = inspect(panel, cal, mode="values")
    assert result["episodes"][0]["giveback"] is False  # Equals, not below100.
    panel["raw_close"].iloc[7, 0] = 49.0
    assert inspect(panel, cal, mode="values")["episodes"][0]["giveback"] is True


def test_decimal_equal_products_are_not_false_giveback_from_float_rounding():
    panel, cal = fixture_panel()
    panel["raw_close"].iloc[0, 0] = 1.10
    panel["adjustment_factor"].iloc[0, 0] = 1.10
    panel["raw_close"].iloc[3:8, 0] = 1.00
    panel["adjustment_factor"].iloc[3:8, 0] = 1.21
    panel["factor_asof_date"].iloc[3:8, 0] = cal[3]
    assert 1.10 * 1.10 > 1.00 * 1.21  # Reproduces the incorrect binary comparison.
    result = inspect(panel, cal, mode="values")
    assert result["episodes"][0]["giveback"] is False
    assert result["daily"]["risk"].iloc[7, 0] == 0.0


def test_strictly_lower_decimal_product_is_still_giveback_without_epsilon():
    panel, cal = fixture_panel()
    panel["raw_close"].iloc[0, 0] = 1.10
    panel["adjustment_factor"].iloc[0, 0] = 1.10
    panel["raw_close"].iloc[3:8, 0] = 1.00
    panel["adjustment_factor"].iloc[3:8, 0] = 1.21
    panel["factor_asof_date"].iloc[3:8, 0] = cal[3]
    panel["adjustment_factor"].iloc[7, 0] = 1.20999
    panel["factor_asof_date"].iloc[7, 0] = cal[7]
    result = inspect(panel, cal, mode="values")
    assert result["episodes"][0]["giveback"] is True
    assert result["daily"]["risk"].iloc[7, 0] == 1.0


def test_known_legal_later_ST_close_remains_a_historical_price_observation():
    panel, cal = fixture_panel()
    panel["is_st"].iloc[4, 0] = True
    panel["raw_close"].iloc[4, 0] = 90.0
    result = inspect(panel, cal, mode="values")
    assert result["daily"]["count"].iloc[7, 0] == 1
    assert result["episodes"][0]["giveback"] is True


def test_three_or_more_upper_closes_count_first_pair_only():
    panel, cal = fixture_panel()
    panel["upper_close"].iloc[3:5, 0] = True
    result = inspect(panel, cal)
    assert len(result["episodes"]) == 1
    assert result["receipt"]["known_continued_upper_pairs_not_new_episodes"] == 2


def test_two_mature_episode_post_windows_overlap_and_are_marked_dependent():
    panel, cal = fixture_panel()
    panel["upper_close"].iloc[4:6, 0] = True
    result = inspect(panel, cal)
    assert [x["second_upper_date"] for x in result["episodes"]] == [cal[2], cal[5]]
    assert result["daily"]["count"].iloc[10, 0] == 2
    assert result["receipt"]["overlapping_mature_post_window_pairs"] == 1
    assert result["episodes"][1]["overlapping_prior_mature_episodes"] == 1


def test_mature_unknown_episode_blocks_partial_history_score_on_shared_domain():
    panel, cal = fixture_panel(16)
    panel["upper_close"].iloc[9:11, 0] = True
    panel["trading"].iloc[4, 0] = False  # First episode is unknown; later one complete.
    result = inspect(panel, cal, mode="values")
    assert result["daily"]["count"].iloc[-1, 0] == 1
    assert result["daily"]["unknown"].iloc[-1, 0] == 1
    assert np.isnan(result["daily"]["risk"].iloc[-1, 0])
    assert np.isnan(result["daily"]["episode_frequency_count"].iloc[-1, 0])


def test_unmatured_new_episode_does_not_erase_older_complete_history():
    panel, cal = fixture_panel()
    panel["upper_close"].iloc[9:11, 0] = True
    result = inspect(panel, cal, mode="values")
    assert result["daily"]["count"].iloc[-1, 0] == 1
    assert result["daily"]["pending"].iloc[-1, 0] == 1
    assert result["daily"]["risk"].iloc[-1, 0] == 0.0
    assert result["daily"]["episode_frequency_count"].iloc[-1, 0] == 1.0


def test_holiday_calendar_is_not_calendar_day_or_received_row_clock():
    panel, _ = fixture_panel(8)
    cal = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08",
           "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
    for frame in panel.values(): frame.index = cal
    assert inspect(panel, cal, cal[6])["daily"]["pending"].iloc[-1, 0] == 1
    assert inspect(panel, cal)["episodes"][0]["maturity_date"] == "2026-10-14"


def test_future_suffix_cannot_change_any_prefix_output():
    panel, cal = fixture_panel()
    before = inspect(panel, cal, cal[7], mode="values")
    for name in panel:
        panel[name] = panel[name].astype(object)
        panel[name].iloc[8:, 0] = "invalid future value"
    after = inspect(panel, cal, cal[7], mode="values")
    assert before["episodes"] == after["episodes"]
    assert before["receipt"] == after["receipt"]
    for name in before["daily"]:
        pd.testing.assert_frame_equal(before["daily"][name], after["daily"][name])


def test_full_asof_series_earlier_values_match_truncated_input():
    panel, cal = fixture_panel()
    panel["upper_close"].iloc[8:10, 0] = True
    long = inspect(panel, cal, mode="values")
    short = inspect(panel, cal, cal[7], mode="values")
    for name in short["daily"]:
        pd.testing.assert_frame_equal(short["daily"][name], long["daily"][name].loc[:cal[7]])


def test_pair_at_history_start_cannot_invent_earlier_nonboard_anchor():
    panel, cal = fixture_panel()
    panel["upper_close"].iloc[0, 0] = True
    result = inspect(panel, cal)
    assert result["episodes"] == []
    assert result["receipt"]["possible_pair_start_unknown_count"] == 1


def test_missing_independent_session_is_retained_as_unknown_not_dropped():
    panel, cal = fixture_panel()
    for name in panel: panel[name] = panel[name].drop(cal[4])
    result = inspect(panel, cal, mode="values")
    assert len(result["daily"]["risk"]) == len(cal)
    assert result["daily"]["unknown"].iloc[7, 0] == 1


def test_other_symbols_and_column_reordering_do_not_mix_histories():
    panel, cal = fixture_panel(symbols=("000001", "000002"))
    panel["raw_close"].iloc[7, 0] = 99.0
    panel["upper_close"].iloc[1:3, 1] = True
    panel["raw_close"].iloc[7, 1] = 101.0
    panel["trading"] = panel["trading"][["000002", "000001"]]
    result = inspect(panel, cal, mode="values")
    assert result["daily"]["risk"].loc[cal[7]].to_dict() == {"000001": 1.0, "000002": 0.0}


def test_contract_rejects_implicit_role_mode_or_missing_fields():
    panel, cal = fixture_panel()
    with pytest.raises(ValueError, match="Explicit"):
        build_episode_history(panel, cal, formation_end=cal[-1], factor_source_id="same-source", factor_role="qfq")
    with pytest.raises(ValueError, match="mode"):
        inspect(panel, cal, mode="scan")
    del panel["legal_price"]
    with pytest.raises(ValueError, match="Missing explicit"):
        inspect(panel, cal)


def test_economic_close_arithmetic_is_pure_and_no_native_claim():
    args = dict(session="2026-01-05", factor_asof_date="2025-01-05",
                factor_source_id="same-source", expected_factor_source_id="same-source",
                factor_role="total_return_factor")
    assert economic_close(50.0, 2.0, **args) == (100.0, None)
    assert economic_close(True, 2.0, **args)[1] == "raw_close_missing_or_invalid"
    assert economic_close(50.0, 0.0, **args)[1] == "factor_missing_or_invalid"
    panel, cal = fixture_panel()
    assert not inspect(panel, cal)["receipt"]["native_price_limit_input_or_production_update_certified"]


@pytest.mark.parametrize("field", ["factor_source_id", "expected_factor_source_id", "factor_role"])
def test_scalar_nullable_source_or_role_is_unknown_without_bool_coercion(field):
    args = dict(session="2026-01-05", factor_asof_date="2025-01-05",
                factor_source_id="same-source", expected_factor_source_id="same-source",
                factor_role="total_return_factor")
    args[field] = pd.NA
    value, reason = economic_close(50.0, 2.0, **args)
    assert value is None
    assert reason in {"factor_source_unknown_or_mismatched", "factor_role_unknown"}
