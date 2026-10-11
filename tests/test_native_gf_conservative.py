import hashlib

import numpy as np
import pandas as pd
import pytest

from panda_alpha.native_gf_conservative import intersect_and_rank_g, render_source_conservative
from panda_alpha.native_gf_transfer import render_native_gf
from panda_alpha.risk_relation import relation_states
from test_native_gf_transfer import example, reference


def generated(source, close, window, mode):
    name = "GFConservative" if mode == "GF" else "GConservative" if mode == "g" else "FConservative"
    code = render_source_conservative(source, {c: "sh" for c in source},
                                     close.index.strftime("%Y-%m-%d").tolist(),
                                     window, mode, name)
    namespace = {"Factor": object}
    exec(compile(code, "<self-contained-conservative-pair>", "exec"), namespace)
    return namespace[name]


def test_exact_parent_identity_and_new_financial_rank_base():
    source = pd.DataFrame({
        "date": ["2025-01-02"] * 4, "symbol": ["600000", "600001", "600002", "600003"],
        "announcement_id": ["a", "b", "c", "d"], "report_date": ["2024-12-31"] * 4,
        "source_record_sha256": ["sha-a", "sha-b", "sha-c", "sha-d"],
        "pit_usable": [True] * 4, "sector": ["A"] * 4,
        "GP_delta": [.1, .2, .3, .4], "G_pct": [.25, .5, .75, 1.],
    })
    parent = source.drop(columns=["sector", "GP_delta", "G_pct"]).copy()
    parent["source_status"] = "ready"
    parent["blocking_event_id"] = ""
    parent.loc[1, "pit_usable"] = False
    parent.loc[1, "source_status"] = "known_six_field_revision_scope_pending"
    # Wrong SHA cannot restore the report even with same date/code/annid/year.
    parent.loc[3, "source_record_sha256"] = "different-bytes"
    actual, review = intersect_and_rank_g(source, parent)
    assert actual.symbol.tolist() == ["600000", "600002"]
    np.testing.assert_array_equal(actual.G_pct, [.5, 1.])
    assert review["exact_identity_missing"] == 1
    assert review["excluded_rows"] == 2
    with pytest.raises(ValueError, match="unique"):
        intersect_and_rank_g(source, pd.concat([parent, parent.iloc[:1]]))


def test_pair_same_finite_support_and_fixed_G_F_math():
    close, source, window, _ = example()
    close.iloc[285, 0] = np.nan
    price = close.rename_axis(index="date", columns="symbol").stack(dropna=False)
    gf = generated(source, close, window, "GF")().calculate({"close": price})
    control = generated(source, close, window, "F141_CONTROL")().calculate({"close": price})
    assert gf.index.equals(control.index)
    np.testing.assert_array_equal(np.isfinite(gf), np.isfinite(control))
    np.testing.assert_allclose(gf, reference(close, source, window), rtol=0, atol=2e-13, equal_nan=True)
    share = relation_states(close)["F141"]
    for day in gf.index.get_level_values("date").unique():
        finite = control.xs(day).dropna()
        values = share.loc[day, finite.index].rank(method="average", pct=True)
        clipped = values.clip(*values.quantile([.01, .99]).tolist())
        expected = (clipped - clipped.mean()) / clipped.std(ddof=0)
        np.testing.assert_allclose(finite, expected, rtol=0, atol=2e-13)


def test_control_does_not_escape_constant_G_or_F_variance_gate():
    close, source, window, _ = example()
    constant = {c: [[window["start"], window["end"], .5]] for c in source}
    price = close.rename_axis(index="date", columns="symbol").stack(dropna=False)
    for mode in ["GF", "F141_CONTROL", "g"]:
        assert generated(constant, close, window, mode)().calculate({"close": price}).isna().all()
        cls = generated(source, close, window, mode)
        # Fixed financial variance is valid, but constant F cannot be used by
        # either mode; no dropped component reweight for the comparator.
        cls._peer_share = staticmethod(lambda matrix: matrix * 0 + .5)
        assert cls().calculate({"close": price}).isna().all()


