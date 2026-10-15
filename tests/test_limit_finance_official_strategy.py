"""Synthetic callback contracts only; no source API, real account or backtest."""
import ast
from datetime import datetime, timedelta
from enum import IntEnum
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import numpy as np
import pytest

from panda_alpha.limit_finance_official_strategy import render_official_strategy


CAL = pd.bdate_range("2031-01-02", periods=80).strftime("%Y-%m-%d").tolist()
WINDOW = {"start": CAL[25], "end": CAL[65]}
EXAMPLES = [{"SCODE": "000001", "DATE": day, "RZYE": 100., "RZMRE": 10., "RZCHE": 5.}
            for day in CAL[20:23]]


class Data:
    def __init__(self, bars):
        self.bars = bars

    def get(self, symbol):
        return self.bars.get(symbol)


class Bar:
    def __init__(self, price=10., volume=100000., lower=10., upper=20.):
        self.open, self.volume, self.limit_down, self.limit_up = price, volume, lower, upper

    @property
    def high(self):
        raise AssertionError("No current-bar high may be used")

    low = close = high


class ClosingBar:
    def __init__(self, price=10., volume=100000., lower=10., upper=20.):
        self.close, self.volume, self.limit_down, self.limit_up = price, volume, lower, upper

    @property
    def open(self):
        raise AssertionError("A close-phase strategy must not read the current open")

    high = low = open


class Inputs:
    def __init__(self, symbols):
        self.calendar = list(CAL)
        self.validation_count = 3
        self.daily_close = {(symbol, day): 9. for symbol in symbols for day in CAL}
        self.features = {}
        self.calls = []

    def validate_run_calendar(self, actual, start, end):
        assert list(actual) == [d for d in CAL if start <= d <= end]

    def features_for(self, symbol, index):
        self.calls.append((symbol, index))
        return dict(self.features.get(symbol, {"net_flow": .1, "turnover_relative": 1.5}),
                    latest_report_date=CAL[index - 2], execution_date=CAL[index])


def worker(monkeypatch, mode="late_confirmed", roster=None, render_matching_type=1, **settings):
    roster = roster or [(WINDOW["start"], "000001")]
    code = render_official_strategy(mode, roster, CAL, EXAMPLES, WINDOW,
                                   matching_type=render_matching_type)
    modules = {name: ModuleType(name) for name in
               ("panda_backtest", "panda_backtest.api", "panda_backtest.api.api", "panda_data")}
    calls, behavior = [], {"action": "fill"}

    class Limit:
        def __init__(self, price):
            self.limit_price = price

    modules["panda_backtest.api.api"].LimitOrderStyle = Limit
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    ns = {}

    def order_shares(account, symbol, quantity, style, remark):
        assert ns["context"]._lf["ready"]
        call = {"account": account, "symbol": symbol, "quantity": quantity,
                "price": style.limit_price, "remark": remark, "id": str(len(calls) + 1)}
        calls.append(call)
        order = SimpleNamespace(account=account, order_book_id=symbol, order_id=call["id"],
                                status=1, quantity=abs(quantity), filled_quantity=0, remark=remark)
        if behavior["action"] == "active":
            ns["on_stock_order_rtn"](ns["context"], order)
            return [order]
        if behavior["action"] == "reject":
            order.status = 3
            ns["stock_order_cancel"](ns["context"], order)
            return [order]
        fill(ns, call, order=order, quantity=behavior.get("fill_quantity"))
        # A stale ACTIVE return after terminal callbacks must not resurrect it.
        stale = SimpleNamespace(**vars(order))
        stale.status = 1
        return [stale]

    modules["panda_backtest.api.api"].order_shares = order_shares
    exec(compile(code, "synthetic_official_strategy.py", "exec"), ns)
    info = SimpleNamespace(frequency="1M", matching_type=render_matching_type, stock_starting_cash=100000.,
                           commission_multiplier=3.75, slippage=0., start_date=WINDOW["start"],
                           end_date=WINDOW["end"], stock_account="synthetic")
    for k, v in settings.items():
        setattr(info, k, v)
    account = SimpleNamespace(cash=100000., total_value=100000., positions={},
                              transaction_cost=0.)
    context = SimpleNamespace(run_info=info, stock_account_dict={"synthetic": account},
        trade_date=WINDOW["start"].replace("-", ""),
        trade_date_list=[d for d in CAL if WINDOW["start"] <= d <= WINDOW["end"]],
        trade_time=datetime.fromisoformat(WINDOW["start"] + " 09:30:00"))
    ns["context"], ns["account"], ns["calls"], ns["behavior"] = context, account, calls, behavior
    inputs = Inputs(ns["FROZEN_SYMBOLS"])
    ns["load_inputs"] = lambda *args, **kwargs: inputs
    return ns, context, inputs, code


