"""Synthetic fixed diagnostic contracts; no actual SDK/account/market access."""
import ast
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest

from panda_alpha.limit_finance_band_probe import ROSTER, WINDOW, render_band_matching_probe


def worker(monkeypatch, mutate=None, action="fill", lower=10.0, opening=11.0):
    modules = {name: ModuleType(name) for name in
               ("panda_backtest", "panda_backtest.api", "panda_backtest.api.api", "panda_data")}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    queries, orders, ns = [], [], {}

    def frame(provider, **kwargs):
        queries.append((provider, kwargs))
        day = kwargs["start_date"]
        if provider == "daily":
            rows = [{"symbol": s, "date": day, "open": opening,
                     "limit_up": 12.0, "limit_down": lower} for s in kwargs["symbol"]]
        else:
            rows = [{"symbol": s, "date": day, "cal_limit_up": 12.0,
                     "cal_limit_down": lower} for s in kwargs["symbol"]]
        result = pd.DataFrame(rows)
        return mutate(provider, day, result) if mutate else result

    modules["panda_data"].get_stock_daily = lambda **k: frame("daily", **k)
    modules["panda_data"].get_factor = lambda **k: frame("factor", **k)

    class Limit:
        def __init__(self, price):
            self.limit_price = price

    modules["panda_backtest.api.api"].LimitOrderStyle = Limit
    account = SimpleNamespace(cash=100000.0, total_value=100000.0,
                              transaction_cost=0.0, positions={})
    context = SimpleNamespace(run_info=SimpleNamespace(
        frequency="1M", matching_type=0, start_date=WINDOW["start"], end_date=WINDOW["end"],
        stock_starting_cash=100000, commission_multiplier=3.75, slippage=0,
        stock_account="synthetic"), stock_account_dict={"synthetic": account},
        trade_date="20260701", trade_time=datetime(2026, 7, 1, 9, 31))

    def submit(account_id, symbol, quantity, style, remark):
        assert context._band_probe["attempt"] is not None  # Before callback/API return.
        orders.append({"symbol": symbol, "quantity": quantity, "price": style.limit_price})
        if action == "exception":
            raise ValueError("limit price precision invalid; code=PRICE_404; token=private123")
        order = SimpleNamespace(account=account_id, order_book_id=symbol,
            order_id="order1", quantity=quantity, filled_quantity=0, status=1, remark=remark)
        if action == "reject":
            order.status = 3
            ns["stock_order_cancel"](context, order)
        else:
            price = 0.0 if action == "zero_price" else style.limit_price
            cost = max(5.0, quantity * price * .003)
            account.cash -= quantity * price + cost
            account.transaction_cost += cost
            account.total_value -= cost
            account.positions[symbol] = SimpleNamespace(quantity=quantity)
            order.status, order.filled_quantity = 2, quantity
            if action != "trade_first":
                ns["on_stock_order_rtn"](context, order)
            trade = SimpleNamespace(account_id=account_id, trade_id="trade1", order_id="order1",
                contract_code=symbol, order_remark=remark, trade_date=context.trade_date,
                business=0, volume=quantity, price=price, cost=cost)
            ns["on_stock_trade_rtn"](context, trade)
            ns["on_stock_trade_rtn"](context, trade)  # Actual tradeID deduplication.
            if action == "trade_first":
                ns["on_stock_order_rtn"](context, order)
        stale = SimpleNamespace(**vars(order))
        stale.status, stale.filled_quantity = 1, 0  # Stale return must not revive terminal.
        return [stale]

    modules["panda_backtest.api.api"].order_shares = submit
    code = render_band_matching_probe()
    exec(compile(code, "synthetic_band_matching_probe.py", "exec"), ns)
    return ns, context, queries, orders, code


def start(ns, context, day="2026-07-01"):
    context.trade_date = day.replace("-", "")
    context.trade_time = datetime.fromisoformat(day + " 09:31:00")
    ns["before_trading"](context)


def minute(ns, context, offset, *, price=10.0, volume=10000.0, opening=11.0, changes=None):
    context.trade_time = datetime.strptime(context.trade_date + "093100", "%Y%m%d%H%M%S") + timedelta(minutes=offset)
    attrs = {"date": int(context.trade_date), "trade_date": context.trade_date,
        "time": int(context.trade_time.strftime("%H%M%S")), "open": opening,
        "close": price, "volume": volume, "limit_up": 0.0, "limit_down": 0.0}
    attrs.update(changes or {})
    bar = SimpleNamespace(**attrs)
    ns["handle_data"](context, SimpleNamespace(get=lambda symbol: bar))


