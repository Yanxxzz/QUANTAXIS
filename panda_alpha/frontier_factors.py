"""Exact ordering for four frozen proposals; source qualifications stay external.

These functions read inputs, never returns. Financial states must be selected
from qualified disclosure events before calling the numerical functions.
"""
from __future__ import annotations

from functools import cmp_to_key
import inspect
import math

import numpy as np
import pandas as pd


def positive_ratio(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        return None
    number = float(value)
    return number.as_integer_ratio() if math.isfinite(number) and number > 0 else None


def compare_ratio(left, right):
    difference = left[0] * right[1] - right[0] * left[1]
    return (difference > 0) - (difference < 0)


def rank_ratios(values, centered=False):
    """Average exact ties, with no float quotient before ordering."""
    result = np.full(len(values), np.nan)
    order = sorted((i for i, value in enumerate(values) if value is not None),
                   key=cmp_to_key(lambda i, j: compare_ratio(values[i], values[j])))
    count, first = len(order), 0
    while first < count:
        end = first + 1
        while end < count and compare_ratio(values[order[first]], values[order[end]]) == 0:
            end += 1
        score = (first + end + (0 if centered else 1)) / (2 * count)
        result[order[first:end]] = score
        first = end
    return result


def gap_ratio(previous, opened, closed):
    p, o, c = map(positive_ratio, (previous, opened, closed))
    if None in (p, o, c):
        return None
    difference = o[0] * p[1] - p[0] * o[1]
    if difference == 0:
        return None
    intraday = compare_ratio(c, o)
    accepted = (difference > 0 and intraday > 0) or (difference < 0 and intraday < 0)
    return difference * (2 if accepted else 1), o[1] * p[0]


def base_expansion_ratio(volume, turnover, prior_volume, prior_turnover):
    v, t, pv, pt = map(positive_ratio, (volume, turnover, prior_volume, prior_turnover))
    if None in (v, t, pv, pt):
        return None
    numerator = v[0] * pt[0] * t[1] * pv[1]
    denominator = v[1] * pt[1] * t[0] * pv[0]
    return (numerator, denominator) if numerator > denominator else None


def field_frame(factors, names):
    inputs, original = {}, None
    for name in names:
        value = factors[name]
        series = value if isinstance(value, pd.Series) else value.series
        if (not isinstance(series, pd.Series) or not isinstance(series.index, pd.MultiIndex)
                or list(series.index.names) != ['date', 'symbol'] or not series.index.is_unique):
            raise ValueError('Expected unique date/symbol fields')
        if original is None:
            original = series.index
        elif set(series.index) != set(original):
            raise ValueError('Field key sets differ')
        # Keep booleans distinct: coercing True to 1 would manufacture a price.
        inputs[name] = series.reindex(original)
    return pd.DataFrame(inputs, index=original), original


def market_scores(factors, calendar, kind):
    names = ('open', 'close') if kind == 'gap' else ('volume', 'turnover')
    frame, original = field_frame(factors, names)
    dates = pd.DatetimeIndex(pd.to_datetime(calendar))
    if dates.has_duplicates or not dates.is_monotonic_increasing or dates.tz is not None:
        raise ValueError('Calendar must be unique ordered naive dates')
    # The independent frozen calendar covers the evaluation plus warmup.
    # Unrelated earlier native prefetch rows are preserved in the output as NaN.
    selected = frame.index.get_level_values('date').isin(dates)
    symbols = frame.index.get_level_values('symbol').unique().sort_values()
    grid = pd.MultiIndex.from_product([dates, symbols], names=['date', 'symbol'])
    full = frame.loc[selected].reindex(grid)
    panels = {name: full[name].unstack('symbol') for name in names}
    output = np.full((len(dates), len(symbols)), np.nan)
    lag = 1 if kind == 'gap' else 5
    for day in range(lag, len(dates)):
        values = []
        for symbol in range(len(symbols)):
            if kind == 'gap':
                value = gap_ratio(panels['close'].iat[day-1, symbol],
                                  panels['open'].iat[day, symbol], panels['close'].iat[day, symbol])
            else:
                value = base_expansion_ratio(panels['volume'].iat[day, symbol],
                    panels['turnover'].iat[day, symbol], panels['volume'].iat[day-5, symbol],
                    panels['turnover'].iat[day-5, symbol])
            values.append(value)
        # log is strictly increasing; rank(k) equals rank(log(k)), including ties.
        output[day] = rank_ratios(values, centered=kind != 'gap')
    return pd.Series(output.reshape(-1), index=grid, name='value').reindex(original)


def fixed_capital_scores(factors):
    frame, original = field_frame(factors, ('bs_net_fix_assets', 'bs_total_assets'))
    result = pd.Series(np.nan, index=original, name='value')
    for _, daily in frame.groupby(level='date', sort=False):
        values = []
        for fixed, assets in daily.itertuples(index=False, name=None):
            a = positive_ratio(assets)
            if (isinstance(fixed, (bool, np.bool_)) or not isinstance(fixed, (int, float, np.integer, np.floating))
                    or not math.isfinite(float(fixed)) or fixed < 0 or a is None or fixed > assets):
                values.append(None)
            else:
                f = float(fixed).as_integer_ratio()
                values.append((f[0] * a[1], f[1] * a[0]))
        result.loc[daily.index] = rank_ratios(values, centered=True)
    return result


def asset_acceleration_scores(states):
    """Three already-qualified fixed periods, never daily REF substitutes.

    Period columns are 20260630, 20260331, 20251231. Monotonic log implies
    ordering a = log(A0*A2/A1**2) by that exact rational without cancellation.
    """
    fields = ('20260630', '20260331', '20251231')
    if (not isinstance(states.index, pd.MultiIndex) or list(states.index.names) != ['date','symbol']
            or not states.index.is_unique):
        raise ValueError('Expected unique date/symbol qualified period states')
    result = pd.Series(np.nan, index=states.index, name='value')
    for _, daily in states.groupby(level='date', sort=False):
        values = []
        for a0, a1, a2 in daily[list(fields)].itertuples(index=False, name=None):
            x, y, z = map(positive_ratio, (a0, a1, a2))
            if None in (x,y,z) or compare_ratio(x,y) < 0 or compare_ratio(y,z) < 0:
                values.append(None)
            else:
                values.append((x[0]*z[0]*y[1]**2, x[1]*z[1]*y[0]**2))
        result.loc[daily.index] = rank_ratios(values, centered=True)
    return result


def native_code(kind, calendar=None):
    """Self-contained adapter: no repository imports or guessed SDK operators."""
    helpers = (positive_ratio, compare_ratio, rank_ratios, gap_ratio, base_expansion_ratio,
               field_frame, market_scores, fixed_capital_scores, asset_acceleration_scores)
    prefix = ('from functools import cmp_to_key\nimport math\nimport numpy as np\nimport pandas as pd\n\n'
              + '\n\n'.join(inspect.getsource(f) for f in helpers))
    if kind in ('gap','base'):
        assert calendar
        fields = ('open', 'close') if kind == 'gap' else ('volume', 'turnover')
        # The provider discovers dependencies through literal factors['field']
        # AST subscripts, before calculate runs. Dynamic helper access alone
        # executes locally but leaves that discovery set empty.
        supplied = '{' + ', '.join(repr(name) + ': factors[' + repr(name) + ']' for name in fields) + '}'
        body = 'return market_scores('+supplied+', '+repr(list(calendar))+', '+repr(kind)+')'
    elif kind == 'fixed':
        body = "return fixed_capital_scores({'bs_net_fix_assets': factors['bs_net_fix_assets'], 'bs_total_assets': factors['bs_total_assets']})"
    else:
        raise ValueError('No native PERIOD_ASOF metadata adapter is verified; offline state evaluator only')
    return prefix+'\n\nclass FrontierFactor(Factor):\n    def calculate(self, factors):\n        '+body+'\n'
