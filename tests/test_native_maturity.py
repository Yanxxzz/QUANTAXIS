"""Synthetic native contract tests; no actual financial values or return labels."""
import ast

import numpy as np
import pandas as pd
import pytest

from panda_alpha.native_maturity import CurrentMaturityFraction, native_code
from panda_alpha.native_preflight import FactorSeriesFixture


def inputs(maturity, noncurrent, *, symbol_first=False):
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(['2031-01-02', '2031-01-03']), ['000001', '000002']],
        names=['date', 'symbol'])
    if symbol_first:
        index = index.reorder_levels(['symbol', 'date'])
    # Unsorted row order tests index preservation without relying on sorting.
    index = index.take([3, 0, 2, 1])
    return {'close': pd.Series([np.nan, 8.0, 9.0, 10.0], index=index),
            'non_current_liability_due_one_year': pd.Series(maturity, index=index),
            'non_current_liabilities': pd.Series(noncurrent, index=index)}


@pytest.mark.parametrize('symbol_first', [False, True])
def test_fraction_preserves_real_index_order_and_wrapper_delegation(symbol_first):
    data = inputs([20.0, 0.0, 30.0, 10.0], [80.0, 60.0, 0.0, 30.0],
                  symbol_first=symbol_first)
    wrappers = {k: FactorSeriesFixture(v) for k, v in data.items()}
    result = CurrentMaturityFraction().calculate(wrappers)
    assert result.index.equals(data['close'].index)
    assert result.name == 'value'
    np.testing.assert_allclose(result, [.2, 0.0, 1.0, .25])
    # close is used for the returned input index, not as a price/value filter.
    assert pd.notna(result.iloc[0]) and pd.isna(data['close'].iloc[0])


@pytest.mark.parametrize('bad', [np.nan, np.inf, -np.inf, -1.0, '\u2014', ''])
def test_missing_nonfinite_or_negative_input_is_not_zero(bad):
    data = inputs([bad, 2.0, bad, 0.0], [20.0, bad, bad, 0.0])
    result = CurrentMaturityFraction().calculate(data)
    assert result.isna().all()


def test_zero_denominator_stays_unknown_but_explicit_zero_maturity_is_valid():
    data = inputs([0.0, 0.0, 10.0, 0.0], [20.0, 0.0, 0.0, np.nan])
    result = CurrentMaturityFraction().calculate(data)
    assert result.iloc[0] == 0.0
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == 1.0
    assert pd.isna(result.iloc[3])


def test_common_currency_unit_scaling_keeps_fraction_unchanged():
    data = inputs([20.0, 0.0, 30.0, 10.0], [80.0, 60.0, 0.0, 30.0])
    scaled = {key: series if key == 'close' else series * 1e6
              for key, series in data.items()}
    pd.testing.assert_series_equal(CurrentMaturityFraction().calculate(data),
                                   CurrentMaturityFraction().calculate(scaled))


def test_future_append_does_not_fill_or_rewrite_existing_rows():
    data = inputs([20.0, np.nan, 30.0, 10.0], [80.0, 60.0, 0.0, 30.0])
    baseline = CurrentMaturityFraction().calculate(data)
    extra_index = pd.MultiIndex.from_product(
        [pd.to_datetime(['2031-01-06']), ['000001', '000002']],
        names=['date', 'symbol'])
    extra = {'close': [11.0, 12.0],
             'non_current_liability_due_one_year': [1.0, 500.0],
             'non_current_liabilities': [999.0, 1.0]}
    extended = {k: pd.concat([v, pd.Series(extra[k], index=extra_index)])
                for k, v in data.items()}
    result = CurrentMaturityFraction().calculate(extended)
    pd.testing.assert_series_equal(result.reindex(baseline.index), baseline)
    assert pd.isna(result.loc[baseline.index[1]])


@pytest.mark.parametrize('invalid', ['duplicates', 'different_order', 'unnamed'])
def test_misaligned_or_ambiguous_input_is_rejected(invalid):
    data = inputs([20.0, 0.0, 30.0, 10.0], [80.0, 60.0, 0.0, 30.0])
    if invalid == 'duplicates':
        data = {k: pd.concat([v, v.iloc[:1]]) for k, v in data.items()}
    elif invalid == 'different_order':
        data['non_current_liabilities'] = data['non_current_liabilities'].iloc[::-1]
    else:
        data = {k: v.rename_axis([None, None]) for k, v in data.items()}
    with pytest.raises(ValueError, match='Aligned unique'):
        CurrentMaturityFraction().calculate(data)


def test_alias_is_not_added_or_silently_substituted():
    data = inputs([20.0, 0.0, 30.0, 10.0], [80.0, 60.0, 0.0, 30.0])
    data['bs_non_cur_liab_due_1y'] = data.pop('non_current_liability_due_one_year')
    with pytest.raises(KeyError, match='non_current_liability_due_one_year'):
        CurrentMaturityFraction().calculate(data)


def test_generated_factor_uses_literal_fields_and_exact_same_calculation():
    code = native_code()
    tree = ast.parse(code)
    fields = {node.slice.value for node in ast.walk(tree)
              if isinstance(node, ast.Subscript)
              and isinstance(node.value, ast.Name) and node.value.id == 'factors'
              and isinstance(node.slice, ast.Constant)}
    assert fields == {'close', 'non_current_liability_due_one_year',
                      'non_current_liabilities'}
    namespace = {'Factor': type('SyntheticFactor', (), {})}
    exec(compile(code, 'synthetic_current_maturity.py', 'exec'), namespace)
    assert issubclass(namespace['CurrentMaturityFraction'], namespace['Factor'])
    data = inputs([20.0, 0.0, np.nan, 10.0], [80.0, 60.0, 0.0, 30.0])
    wrapped = {k: FactorSeriesFixture(v) for k, v in data.items()}
    pd.testing.assert_series_equal(
        namespace['CurrentMaturityFraction']().calculate(wrapped),
        CurrentMaturityFraction().calculate(wrapped))