def test_self_contained_exact_four_requests_and_before_trading_no_open_use(monkeypatch):
    ns, context, queries, orders, code = worker(monkeypatch)
    forbidden = {"eval", "exec", "getattr", "setattr", "hasattr", "__import__", "compile"}
    tree = ast.parse(code)
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in forbidden for n in ast.walk(tree))
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("panda_alpha")
                   for n in ast.walk(tree))
    ns["initialize"](context)
    assert len(queries) == 4
    for provider, kwargs in queries:
        day = ns["_day"](kwargs["start_date"])
        assert kwargs["start_date"] == kwargs["end_date"]
        assert kwargs["symbol"] == ROSTER[day]
        assert (kwargs["st"], kwargs["indicator"]) == (True, "") if provider == "daily" else kwargs["type"] == "stock"
    start(ns, context)
    assert context._band_probe["current"]["open_alignment"] == {} and orders == []
    minute(ns, context, 1)  # A late first quote cannot certify daily open.
    assert set(context._band_probe["current"]["open_alignment"].values()) == {"first_available_not_0931"}
    assert orders == []
    other, native_context, _, _, _ = worker(monkeypatch)
    native_context.run_info.stock_account = 42
    native_context.stock_account_dict = {42: native_context.stock_account_dict["synthetic"]}
    other["initialize"](native_context)
    assert native_context.account == 42  # Preserve native account-key type.


def test_declared_multiindex_both_orders_and_single_date_index(monkeypatch):
    for names in (["symbol", "date"], ["date", "symbol"], ["date"]):
        ns, context, queries, orders, _ = worker(monkeypatch,
            mutate=lambda provider, day, frame: frame.set_index(names))
        ns["initialize"](context)
        assert all(p["status"] == "band_concordant"
                   for pairs in context._band_probe["pairs"].values() for p in pairs.values())
        assert len(queries) == 4 and orders == []


def test_schema_identity_and_value_failures_do_not_expand_or_retry(monkeypatch):
    def bad(provider, day, frame, kind):
        if provider != "daily":
            return frame
        frame = frame.copy()
        if kind == "missing":
            return frame.drop(columns="limit_up")
        if kind == "duplicate":
            return pd.concat([frame.iloc[:1], frame.iloc[:1]], ignore_index=True)
        if kind == "unexpected":
            frame.loc[0, "symbol"] = "000001.SZ"
        elif kind == "wrong_date":
            frame["date"] = "20260703"
        elif kind == "bool":
            frame["limit_down"] = True
        elif kind == "zero":
            frame["limit_down"] = 0
        elif kind == "text_price":
            frame["limit_down"] = "10.0"
        elif kind == "disagree":
            frame["limit_down"] = 9.99
        elif kind == "exception":
            raise RuntimeError("synthetic provider unsupported")
        return frame
    for kind in ("missing", "duplicate", "unexpected", "wrong_date", "bool", "zero", "text_price", "disagree", "exception"):
        ns, context, queries, orders, _ = worker(monkeypatch,
            mutate=lambda p, d, f: bad(p, d, f, kind))
        ns["initialize"](context)
        assert len(queries) == 4  # Other preset channel inspected even after failure.
        start(ns, context)
        minute(ns, context, 0)
        minute(ns, context, 1)
        assert not orders and context._band_probe["current"]["provider_eligible"] == 0


def test_exact_stamp_and_first_0931_open_required_and_no_timestamp_aliases(monkeypatch):
    for changes in ({"date": "20260701"}, {"trade_date": 20260701}, {"time": "093100"},
                    {"date": 20260702}, {"time": 93200}, {"open": 11.01}):
        ns, context, _, orders, _ = worker(monkeypatch)
        ns["initialize"](context)
        start(ns, context)
        minute(ns, context, 0, changes=changes)
        minute(ns, context, 1)
        assert not orders
        assert set(context._band_probe["current"]["open_alignment"].values()) != {"aligned"}


def test_minute_changes_volume_sequence_first_three_samples_and_no_touch(monkeypatch, capsys):
    ns, context, _, orders, _ = worker(monkeypatch)
    ns["initialize"](context)
    start(ns, context)
    for i, (price, volume) in enumerate([(10.01, 10000), (10.02, 10000), (10.03, 12000), (10.03, 9000)]):
        minute(ns, context, i, price=price, volume=volume)
    state = context._band_probe["current"]
    assert state["bar_stamp_match"] == 20
    assert (state["close_changes"], state["volume_equal"], state["volume_increase"], state["volume_decrease"]) == (10, 5, 5, 5)
    assert len(state["samples"]) == 3
    assert [s["context_minute"][-5:] for s in state["samples"]] == ["09:31", "09:32", "09:33"]
    assert orders == []
    ns["after_trading"](context)
    out = capsys.readouterr().out
    assert "MATCHING_UNOBSERVED_NO_TOUCH" in out
    assert '"economic_pass": false' in out and '"volume_interval_and_units_verified": false' in out


