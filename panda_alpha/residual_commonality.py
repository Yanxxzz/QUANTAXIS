"""Common covariance left after an own-excluded market regression.

Use the time-space Gram identity to avoid an N by N correlation matrix.
Missing calendar returns stay missing; no statistical peer links are inferred.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def residual_commonality(close: pd.DataFrame, window: int = 120,
                         smooth: int = 20, minimum_assets: int = 500,
                         minimum_peers: int = 30) -> pd.DataFrame:
    if window < 4 or smooth < 1 or minimum_assets < 2 or minimum_peers < 1:
        raise ValueError("Invalid commonality parameters")
    if (not close.index.is_unique or not close.index.is_monotonic_increasing
            or not close.columns.is_unique):
        raise ValueError("Unique ordered calendar and asset columns required")
    prices = close.where(np.isfinite(close) & close.gt(0))
    returns = (prices / prices.shift(1) - 1).to_numpy(dtype=float)
    finite = np.isfinite(returns)
    totals = np.nansum(returns, axis=1)
    counts = finite.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        market = (totals[:, None] - returns) / (counts[:, None] - 1)
    market[~finite | ((counts[:, None] - 1) < minimum_peers)] = np.nan
    raw = np.full_like(returns, np.nan)
    for end in range(window, len(returns)):
        sl = slice(end - window + 1, end + 1)
        y = returns[sl]
        x = market[sl]
        good = np.isfinite(y).all(axis=0) & np.isfinite(x).all(axis=0)
        ids = np.flatnonzero(good)
        if len(ids) < minimum_assets:
            continue
        y = y[:, ids] - y[:, ids].mean(axis=0)
        x = x[:, ids] - x[:, ids].mean(axis=0)
        xx = np.sum(x * x, axis=0)
        ok = xx > 1e-12
        ids, x, y, xx = ids[ok], x[:, ok], y[:, ok], xx[ok]
        if len(ids) < minimum_assets:
            continue
        beta = np.sum(x * y, axis=0) / xx
        e = y - x * beta
        norm2 = np.sum(e * e, axis=0)
        ok = norm2 > 1e-12
        ids, e, norm2 = ids[ok], e[:, ok], norm2[ok]
        if len(ids) < minimum_assets:
            continue
        unit = e / np.sqrt(norm2)
        gram = unit @ unit.T
        # sum_j corr(i,j)^2 = u_i' (U U') u_i; remove corr(i,i)^2.
        value = (np.sum(unit * (gram @ unit), axis=0) - 1) / (len(ids) - 1)
        ok = np.isfinite(value) & (value >= -1e-10) & (value <= 1 + 1e-10)
        raw[end, ids[ok]] = np.clip(value[ok], 0, 1)
    return pd.DataFrame(raw, index=close.index, columns=close.columns).rolling(
        smooth, min_periods=smooth).mean()
