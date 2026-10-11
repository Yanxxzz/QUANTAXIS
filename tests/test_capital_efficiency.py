"""Synthetic domain/identity/math checks; no real financial or price inputs."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from panda_alpha.capital_efficiency import capital_efficiency


def operands():
    index = pd.MultiIndex.from_tuples(
        [("2026-09-16", "600001"), ("2026-09-16", "600002"), ("2026-09-23", "600001")],
        names=["date", "symbol"],
    )
    return [pd.Series(values, index=index) for values in
            ([120.0, 90.0, 120.0], [100.0, 100.0, 100.0], [220.0, 200.0, 240.0], [200.0, 200.0, 200.0])]


def test_fixed_direction_sales_growth_relative_to_asset_expansion():
    args = operands()
    out = capital_efficiency(*args)
    expected = np.log(args[0] / args[1]) - np.log(args[2] / args[3])
    np.testing.assert_allclose(out, expected, atol=1e-14, rtol=0)
    assert out.iloc[0] > 0 and out.iloc[1] < 0
    assert abs(out.iloc[2]) < 1e-14
    assert out.index.equals(args[0].index) and out.name == "capital_efficiency"


@pytest.mark.parametrize("operand", range(4))
@pytest.mark.parametrize("invalid", [0.0, -1.0, np.nan, np.inf, -np.inf])
def test_each_of_four_operands_requires_positive_finite_without_imputation(operand, invalid):
    args = operands()
    args[operand].iloc[1] = invalid
    out = capital_efficiency(*args)
    assert np.isnan(out.iloc[1])
    assert np.isfinite(out.iloc[[0, 2]]).all()
    assert len(out) == 3 and out.index.equals(args[0].index)


@pytest.mark.parametrize("bad", ["order", "missing_identity", "different_identity", "index_names"])
def test_identity_alignment_errors_cannot_silently_reindex(bad):
    args = operands()
    if bad == "order":
        args[1] = args[1].iloc[::-1]
    elif bad == "missing_identity":
        args[1] = args[1].iloc[:-1]
    elif bad == "different_identity":
        args[1] = args[1].set_axis(pd.MultiIndex.from_tuples(
            [("2026-09-16", "600001"), ("2026-09-16", "600003"), ("2026-09-23", "600001")],
            names=["date", "symbol"],
        ))
    else:
        args[1] = args[1].rename_axis(index=["date", "ticker"])
    with pytest.raises(ValueError, match="identity index/order/names mismatch"):
        capital_efficiency(*args)


@pytest.mark.parametrize("index", [pd.Index(["600001", "600001", "600002"], name="symbol"),
                                   pd.Index(["600001", None, "600002"], name="symbol")])
def test_ambiguous_or_missing_identity_is_rejected(index):
    args = [value.set_axis(index) for value in operands()]
    with pytest.raises(ValueError, match="unique and nonmissing"):
        capital_efficiency(*args)


def test_independent_unit_scaling_of_each_bound_pair_cancels():
    args = operands()
    rescaled = [args[0] * 10000, args[1] * 10000, args[2] * 1e-6, args[3] * 1e-6]
    np.testing.assert_allclose(capital_efficiency(*rescaled), capital_efficiency(*args), atol=1e-14, rtol=0)


def test_equal_asset_turnover_change_is_the_same_fixed_formula():
    rc, rp, ac, ao = operands()
    expected = np.log((rc / ac) / (rp / ao))
    np.testing.assert_allclose(capital_efficiency(rc, rp, ac, ao), expected, atol=1e-14, rtol=0)


def test_argument_roles_are_not_inferred_or_swapped():
    rc, rp, ac, ao = operands()
    np.testing.assert_allclose(capital_efficiency(rp, rc, ao, ac), -capital_efficiency(rc, rp, ac, ao), atol=1e-14, rtol=0)


def test_positive_finite_extremes_need_no_overflowing_intermediate_ratio():
    index = pd.Index(["600001"], name="symbol")
    args = [pd.Series([value], index=index) for value in [1e300, 1e-300, 100.0, 100.0]]
    out = capital_efficiency(*args)
    assert np.isfinite(out.iloc[0])
    np.testing.assert_allclose(out.iloc[0], 600 * np.log(10), atol=1e-12, rtol=0)


def test_inputs_and_missing_positions_are_preserved():
    args = operands()
    args[0] = args[0].astype("Float64")
    args[0].iloc[1] = pd.NA
    original = [value.copy(deep=True) for value in args]
    out = capital_efficiency(*args)
    assert np.isnan(out.iloc[1])
    for actual, expected in zip(args, original):
        pd.testing.assert_series_equal(actual, expected)