def test_previous_completed_volume_only_and_one_order_before_return_callbacks(monkeypatch, capsys):
    ns, context, _, orders, _ = worker(monkeypatch)
    ns["initialize"](context)
    start(ns, context)
    minute(ns, context, 0, volume=9999)
    assert orders == []
    minute(ns, context, 1, volume=99999999)
    assert orders == []  # Huge current volume cannot rescue the previous shortfall.
    minute(ns, context, 2)
    assert len(orders) == 1 and orders[0]["quantity"] == 100
    root = context._band_probe
    assert len(root["trades"]) == 1 and root["orders"]["order1"]["terminal"]
    assert root["orders"]["order1"]["reported_filled"] == 100
    ns["after_trading"](context)
    assert "MATCHING_VALIDATED_SINGLE_ATTEMPT_ONLY" in capsys.readouterr().out
    start(ns, context, "2026-07-02")
    minute(ns, context, 0)
    minute(ns, context, 1)
    assert len(orders) == 1


def test_reject_and_zero_price_fill_never_retry_or_economic_pass(monkeypatch, capsys):
    for action, expected in [("reject", "MATCHING_ATTEMPTED_NOT_VALIDATED_NO_FILL"),
                             ("zero_price", "EXPLICIT_CONTRACT_FAILURE")]:
        ns, context, _, orders, _ = worker(monkeypatch, action=action)
        ns["initialize"](context)
        start(ns, context)
        for i in range(4):
            minute(ns, context, i)
        assert len(orders) == 1
        if action == "zero_price":
            assert context._band_probe["invalid_trade_samples"][0]["price"] == 0.0
        ns["after_trading"](context)
        out = capsys.readouterr().out
        assert expected in out and '"economic_pass": false' in out


def test_float32_same_tick_serialization_does_not_admit_higher_tick(monkeypatch):
    native = float(np.float32(4.8))
    ns, context, _, orders, _ = worker(monkeypatch, lower=4.8, opening=5.0)
    ns["initialize"](context)
    assert ns["_tick"](native) == ns["_tick"](4.8) == 480
    assert ns["_tick"](4.8001) is None
    start(ns, context)
    minute(ns, context, 0, price=native, opening=5.0)
    minute(ns, context, 1, price=4.81, opening=5.0)
    assert orders == []
    minute(ns, context, 2, price=native, opening=5.0)
    assert len(orders) == 1 and orders[0]["price"] == native
    assert not context._band_probe["faults"]


def test_bounded_error_detail_retained_without_credentials_or_ids(monkeypatch):
    def unsupported(provider, day, frame):
        if provider == "daily":
            raise RuntimeError("Unsupported field limit_up; code=FIELDS_404; token=private123; "
                               "user_id=12345678901234567890; url=https://invalid.test/?token=secret")
        return frame
    ns, context, queries, orders, _ = worker(monkeypatch, mutate=unsupported)
    ns["initialize"](context)
    error = context._band_probe["provider_summary"][0]["providers"]["daily"]["error"]
    assert len(queries) == 4 and orders == []
    assert "Unsupported field limit_up" in error["message"] and "FIELDS_404" in error["message"]
    assert not any(v in error["message"] for v in ("private123", "12345678901234567890", "https://"))
    assert len(error["message"]) <= 240
    ns, context, _, orders, _ = worker(monkeypatch, action="exception")
    ns["initialize"](context)
    start(ns, context)
    for i in range(3):
        minute(ns, context, i)
    assert len(orders) == 1
    error = context._band_probe["attempt"]["exception"]
    assert error["type"] == "ValueError" and "price precision invalid" in error["message"]
    assert "private123" not in error["message"]


def test_day_end_order_trade_identity_and_filled_quantity_must_reconcile(monkeypatch, capsys):
    for mismatch in ("matched_trade_first", "unknown_order_id", "filled_quantity"):
        ns, context, _, orders, _ = worker(monkeypatch, action="trade_first")
        ns["initialize"](context)
        start(ns, context)
        minute(ns, context, 0)
        minute(ns, context, 1)
        root = context._band_probe
        assert len(orders) == 1 and len(root["trades"]) == 1
        if mismatch == "unknown_order_id":
            root["trades"][0]["order_id"] = "missing-native-order"
        elif mismatch == "filled_quantity":
            root["orders"]["order1"]["reported_filled"] = 50
        minute(ns, context, 2)
        assert len(orders) == 1  # No replacement order or automatic repair.
        ns["after_trading"](context)
        output = capsys.readouterr().out
        if mismatch == "matched_trade_first":
            assert "MATCHING_VALIDATED_SINGLE_ATTEMPT_ONLY" in output
            assert '"order_trade_reconciled": true' in output
            continue
        assert "FILL_OBSERVED_NATIVE_RECONCILIATION_PENDING" in output
        assert '"order_trade_reconciled": false' in output
        assert '"native_reconciled": false' in output
        assert "MATCHING_VALIDATED_SINGLE_ATTEMPT_ONLY" not in output
