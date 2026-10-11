import ast
import hashlib

import numpy as np
import pandas as pd
import pytest

from panda_alpha.native_gf_conservative import render_source_conservative
from panda_alpha.native_product_gf import bounded_product_reference, render_product_from_midpoint_code
from panda_alpha.risk_relation import relation_states
from test_native_gf_transfer import example


def native(code, name):
    namespace = {"Factor": object}
    exec(compile(code, "<synthetic-product-native>", "exec"), namespace)
    return namespace[name]


def toy_source(close, source, window):
    # Synthetic midpoint CDF values with genuine repeats, independent of prices.
    return {code: [[window["start"], window["end"], (.1, .3, .5, .7, .9)[i % 5]]]
            for i, code in enumerate(source)}


def test_bounded_joint_product_is_positive_and_average_ties_prefer_low_raw_F():
    frame = pd.DataFrame({"date": ["2026-08-26"] * 4, "symbol": list("abcd"),
                          "G_pct": [.2, .4, .8, .9], "F_share": [.2, .2, .7, .9]})
    result = bounded_product_reference(frame)
    np.testing.assert_array_equal(result, frame.G_pct * pd.Series([.875, .875, .5, .25]))
    assert result.between(0, 1, inclusive="neither").all()


def test_raw_F_tail_order_is_not_recovered_from_winsorized_F_Z():
    frame = pd.DataFrame({"date": ["2026-08-26"] * 101,
                          "symbol": [str(i) for i in range(101)],
                          "G_pct": [.5, .5] + [.2 + .006 * i for i in range(99)],
                          "F_share": np.arange(101, dtype=float) / 1000})
    f_pct = frame.F_share.rank(pct=True)
    clipped = f_pct.clip(*f_pct.quantile([.01, .99]).tolist())
    assert clipped.iloc[0] == clipped.iloc[1]
    actual = bounded_product_reference(frame)
    assert actual.iloc[0] == .5
    assert actual.iloc[1] == .5 * 100 / 101
    assert actual.iloc[0] > actual.iloc[1]


def test_native_product_uses_all_peers_and_matches_reference_shared_mask():
    close, source, window, _ = example()
    source = toy_source(close, source, window)
    close.iloc[285, 0] = np.nan
    midpoint = render_source_conservative(source, {c: "sh" for c in source},
        close.index.strftime("%Y-%m-%d").tolist(), window, "GF", "MIDNative")
    product = render_product_from_midpoint_code(midpoint, "ProductNative")
    price = close.rename_axis(index="date", columns="symbol").stack(dropna=False)
    actual = native(product, "ProductNative")().calculate({"close": price})
    old = native(midpoint, "MIDNative")().calculate({"close": price})
    np.testing.assert_array_equal(np.isfinite(actual), np.isfinite(old))
    pd.testing.assert_index_equal(actual.index, old.index)
    share = relation_states(close)["F141"]
    rows = actual.reset_index().drop(columns="value")
    rows["G_pct"] = [source[c][0][2] if c in source else np.nan for c in rows.symbol]
    rows["F_share"] = [share.loc[d, c] for d, c in zip(rows.date, rows.symbol)]
    expected = bounded_product_reference(rows)
    np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy(), rtol=0, atol=2e-13, equal_nan=True)
    assert (actual.dropna() > 0).all() and (actual.dropna() < 1).all()
    assert actual.xs(close.index[285]).loc[close.columns[0]] != actual.xs(close.index[285]).loc[close.columns[0]]


@pytest.mark.parametrize("constant_leg", ["G", "F"])
def test_product_keeps_both_original_variance_gates(constant_leg):
    close, source, window, _ = example()
    source = toy_source(close, source, window)
    if constant_leg == "G":
        source = {c: [[window["start"], window["end"], .5]] for c in source}
    midpoint = render_source_conservative(source, {c: "sh" for c in source},
        close.index.strftime("%Y-%m-%d").tolist(), window, "GF", "MIDNative")
    cls = native(render_product_from_midpoint_code(midpoint, "ProductNative"), "ProductNative")
    if constant_leg == "F":
        cls._peer_share = staticmethod(lambda matrix: matrix * 0 + .5)
    price = close.rename_axis(index="date", columns="symbol").stack(dropna=False)
    assert cls().calculate({"close": price}).isna().all()


def test_product_true_operand_ties_remain_tied_and_quantiles_fail_without_jitter():
    frame = pd.DataFrame({"date": ["2026-08-26"] * 40,
                          "symbol": [str(i) for i in range(40)],
                          "G_pct": [.25] * 20 + [.75] * 20,
                          "F_share": [.1] * 20 + [.9] * 20})
    result = bounded_product_reference(frame)
    assert result.nunique() == 2
    with pytest.raises(ValueError, match="unique"):
        pd.qcut(result, 10, labels=False, duplicates="raise")


def test_wrapper_preserves_SOURCE_and_all_default_renderer_bytes():
    close, source, window, _ = example()
    source = toy_source(close, source, window)
    calendar = close.index.strftime("%Y-%m-%d").tolist()
    originals = {mode: render_source_conservative(source, {c: "sh" for c in source},
        calendar, window, mode, "Original" + mode.replace("_", "")) for mode in ("GF", "F141_CONTROL", "g")}
    before = {mode: hashlib.sha256(code.encode()).hexdigest() for mode, code in originals.items()}
    product = render_product_from_midpoint_code(originals["GF"], "ProductNative")
    def source_literal(code):
        tree = ast.parse(code)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        return next(ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "SOURCE" for t in n.targets))
    assert source_literal(product) == source_literal(originals["GF"])
    assert {mode: hashlib.sha256(code.encode()).hexdigest() for mode, code in originals.items()} == before
    for mode in originals:
        fresh = render_source_conservative(source, {c: "sh" for c in source}, calendar,
            window, mode, "Original" + mode.replace("_", ""))
        assert fresh == originals[mode]
    roots = {alias.name.split(".")[0] for n in ast.walk(ast.parse(product)) if isinstance(n, ast.Import) for alias in n.names}
    assert roots == {"numpy", "pandas"}


def test_wrapper_rejects_template_drift_instead_of_changing_other_code():
    close, source, window, _ = example()
    midpoint = render_source_conservative(toy_source(close, source, window), {c: "sh" for c in source},
        close.index.strftime("%Y-%m-%d").tolist(), window, "GF", "MIDNative")
    with pytest.raises(ValueError, match="template drifted"):
        render_product_from_midpoint_code(midpoint.replace("component = ((signals[0]", "drift = ((signals[0]"), "ProductNative")
