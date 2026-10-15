"""Native price representation and rejection witnesses; no market replay."""
import json

import pytest

from test_limit_finance_official_strategy import CAL, ClosingBar, Data, day, holding, minute, worker


@pytest.mark.parametrize("native_close", [4.800000190734863, 4.799999809265137])
def test_float32_legal_down_limit_tick_reaches_the_unchanged_entry_gate(monkeypatch, native_close):
    ns, context, _, _ = worker(monkeypatch, mode="control", render_matching_type=0)
    ns["initialize"](context)
    day(ns, context)
    bars = {"000001.SZ": ClosingBar(price=native_close, lower=4.8, upper=5.86)}
    minute(ns, context, 0, bars)
    assert ns["calls"] == []
    minute(ns, context, 1, bars)
    assert len(ns["calls"]) == 1
    assert ns["calls"][0]["price"] == native_close
    assert context._lf["tokens"][-1]["economic_price"] == 4.8
    assert ns["calls"][0]["quantity"] == 1000
    counts = context._lf["daily_gates"]
    assert counts["entry_prior_volume_capacity_below_one_lot"] == 1
    assert counts["entry_all_intent_gates_pass"] == counts["order_api_calls"] == 1
    assert counts["returned_order_objects"] == counts["unique_actual_trade_callbacks"] == 1


def test_next_cent_tick_stays_above_down_limit_and_true_off_tick_remains_rejected(monkeypatch):
    ns, context, _, _ = worker(monkeypatch, mode="control", render_matching_type=0)
    ns["initialize"](context)
    day(ns, context)
    bars = {"000001.SZ": ClosingBar(price=4.81, lower=4.8, upper=5.86)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert ns["calls"] == [] and ns["_lf_price"](4.81, "test") == 4.81
    with pytest.raises(RuntimeError, match="legal cent tick"):
        ns["_lf_price"](4.801, "test")
    bar = Data({"000001.SZ": ClosingBar(price=4.801, lower=4.8, upper=5.86)})
    assert ns["_lf_bar"](bar, "000001.SZ", context) is None
    assert context._lf["source_contract_valid"] is False
    assert context._lf["source_rejections"]["off_tick_or_ambiguous_price_precision"] == 1


def test_zero_native_bands_remain_rejected_and_daily_summary_explains_zero_orders(monkeypatch, capsys):
    ns, context, _, _ = worker(monkeypatch, mode="control", render_matching_type=0)
    ns["initialize"](context)
    day(ns, context)
    bars = {"000001.SZ": ClosingBar(price=4.800000190734863, lower=0., upper=0.)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    assert ns["calls"] == []
    ns["after_trading"](context)
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("LF_SOURCE_REJECT ") for line in lines) == 1
    summary = json.loads(next(line[len("LF_DAILY "):] for line in lines if line.startswith("LF_DAILY ")))
    assert summary["source_contract_valid"] is False
    assert summary["source_rejection_counts"] == {"nonpositive_or_missing_price_or_band": 2}
    assert summary["gate_and_order_counts"]["minute_callback_events"] == 2
    assert summary["gate_and_order_counts"]["observed_symbol_events"] == 2
    assert summary["gate_and_order_counts"].get("order_api_calls", 0) == 0
    assert summary["actual_trade_callbacks_today"] == 0
    day(ns, context, CAL[26])
    assert context._lf["source_contract_valid"] is True
    assert context._lf["daily_gates"] == {} and context._lf["source_rejections"] == {}


def test_empty_ioc_response_is_observed_without_fabricating_a_trade(monkeypatch):
    ns, context, _, _ = worker(monkeypatch, mode="control", render_matching_type=0)
    ns["order_shares"] = lambda *args, **kwargs: []
    ns["initialize"](context)
    day(ns, context)
    bars = {"000001.SZ": ClosingBar(price=4.8, lower=4.8, upper=5.86)}
    minute(ns, context, 0, bars)
    minute(ns, context, 1, bars)
    counters = context._lf["daily_gates"]
    assert counters["submit_attempts"] == counters["order_api_calls"] == counters["empty_order_api_returns"] == 1
    assert counters.get("unique_actual_trade_callbacks", 0) == 0
    assert context._lf["source_contract_valid"] is True
    assert ns["account"].cash == 100000. and ns["account"].positions == {}


@pytest.mark.parametrize("side", ["buy", "take_profit", "conditional_stop_loss"])
def test_same_tick_native_reference_reaches_direct_comparison_engine_without_price_chase(monkeypatch, side):
    mode = "late_confirmed" if side == "conditional_stop_loss" else "control"
    ns, context, inputs, _ = worker(monkeypatch, mode=mode, render_matching_type=0)
    ns["initialize"](context)
    if side == "buy":
        raw, economic, basis = 4.800000190734863, 4.8, None
    elif side == "take_profit":
        raw, economic, basis = 11.029999732971191, 11.03, 10.027
        holding(ns, "000001.SZ", basis=basis)
    else:
        raw, economic, basis = 9.1899995803833, 9.19, 10.
        holding(ns, "000001.SZ", basis=basis)
        inputs.features["000001.SZ"] = {"net_flow": -.1, "turnover_relative": 1.5}
    original_position = ns["account"].positions.get("000001.SZ")
    day(ns, context)
    submit = ns["order_shares"]
    attempts = []

    def directly_compared_native_order(account, symbol, quantity, style, remark):
        attempts.append((quantity, style.limit_price))
        # Retained public matcher semantics: no fuzzy/tick-normalized compare.
        if (quantity > 0 and style.limit_price < raw) or (quantity < 0 and style.limit_price > raw):
            return []
        return submit(account, symbol, quantity, style, remark)

    ns["order_shares"] = directly_compared_native_order
    bar = ClosingBar(price=raw, lower=4.8 if side == "buy" else 8., upper=15.)
    for n in (0, 1):
        minute(ns, context, n, {"000001.SZ": bar})
    assert len(ns["calls"]) == 1
    assert ns["calls"][0]["price"] == raw and bar.close == raw
    assert abs(ns["calls"][0]["quantity"]) == 1000
    token = context._lf["tokens"][-1]
    assert token["economic_price"] == economic and token["price"] == raw
    assert len(attempts) == 1 and context._lf["traded_today"]["000001.SZ"] == 1000
    if original_position is not None:
        assert original_position.avg_price == basis  # Never round a true weighted cost.


def test_takeprofit_quote_one_tick_above_target_keeps_original_target_limit(monkeypatch):
    ns, context, _, _ = worker(monkeypatch, mode="control", render_matching_type=0)
    ns["initialize"](context)
    holding(ns, "000001.SZ", basis=10.027)
    day(ns, context)
    bar = ClosingBar(price=11.039999961853027, lower=8., upper=15.)
    for n in (0, 1):
        minute(ns, context, n, {"000001.SZ": bar})
    assert ns["calls"][0]["price"] == 11.03  # Not the higher raw11.04 tick.
    assert context._lf["tokens"][-1]["economic_price"] == 11.03
