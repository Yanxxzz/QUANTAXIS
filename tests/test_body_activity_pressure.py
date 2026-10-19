from decimal import Decimal, localcontext
from fractions import Fraction
import math

import numpy as np
import pandas as pd
import pytest

from panda_alpha.body_activity_pressure import mean_weighted_gap, calculate_fields, native_code


def oracle(values, weights):
    with localcontext() as ctx:
        ctx.prec = 90
        e, t = [Decimal.from_float(float(x)) for x in values], [Decimal.from_float(float(x)) for x in weights]
        return float(sum(e) / len(e) - sum(x*y for x, y in zip(e, t)) / sum(t))


def sample():
    index = pd.MultiIndex.from_product([pd.bdate_range('2024-01-02', periods=14), ['a', 'b', 'c', 'd']], names=['date', 'symbol'])
    rng = np.random.default_rng(20261019)
    return pd.DataFrame({'open': 2., 'close': rng.uniform(1.1, 3.9, len(index)),
                         'high': 4., 'low': 1., 'turnover': rng.uniform(.1, 10, len(index))}, index=index)


@pytest.mark.parametrize('weight', [.1, 1., 10., 1e-300, 1e300])
def test_constant_weight_is_exact_zero(weight):
    assert mean_weighted_gap([.1, -.2, .3, .4, -.5], [weight]*5) == 0


def test_constant_body_is_exact_zero():
    assert mean_weighted_gap([.1]*5, [.13, .71, 1.2, 3.4, 9.9]) == 0


def test_small_genuine_effect_is_retained_without_zero_threshold():
    e, t = [.1]*4 + [.1 + 1e-12], [1.]*4 + [1. + 1e-12]
    got = mean_weighted_gap(e, t)
    assert got < 0
    assert got == pytest.approx(oracle(e, t), rel=1e-12, abs=0)


def test_nonconstant_exact_zero_covariance_preserves_native_ties():
    e, t = [-1., -.5, 0., .5, 1.], [1., 3., 2., 1., 2.]
    assert mean_weighted_gap(e, t) == 0
    index = pd.MultiIndex.from_product([pd.bdate_range('2024-01-02', periods=5), ['a', 'b']], names=['date', 'symbol'])
    frame = pd.DataFrame({'open': [3,2,2,2,2,2,2,2,1,2],
        'close': [1,2,1,2,2,2,3,2,3,2], 'high': 3., 'low': 1.,
        'turnover': [1,1,3,1,2,1,1,1,2,1]}, index=index)
    scope = {'Factor': type('Factor', (), {})}
    exec(compile(native_code(), '<native-nonconstant-null>', 'exec'), scope)
    got = scope['MarketBodyActivityPressureStable']().calculate(frame)
    assert got.loc[index.get_level_values('date').max()].to_dict() == {'a': .75, 'b': .75}


def test_nonconstant_null_family_and_exact_fraction_reference():
    rng = np.random.default_rng(42)
    for _ in range(100):
        a, b, c, d = rng.integers(1, 100, 4)
        final = float(a + b/2 - d/2)
        if final > 0:
            assert mean_weighted_gap([-1., -.5, 0., .5, 1.], [a,b,c,d,final]) == 0
        e, t = rng.uniform(-1, 1, 5), np.exp(rng.uniform(-100, 100, 5))
        fe, ft = [Fraction.from_float(float(x)) for x in e], [Fraction.from_float(float(x)) for x in t]
        expected = sum(fe)/5 - sum(x*y for x,y in zip(fe,ft))/sum(ft)
        assert mean_weighted_gap(e, t) == float(expected)


def test_high_precision_reference_unit_scaling_and_permutation():
    rng = np.random.default_rng(617)
    for _ in range(100):
        e, t = rng.uniform(-1, 1, 5), np.exp(rng.uniform(-5, 5, 5))
        got = mean_weighted_gap(e, t)
        assert got == pytest.approx(oracle(e, t), abs=5e-16)
        order = rng.permutation(5)
        assert mean_weighted_gap(e[order], t[order]) == pytest.approx(got, abs=5e-16)
        for scale in (1e-290, .01, 100., 1e290):
            assert mean_weighted_gap(e, t*scale) == pytest.approx(got, abs=5e-16)


@pytest.mark.parametrize('case', ['constant_turnover', 'constant_body'])
def test_native_adapter_does_not_manufacture_cross_sectional_ranks(case):
    frame = sample()
    if case == 'constant_turnover':
        frame['turnover'] = .1
    else:
        frame['close'] = 2.3
    scope = {'Factor': type('Factor', (), {})}
    exec(compile(native_code(), '<self-contained-tested-factor>', 'exec'), scope)
    got = scope['MarketBodyActivityPressureStable']().calculate(frame)
    assert got.groupby(level='date').nunique().iloc[:4].eq(0).all()
    assert got.groupby(level='date').nunique().iloc[4:].eq(1).all()
    pd.testing.assert_series_equal(got, calculate_fields(frame))


def test_prefix_missing_calendar_slot_and_order_are_preserved():
    frame = sample()
    got = calculate_fields(frame)
    dates = frame.index.get_level_values('date').unique()
    pd.testing.assert_series_equal(calculate_fields(frame.loc[:dates[8]]), got.loc[:dates[8]])
    pd.testing.assert_series_equal(calculate_fields(frame.sample(frac=1, random_state=2)).reindex(got.index), got)
    missing = frame.copy()
    missing.loc[dates[5], :] = np.nan
    changed = calculate_fields(missing)
    assert changed.loc[dates[5:10]].isna().all()
    pd.testing.assert_series_equal(changed.loc[dates[10]], got.loc[dates[10]])
    for scale in (.01, 100., 1e290, 1e-290):
        scaled = frame.copy()
        scaled['turnover'] *= scale
        pd.testing.assert_series_equal(calculate_fields(scaled), got)


def test_invalid_pairs_and_malformed_field_keys():
    assert math.isnan(mean_weighted_gap([.1]*5, [1, 1, 0, 1, 1]))
    assert math.isnan(mean_weighted_gap([.1]*4 + [math.inf], [1]*5))
    with pytest.raises(ValueError):
        mean_weighted_gap([1], [1, 2])
    frame = sample()
    with pytest.raises(ValueError, match='unique'):
        calculate_fields(pd.concat([frame, frame.iloc[:1]]))
    inputs = {key: frame[key] for key in frame}
    inputs['turnover'] = inputs['turnover'].iloc[1:]
    with pytest.raises(ValueError, match='key sets differ'):
        calculate_fields(inputs)