def day(ns, context, date=None):
    date = date or WINDOW["start"]
    context.trade_date = date.replace("-", "")
    context.trade_time = datetime.fromisoformat(date + " 09:30:00")
    ns["before_trading"](context)


def minute(ns, context, n, bars):
    context.trade_time = datetime.fromisoformat(context.trade_date + " 09:30:00") + timedelta(minutes=n)
    ns["handle_data"](context, Data(bars))


def fill(ns, call, *, order=None, quantity=None, trade_id=None):
    context, account = ns["context"], ns["account"]
    quantity = abs(call["quantity"]) if quantity is None else quantity
    order = order or SimpleNamespace(account=context.account, order_book_id=call["symbol"],
        order_id=call["id"], status=1, quantity=abs(call["quantity"]),
        filled_quantity=0, remark=call["remark"])
    symbol, price = call["symbol"], call["price"]
    side = 0 if call["quantity"] > 0 else 1
    cost = max(5., price * quantity * .003)
    position = account.positions.get(symbol)
    if side == 0:
        previous = position.quantity if position else 0
        basis = (previous * position.avg_price + quantity * price) / (previous + quantity) if position else price
        account.positions[symbol] = SimpleNamespace(quantity=previous + quantity,
            sellable=position.sellable if position else 0, avg_price=basis)
        account.cash -= quantity * price + cost
    else:
        assert position and position.sellable >= quantity
        position.quantity -= quantity
        position.sellable -= quantity
        if position.quantity == 0:
            del account.positions[symbol]
        account.cash += quantity * price - cost
    account.transaction_cost += cost
    order.status = 2 if quantity == order.quantity else 5
    order.filled_quantity = quantity
    # Native order and actual trade callbacks both precede API return.
    ns["on_stock_order_rtn"](context, order)
    trade = SimpleNamespace(account_id=context.account, trade_id=trade_id or "trade-" + call["id"],
        order_id=call["id"], contract_code=symbol, business=side, volume=quantity, price=price,
        cost=cost, trade_date=context.trade_date, order_remark=call["remark"])
    ns["on_stock_trade_rtn"](context, trade)
    return order, trade


def holding(ns, symbol, quantity=1000, basis=10., offset=1, day_index=25):
    ns["account"].positions[symbol] = SimpleNamespace(quantity=quantity, sellable=quantity, avg_price=basis)
    ns["context"]._lf["position_state"][symbol] = {
        "entry_index": day_index - offset, "entry_date": CAL[day_index - offset], "flags": {}}


def test_render_is_self_contained_and_preserves_untouched_helpers(monkeypatch):
    ns, context, inputs, code = worker(monkeypatch)
    tree = ast.parse(code)
    assert not any(isinstance(node, ast.ImportFrom) and
                   (node.module or "").startswith("panda_alpha") for node in ast.walk(tree))
    forbidden = {"eval", "exec", "getattr", "setattr", "hasattr", "delattr", "__import__", "compile"}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in forbidden for node in ast.walk(tree))
    assert "from __future__" not in code
    assert "trade.cost" in code and "account.total_value" in code
    assert ns["MIGRATION_METADATA"]["migration_not_original_daily_potential_fill_reproduction"]
    assert len(ns["MIGRATION_METADATA"]["rule_input_module_sha256"]) == 2
    with pytest.raises(ValueError, match="mode"):
        render_official_strategy("best", [(WINDOW["start"], "000001")], CAL, EXAMPLES, WINDOW)


def test_settings_and_input_permission_fail_before_any_order(monkeypatch):
    for settings in ({"frequency": "1d"}, {"matching_type": 0}, {"stock_starting_cash": 99999},
                     {"commission_multiplier": 1}, {"slippage": .001}, {"end_date": CAL[64]}):
        ns, context, inputs, _ = worker(monkeypatch, **settings)
        with pytest.raises(RuntimeError):
            ns["initialize"](context)
        assert ns["calls"] == []
    ns, context, inputs, _ = worker(monkeypatch)
    ns["load_inputs"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("permission missing"))
    with pytest.raises(RuntimeError, match="permission"):
        ns["initialize"](context)
    assert not context._lf["ready"] and ns["calls"] == []


