"""Raw-price F174 source transfer; adjusted prices belong to wealth accounting.

Cross-asset ranks depend on stock-specific price scales. Do not independently
back-adjust the inputs and call that a reproduction of the raw-price factor.
Volume is explicitly shares here; QA hands must be converted by the caller.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def slow_vwap_dislocation(raw_close: pd.DataFrame, amount_yuan: pd.DataFrame,
                         volume_shares: pd.DataFrame, raw_high: pd.DataFrame,
                         raw_low: pd.DataFrame, *, window: int = 20,
                         minimum_assets: int = 30, quote_tolerance: float = .011) -> pd.DataFrame:
    frames = [amount_yuan, volume_shares, raw_high, raw_low]
    if window < 1 or minimum_assets < 2 or quote_tolerance < 0:
        raise ValueError('Invalid dislocation parameters')
    if (not raw_close.index.is_unique or not raw_close.index.is_monotonic_increasing
            or not raw_close.columns.is_unique):
        raise ValueError('Ordered unique calendar and assets required')
    if any(not x.index.equals(raw_close.index) or not x.columns.equals(raw_close.columns) for x in frames):
        raise ValueError('Input panels must have identical date/asset axes')
    vwap = amount_yuan / volume_shares.where(volume_shares.gt(0))
    finite = np.isfinite(raw_close) & np.isfinite(vwap) & np.isfinite(raw_high) & np.isfinite(raw_low)
    valid = (finite & raw_close.gt(0) & amount_yuan.gt(0) & volume_shares.gt(0)
             & raw_low.gt(0) & raw_high.ge(raw_low)
             & raw_close.ge(raw_low - quote_tolerance) & raw_close.le(raw_high + quote_tolerance)
             & vwap.ge(raw_low - quote_tolerance) & vwap.le(raw_high + quote_tolerance))
    valid = valid & valid.sum(axis=1).ge(minimum_assets).to_numpy()[:, None]
    difference = (vwap - raw_close).where(valid)
    scale = (vwap + raw_close).where(valid)
    ratio = difference.rank(axis=1, method='average', pct=True) / scale.rank(axis=1, method='average', pct=True)
    return ratio.rolling(window, min_periods=window).mean()