def test_duplicate_nonfinancial_peer_alias_rejected_and_alias_epochs_canonical():
    close, source, window, _ = example()
    cls = generated(source, close, window, "GF")
    price = close.rename_axis(index="date", columns="symbol").stack(dropna=False)
    outside = close.columns[-1]
    extra = price[price.index.get_level_values("symbol") == outside].iloc[[0]].copy()
    extra.index = pd.MultiIndex.from_arrays([extra.index.get_level_values("date"), [outside + ".SH"]],
                                          names=["date", "symbol"])
    with pytest.raises(ValueError, match="canonical native aliases"):
        cls().calculate({"close": pd.concat([price, extra])})
    # An outside-source peer changing representational suffix must remain one
    # economic price history, rather than two incomplete model peers.
    alias = price.copy()
    symbols = alias.index.get_level_values("symbol").to_numpy().copy()
    use = ((alias.index.get_level_values("date") > close.index[160]) & (symbols == outside))
    symbols[use] = outside + ".SH"
    alias.index = pd.MultiIndex.from_arrays([alias.index.get_level_values("date"), symbols],
                                          names=["date", "symbol"])
    actual = cls().calculate({"close": alias})
    original = cls().calculate({"close": price})
    np.testing.assert_allclose(actual.to_numpy(), original.to_numpy(), rtol=0, atol=2e-13, equal_nan=True)


def test_render_rejects_unknown_mode_and_template_drift(monkeypatch):
    close, source, window, _ = example()
    with pytest.raises(ValueError, match="mode"):
        generated(source, close, window, "OPTIONAL_REWEIGHT")
    import panda_alpha.native_gf_conservative as module
    baseline = module.render_native_gf
    monkeypatch.setattr(module, "render_native_gf", lambda *a, **kw: baseline(*a, **kw) +
                        "\n    def _source_code(self, symbol):\n")
    with pytest.raises(ValueError, match="template drifted"):
        generated(source, close, window, "GF")



def test_g_mode_is_true_winsorized_G_on_exact_shared_support():
    close, source, window, _ = example()
    close.iloc[285, 0] = np.nan
    price = close.rename_axis(index="date", columns="symbol").stack(dropna=False)
    outputs = {mode: generated(source, close, window, mode)().calculate({"close": price})
               for mode in ["GF", "F141_CONTROL", "g"]}
    g = outputs["g"]
    pd.testing.assert_index_equal(g.index, outputs["GF"].index)
    np.testing.assert_array_equal(np.isfinite(g), np.isfinite(outputs["GF"]))
    np.testing.assert_array_equal(np.isfinite(g), np.isfinite(outputs["F141_CONTROL"]))
    for day in g.index.get_level_values("date").unique():
        actual = g.xs(day).dropna()
        values = pd.Series({c: source[c][0][2] for c in actual.index})
        clipped = values.clip(*values.quantile([.01, .99]).tolist())
        expected = (clipped - clipped.mean()) / clipped.std(ddof=0)
        # True component output, not a cancellation-based reconstruction.
        np.testing.assert_array_equal(actual.to_numpy(), expected.to_numpy())
    np.testing.assert_allclose(g, 2*outputs["GF"]+outputs["F141_CONTROL"], rtol=0, atol=2e-13, equal_nan=True)


def test_true_g_ties_cannot_be_split_by_F_or_cancellation_noise():
    close, source, window, _ = example()
    tied = {c: [[window["start"], window["end"], (.25,.5,.75,1.)[i%4]]]
            for i,c in enumerate(source)}
    price = close.rename_axis(index="date", columns="symbol").stack(dropna=False)
    g = generated(tied, close, window, "g")().calculate({"close": price})
    gf = generated(tied, close, window, "GF")().calculate({"close": price})
    f = generated(tied, close, window, "F141_CONTROL")().calculate({"close": price})
    assert not g.dropna().empty
    for day in g.index.get_level_values("date").unique():
        actual = g.xs(day).dropna()
        assert actual.nunique() == 4
        with pytest.raises(ValueError, match="unique"):
            pd.qcut(actual, 10, labels=False, duplicates="raise")
    # Arithmetic reconstruction is allowed only as a tolerant math reference.
    np.testing.assert_allclose(g, 2*gf+f, rtol=0, atol=2e-13, equal_nan=True)
    assert gf.groupby(level="date").nunique().min() >= 10


def test_g_and_pair_rendering_keep_original_GF_F_code_bytes():
    close, source, window, _ = example()
    expected = {"GF": "da07b43d901830d6cc1ebb6e9fd46206c1d76c34f871b80e12b4e1a6f5740a40", "F141_CONTROL": "4c348f38461c318bf3dc70c937af513e2a34424ab2d6d98ce18e852032a1ea0d"}
    for mode,name in [("GF","GFConservative"),("F141_CONTROL","FConservative")]:
        code = render_source_conservative(source,{c:"sh" for c in source},
                                         close.index.strftime("%Y-%m-%d").tolist(),window,mode,name)
        assert hashlib.sha256(code.encode()).hexdigest() == expected[mode]
