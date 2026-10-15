"""No real market data or account: prove diagnostic gates distinguish causes."""
import ast
from datetime import datetime,timedelta
from types import ModuleType,SimpleNamespace
import sys

from panda_alpha.limit_finance_execution_probe import render_zero_gate_probe


def probe(monkeypatch):
    for name in ('panda_backtest','panda_backtest.api','panda_backtest.api.api'):
        monkeypatch.setitem(sys.modules,name,ModuleType(name))
    source=render_zero_gate_probe({'2026-07-01':['000566.SZ'],
                                  '2026-07-02':['000566.SZ']})
    ns={}
    exec(compile(source,'no_order_probe.py','exec'),ns)
    c=SimpleNamespace(run_info=SimpleNamespace(frequency='1M',matching_type=0),
        trade_date='20260701',trade_time=datetime(2026,7,1,9,31))
    ns['initialize'](c);ns['before_trading'](c)
    return ns,c,source


def tick(ns,c,bar,offset=0):
    c.trade_time=datetime(2026,7,1,9,31)+timedelta(minutes=offset)
    ns['handle_data'](c,SimpleNamespace(get=lambda symbol:bar))
    return c._zero_probe['current']


def test_valid_ohlcv_with_inherited_zero_limits_is_not_confused_with_no_bar(monkeypatch):
    ns,c,_=probe(monkeypatch)
    tick(ns,c,None)
    assert c._zero_probe['current']['none_bars']==1
    ns['before_trading'](c)
    class PublicDefaultBar:
        limit_up=limit_down=0.0
    bar=PublicDefaultBar();bar.close=10.;bar.volume=100000.
    tick(ns,c,bar)
    s=tick(ns,c,bar,1)
    assert (s['callbacks'],s['bar_objects'],s['valid_close'])==(2,2,2)
    assert s['nonpositive_or_missing_limits']==2
    assert s['previous_volume_capacity_ge100']==1
    assert s['all_entry_gates_pass']==0
    assert s['samples']['positive_close_missing_limits']['limit_down']==0


def test_previous_minute_only_and_strict_close_trigger_are_observed(monkeypatch):
    ns,c,source=probe(monkeypatch)
    bar=SimpleNamespace(close=10.,limit_down=10.,limit_up=12.,volume=100000.)
    assert tick(ns,c,bar)['all_entry_gates_pass']==0
    assert tick(ns,c,bar)['all_entry_gates_pass']==0  # same minute is not completed volume
    s=tick(ns,c,bar,1)
    assert s['all_entry_gates_pass']==1
    assert s['samples']['first_would_submit']['previous_completed_volume']==100000.
    bar.close=10.01
    tick(ns,c,bar,2)
    assert s['all_entry_gates_pass']==1  # intrabar touch cannot substitute for close
    tree=ast.parse(source)
    assert not any(isinstance(n,ast.Name) and n.id.startswith(('order','panda_data')) for n in ast.walk(tree))
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in
        ('open','eval','exec','getattr','__import__') for n in ast.walk(tree))
