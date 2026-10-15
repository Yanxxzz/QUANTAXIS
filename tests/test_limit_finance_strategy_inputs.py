"""Input and causal-clock checks; no account/API calls or strategy orders.

The three 000001 values are the published official doc37 examples independently
matched in finance_repayment_role_receipt.json, not a balance-identity inference.
"""
import math

import pandas as pd
import pytest

from panda_alpha.limit_finance_strategy_inputs import StrategyInputs, load_inputs


PUBLIC_EXAMPLES = {
    "2025-01-02": {"SCODE": "000001", "DATE": "2025-01-02 00:00:00", "RZYE": 4659469716, "RZMRE": 359418283, "RZCHE": 280885137},
    "2025-01-03": {"SCODE": "000001", "DATE": "2025-01-03 00:00:00", "RZYE": 4669652607, "RZMRE": 169639270, "RZCHE": 159456379},
    "2025-01-06": {"SCODE": "000001", "DATE": "2025-01-06 00:00:00", "RZYE": 4669211757, "RZMRE": 155556810, "RZCHE": 155997660},
}


class DocumentedDataFixture:
    def __init__(self):
        self.calendar = pd.bdate_range("2024-12-02", "2025-03-31").strftime("%Y-%m-%d").tolist()
        self.calls = []
        rows = []
        for symbol in ("000001.SZ", "600006.SH"):
            for index, day in enumerate(self.calendar):
                fact = PUBLIC_EXAMPLES.get(day) if symbol == "000001.SZ" else None
                rows.append({"symbol": symbol, "date": day,
                             "margin_balance": fact["RZYE"] if fact else 1000 + index,
                             "buy_on_margin_value": fact["RZMRE"] if fact else 200 + index,
                             "margin_repayment": fact["RZCHE"] if fact else 100,
                             "turnover": 1 + index / 10,
                             "close": 10 + index / 100})
        self.frame = pd.DataFrame(rows)
        self.missing = None
        self.bad_date = False
        self.conflict = False
        self.validation_error = False

    def get_margin(self, **kwargs):
        self.calls.append(("margin", kwargs))
        assert kwargs["margin_type"] == "cash"
        result = self._select(kwargs, ["symbol", "date", "margin_balance", "buy_on_margin_value", "margin_repayment"])
        if self.missing:
            result = result.drop(columns=self.missing)
        if self.bad_date:
            result.loc[result.index[0], "date"] = "2025-02-30"
        if self.validation_error:
            result.loc[result.index[0], "margin_repayment"] += 1
        if self.conflict:
            duplicate = result.iloc[[0]].copy()
            duplicate["margin_balance"] += 1
            result = pd.concat([result, duplicate])
        return result

    def get_factor(self, **kwargs):
        self.calls.append(("factor", kwargs))
        assert kwargs["factors"] == ["turnover"]
        return self._select(kwargs, ["symbol", "date", "turnover"]).set_index(["date", "symbol"])

    def get_stock_daily(self, **kwargs):
        self.calls.append(("daily", kwargs))
        assert "adjust" not in kwargs and "adj_type" not in kwargs
        return self._select(kwargs, ["symbol", "date", "close"])

    def _select(self, kwargs, columns):
        requested = kwargs["symbol"]
        if isinstance(requested, str):
            requested = [requested]
        first = pd.Timestamp(kwargs["start_date"]).strftime("%Y-%m-%d")
        last = pd.Timestamp(kwargs["end_date"]).strftime("%Y-%m-%d")
        return self.frame.loc[self.frame.symbol.isin(requested) & self.frame.date.between(first, last), columns].copy()


def load(fixture, **kwargs):
    return load_inputs(fixture, ["000001", "600006"], "2025-01-02", "2025-02-28", PUBLIC_EXAMPLES,
                       reference_calendar=fixture.calendar, **kwargs)


def test_public_three_examples_pass_before_any_other_input_query():
    fixture = DocumentedDataFixture()
    result = load(fixture)
    assert result.validation_count == 3
    label, first = fixture.calls[0]
    assert label == "margin" and first["symbol"] == "000001.SZ"
    assert first["start_date"] == "20250102" and first["end_date"] == "20250106"
    assert result.financing["000001.SZ", "2025-01-02"]["RZCHE"] == 280885137
    assert result.daily_close["600006.SH", "2025-01-02"] > 0


def test_repayment_value_mismatch_fails_before_bulk_loading():
    fixture = DocumentedDataFixture()
    fixture.validation_error = True
    with pytest.raises(RuntimeError, match="validation failed"):
        load(fixture)
    assert len(fixture.calls) == 1


