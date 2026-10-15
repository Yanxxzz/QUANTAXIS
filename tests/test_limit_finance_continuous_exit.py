import pytest

from panda_alpha.limit_finance_continuous_exit import ContinuousFinanceExit
from panda_alpha.limit_reversal import Policy, simulate
from test_limit_finance_exit_policies import fixture, sells


def direct(controller, position, i, *, previous=-.03, low=9.5, opening=9.8):
    return controller.evaluate(day=str(i), index=i, code="A", position=position,
        ohlc={"open": opening, "low": low}, basis=10., previous_return=previous, tick=.01)


def test_new_negative_flow_exits_after_a_previously_waived_review():
    c = ContinuousFinanceExit(lambda code, day: {"net_flow": .1 if day == "5" else -.1})
    p = {"entry_index": 0, "entry_date": "0"}
    assert direct(c, p, 5) is None
    assert direct(c, p, 6) is None
    assert direct(c, p, 10)["price"] == 9.8
    assert [r["date"] for r in c.reviews] == ["5", "10"]


def test_confirmed_outflow_can_wait_for_second_nonoverlapping_window():
    c = ContinuousFinanceExit(lambda *_: {"net_flow": -.1, "turnover_relative": 1.5}, confirmed=True)
    p = {"entry_index": 0, "entry_date": "0"}
    assert direct(c, p, 5) is None
    assert direct(c, p, 10)["reason"] == "continuous_confirmed_loss"
    assert c.reviews[-1]["outflow_streak"] == 2


@pytest.mark.parametrize("middle", [None, float("nan"), 0., .1])
def test_unknown_or_nonnegative_flow_breaks_persistence(middle):
    flows = {"5": -.1, "10": middle, "15": -.1}
    c = ContinuousFinanceExit(lambda _, d: {"net_flow": flows[d], "turnover_relative": 1.5}, confirmed=True)
    p = {"entry_index": 0, "entry_date": "0"}
    for i in (5, 10, 15):
        assert direct(c, p, i) is None
    assert c.reviews[-1]["outflow_streak"] == 1


def test_missing_turnover_does_not_block_confirmed_financing_persistence():
    c = ContinuousFinanceExit(lambda *_: {"net_flow": -.1, "turnover_relative": None}, confirmed=True)
    p = {"entry_index": 0, "entry_date": "0"}
    assert direct(c, p, 5) is None
    assert direct(c, p, 10) is not None


def test_cooling_withdrawal_exits_at_first_review_but_no_outflow_does_not():
    for flow, exits in [(-.1, True), (0., False), (.1, False), (None, False)]:
        c = ContinuousFinanceExit(lambda *_: {"net_flow": flow, "turnover_relative": .5}, confirmed=True)
        assert (direct(c, {"entry_index": 0, "entry_date": "0"}, 5) is not None) == exits


def test_positive_previous_close_waives_exit_and_no_review_on_entry():
    c = ContinuousFinanceExit(lambda *_: {"net_flow": -.1, "turnover_relative": .5}, confirmed=True)
    p = {"entry_index": 0, "entry_date": "0"}
    assert direct(c, p, 0, low=8.) is None
    assert direct(c, p, 5, previous=.01) is None
    assert len(c.reviews) == 1


def test_legacy_first_touch_and_twenty_day_review_remain():
    for i, low, reason in [(1, 9., "conditional_stop_loss"), (20, 9.5, "conditional_stale_loss")]:
        c = ContinuousFinanceExit(lambda *_: {"net_flow": -.1, "turnover_relative": 1.5}, confirmed=True)
        assert direct(c, {"entry_index": 0, "entry_date": "0"}, i, low=low)["reason"] == reason


def test_scheduled_review_precedes_intraday_touch_and_uses_previous_close():
    c = ContinuousFinanceExit(lambda *_: {"net_flow": -.1})
    p = {"entry_index": 0, "entry_date": "0"}
    assert direct(c, p, 5, low=8., opening=10.3) == {"reason": "continuous_finance_loss", "price": 10.3}


def test_blocked_continuous_exit_stays_pending_and_never_uses_future_flow():
    days, rows = fixture(11)
    rows[6].update(open=9.8, high=10., low=9.7, close=9.8)
    rows[7].update(open=9., high=9., low=9., close=9., down_limit=9.)
    rows[8].update(open=9., high=9., low=9., close=9., trade_status="suspended", volume=0.)
    rows[9].update(open=8.8, high=9.1, low=8.7, close=9.)
    calls = []
    def provider(code, day):
        calls.append(day)
        return {"net_flow": -.1 if day == days[7] else .2, "turnover_relative": .5}
    c = ContinuousFinanceExit(provider)
    result = simulate(rows, days, policy=Policy("continuous"), conditional_exit_controller=c,
                      preopen_signal_filter={(days[2], "A"): True})
    assert calls == [days[7]]
    assert sells(result)[0]["date"] == days[9] and sells(result)[0]["price"] == 8.8


def test_suspended_review_queues_exit_and_no_hard_holding_cap():
    days, rows = fixture(70)
    c = ContinuousFinanceExit(lambda *_: {"net_flow": .1, "turnover_relative": .5})
    result = simulate(rows, days, policy=Policy("continuous"), conditional_exit_controller=c,
                      preopen_signal_filter={(days[2], "A"): True})
    assert not sells(result)
    assert len(c.reviews) == 13
    days, rows = fixture(11)
    rows[6].update(open=9.8, high=10., low=9.7, close=9.8)
    rows[7].update(open=9.8, high=9.8, low=9.8, close=9.8, trade_status="suspended", volume=0.)
    c = ContinuousFinanceExit(lambda *_: {"net_flow": -.1})
    result = simulate(rows, days, policy=Policy("continuous"), conditional_exit_controller=c,
                      preopen_signal_filter={(days[2], "A"): True})
    assert sells(result)[0]["date"] == days[8]
