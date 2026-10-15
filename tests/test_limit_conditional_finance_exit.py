import pytest
from panda_alpha.limit_reversal import simulate, Policy
from panda_alpha.limit_conditional_finance_exit import ConditionalFinanceExit
from test_limit_finance_exit_policies import fixture, sells


def execute(rows, days, flow, turnover=1.5, cooling=False):
    calls=[]
    def source(code, day):
        calls.append((code,day));return {'net_flow':flow,'turnover_relative':turnover}
    c=ConditionalFinanceExit(source,require_cooling=cooling)
    result=simulate(rows,days,policy=Policy('conditional',max_holding_sessions=61),
                    conditional_exit_controller=c,preopen_signal_filter={(days[2],'A'):True})
    return result,c,calls


def test_first_loss_waived_once_then_profit_and_no_entry_day_review():
    days,rows=fixture(7)
    for i in [3,4]:rows[i].update(open=9.,low=8.5,high=10.,close=9.)
    rows[5].update(open=10.,high=11.01,low=9.8,close=11.)
    r,c,calls=execute(rows,days,.1)
    assert len(calls)==1 and calls[0][1]==days[3]
    assert sells(r)[0]['reason']=='take_profit'


def test_outflow_gap_and_same_bar_profit_stop_priority():
    days,rows=fixture(5);rows[3].update(open=8.8,low=8.5,high=11.2,close=10.)
    r,_,_=execute(rows,days,-.1)
    assert sells(r)[0]['price']==8.8 and sells(r)[0]['reason']=='conditional_stop_loss'


@pytest.mark.parametrize('flow,turnover',[(None,.5),(-.1,None),(-.1,1.0),(0.,.5)])
def test_unknown_or_neutral_does_not_trigger_joint_exit(flow,turnover):
    days,rows=fixture(5);rows[3].update(open=9.,low=8.5,high=10.,close=9.)
    r,_,_=execute(rows,days,flow,turnover,cooling=True)
    assert not sells(r)


def test_stale_loss_uses_prior_close_not_today_close_or_open():
    days,rows=fixture(25)
    rows[21].update(open=9.5,low=9.4,high=10.,close=9.8)
    rows[22].update(open=10.4,low=10.,high=10.5,close=10.5)
    r,c,_=execute(rows,days,-.1,.7,cooling=True)
    trade=sells(r)[0]
    assert trade['date']==days[22] and trade['reason']=='conditional_stale_loss'
    assert trade['price']==10.4 and len(c.reviews)==1


def test_trigger_remains_pending_through_down_limit_and_suspension():
    days,rows=fixture(7)
    rows[3].update(open=9.,high=9.,low=9.,close=9.,down_limit=9.)
    rows[4].update(open=9.,high=9.,low=9.,close=9.,trade_status='suspended',volume=0.)
    rows[5].update(open=8.8,high=9.2,low=8.7,close=9.)
    r,_,calls=execute(rows,days,-.1)
    assert len(calls)==1 and sells(r)[0]['date']==days[5] and sells(r)[0]['price']==8.8


def test_sixty_day_cap_applies_even_if_all_optional_reviews_unknown():
    days,rows=fixture(65)
    r,_,_=execute(rows,days,None)
    assert sells(r)[0]['date']==days[62] and sells(r)[0]['reason']=='max_holding_sessions'


def test_suspended_twenty_day_review_queues_next_trading_open():
    days,rows=fixture(25);rows[21].update(open=9.8,low=9.7,high=10.,close=9.8)
    rows[22].update(open=9.8,low=9.8,high=9.8,close=9.8,trade_status='suspended',volume=0.)
    r,_,calls=execute(rows,days,-.1)
    assert calls==[('A',days[22])]
    assert sells(r)[0]['date']==days[23] and sells(r)[0]['reason']=='conditional_stale_loss'


def test_suspended_quoted_low_cannot_create_a_new_intraday_touch():
    days,rows=fixture(6)
    rows[3].update(open=9.,high=9.,low=9.,close=9.,trade_status='suspended',volume=0.)
    rows[4].update(open=10.,high=10.5,low=9.8,close=10.)
    r,_,calls=execute(rows,days,-.1)
    assert calls==[] and sells(r)==[]
