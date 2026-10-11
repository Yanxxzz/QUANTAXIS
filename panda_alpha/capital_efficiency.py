"""Pure sales growth relative to fiscal capital expansion.

The caller binds revenue current/comparative fiscal spans, closing/opening
asset roles, same-basis units and source identities. This helper does not
infer or certify those roles, select a cohort, or construct portfolio weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def capital_efficiency(
    revenue_current: pd.Series,
    revenue_comparative: pd.Series,
    assets_closing: pd.Series,
    assets_opening: pd.Series,
) -> pd.Series:
    """Return log(Rcurrent/Rcomparative) - log(Aclosing/Aopening).

    Inputs must be Series with the same unique, nonmissing identity index,
    order and index names. A future formation projection can use (date,
    symbol); the source contract validates financial report identities first.
    Rows outside the four-positive-finite domain stay NaN in place. There is
    no reindexing, amount-role swap, zero filling or implicit row removal.
    """
    operands = {
        "revenue_current": revenue_current,
        "revenue_comparative": revenue_comparative,
        "assets_closing": assets_closing,
        "assets_opening": assets_opening,
    }
    if any(not isinstance(value, pd.Series) for value in operands.values()):
        raise TypeError("capital_efficiency requires four indexed pandas Series")
    index = revenue_current.index
    if not index.is_unique or index.to_frame(index=False).isna().to_numpy().any():
        raise ValueError("capital_efficiency identity index must be unique and nonmissing")
    for name, value in operands.items():
        if not index.equals(value.index) or list(index.names) != list(value.index.names):
            raise ValueError("capital_efficiency identity index/order/names mismatch: " + name)
    arrays = [pd.to_numeric(value, errors="raise").to_numpy(dtype=np.float64, na_value=np.nan)
              for value in operands.values()]
    valid = np.logical_and.reduce([np.isfinite(value) & (value > 0) for value in arrays])
    result = np.full(len(index), np.nan, dtype=np.float64)
    rc, rp, ac, ao = [value[valid] for value in arrays]
    # Log differences are algebraically the fixed ratio formula, while finite
    # positive operands need not form a representable intermediate ratio.
    result[valid] = (np.log(rc) - np.log(rp)) - (np.log(ac) - np.log(ao))
    return pd.Series(result, index=index, name="capital_efficiency")
