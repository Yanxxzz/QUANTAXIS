"""Synthetic native contract tests; no real financial ratios or return labels."""
import ast

import numpy as np
import pandas as pd
import pytest

from panda_alpha.native_goodwill import GoodwillAssetShare, native_code
from panda_alpha.native_preflight import FactorSeriesFixture


def inputs(goodwill, assets, *, symbol_first=False):
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(['2031-01-02', '2031-01-03']), ['000001', '000002']],
        names=['date', 'symbol'])
    if symbol_first:
        index = index.reorder_levels(['symbol', 'date'])
    index = index.take([3, 0, 2, 1])
    return {'close': pd.Series([np.nan, 8.0, 9.0, 10.0], index=index),
            'goodwill': pd.Series(goodwill, index=index),
            'total_assets': pd.Series(assets, index=index)}


@pytest.mark.parametrize('symbol_first', [False, True])
def test_share_preserves_wrapper_input_index_and_unaltered_fraction(symbol_first):
    data = inputs([20.0, 0.0, 30.0, 10.0], [100.0, 60.0, 30.0, 40.0],
                  symbol_first=symbol_first)
    result = GoodwillAssetShare().calculate(
        {k: FactorSeriesFixture(v) for k, v in data.items()})
    assert result.index.equals(data['close'].index)
    assert result.name == 'value'
    np.testing.assert_allclose(result, [.2, 0.0, 1.0, .25])
    # close contributes the output index, not a new accounting filter.
    assert pd.notna(result.iloc[0]) and pd.isna(data['close'].iloc[0])


@pytest.mark.parametrize('bad', [np.nan, np.inf, -np.inf, -1.0, '\u2014', ''])
def test_missing_negative_or_nonfinite_input_is_unavailable(bad):
    data = inputs([bad, 2.0, bad, 0.0], [20.0, bad, bad, 0.0])
    assert GoodwillAssetShare().calculate(data).isna().all()


def test_zero_goodwill_requires_known_positive_assets_and_excess_is_invalid():
    data = inputs([0.0, 0.0, 21.0, 0.0], [20.0, 0.0, 20.0, np.nan])
    result = GoodwillAssetShare().calculate(data)
    assert result.iloc[0] == 0.0
    assert result.iloc[1:].isna().all()


def test_common_currency_unit_scaling_preserves_the_share():
    data = inputs([20.0, 0.0, 30.0, 10.0], [100.0, 60.0, 30.0, 40.0])
    scaled = {key: series if key == 'close' else series * 1e6
              for key, series in data.items()}
    pd.testing.assert_series_equal(GoodwillAssetShare().calculate(data),
                                   GoodwillAssetShare().calculate(scaled))


def test_future_records_do_not_fill_or_rewrite_past_missing_goodwill():
    data = inputs([20.0, np.nan, 30.0, 10.0], [100.0, 60.0, 30.0, 40.0])
    baseline = GoodwillAssetShare().calculate(data)
    extra_index = pd.MultiIndex.from_product(
        [pd.to_datetime(['2031-01-06']), ['000001', '000002']],
        names=['date', 'symbol'])
    extra = {'close': [11.0, 12.0], 'goodwill': [1.0, 50.0],
             'total_assets': [999.0, 100.0]}
    extended = {k: pd.concat([v, pd.Series(extra[k], index=extra_index)])
                for k, v in data.items()}
    result = GoodwillAssetShare().calculate(extended)
    pd.testing.assert_series_equal(result.reindex(baseline.index), baseline)
    assert pd.isna(result.loc[baseline.index[1]])


@pytest.mark.parametrize('invalid', ['duplicates', 'different_order', 'unnamed'])
def test_misaligned_or_ambiguous_input_is_rejected(invalid):
    data = inputs([20.0, 0.0, 30.0, 10.0], [100.0, 60.0, 30.0, 40.0])
    if invalid == 'duplicates':
        data = {k: pd.concat([v, v.iloc[:1]]) for k, v in data.items()}
    elif invalid == 'different_order':
        data['total_assets'] = data['total_assets'].iloc[::-1]
    else:
        data = {k: v.rename_axis([None, None]) for k, v in data.items()}
    with pytest.raises(ValueError, match='Aligned unique'):
        GoodwillAssetShare().calculate(data)


def test_prefixed_alias_or_intangible_assets_are_not_silent_substitutes():
    data = inputs([20.0, 0.0, 30.0, 10.0], [100.0, 60.0, 30.0, 40.0])
    data['bs_goodwill'] = data.pop('goodwill')
    data['intangible_assets'] = data['bs_goodwill']
    with pytest.raises(KeyError, match='goodwill'):
        GoodwillAssetShare().calculate(data)


def test_generated_factor_uses_literal_fields_and_the_same_math():
    code = native_code()
    tree = ast.parse(code)
    fields = {node.slice.value for node in ast.walk(tree)
              if isinstance(node, ast.Subscript)
              and isinstance(node.value, ast.Name) and node.value.id == 'factors'
              and isinstance(node.slice, ast.Constant)}
    assert fields == {'close', 'goodwill', 'total_assets'}
    namespace = {'Factor': type('SyntheticFactor', (), {})}
    exec(compile(code, 'synthetic_goodwill_share.py', 'exec'), namespace)
    assert issubclass(namespace['GoodwillAssetShare'], namespace['Factor'])
    data = inputs([20.0, 0.0, np.nan, 41.0], [100.0, 60.0, 30.0, 40.0])
    wrapped = {k: FactorSeriesFixture(v) for k, v in data.items()}
    pd.testing.assert_series_equal(
        namespace['GoodwillAssetShare']().calculate(wrapped),
        GoodwillAssetShare().calculate(wrapped))
