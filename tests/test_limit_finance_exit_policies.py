"""Synthetic execution-clock checks for the new fixed exit comparison."""
import pandas as pd

from panda_alpha.limit_finance_exit_policies import FINANCE_EXIT_POLICIES
from panda_alpha.limit_reversal import simulate


def fixture(n=26):
    days = pd.bdate_range("2024-01-02", periods=n).strftime("%Y-%m-%d").tolist()
    rows = [{"date":d,"symbol":"A","open":10.,"high":10.2,"low":9.9,"close":10.,
             "up_limit":20.,"down_limit":1.,"volume":1_000_000.,"trade_status":"trading",
             "entry_eligible":True,"adjustment_factor":1.} for d in days]
    rows[0].update(open=11.,high=11.,low=11.,close=11.,up_limit=11.)
    rows[1].update(open=12.,high=12.,low=12.,close=12.,up_limit=12.)
    rows[2].update(open=10.5,high=12.,low=10.,close=10.,down_limit=10.)
    return days, rows


def run(role, days, rows):
    return simulate(rows, days, policy=FINANCE_EXIT_POLICIES[role],
                    preopen_signal_filter={(days[2],"A"):True}, cost=.003)


def sells(result):
    return [x for x in result["trades"] if x["side"] == "sell"]


def test_profit5_keeps_T1_and_strict_price_tick():
    days, rows = fixture(6)
    rows[3].update(open=10.5,high=10.5,low=10.4,close=10.5)
    rows[4].update(open=10.5,high=10.51,low=10.4,close=10.5)
    trade = sells(run("profit5",days,rows))[0]
    assert trade["date"] == days[4] and trade["price"] == 10.51
    assert trade["reason"] == "take_profit"


def test_stop8_accounts_for_gap_and_pessimistic_dual_touch():
    for opening, expected in [(8.8,8.8),(10.,9.2)]:
        days, rows = fixture(5)
        rows[3].update(open=opening,high=11.5,low=8.5,close=10.)
        trade = sells(run("stop8",days,rows))[0]
        assert trade["date"] == days[3] and trade["price"] == expected
        assert trade["reason"] == "stop_loss"


def test_time20_is_t_plus20_open_not_twentieth_inclusive_day():
    days, rows = fixture()
    trade = sells(run("time20",days,rows))[0]
    assert trade["date"] == days[22] and trade["holding_sessions"] == 21
    assert trade["reason"] == "max_holding_sessions"


def test_time_expiry_persists_through_limit_and_suspension():
    days, rows = fixture()
    rows[22].update(open=9.,high=9.,low=9.,close=9.,down_limit=9.)
    rows[23].update(open=9.,high=9.,low=9.,close=9.,trade_status="suspended",volume=0.)
    rows[24].update(open=8.5,high=9.,low=8.4,close=8.8)
    result = run("time20",days,rows); trade = sells(result)[0]
    assert trade["date"] == days[24] and trade["price"] == 8.5
    assert trade["holding_sessions"] == 23
    assert result["blocked"]["sell_blocked_one_price_limit_down"] == 1
    assert result["blocked"]["sell_blocked_suspension"] == 1


def test_combined_stop_precedes_profit_when_daily_sequence_unknown():
    days, rows = fixture(5)
    rows[3].update(open=10.,high=11.,low=9.,close=10.)
    trade = sells(run("combined",days,rows))[0]
    assert trade["reason"] == "stop_loss" and trade["price"] == 9.2


def test_combined_time_exit_precedes_same_session_new_touch():
    days, rows = fixture()
    rows[22].update(open=10.,high=11.,low=9.,close=10.)
    trade = sells(run("combined",days,rows))[0]
    assert trade["date"] == days[22] and trade["price"] == 10.
    assert trade["reason"] == "max_holding_sessions"
