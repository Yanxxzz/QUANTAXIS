"""Stable five-day arithmetic and a self-contained native factor adapter.

Input calendars and native source availability remain caller obligations.
"""
from __future__ import annotations

import inspect
import math

import numpy as np
import pandas as pd


def mean_weighted_gap(values, weights):
    """Mean gap with exact arithmetic on the supplied binary float inputs.

    Body values are bounded by the caller's price validation. Conversion of
    OHLC to body and the final returned float retain their usual rounding.
    """
    values, weights = tuple(map(float, values)), tuple(map(float, weights))
    if not values or len(values) != len(weights):
        raise ValueError('A nonempty paired window is required')
    if any(not math.isfinite(x) for x in values + weights) or any(w <= 0 for w in weights):
        return math.nan
    # Every finite float is a dyadic rational. Align denominators (powers of
    # two), perform the cancellation with integers, then round only once.
    # Unlike scaled floating products, this also preserves nonconstant nulls.
    er = [x.as_integer_ratio() for x in values]
    tr = [x.as_integer_ratio() for x in weights]
    ed, td = max(d for _, d in er), max(d for _, d in tr)
    en = [n * (ed // d) for n, d in er]
    tn = [n * (td // d) for n, d in tr]
    count, total_weight = len(en), sum(tn)
    numerator = sum(en) * total_weight - count * sum(e*t for e, t in zip(en, tn))
    return numerator / (count * ed * total_weight)


def calculate_fields(factors):
    """Keep supplied date/symbol keys and five complete calendar positions."""
    fields = ('open', 'close', 'high', 'low', 'turnover')
    inputs, original = {}, None
    for name in fields:
        value = factors[name]
        series = value if isinstance(value, pd.Series) else value.series
        if not isinstance(series, pd.Series) or not isinstance(series.index, pd.MultiIndex):
            raise ValueError('Expected indexed numeric fields')
        if series.index.names != ['date', 'symbol'] or series.index.has_duplicates:
            raise ValueError('Expected unique date/symbol input')
        if original is None:
            original = series.index
        elif set(series.index) != set(original):
            raise ValueError('Field key sets differ')
        inputs[name] = pd.to_numeric(series.reindex(original), errors='coerce').replace([np.inf, -np.inf], np.nan)
    panels = {name: values.unstack('symbol').sort_index() for name, values in inputs.items()}
    o, c, h, l, t = (panels[name] for name in fields)
    valid = ((l > 0) & (h > l) & (o >= l) & (o <= h)
             & (c >= l) & (c <= h) & (t > 0))
    for panel in panels.values():
        valid &= np.isfinite(panel)
    complete = valid.astype(float).rolling(5, min_periods=5).sum().eq(5).to_numpy()
    body = ((c - o) / (h - l).where(h > l)).to_numpy()
    turnover = t.to_numpy()
    raw = np.full(body.shape, np.nan)
    for end in range(4, len(body)):
        for column in np.flatnonzero(complete[end]):
            raw[end, column] = mean_weighted_gap(body[end-4:end+1, column], turnover[end-4:end+1, column])
    ranked = pd.DataFrame(raw, index=t.index, columns=t.columns).rank(axis=1, method='average', pct=True)
    grid = pd.MultiIndex.from_product([ranked.index, ranked.columns], names=['date', 'symbol'])
    return pd.Series(ranked.to_numpy().reshape(-1), index=grid, name='value').reindex(original)


def native_code():
    """Embed the tested arithmetic; remote workers need no repository import."""
    return ('import math\nimport numpy as np\nimport pandas as pd\n\n'
            + inspect.getsource(mean_weighted_gap) + '\n\n'
            + inspect.getsource(calculate_fields) + '\n\n'
            + 'class MarketBodyActivityPressureStable(Factor):\n'
              '    def calculate(self, factors):\n'
              '        return calculate_fields(factors)\n')
