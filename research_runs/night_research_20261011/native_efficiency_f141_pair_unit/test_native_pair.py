"""Synthetic EF math, native peer scope and immutable-operator checks only."""
from __future__ import annotations

import ast
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

UNIT = Path(__file__).resolve().parent
sys.path.insert(0, str(UNIT))
sys.path.insert(0, str(UNIT.parent / "native_capital_efficiency_unit"))
from native_pair import render_efficiency_f141_pair
from native_capital import render_capital_code
from test_native_capital import fixture, calculate


def z(values):
    values = pd.Series(values)
    clipped = values.clip(*values.quantile([.01, .99]).tolist())
    return (clipped - clipped.mean()) / clipped.std(ddof=0)


def test_pair_blends_true_Z_signals_with_directional_F_and_no_final_Z():
    original, e_source, prices = fixture(np.sin(np.arange(20) * .7))
    e_code = render_capital_code(original, e_source, "SyntheticE")
    pair, seen = calculate(render_efficiency_f141_pair(e_code, "SyntheticEF"), prices)
    e, _ = calculate(e_code, prices)
    f, _ = calculate(render_capital_code(original, e_source, "SyntheticF", "F141_CONTROL"), prices)
    expected = (z(np.sin(np.arange(20) * .7)) + z(-pd.Series(np.linspace(.05, .95, 20)).rank(pct=True))) / 2
    np.testing.assert_allclose(pair.iloc[:20], expected, atol=1e-13, rtol=0)
    np.testing.assert_allclose(pair.iloc[:20], (e.iloc[:20] - f.iloc[:20]) / 2, atol=1e-13, rtol=0)
    assert not np.isclose(pair.iloc[:20].std(ddof=0), 1)
    assert pair.iloc[20:].isna().all() and pair.index.equals(prices.index) and len(seen[0]) == 60


def test_only_class_and_component_change_including_raw_peer_F_and_all_sources():
    original, e_source, _ = fixture()
    e_code = render_capital_code(original, e_source, "SyntheticE")
    new_code = render_efficiency_f141_pair(e_code, "SyntheticEF")
    def without_allowed_changes(code):
        tree = ast.parse(code)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        cls.name = "SAME"
        component = next(n for n in ast.walk(cls) if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "component" for t in n.targets))
        component.value = ast.Constant(value="SAME")
        return ast.dump(tree, include_attributes=False)
    assert without_allowed_changes(e_code) == without_allowed_changes(new_code)
    assert e_code != new_code


@pytest.mark.parametrize("bad", ["E_constant", "G_constant", "E_missing"])
def test_pair_preserves_old_shared_gates_and_never_reweights_missing_leg(bad):
    original, e_source, prices = fixture(np.ones(20) if bad == "E_constant" else None)
    if bad == "G_constant":
        cls = next(n for n in ast.parse(original).body if isinstance(n, ast.ClassDef))
        node = next(n for n in cls.body if isinstance(n, ast.Assign) and n.targets[0].id == "SOURCE")
        original = original.replace(ast.get_source_segment(original, node), "SOURCE = " + repr({s: [["2025-08-14", "2025-08-14", .5]] for s in e_source}))
    elif bad == "E_missing":
        e_source.pop("000001")
    e_code = render_capital_code(original, e_source, "SyntheticE")
    pair, _ = calculate(render_efficiency_f141_pair(e_code, "SyntheticEF"), prices)
    e, _ = calculate(e_code, prices)
    assert np.array_equal(np.isfinite(pair), np.isfinite(e))
    if bad == "E_missing":
        assert pd.isna(pair.iloc[0]) and pair.iloc[1:20].notna().all()
    else:
        assert pair.isna().all()


def test_wrong_bound_mode_and_component_drift_are_rejected():
    original, e_source, _ = fixture()
    e_code = render_capital_code(original, e_source, "SyntheticE")
    with pytest.raises(ValueError, match="standalone E"):
        render_efficiency_f141_pair(e_code.replace("MODE = 'E'", "MODE = 'F141_CONTROL'"))
    with pytest.raises(ValueError, match="template drift"):
        render_efficiency_f141_pair(e_code.replace("else -signals[1]", "else signals[1]"))