def test_previous_bar_capacity_partial_buys_no_same_minute_duplicate_or_callback_resurrection(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch)
    ns["initialize"](context)
    day(ns, context)
    bars = {"000001.SZ": Bar()}
    minute(ns, context, 0, bars)
    assert ns["calls"] == []  # Current100000 shares are not yet in the sizing budget.
    minute(ns, context, 1, bars)
    assert ns["calls"][0]["quantity"] == 1000
    assert not context._lf["active"]["000001.SZ"]
    assert context._lf["position_state"]["000001.SZ"]["entry_index"] == 25
    minute(ns, context, 1, bars)
    assert len(ns["calls"]) == 1
    minute(ns, context, 2, bars)
    assert ns["calls"][1]["quantity"] == 900
    assert context._lf["reservations"]["000001.SZ"]["actual_bought"] == 1900
    assert context._lf["traded_today"]["000001.SZ"] == 1900
    assert context._lf["daily_buy_debits"] == pytest.approx(19057.)
    # Duplicate fill delivery changes neither capacity nor budget counters.
    call = ns["calls"][0]
    trade = SimpleNamespace(account_id=context.account, trade_id="trade-1", order_id="1",
        contract_code="000001.SZ", business=0, volume=1000, price=10., cost=30.,
        trade_date=context.trade_date, order_remark=call["remark"])
    ns["on_stock_trade_rtn"](context, trade)
    assert context._lf["traded_today"]["000001.SZ"] == 1900


def test_nonterminal_and_partial_terminal_orders_are_not_treated_as_no_position(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch)
    ns["initialize"](context)
    day(ns, context)
    ns["behavior"]["action"] = "active"
    bars = {"000001.SZ": Bar()}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    call = ns["calls"][0]
    active = SimpleNamespace(account=context.account, order_book_id="000001.SZ", order_id="1",
        status=4, quantity=1000, filled_quantity=0, remark=call["remark"])
    ns["on_stock_order_rtn"](context, active)
    minute(ns, context, 2, bars)
    assert len(ns["calls"]) == 1 and "000001.SZ" not in context._lf["position_state"]
    _, trade = fill(ns, call, quantity=400)
    assert ns["account"].positions["000001.SZ"].quantity == 400
    assert context._lf["position_state"]["000001.SZ"]["entry_index"] == 25
    ns["behavior"]["action"] = "fill"
    minute(ns, context, 3, bars)
    assert ns["calls"][1]["quantity"] == 1500  # Actual target remainder, not previous submitted qty.
    assert ns["account"].positions["000001.SZ"].quantity == 1900


