"""Synthetic report-identity/native-operand tests; no actual source reads."""
from __future__ import annotations

import ast
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_capital import FINANCIAL_KEY, canonical_amounts, project_capital_source, render_capital_code
from panda_alpha.native_gf_conservative import render_source_conservative
from panda_alpha.native_preflight import FactorSeriesFixture, _FactorFixture


def fixture(e_values=None):
    day = "2025-08-14"
    source = {f"{i:06d}": [[day, day, i / 20.0]] for i in range(1, 21)}
    E = np.linspace(-.3, .4, 20) if e_values is None else e_values
    e_source = {symbol: [[day, day, float(value)]] for symbol, value in zip(source, E)}
    original = render_source_conservative(source, {s: "sh" for s in source}, [day], {"start": day, "end": day}, "GF", "OriginalSyntheticGF")
    index = pd.MultiIndex.from_product([[pd.Timestamp(day)], [f"{i:06d}" for i in range(1, 61)]], names=["date", "symbol"])
    prices = pd.Series(np.arange(60) + 100., index=index)
    return original, e_source, prices


def calculate(code, prices):
    cls = next(n for n in ast.parse(code).body if isinstance(n, ast.ClassDef))
    namespace = {"Factor": _FactorFixture, "__name__": "synthetic_capital"}
    exec(compile(code, "<synthetic_capital>", "exec"), namespace)
    instance = namespace[cls.name]()
    seen = []
    def share(wide):
        seen.append(list(wide.columns))
        return pd.DataFrame(np.broadcast_to(np.linspace(.05, .95, len(wide.columns)), wide.shape), index=wide.index, columns=wide.columns)
    instance._peer_share = share
    return instance.calculate({"close": FactorSeriesFixture(prices)}), seen


def test_actual_raw_E_alone_and_all_price_peers_before_source_filter():
    original, e_source, prices = fixture()
    out, seen = calculate(render_capital_code(original, e_source, "SyntheticE"), prices)
    E = pd.Series(np.linspace(-.3, .4, 20))
    clipped = E.clip(*E.quantile([.01, .99]).tolist())
    expected = (clipped - clipped.mean()) / clipped.std(ddof=0)
    np.testing.assert_allclose(out.iloc[:20], expected, atol=1e-13, rtol=0)
    assert out.iloc[20:].isna().all() and len(seen[0]) == 60
    assert out.index.equals(prices.index)


def test_old_SOURCE_and_native_peer_method_are_unchanged():
    original, e_source, _ = fixture()
    new = render_capital_code(original, e_source, "SyntheticE")
    def parts(code):
        cls = next(n for n in ast.parse(code).body if isinstance(n, ast.ClassDef))
        source = next(ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign) and n.targets[0].id == "SOURCE")
        peer = next(ast.dump(n) for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_peer_share")
        return source, peer
    assert parts(original) == parts(new)


@pytest.mark.parametrize("bad", ["E_constant", "G_constant", "E_missing"])
def test_E_has_no_dropped_component_reweight_or_false_finite_rows(bad):
    original, e_source, prices = fixture(np.ones(20) if bad == "E_constant" else None)
    if bad == "G_constant":
        cls = next(n for n in ast.parse(original).body if isinstance(n, ast.ClassDef))
        node = next(n for n in cls.body if isinstance(n, ast.Assign) and n.targets[0].id == "SOURCE")
        original = original.replace(ast.get_source_segment(original, node), "SOURCE = " + repr({s: [["2025-08-14", "2025-08-14", .5]] for s in e_source}))
    elif bad == "E_missing":
        e_source.pop("000001")
    out, _ = calculate(render_capital_code(original, e_source, "SyntheticE"), prices)
    control, _ = calculate(render_capital_code(original, e_source, "SyntheticF", "F141_CONTROL"), prices)
    assert np.array_equal(np.isfinite(out), np.isfinite(control))
    if bad == "E_missing":
        assert pd.isna(out.iloc[0]) and out.iloc[1:20].notna().all()
    else:
        assert out.isna().all()


def test_financial_exclusion_is_exact_report_identity_and_not_entire_issuer():
    first = {"symbol": "600001", "announcement_id": "a", "report_date": "2024-12-31", "source_record_sha256": "a" * 64}
    second = {"symbol": "600001", "announcement_id": "b", "report_date": "2025-12-31", "source_record_sha256": "b" * 64}
    source = pd.DataFrame([{**first, "date": "2025-08-14"}, {**second, "date": "2026-08-26"}])
    amounts = pd.DataFrame([{**first, "operating_revenue_1": 120, "operating_revenue_0": 100, "total_assets_1": 220, "total_assets_0": 200},
                            {**second, "operating_revenue_1": 100, "operating_revenue_0": 100, "total_assets_1": 200, "total_assets_0": 200}])
    selected = project_capital_source(source, amounts, [first])
    assert len(selected) == 1 and selected.iloc[0].announcement_id == "a"
    np.testing.assert_allclose(selected.E_raw, np.log(1.2) - np.log(1.1), atol=1e-14)
    amounts.loc[0, "operating_revenue_0"] = 0
    with pytest.raises(ValueError, match="no implicit extra exclusion"):
        project_capital_source(source, amounts, [first])


def test_original_amount_file_code_schema_is_explicit_and_not_an_identity_or_role_swap():
    record = {"code": "000001", "announcement_id": "a", "report_date": "2024-12-31", "source_record_sha256": "a" * 64,
              "operating_revenue_1": 120, "operating_revenue_0": 100, "total_assets_1": 220, "total_assets_0": 200}
    raw = pd.DataFrame([record])
    original = raw.copy(deep=True)
    amounts = canonical_amounts(raw)
    assert amounts.iloc[0].symbol == "000001" and "code" not in amounts.columns
    for column in ["announcement_id", "report_date", "source_record_sha256", "operating_revenue_1", "operating_revenue_0", "total_assets_1", "total_assets_0"]:
        assert amounts.iloc[0][column] == raw.iloc[0][column]
    pd.testing.assert_frame_equal(raw, original)
    with pytest.raises(ValueError, match="declared code-based schema"):
        canonical_amounts(raw.assign(symbol="999999"))
