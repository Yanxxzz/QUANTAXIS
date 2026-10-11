def peer_share(close):
    valid = close.where(np.isfinite(close) & close.gt(0))
    returns = valid / valid.shift(1) - 1.0
    counts = returns.notna().sum(axis=1)
    total = returns.sum(axis=1, min_count=1)
    market = returns.rsub(total, axis=0).div(counts - 1, axis=0)
    peers = pd.DataFrame(np.broadcast_to((counts - 1 >= 30).to_numpy()[:, None], returns.shape),
                         index=returns.index, columns=returns.columns)
    market = market.where(returns.notna() & peers)
    mr = returns.rolling(120, min_periods=120).mean()
    mm = market.rolling(120, min_periods=120).mean()
    mrm = (returns * market).rolling(120, min_periods=120).mean()
    mm2 = market.pow(2).rolling(120, min_periods=120).mean()
    mv = mm2 - mm.pow(2)
    beta = ((mrm - mr * mm) / mv).where(mv * 120 > 1e-12)
    alpha = mr - beta * mm
    endpoint = returns - alpha - beta * market
    total_var = returns.rolling(120, min_periods=120).var(ddof=1)
    residual_var = endpoint.rolling(120, min_periods=120).var(ddof=1)
    share = residual_var / (total_var + 1e-6)
    share = share.where(share.ge(-1e-12) & share.le(1.0 + 1e-12)).clip(0.0, 1.0)
    return share.rolling(20, min_periods=20).mean()

