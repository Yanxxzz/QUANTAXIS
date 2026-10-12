"""Synthetic algebra/time/availability tests, not empirical factor evidence."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def factor_class():
    path = Path(__file__).resolve().parents[1] / "panda_alpha" / "native_liquidity_beta.py"
    namespace = {"Factor": type("SyntheticFactorBase", (), {}), "__file__": str(path)}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace["MarketCostShockProtection"]


def synthetic_inputs(periods=220, symbols=40):
    rng = np.random.default_rng(210413)
    dates = pd.bdate_range("2031-01-02", periods=periods)
    names = [f"{n+1:06d}" for n in range(symbols)]
    common = rng.normal(0, .009, (periods, 1))
    returns = common * rng.uniform(.7, 1.4, (1, symbols)) + rng.normal(0, .008, (periods, symbols))
    prices = (100 * np.cumprod(1 + returns, axis=0))
    amount = rng.lognormal(19, .55, (periods, symbols)) * np.exp(rng.normal(0, .3, (periods, 1)))
    index = pd.MultiIndex.from_product([dates, names], names=["date", "symbol"])
    return {"close": pd.Series(prices.ravel(), index=index, name="close"),
            "amount": pd.Series(amount.ravel(), index=index, name="amount")}


@pytest.fixture
def inputs():
    return synthetic_inputs()


def test_beta_matches_numpy_dual_regressor_ols_with_intercept(factor_class, inputs):
    factor = factor_class()
    model = factor._model_inputs(inputs)
    output = factor.calculate(inputs)
    date = inputs["close"].index.get_level_values("date").max()
    for symbol in ["000001", "000013", "000040"]:
        data = model.xs(symbol, level="symbol").sort_index().tail(120)
        assert np.isfinite(data).all(axis=None)
        design = np.column_stack([np.ones(120), data.market, data.cost_shock])
        coefficients = np.linalg.lstsq(design, data.r, rcond=None)[0]
        assert output.loc[(date, symbol)] == pytest.approx(coefficients[2], rel=1e-9, abs=1e-12)
    assert output.index.equals(inputs["close"].index)


def test_shock_uses_previous_twenty_cost_days_and_excludes_current_day(factor_class, inputs):
    close = inputs["close"]
    returns = close.groupby(level="symbol").pct_change(fill_method=None)
    impact = returns.abs() / (inputs["amount"] / 1e8)
    medians = impact.groupby(level="date").median().sort_index()
    dates = medians.index
    when = dates[80]
    expected = np.log(medians.loc[when]) - np.log(medians.iloc[60:80]).mean()
    model = factor_class._model_inputs(inputs)
    assert model.xs(when, level="date").cost_shock.to_numpy() == pytest.approx(np.repeat(expected, 40))
    changed = {k: v.copy() for k, v in inputs.items()}
    current = changed["amount"].index.get_level_values("date") == when
    changed["amount"].loc[current] *= np.exp(2)
    changed_model = factor_class._model_inputs(changed)
    assert changed_model.xs(when, level="date").cost_shock.iloc[0] == pytest.approx(expected - 2)
    assert model.xs(dates[20], level="date").cost_shock.isna().all()
    assert model.xs(dates[21], level="date").cost_shock.notna().all()


def test_future_price_and_amount_change_never_rewrites_past_beta(factor_class, inputs):
    factor = factor_class()
    before = factor.calculate(inputs)
    dates = inputs["close"].index.get_level_values("date")
    cutoff = sorted(set(dates))[175]
    changed = {k: v.copy() for k, v in inputs.items()}
    future = dates > cutoff
    changed["close"].loc[future] *= np.linspace(.3, 3, int(future.sum()))
    changed["amount"].loc[future] *= np.linspace(.1, 5, int(future.sum()))
    after = factor.calculate(changed)
    pd.testing.assert_series_equal(before.loc[dates <= cutoff], after.loc[dates <= cutoff])
    assert before.loc[dates <= cutoff].notna().any()


def test_constant_amount_unit_scaling_leaves_cost_shock_and_beta_unchanged(factor_class, inputs):
    scaled = {"close": inputs["close"], "amount": inputs["amount"] * 10000}
    original_model = factor_class._model_inputs(inputs)
    scaled_model = factor_class._model_inputs(scaled)
    np.testing.assert_allclose(original_model.cost_shock, scaled_model.cost_shock,
                               rtol=1e-10, atol=1e-12, equal_nan=True)
    original = factor_class().calculate(inputs)
    alternate = factor_class().calculate(scaled)
    np.testing.assert_allclose(original, alternate, rtol=1e-9, atol=1e-11, equal_nan=True)


def test_missing_amount_day_stays_missing_and_invalidates_complete_regression_window(factor_class, inputs):
    changed = {k: v.copy() for k, v in inputs.items()}
    dates = changed["amount"].index.get_level_values("date")
    when = sorted(set(dates))[180]
    changed["amount"].loc[dates == when] = np.nan
    model = factor_class._model_inputs(changed)
    assert model.xs(when, level="date").cost_shock.isna().all()
    output = factor_class().calculate(changed)
    assert output.loc[dates >= when].isna().all()
    assert output.loc[dates < when].notna().any()


def test_missing_individual_amount_excluded_from_cost_proxy_not_zero_imputed(factor_class, inputs):
    changed = {k: v.copy() for k, v in inputs.items()}
    dates = changed["amount"].index.get_level_values("date")
    when = sorted(set(dates))[80]
    changed["amount"].loc[(when, "000001")] = np.nan
    close = changed["close"]
    returns = close.groupby(level="symbol").pct_change(fill_method=None)
    impact = returns.abs() / (changed["amount"] / 1e8)
    assert np.isnan(impact.loc[(when, "000001")])
    median = impact.groupby(level="date").median().sort_index()
    expected = np.log(median.loc[when]) - np.log(median.iloc[60:80]).mean()
    model = factor_class._model_inputs(changed)
    assert model.xs(when, level="date").cost_shock.iloc[0] == pytest.approx(expected)


def test_zero_median_cost_is_unknown_not_zero_cost_shock(factor_class, inputs):
    changed = {k: v.copy() for k, v in inputs.items()}
    dates = changed["close"].index.get_level_values("date")
    reference = sorted(set(dates))
    when = reference[180]
    previous = changed["close"].xs(reference[179], level="date")
    changed["close"].loc[dates == when] = previous.to_numpy()
    model = factor_class._model_inputs(changed)
    assert model.xs(when, level="date").r.eq(0).all()
    assert model.xs(when, level="date").cost_shock.isna().all()
    assert factor_class().calculate(changed).loc[dates == when].isna().all()


@pytest.mark.parametrize("bad_amount", [0, -1, np.inf])
def test_invalid_amount_does_not_enter_positive_cost_proxy(factor_class, inputs, bad_amount):
    changed = {"close": inputs["close"], "amount": inputs["amount"] * 0 + bad_amount}
    assert factor_class._model_inputs(changed).cost_shock.isna().all()
    assert factor_class().calculate(changed).isna().all()


def test_below_thirty_available_market_cost_contributors_stays_pending(factor_class):
    inputs = synthetic_inputs(symbols=29)
    assert factor_class._model_inputs(inputs).cost_shock.isna().all()
    assert factor_class().calculate(inputs).isna().all()


def test_thirty_total_symbols_with_missing_amount_has_insufficient_cost_count(factor_class):
    inputs = synthetic_inputs(symbols=30)
    inputs["amount"].loc[(slice(None), "000001")] = np.nan
    assert factor_class._model_inputs(inputs).cost_shock.isna().all()


def test_misaligned_or_duplicate_index_rejected(factor_class, inputs):
    misaligned = {"close": inputs["close"], "amount": inputs["amount"].iloc[::-1]}
    with pytest.raises(ValueError, match="Aligned unique"):
        factor_class().calculate(misaligned)
    duplicated = {key: pd.concat([value, value.iloc[:1]]) for key, value in inputs.items()}
    with pytest.raises(ValueError, match="Aligned unique"):
        factor_class().calculate(duplicated)


def test_stock_market_control_is_leave_one_out(factor_class, inputs):
    model = factor_class._model_inputs(inputs)
    when = sorted(set(inputs["close"].index.get_level_values("date")))[80]
    current = model.xs(when, level="date")
    expected = current.loc[current.index != "000001", "r"].mean()
    assert current.loc["000001", "market"] == pytest.approx(expected)
