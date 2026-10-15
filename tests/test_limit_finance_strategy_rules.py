"""Clock and persistence checks; no historical data, account or PnL replay."""
import pytest

from panda_alpha.limit_finance_strategy_rules import ADAPTER_METADATA, evaluate_exit


def position(**flags):
    return {"entry_index": 100, "basis": 10., "flags": flags}


def evaluate(p, offset, *, mode="late_confirmed", previous=9.5, quote=9.5,
             flow=.1, turnover=2.):
    return evaluate_exit(mode, session_index=100 + offset, position=p,
                         previous_close=previous, reference_price=quote,
                         net_flow=flow, turnover_relative=turnover)


def test_control_strict_profit_tick_and_T_plus_1():
    p = position()
    assert evaluate(p, 0, mode="control", quote=12.) is None
    assert evaluate(p, 1, mode="control", quote=11.) is None
    result = evaluate(p, 1, mode="control", quote=11.01)
    assert result["reason"] == "take_profit"
    assert result["trigger_price"] == 11.01
    assert result["sticky"] is False
    assert "pending_exit" not in p["flags"]


def test_control_has_neither_finance_stop_nor_hard_timeout():
    p = position()
    assert evaluate(p, 1, mode="control", quote=8., flow=-.2, turnover=.5) is None
    assert evaluate(p, 600, mode="control", quote=8., flow=-.2, turnover=.5) is None
    assert p["flags"] == {}


def test_first_eight_percent_touch_with_outflow_is_sticky():
    p = position()
    result = evaluate(p, 1, quote=9.2, flow=-.1)
    assert result["reason"] == "conditional_stop_loss"
    assert result["trigger_price"] == 9.2
    later = evaluate(p, 2, quote=9.8, flow=.3)
    assert later["reason"] == result["reason"]
    assert later["sticky"] is True


def test_unknown_first_touch_is_waived_once_not_retested():
    p = position()
    assert evaluate(p, 1, quote=9.1, flow=None) is None
    assert p["flags"]["conditional_first_loss_reviewed"] is True
    assert evaluate(p, 2, quote=8.9, flow=-.1) is None


def test_stale_loss_uses_previous_close_not_current_quote():
    p = position(conditional_first_loss_reviewed=True)
    assert evaluate(p, 19, previous=9., flow=-.1) is None
    result = evaluate(p, 20, previous=9.9, quote=10.2, flow=-.1)
    assert result["reason"] == "conditional_stale_loss"
    other = position(conditional_first_loss_reviewed=True)
    assert evaluate(other, 20, previous=10.1, quote=9.8, flow=-.1) is None
    assert other["flags"]["conditional_time_reviewed"] is True
    assert evaluate(other, 21, previous=9.8, flow=-.1) is None


def test_continuous_clock_starts25_not20_and_not_each_minute():
    p = position(conditional_first_loss_reviewed=True, conditional_time_reviewed=True)
    assert evaluate(p, 20, flow=-.1) is None
    assert "continuous_last_index" not in p["flags"]
    assert evaluate(p, 24, flow=-.1) is None
    assert evaluate(p, 25, flow=-.1) is None
    assert evaluate(p, 25, flow=-.1) is None
    assert p["flags"]["continuous_outflow_streak"] == 1
    assert evaluate(p, 26, flow=-.1) is None


def test_cooling_and_outflow_confirm_at_first_scheduled_review():
    p = position(conditional_first_loss_reviewed=True, conditional_time_reviewed=True)
    result = evaluate(p, 25, flow=-.1, turnover=.9)
    assert result["reason"] == "continuous_confirmed_loss"


def test_two_nonoverlapping_outflow_reviews_confirm_without_turnover():
    p = position(conditional_first_loss_reviewed=True, conditional_time_reviewed=True)
    assert evaluate(p, 25, flow=-.1, turnover=None) is None
    result = evaluate(p, 30, flow=-.1, turnover=None)
    assert result["reason"] == "continuous_confirmed_loss"
    assert p["flags"]["continuous_outflow_streak"] == 2


def test_unknown_resets_continuous_streak_instead_of_bridging():
    p = position(conditional_first_loss_reviewed=True, conditional_time_reviewed=True)
    assert evaluate(p, 25, flow=-.1) is None
    assert evaluate(p, 30, flow=float("nan")) is None
    assert p["flags"]["continuous_outflow_streak"] == 0
    assert evaluate(p, 35, flow=-.1) is None
    assert p["flags"]["continuous_outflow_streak"] == 1


def test_nonnegative_flow_or_skipped_review_breaks_streak():
    p = position(conditional_first_loss_reviewed=True, conditional_time_reviewed=True)
    assert evaluate(p, 25, flow=-.1) is None
    assert evaluate(p, 30, flow=0.) is None
    assert evaluate(p, 35, flow=-.1) is None
    assert p["flags"]["continuous_outflow_streak"] == 1
    assert evaluate(p, 45, flow=-.1) is None
    assert p["flags"]["continuous_outflow_streak"] == 1


def test_no_quote_can_confirm_only_scheduled_exit_and_pending_waits():
    p = position()
    assert evaluate(p, 1, quote=None, flow=-.1) is None
    assert "conditional_first_loss_reviewed" not in p["flags"]
    result = evaluate(p, 20, quote=None, flow=-.1)
    assert result["reason"] == "conditional_stale_loss"
    assert result["reference_price"] is None
    assert evaluate(p, 21, quote=None, flow=.2)["sticky"] is True


def test_profitable_position_not_sold_by_reviews_and_basis_is_explicit():
    p = position(conditional_first_loss_reviewed=True, conditional_time_reviewed=True)
    assert evaluate(p, 25, previous=10.1, quote=10.2, flow=-.1, turnover=.5) is None
    assert evaluate(p, 30, previous=10.1, quote=10.2, flow=-.1, turnover=.5) is None
    assert "not_old_total_return_equivalent_units" in ADAPTER_METADATA["native_average_price_role"]
    with pytest.raises(ValueError, match="Bound position basis"):
        evaluate_exit("control", session_index=101, position={"entry_index":100, "basis":None},
                      previous_close=9., reference_price=9.)
