"""Fixed equal signal composition on one explicitly supplied common cohort.

Source/PIT qualification is the caller's job. Missing components never cause
silent weight redistribution; no labels or fitted expected returns are used.
"""
from collections.abc import Mapping
import numpy as np
import pandas as pd


def equal_signal(frame: pd.DataFrame, directions: Mapping[str, int], *,
                 winsor: tuple[float, float] = (.01, .99)) -> pd.Series:
    """Direction align, winsorize, Z-score (population SD), then equal average.

    Each formation date must already contain the same finite source-qualified
    stock cohort for every component. A constant component has no ranking
    information, so it is rejected rather than silently removed or imputed.
    """
    if not directions or not {'date', 'symbol'}.issubset(frame):
        raise ValueError('Explicit formation date/symbol and components required')
    if frame.duplicated(['date', 'symbol']).any() or not frame.index.is_unique:
        raise ValueError('Duplicate stock/date or row index')
    if any(type(d) is not int or d not in (0, 1) for d in directions.values()):
        raise ValueError('Every component needs direction 0 or 1')
    if not 0 <= winsor[0] < winsor[1] <= 1:
        raise ValueError('Invalid winsor quantiles')
    components = list(directions)
    if not set(components).issubset(frame):
        raise ValueError('Missing component columns')
    values = frame[components].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError('Common source missing; no automatic reweighting')
    output = pd.Series(index=frame.index, dtype=float, name='value')
    for _, rows in frame.groupby('date', sort=False):
        standardized = []
        for name, direction in directions.items():
            signal = rows[name].astype(float) * (1 if direction else -1)
            clipped = signal.clip(*signal.quantile(list(winsor)).tolist())
            deviation = float(clipped.std(ddof=0))
            if not np.isfinite(deviation) or deviation <= 1e-12:
                raise ValueError(f'Constant component on formation date: {name}')
            standardized.append((clipped - clipped.mean()) / deviation)
        output.loc[rows.index] = sum(standardized) / len(standardized)
    return output
