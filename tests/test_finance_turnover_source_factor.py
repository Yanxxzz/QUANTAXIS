"""Synthetic replay tests only; no real inputs, returns, account or network."""
import ast
import hashlib

import numpy as np
import pandas as pd
import pytest

from panda_alpha.finance_turnover_source_factor import (
    render_finance_turnover_source_factor, renderer_metadata,
)
from panda_alpha.native_preflight import FactorSeriesFixture


DAY = "2031-01-03"
WINDOW = {"start": DAY, "end": "2031-01-06"}


def factor(source, mode="target", **kwargs):
    code = render_finance_turnover_source_factor(source, WINDOW, mode=mode, **kwargs)
    namespace = {"Factor": type("SyntheticBase", (), {})}
    exec(compile(code, "synthetic_finance_turnover_replay.py", "exec"), namespace)
    return namespace["FinanceTurnoverSourceFactor"](), code


def close(symbols, dates=None, *, symbol_first=False, wrapper=False):
    index = pd.MultiIndex.from_arrays([dates or [DAY] * len(symbols), symbols],
                                     names=["date", "symbol"])
    if symbol_first:
        index = index.reorder_levels(["symbol", "date"])
    value = pd.Series([np.nan] * len(symbols), index=index)
    return {"close": FactorSeriesFixture(value) if wrapper else value}, index


def test_both_orderings_common_domain_and_close_is_only_index_carrier():
    source = {"000001": {DAY: [-.4, 3.]}, "000002": {DAY: [.1, 1.]},
              "000003": {DAY: [.8, 2.]}, "000004": {DAY: [np.nan, 10.]},
              "000005": {DAY: [np.inf, 0.]}, "000006": {DAY: [0., -1.]}}
    symbols = list(source) + ["000007"]
    inputs, index = close(symbols, wrapper=True)
    target, _ = factor(source)
    control, _ = factor(source, "control")
    a, b = target.calculate(inputs), control.calculate(inputs)
    assert a.index.equals(index) and b.index.equals(index)
    np.testing.assert_allclose(a.iloc[:3], [2/3, .5, 5/6])
    np.testing.assert_allclose(b.iloc[:3], [1., 1/3, 2/3])
    assert a.iloc[3:].isna().all() and b.iloc[3:].isna().all()


def test_string_and_timestamp_dates_reordered_index_and_original_projection():
    source = {"000001": {DAY: [0., 0.], "2031-01-06": [1., 1.]}}
    instance, _ = factor(source)
    for dates in (["2031-01-06", "2031-01-02", DAY],
                  pd.to_datetime(["2031-01-06", "2031-01-02", DAY]).tolist()):
        inputs, index = close(["000001"] * 3, dates, symbol_first=True)
        result = instance.calculate(inputs)
        assert result.index.equals(index.take([0, 2]))
        np.testing.assert_array_equal(result, [1., 1.])


def test_ties_are_average_rank_and_never_split_or_extended():
    source = {"000001": {DAY: [1., 2.]}, "000002": {DAY: [1., 2.]}}
    instance, _ = factor(source)
    inputs, _ = close(["000001", "000002", "000001"], [DAY, DAY, "2031-01-06"])
    result = instance.calculate(inputs)
    np.testing.assert_array_equal(result.iloc[:2], [.75, .75])
    assert pd.isna(result.iloc[2])


def test_explicit_native_gf_market_aliases_and_unknown_formats():
    source = {"000001.XSHE": {DAY: [1., 2.]}, "600001": {DAY: [2., 1.]}}
    instance, _ = factor(source, markets={"000001": "sz", "600001": "sh"})
    inputs, _ = close(["000001.SZ", "600001.XSHG", "000001.SH", "SZ000001"])
    result = instance.calculate(inputs)
    np.testing.assert_allclose(result.iloc[:2], [.75, .75])
    assert result.iloc[2:].isna().all()
    with pytest.raises(ValueError, match="market alias"):
        factor({"000001.XSHE": {DAY: [1., 2.]}})


def test_source_shape_bool_mode_and_canonical_collisions_rejected():
    bad = [{"000001": {DAY: [1.]}}, {"000001": {DAY: [True, 1.]}},
           {"000001": {DAY: [1., "2"]}},
           {"000001": {DAY: [1., 2.]}, "000001.SZ": {DAY: [1., 2.]}}]
    for source in bad:
        with pytest.raises(ValueError):
            factor(source, markets={"000001": "sz"})
    with pytest.raises(ValueError, match="Unknown"):
        factor({}, mode="best_group")
    with pytest.raises(ValueError, match="Duplicate"):
        factor({"000001": {DAY: [1., 2.], pd.Timestamp(DAY): [2., 3.]}})


def test_input_alias_or_daily_identity_duplicates_are_rejected():
    instance, _ = factor({"000001": {DAY: [1., 2.]}}, markets={"000001": "sz"})
    for symbols in (["000001", "000001"], ["000001", "000001.SZ"]):
        inputs, _ = close(symbols)
        with pytest.raises(ValueError, match="[Dd]uplicate|Unique"):
            instance.calculate(inputs)


def test_dates_and_class_identity_cannot_change_frozen_scope_or_execute_code():
    for source in ({"000001": {"2031-01-07": [1., 2.]}},
                   {"000001": {DAY + " 12:00": [1., 2.]}}):
        with pytest.raises(ValueError):
            factor(source)
    for class_name in ("class", "BadName\npass", "_Private"):
        with pytest.raises(ValueError, match="class identity"):
            factor({}, class_name=class_name)


def test_generated_source_is_one_factor_exact_fields_and_stable_file_bytes(tmp_path):
    source = {"000002": {DAY: [.2, 1.]}, "000001": {DAY: [.1, 2.]}}
    instance, code = factor(source)
    reversed_code = render_finance_turnover_source_factor(dict(reversed(list(source.items()))), WINDOW)
    assert code == reversed_code and "\r" not in code
    tree = ast.parse(code)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert len(classes) == 1 and classes[0].bases[0].id == "Factor"
    fields = {node.slice.value for node in ast.walk(tree)
              if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
              and node.value.id == "factors" and isinstance(node.slice, ast.Constant)}
    assert fields == {"close"}
    path = tmp_path / "factor.py"
    path.write_bytes(code.encode("utf-8"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(code.encode()).hexdigest()
    assert instance.REPLAY_METADATA == renderer_metadata()
    assert not instance.REPLAY_METADATA["holding_exit_strategy_included"]
    assert not instance.REPLAY_METADATA["PIT_certified"]
