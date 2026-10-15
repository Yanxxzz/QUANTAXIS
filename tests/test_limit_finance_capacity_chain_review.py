"""Independent frozen capacity/order chain witnesses, not a market replay."""
import ast
from pathlib import Path

import pytest

from test_limit_finance_official_strategy import ClosingBar, day, fill, minute, worker


def frozen_worker(monkeypatch):
    ns, context, inputs, _ = worker(monkeypatch, mode="control", render_matching_type=0)
    # Execute the exact completed strategy's gate functions when retained locally;
    # synthetic calendar/account/quotes remain the only inputs in either case.
    source = Path(__file__).resolve().parents[1] / (
        "research_runs/finance_full_strategy_short_official_20261015/close_phase_ready/target/Strategy.py")
    if source.is_file():
        names = {"_lf_capacity", "_lf_pending_buy_estimate", "_lf_submit", "_lf_bar", "handle_data"}
        body = [node for node in ast.parse(source.read_text(encoding="utf-8")).body
                if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in body} == names
        exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), ns)
    ns["initialize"](context)
    day(ns, context)
    return ns, context


def test_only_first_bar_closes_at_down_limit_can_legally_produce_zero_orders(monkeypatch):
    ns, context = frozen_worker(monkeypatch)
    minute(ns, context, 0, {"000001.SZ": ClosingBar(price=10., volume=100000., lower=10.)})
    assert ns["_lf_capacity"](context, "000001.SZ") == 0
    minute(ns, context, 1, {"000001.SZ": ClosingBar(price=10.01, volume=100000., lower=10.)})
    assert ns["_lf_capacity"](context, "000001.SZ") == 1000
    assert ns["calls"] == []  # Current close no longer reaches the frozen DL bid.


def test_share_volume_lot_threshold_and_previous_bar_clock(monkeypatch):
    ns, context = frozen_worker(monkeypatch)
    minute(ns, context, 0, {"000001.SZ": ClosingBar(volume=9999.)})
    minute(ns, context, 1, {"000001.SZ": ClosingBar(volume=1.)})
    assert ns["_lf_capacity"](context, "000001.SZ") == 99
    assert ns["calls"] == []
    minute(ns, context, 2, {"000001.SZ": ClosingBar(volume=100000.)})
    assert ns["calls"][0]["quantity"] == 100  # Exactly10000prior shares, not the current bar.


def test_empty_IOC_result_does_not_invent_fill_or_permanently_block_next_minute(monkeypatch):
    ns, context = frozen_worker(monkeypatch)
    attempts = []

    def no_fill(account, symbol, quantity, style, remark):
        attempts.append((symbol, quantity, style.limit_price))
        return []

    ns["order_shares"] = no_fill
    bars = {"000001.SZ": ClosingBar(volume=100000.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    minute(ns, context, 1, bars)
    assert attempts == [("000001.SZ", 1000, 10.)]
    minute(ns, context, 2, bars)
    assert attempts[-1] == ("000001.SZ", 1900, 10.)
    assert ns["account"].positions == {}
    assert context._lf["position_state"] == {}
    assert context._lf["daily_buy_debits"] == 0


def test_volume_in_lots_would_be_100fold_more_restrictive_without_unit_binding(monkeypatch):
    ns, context = frozen_worker(monkeypatch)
    # Same economic100000shares: a provider reporting1000lots instead of100000
    # shares cannot be treated interchangeably.  This witnesses the dependency;
    # it does not assert which unit the real worker supplies.
    minute(ns, context, 0, {"000001.SZ": ClosingBar(volume=1000.)})
    minute(ns, context, 1, {"000001.SZ": ClosingBar(volume=1000.)})
    assert ns["_lf_capacity"](context, "000001.SZ") == 10
    assert ns["calls"] == []


def test_late_reentrant_partial_fill_is_kept_in_pending_cash_and_five_slot_state(monkeypatch):
    ns, context = frozen_worker(monkeypatch)
    ns["behavior"]["action"] = "active"
    bars = {"000001.SZ": ClosingBar(volume=100000.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert ns["_lf_pending_buy_estimate"](context) == pytest.approx(10030.)
    call = ns["calls"][0]
    fill(ns, call, quantity=400)
    assert ns["account"].positions["000001.SZ"].quantity == 400
    assert context._lf["reservations"]["000001.SZ"]["actual_bought"] == 400
    assert ns["_lf_pending_buy_estimate"](context) == 0
    assert context._lf["preopen_position_count"] == 0
    assert len(context._lf["reservations"]) == 1  # Partial fill does not allocate another slot.