def test_five_preopen_slots_cannot_be_recycled_after_same_day_sale(monkeypatch):
    codes = ["000001", "000002", "000003", "000004", "000005", "000006"]
    ns, context, inputs, _ = worker(monkeypatch, mode="control",
        roster=[(WINDOW["start"], code) for code in codes])
    ns["initialize"](context)
    for code in codes[:5]:
        holding(ns, code + ".SZ")
    day(ns, context)
    assert context._lf["reservations"] == {}
    bars = {codes[0] + ".SZ": Bar(price=12., lower=8., upper=15.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert len(ns["calls"]) == 1 and ns["calls"][0]["quantity"] == -1000
    assert len(ns["account"].positions) == 4
    minute(ns, context, 2, {**bars, "000006.SZ": Bar(price=8., lower=8., upper=15.)})
    assert len(ns["calls"]) == 1


def test_same_day_sale_cash_never_increases_frozen_buy_budget(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch, mode="control",
        roster=[(WINDOW["start"], "000001"), (WINDOW["start"], "000002")])
    ns["initialize"](context)
    holding(ns, "000001.SZ")
    ns["account"].cash = 1000.
    day(ns, context)
    bars = {"000001.SZ": Bar(price=12., lower=8., upper=15.),
            "000002.SZ": Bar(price=8., lower=8., upper=15.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert [c["quantity"] for c in ns["calls"]] == [-1000, 100]
    assert context._lf["preopen_cash"] == 1000.
    assert context._lf["daily_buy_debits"] <= 1000.
    assert ns["account"].cash > 1000.


def test_scheduled_loss_confirms_without_quote_and_stays_pending_after_rejected_ioc(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch)
    ns["initialize"](context)
    holding(ns, "000001.SZ", offset=25, day_index=50)
    inputs.features["000001.SZ"] = {"net_flow": -.1, "turnover_relative": .5}
    day(ns, context, CAL[50])
    flags = context._lf["position_state"]["000001.SZ"]["flags"]
    assert flags["pending_exit"]["reason"] == "continuous_confirmed_loss"
    assert ns["calls"] == []
    assert inputs.calls[-1] == ("000001.SZ", 50)
    ns["behavior"]["action"] = "reject"
    bars = {"000001.SZ": Bar(price=9., lower=8., upper=15.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert ns["calls"][0]["quantity"] == -1000
    assert "000001.SZ" in context._lf["position_state"]
    context._lf["features"]["000001.SZ"] = {"net_flow": .5, "turnover_relative": 2.}
    ns["behavior"]["action"] = "fill"
    minute(ns, context, 2, bars)
    assert ns["calls"][1]["quantity"] == -1000
    assert "000001.SZ" not in context._lf["position_state"]


def test_current_open_only_strict_takeprofit_and_native_sellable_guard(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch, mode="control")
    ns["initialize"](context)
    holding(ns, "000001.SZ")
    day(ns, context)
    bars = {"000001.SZ": Bar(price=11., lower=8., upper=15.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert ns["calls"] == []  # Exactly10% is not strictly above10%.
    ns["account"].positions["000001.SZ"].sellable = 0
    minute(ns, context, 2, {"000001.SZ": Bar(price=11.01, lower=8., upper=15.)})
    assert ns["calls"] == []
    ns["account"].positions["000001.SZ"].sellable = 1000
    minute(ns, context, 3, {"000001.SZ": Bar(price=11.01, lower=8., upper=15.)})
    assert ns["calls"][0]["quantity"] == -1000 and ns["calls"][0]["price"] == 11.01


def test_invalid_limits_do_not_create_fake_orders_or_same_day_price_inputs(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch)
    ns["initialize"](context)
    day(ns, context)
    for n in range(3):
        minute(ns, context, n, {"000001.SZ": Bar(price=10., lower=0., upper=20.)})
    assert ns["calls"] == [] and context._lf["position_state"] == {}
    # A later legal reference may use only previously observed volume.
    minute(ns, context, 3, {"000001.SZ": Bar()})
    assert ns["calls"][0]["quantity"] == 1900


def test_partial_sticky_sale_preserves_state_until_actual_native_flat(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch)
    ns["initialize"](context)
    holding(ns, "000001.SZ", offset=25, day_index=50)
    inputs.features["000001.SZ"] = {"net_flow": -.1, "turnover_relative": .5}
    day(ns, context, CAL[50])
    ns["behavior"]["fill_quantity"] = 400
    bars = {"000001.SZ": Bar(price=9., lower=8., upper=15.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert ns["account"].positions["000001.SZ"].quantity == 600
    assert "pending_exit" in context._lf["position_state"]["000001.SZ"]["flags"]
    ns["behavior"].pop("fill_quantity")
    minute(ns, context, 2, bars)
    assert [row["quantity"] for row in ns["calls"]] == [-1000, -600]
    assert "000001.SZ" not in ns["account"].positions
    assert "000001.SZ" not in context._lf["position_state"]


def test_guard_diagnostics_include_both_values_and_all_nonsecret_settings(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch, frequency="1m", matching_type=0)
    with pytest.raises(RuntimeError) as captured:
        ns["initialize"](context)
    message = str(captured.value)
    for text in ('"frequency": "1m"', '"matching_type": "0"', '"stock_starting_cash": "100000.0"',
                 '"commission_multiplier": "3.75"', '"slippage": "0.0"',
                 '"start_date": "' + WINDOW["start"], '"end_date": "' + WINDOW["end"]):
        assert text in message
    assert "synthetic" not in message and "stock_account" not in message
    assert ns["calls"] == []


def test_unknown_runtime_representations_remain_rejected_until_actual_evidence(monkeypatch):
    class Matching(IntEnum):
        OPEN = 1
    for settings in ({"frequency": "1m"}, {"matching_type": np.int64(1)},
                     {"matching_type": Matching.OPEN}):
        ns, context, inputs, _ = worker(monkeypatch, **settings)
        with pytest.raises(RuntimeError, match="observed_settings"):
            ns["initialize"](context)
        assert ns["calls"] == []


def test_fee_and_window_mismatches_also_report_actual_settings_without_relaxation(monkeypatch):
    for settings in ({"commission_multiplier": 1}, {"slippage": .001},
                     {"end_date": CAL[64]}):
        ns, context, inputs, _ = worker(monkeypatch, **settings)
        with pytest.raises(RuntimeError, match="observed_settings") as captured:
            ns["initialize"](context)
        assert '"frequency": "1M"' in str(captured.value)
        assert '"matching_type": "1"' in str(captured.value)
        assert ns["calls"] == []


def test_explicit_close_phase_guard_and_metadata_do_not_guess_frequency_or_dtype(monkeypatch):
    ns, context, inputs, code = worker(monkeypatch, render_matching_type=0)
    ns["initialize"](context)
    assert ns["FROZEN_MATCHING_TYPE"] == 0
    assert ns["MIGRATION_METADATA"]["reference_phase"] == "close"
    assert ns["MIGRATION_METADATA"]["close_phase_is_actual_decision_clock_change_not_dtype_alias"]
    assert ns["MIGRATION_METADATA"]["same_explicit_reference_phase_for_both_comparison_roles"]
    assert ns["MIGRATION_METADATA"]["no_current_bar_volume_for_sizing"]
    for settings in ({"matching_type": 1}, {"matching_type": "0"}, {"frequency": "1m"}):
        ns, context, inputs, _ = worker(monkeypatch, render_matching_type=0, **settings)
        with pytest.raises(RuntimeError, match="observed_settings"):
            ns["initialize"](context)
    for wrong in (False, True, "0", np.int64(0), 2):
        with pytest.raises(ValueError, match="matching_type"):
            render_official_strategy("control", [(WINDOW["start"], "000001")], CAL,
                                     EXAMPLES, WINDOW, matching_type=wrong)


def test_close_phase_entry_uses_close_and_only_previous_bar_volume(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch, render_matching_type=0)
    ns["initialize"](context)
    day(ns, context)
    minute(ns, context, 0, {"000001.SZ": ClosingBar(volume=100000.)})
    assert ns["calls"] == []
    minute(ns, context, 1, {"000001.SZ": ClosingBar(volume=1e9)})
    assert ns["calls"][0]["quantity"] == 1000
    assert ns["calls"][0]["price"] == 10.
    # Prior volume may now be ample, but a close aboveDL cannot fill aDL bid.
    minute(ns, context, 2, {"000001.SZ": ClosingBar(price=10.5)})
    assert len(ns["calls"]) == 1


def test_close_phase_strict_takeprofit_does_not_use_open_high_or_low(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch, mode="control", render_matching_type=0)
    ns["initialize"](context)
    holding(ns, "000001.SZ")
    day(ns, context)
    minute(ns, context, 0, {"000001.SZ": ClosingBar(price=11., lower=8.)})
    minute(ns, context, 1, {"000001.SZ": ClosingBar(price=11., lower=8.)})
    assert ns["calls"] == []
    minute(ns, context, 2, {"000001.SZ": ClosingBar(price=11.01, lower=8.)})
    assert ns["calls"][0]["quantity"] == -1000 and ns["calls"][0]["price"] == 11.01


def test_close_phase_first_loss_touch_retains_sticky_exit_and_lagged_features(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch, render_matching_type=0)
    ns["initialize"](context)
    holding(ns, "000001.SZ")
    inputs.features["000001.SZ"] = {"net_flow": -.1, "turnover_relative": 1.5}
    day(ns, context)
    minute(ns, context, 0, {"000001.SZ": ClosingBar(price=9.19, lower=8.)})
    pending = context._lf["position_state"]["000001.SZ"]["flags"]["pending_exit"]
    assert pending["reason"] == "conditional_stop_loss"
    assert pending["trigger_price"] == 9.2
    assert ns["calls"] == []  # Even confirmed exit cannot spend current-bar volume.
    assert inputs.calls == [("000001.SZ", 25)]
    minute(ns, context, 1, {"000001.SZ": ClosingBar(price=9.3, lower=8.)})
    assert ns["calls"][0]["quantity"] == -1000 and ns["calls"][0]["price"] == 9.3