@pytest.mark.parametrize("field", ["margin_balance", "buy_on_margin_value", "margin_repayment"])
def test_missing_required_column_fails_without_empty_result(field):
    fixture = DocumentedDataFixture()
    fixture.missing = field
    with pytest.raises(RuntimeError, match="missing required columns"):
        load(fixture)
    assert len(fixture.calls) == 1


def test_invalid_date_is_rejected_not_coerced_to_missing():
    fixture = DocumentedDataFixture()
    fixture.bad_date = True
    with pytest.raises(ValueError, match="Malformed PandaData date"):
        load(fixture)


def test_conflicting_duplicate_identity_cannot_pick_latest():
    fixture = DocumentedDataFixture()
    fixture.conflict = True
    with pytest.raises(RuntimeError, match="conflicting duplicate"):
        load(fixture)


def test_exact_duplicate_identity_is_deduplicated():
    fixture = DocumentedDataFixture()
    fixture.frame = pd.concat([fixture.frame, fixture.frame.iloc[[0]]], ignore_index=True)
    result = load(fixture)
    assert len(result.daily_close) == len(set(result.daily_close))


def test_features_end_t_minus_two_and_ignore_later_finance_and_turnover():
    fixture = DocumentedDataFixture()
    day = "2025-02-14"
    index = fixture.calendar.index(day)
    ignored_dates = fixture.calendar[index - 1:]
    marker = fixture.frame.symbol.eq("600006.SH") & fixture.frame.date.isin(ignored_dates)
    fixture.frame.loc[marker, ["buy_on_margin_value", "margin_repayment", "turnover"]] = [0, 999999, 999999]
    result = load(fixture)
    actual = result.features_for("600006", index)
    fd = fixture.calendar[index - 7:index - 1]
    td = fixture.calendar[index - 21:index - 1]
    expected_fin = [result.financing["600006.SH", d] for d in fd]
    expected_flow = sum(r["RZMRE"] - r["RZCHE"] for r in expected_fin[1:]) / sum(r["RZMRE"] + r["RZCHE"] for r in expected_fin[1:])
    values = [result.turnover["600006.SH", d] for d in td]
    assert actual["net_flow"] == expected_flow > 0
    assert actual["turnover_relative"] == (sum(values[-5:]) / 5) / (sum(values) / 20)
    assert actual["latest_report_date"] == fixture.calendar[index - 2]
    assert actual["execution_date"] == day
    assert result.asoffeatures(index)["600006.SH"] == actual


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_invalid_financing_amount_stays_unknown_without_zero(bad):
    fixture = DocumentedDataFixture()
    index = fixture.calendar.index("2025-02-14")
    bad_day = fixture.calendar[index - 2]
    marker = fixture.frame.symbol.eq("600006.SH") & fixture.frame.date.eq(bad_day)
    fixture.frame.loc[marker, "margin_repayment"] = bad
    result = load(fixture)
    assert result.financing["600006.SH", bad_day]["RZCHE"] is None
    assert result.features_for("600006", index)["net_flow"] is None


def test_missing_turnover_does_not_become_zero_or_kill_valid_financing():
    fixture = DocumentedDataFixture()
    index = fixture.calendar.index("2025-02-14")
    marker = fixture.frame.symbol.eq("600006.SH") & fixture.frame.date.eq(fixture.calendar[index - 2])
    fixture.frame.loc[marker, "turnover"] = float("nan")
    actual = load(fixture).features_for("600006", index)
    assert actual["turnover_relative"] is None and actual["net_flow"] > 0


def test_calendar_mismatch_blocks_run_and_warmup_has_no_fabricated_values():
    fixture = DocumentedDataFixture()
    result = load(fixture)
    actual = [d for d in fixture.calendar if "2025-01-02" <= d <= "2025-02-28"]
    assert result.validate_run_calendar(actual, "2025-01-02", "2025-02-28")
    with pytest.raises(RuntimeError, match="calendar differs"):
        result.validate_run_calendar(actual[:-1], "2025-01-02", "2025-02-28")
    assert result.features_for("600006", 5)["net_flow"] is None


def test_feature_result_copy_prevents_mutating_cache():
    fixture = DocumentedDataFixture()
    result = load(fixture)
    index = fixture.calendar.index("2025-02-14")
    actual = result.features_for("600006", index)
    actual["net_flow"] = -100
    assert result.features_for("600006", index)["net_flow"] > 0
