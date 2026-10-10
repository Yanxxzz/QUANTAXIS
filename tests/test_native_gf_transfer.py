import numpy as np
import pandas as pd
import pytest

from panda_alpha.factor_blend import equal_signal
from panda_alpha.native_gf_transfer import render_native_gf, compress_g_intervals
from panda_alpha.risk_relation import relation_states


def example():
    rng = np.random.default_rng(7319)
    dates = pd.bdate_range('2024-01-02', periods=340)
    codes = [f'{600000+n:06d}' for n in range(80)]
    market = rng.normal(0, .006, len(dates))
    returns = market[:, None] + rng.normal(0, .002, (len(dates), len(codes)))
    close = pd.DataFrame(10 * np.cumprod(1+returns, axis=0), index=dates, columns=codes)
    window = {'start': str(dates[280].date()), 'end': str(dates[-1].date())}
    source = {c: [[window['start'], window['end'], (n+1)/40]] for n, c in enumerate(codes[:40])}
    code = render_native_gf(source, {c:'sh' for c in source}, dates.strftime('%Y-%m-%d').tolist(), window)
    namespace = {'Factor': object}
    exec(compile(code, '<test-self-contained-GF>', 'exec'), namespace)
    return close, source, window, namespace['WC03NativeGF']


def reference(close, source, window):
    state = relation_states(close)['F141'].rank(axis=1, method='average', pct=True)
    rows = []
    for day, values in state.loc[window['start']:window['end']].iterrows():
        for symbol in source:
            if np.isfinite(values[symbol]):
                rows.append({'date': str(day.date()), 'symbol': symbol,
                             'G_pct': source[symbol][0][2], 'F_pct': values[symbol]})
    frame = pd.DataFrame(rows)
    index = pd.MultiIndex.from_product([close.loc[window['start']:window['end']].index, close.columns], names=['date','symbol'])
    if frame.empty:
        return pd.Series(np.nan, index=index, dtype=float, name='value')
    frame['F_pct'] = frame.groupby('date').F_pct.rank(method='average', pct=True)
    frame['value'] = equal_signal(frame, {'G_pct':1,'F_pct':0})
    frame['date'] = pd.to_datetime(frame.date)
    expected = frame.set_index(['date','symbol']).value
    return expected.reindex(index)


def test_exact_original_math_and_fixed_complete_projection():
    close, source, window, cls = example()
    actual = cls().calculate({'close':close.rename_axis(index='date',columns='symbol').stack(dropna=False)})
    expected = reference(close, source, window)
    pd.testing.assert_index_equal(actual.index, expected.index)
    assert np.array_equal(np.isfinite(actual), np.isfinite(expected))
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-13, equal_nan=True)
    assert actual.index.get_level_values('date').min() == pd.Timestamp(window['start'])
    assert actual.index.get_level_values('date').max() == pd.Timestamp(window['end'])


def test_all_peer_matrix_precedes_financial_mask_and_exact_missing_calendar():
    close, source, window, cls = example()
    changed = close.copy()
    rng = np.random.default_rng(948)
    changed.iloc[:,40:] *= np.cumprod(1+rng.normal(0,.008,(len(close),40)),axis=0)
    original = cls().calculate({'close':close.rename_axis(index='date',columns='symbol').stack(dropna=False)})
    actual = cls().calculate({'close':changed.rename_axis(index='date',columns='symbol').stack(dropna=False)})
    assert not np.allclose(original, actual, equal_nan=True)
    expected = reference(changed, source, window)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-13, equal_nan=True)
    # This whole source date is absent from the incoming rows. The independent
    # calendar still prevents returns being manufactured across the missing day.
    sparse = changed.drop(changed.index[80])
    actual = cls().calculate({'close':sparse.rename_axis(index='date',columns='symbol').stack(dropna=False)})
    expected = reference(sparse.reindex(changed.index), source, window)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-13, equal_nan=True)


def test_no_price_fill_no_component_reweight_and_constant_rejected_date():
    close, source, window, cls = example()
    close.iloc[285,0] = np.nan
    actual = cls().calculate({'close':close.rename_axis(index='date',columns='symbol').stack(dropna=False)})
    expected = reference(close, source, window)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-13, equal_nan=True)
    assert actual.xs(close.columns[0],level='symbol').loc[close.index[285]:].isna().all()
    constant = {c:[[window['start'],window['end'],.5]] for c in source}
    code = render_native_gf(constant, {c:'sh' for c in constant}, close.index.strftime('%Y-%m-%d').tolist(), window)
    namespace = {'Factor':object};exec(compile(code,'<constant-test>','exec'),namespace)
    output = namespace['WC03NativeGF']().calculate({'close':close.rename_axis(index='date',columns='symbol').stack(dropna=False)})
    assert output.isna().all()


def test_aliases_original_index_and_ambiguous_identity_rejection():
    close, source, window, cls = example()
    short = close.rename_axis(index='date',columns='symbol').stack(dropna=False)
    expected = cls().calculate({'close':short})
    alias = short.copy()
    alias.index = pd.MultiIndex.from_arrays([short.index.get_level_values('date'), short.index.get_level_values('symbol')+'.SH'],names=['date','symbol'])
    actual = cls().calculate({'close':alias})
    assert actual.index.equals(alias.loc[window['start']:window['end']].index)
    np.testing.assert_array_equal(actual.to_numpy(), expected.to_numpy())
    extra = alias.iloc[[0]]
    with pytest.raises(ValueError,match='aliases'):
        cls().calculate({'close':pd.concat([short,extra])})
    with pytest.raises(ValueError,match='named daily'):
        cls().calculate({'close':short.swaplevel()})


def test_interval_compression_does_not_bridge_unqualified_source_day():
    frame = pd.DataFrame({'symbol':['600000']*3,'date':['2025-01-02','2025-01-03','2025-01-07'],
                          'G_pct':[.5]*3,'announcement_id':['a']*3,'report_date':['2024-12-31']*3,
                          'source_record_sha256':['x']*3,'sector':['C']*3,'sector_asof':['2024-12-01']*3})
    source=compress_g_intervals(frame,['2025-01-02','2025-01-03','2025-01-06','2025-01-07'])
    assert source['600000']==[['2025-01-02','2025-01-03',.5],['2025-01-07','2025-01-07',.5]]
