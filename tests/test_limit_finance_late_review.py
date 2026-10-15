from panda_alpha.limit_finance_late_review import LateContinuousFinanceExit
from test_limit_finance_continuous_exit import direct


def test_no_new_reviews_before_twenty_five_and_twenty_day_baseline_retained():
    calls=[]
    def source(code, day):
        calls.append(day)
        return {"net_flow": -.1, "turnover_relative": 1.5}
    c=LateContinuousFinanceExit(source, confirmed=True)
    p={"entry_index": 0, "entry_date": "0"}
    for i in [5, 10, 15]:
        assert direct(c,p,i) is None
    assert calls == []
    assert direct(c,p,20)["reason"] == "conditional_stale_loss"
    assert calls == ["20"]


def test_late_finance_exit_begins_at_twenty_five():
    c=LateContinuousFinanceExit(lambda _,d: {"net_flow": .1 if d == "20" else -.1})
    p={"entry_index":0,"entry_date":"0"}
    assert direct(c,p,20) is None
    assert direct(c,p,24) is None
    assert direct(c,p,25)["reason"] == "continuous_finance_loss"


def test_late_confirmation_does_not_count_old_twenty_day_as_new_streak():
    c=LateContinuousFinanceExit(lambda *_: {"net_flow": -.1, "turnover_relative": 1.5}, confirmed=True)
    p={"entry_index":0,"entry_date":"0"}
    assert direct(c,p,20,previous=.01) is None
    assert direct(c,p,25) is None
    assert direct(c,p,30)["reason"] == "continuous_confirmed_loss"
    assert c.reviews[-1]["outflow_streak"] == 2


def test_first_intraday_loss_is_still_reviewed_before_twenty_five():
    c=LateContinuousFinanceExit(lambda *_: {"net_flow": -.1},confirmed=True)
    assert direct(c,{"entry_index":0,"entry_date":"0"},1,low=9.)["reason"] == "conditional_stop_loss"
