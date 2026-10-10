"""Risk-state primitives with calendar-adjacent returns and prior-fit errors."""
from __future__ import annotations

import numpy as np
import pandas as pd


def relation_states(close: pd.DataFrame, *, model=120, state=120, smooth=20,
                    epsilon=1e-6, minimum_peers=30) -> dict[str, pd.DataFrame]:
    """Use all supplied source-time returns; missing observations stay missing.

    RR01 compares realized shocks with the previously estimated market relation.
    RR02 measures instability of that relation, normalized by return variation.
    Both differ from a raw residual-volatility or momentum window grid.
    """
    if close.index.has_duplicates or close.columns.has_duplicates or not close.index.is_monotonic_increasing:
        raise ValueError("Unique ordered calendar and security columns required")
    valid = close.where(np.isfinite(close) & close.gt(0))
    returns = valid / valid.shift(1) - 1
    counts = returns.notna().sum(axis=1)
    total = returns.sum(axis=1, min_count=1)
    market = returns.rsub(total, axis=0).div(counts - 1, axis=0)
    market = market.where(returns.notna() & pd.DataFrame(
        np.broadcast_to((counts - 1 >= minimum_peers).to_numpy()[:, None], returns.shape),
        index=returns.index, columns=returns.columns))
    mr = returns.rolling(model, min_periods=model).mean()
    mm = market.rolling(model, min_periods=model).mean()
    mrm = (returns * market).rolling(model, min_periods=model).mean()
    mm2 = market.pow(2).rolling(model, min_periods=model).mean()
    mv = mm2 - mm.pow(2)
    beta = ((mrm - mr * mm) / mv).where(mv * model > 1e-12)
    alpha = mr - beta * mm
    endpoint = returns - alpha - beta * market
    prior_error = returns - alpha.shift(1) - beta.shift(1) * market
    total_variance = returns.rolling(state, min_periods=state).var(ddof=1)
    endpoint_variance = endpoint.rolling(state, min_periods=state).var(ddof=1)
    parent_ratio = endpoint_variance / (total_variance + epsilon)
    parent_ratio = parent_ratio.where(parent_ratio.ge(-1e-12) & parent_ratio.le(1 + 1e-12)).clip(0, 1)
    parent = parent_ratio.rolling(smooth, min_periods=smooth).mean()
    forecast_ratio = prior_error.rolling(state, min_periods=state).var(ddof=1) / (total_variance + epsilon)
    forecast = forecast_ratio.where(np.isfinite(forecast_ratio) & forecast_ratio.ge(0)).rolling(smooth, min_periods=smooth).mean()
    instability = beta.rolling(state, min_periods=state).var(ddof=1) * market.rolling(state, min_periods=state).var(ddof=1) / (total_variance + epsilon)
    instability = instability.where(np.isfinite(instability) & instability.ge(0)).rolling(smooth, min_periods=smooth).mean()
    return {"returns": returns, "market": market, "alpha": alpha, "beta": beta,
            "endpoint_residual": endpoint, "prior_fit_error": prior_error,
            "F141": parent, "RR01": forecast, "RR02": instability}
