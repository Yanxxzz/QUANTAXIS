"""Bounded continuation helpers; no new labels or account execution."""
import numpy as np
import pandas as pd

def extension_panel(close,factor):
    if not close.index.equals(factor.index) or not close.columns.equals(factor.columns):
        raise ValueError("Aligned independent reference grids required")
    valid=np.isfinite(close)&close.gt(0)&np.isfinite(factor)&factor.gt(0)
    adjusted=(close*factor).where(valid)
    adjusted=adjusted.where(np.isfinite(adjusted)&adjusted.gt(0))
    complete=adjusted.rolling(21,min_periods=21).count().eq(21).shift(3,fill_value=False)
    score=(adjusted.shift(3)/adjusted.shift(23)-1).where(complete)
    return score.where(np.isfinite(score))
