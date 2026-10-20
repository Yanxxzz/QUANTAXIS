"""One frozen fixed-universe peer-diffusion definition, never a backtest engine.

Correlations order exact moments of already-rounded binary log returns. Source
precision, log/market subtraction and final conversion remain floating point.
"""
from __future__ import annotations

import inspect
import math
import numpy as np
import pandas as pd


def integer_moments(values):
    ratios = [float(v).as_integer_ratio() for v in values]
    denominator = max(d for _, d in ratios)
    integers = [n * (denominator // d) for n, d in ratios]
    total = sum(integers)
    variance = len(integers) * sum(n*n for n in integers) - total*total
    return integers, total, variance


def exact_positive_peers(history, symbols):
    """Top five strictly positive correlations, exact comparison, symbol ties."""
    moments = [integer_moments(history[:, i]) for i in range(history.shape[1])]
    count = len(history)
    peers = {}
    for i, (left, sx, vx) in enumerate(moments):
        if vx == 0:
            continue
        top = []
        for j, (right, sy, vy) in enumerate(moments):
            if i == j or vy == 0:
                continue
            covariance = count * sum(a*b for a,b in zip(left, right)) - sx*sy
            if covariance <= 0:
                continue
            # vx cancels between comparisons for the same target security.
            numerator, denominator = covariance*covariance, vy
            slot = len(top)
            for k, (other, on, od) in enumerate(top):
                delta = numerator*od - on*denominator
                if delta > 0 or (delta == 0 and str(symbols[j]) < str(symbols[other])):
                    slot = k
                    break
            top.insert(slot, (j,numerator,denominator))
            top = top[:5]
        if len(top) == 5:
            peers[i] = [j for j,_,_ in top]
    return peers


def exact_peer_gap(recent, own, others):
    """Mean of five peer sums minus own sum, one final rounding, no epsilon."""
    values = [float(v) for j in [own,*others] for v in recent[:,j]]
    ratios = [v.as_integer_ratio() for v in values]
    denominator = max(d for _,d in ratios)
    numerators = [n*(denominator//d) for n,d in ratios]
    own_sum = sum(numerators[:len(recent)])
    return (sum(numerators[len(recent):]) - 5*own_sum) / (5*denominator)


def peer_diffusion(close, calendar, symbols, *, audit=None):
    """Return raw scores on full independent grid; no labels or future filtering."""
    dates = pd.DatetimeIndex(pd.to_datetime(calendar))
    symbols = sorted(str(s) for s in symbols)
    if not dates.is_unique or not dates.is_monotonic_increasing or len(set(symbols)) != len(symbols):
        raise ValueError('Unique ordered calendar and unique frozen symbols required')
    if not close.index.is_unique or not close.columns.is_unique:
        raise ValueError('Duplicate price identity')
    prices = close.reindex(index=dates,columns=symbols).copy()
    for col in prices:
        boolean = prices[col].map(lambda v:isinstance(v,(bool,np.bool_)))
        prices[col] = pd.to_numeric(prices[col],errors='coerce').where(~boolean)
    prices = prices.where(np.isfinite(prices) & prices.gt(0))
    returns = np.log(prices/prices.shift(1))
    market = returns.median(axis=1).where(returns.notna().sum(axis=1)>=60)
    excess = returns.sub(market,axis=0).to_numpy(float)
    output = np.full(excess.shape,np.nan)
    for end in range(64,len(dates)):
        history = excess[end-64:end-4]
        eligible = np.flatnonzero(np.isfinite(history).all(axis=0))
        eligible = [i for i in eligible if integer_moments(history[:,i])[2]>0]
        if len(eligible)<60:
            continue
        peers = exact_positive_peers(history[:,eligible], [symbols[i] for i in eligible])
        recent = excess[end-4:end+1]
        day = {}
        for local_i, local_peers in peers.items():
            i = eligible[local_i]
            others = [eligible[j] for j in local_peers]
            if not np.isfinite(recent[:,[i,*others]]).all():
                continue
            output[end,i] = exact_peer_gap(recent,i,others)
            day[symbols[i]] = [symbols[j] for j in others]
        if audit is not None:
            audit[dates[end].strftime('%Y-%m-%d')] = day
    return pd.DataFrame(output,index=dates,columns=symbols).rename_axis(index='date',columns='symbol')


def native_code(calendar, symbols):
    """Embed the exact tested code and explicit independent calendar/universe."""
    calendar = [pd.Timestamp(d).strftime('%Y-%m-%d') for d in calendar]
    helpers = '\n\n'.join(inspect.getsource(f) for f in
                           (integer_moments,exact_positive_peers,exact_peer_gap,peer_diffusion))
    wrapper = '''
class FixedPeerDiffusionFactor(Factor):
    def calculate(self, factors):
        source = factors['close']
        source = source if isinstance(source,pd.Series) else source.series
        if not isinstance(source.index,pd.MultiIndex) or source.index.names!=['date','symbol'] or not source.index.is_unique:
            raise ValueError('Unique date/symbol field required')
        original = source.index
        def day(value):
            if isinstance(value,(int,np.integer)):
                value=str(int(value))
            result=pd.Timestamp(value)
            if pd.isna(result):
                raise ValueError('Missing date')
            if result.tzinfo is not None:
                result=result.tz_convert('Asia/Shanghai').tz_localize(None)
            return result.normalize()
        mapped={v:day(v) for v in original.get_level_values('date').unique()}
        index=pd.MultiIndex.from_arrays([original.get_level_values('date').map(mapped),original.get_level_values('symbol')],names=['date','symbol'])
        if not index.is_unique:
            raise ValueError('Duplicate normalized date')
        aliases={}
        for code in SYMBOLS:
            exchange='SH' if code.startswith('6') else 'SZ'
            suffix='XSHG' if exchange=='SH' else 'XSHE'
            for alias in [code,code+'.'+exchange,code+'.'+suffix,exchange+code,exchange.lower()+code,exchange.lower()+'.'+code]:
                aliases[alias]=code
        normalized=[aliases.get(str(v),'__outside__') for v in index.get_level_values('symbol')]
        keys=pd.MultiIndex.from_arrays([index.get_level_values('date'),normalized],names=['date','symbol'])
        selected=np.array(normalized)!='__outside__'
        local=pd.Series(source.to_numpy()[selected],index=keys[selected])
        if not local.index.is_unique:
            raise ValueError('Duplicate canonical stock identity')
        daily=local.unstack('symbol')
        calendar=pd.DatetimeIndex(CALENDAR)
        calendar=calendar[(calendar>=index.get_level_values('date').min())&(calendar<=index.get_level_values('date').max())]
        scores=peer_diffusion(daily,calendar,SYMBOLS)
        output=scores.stack(dropna=False).reindex(keys)
        output.index=pd.MultiIndex.from_arrays([index.get_level_values('date').strftime('%Y%m%d'),index.get_level_values('symbol')],names=['date','symbol'])
        output.name='value'
        return output
'''
    return ('import math\nimport numpy as np\nimport pandas as pd\n\n'+helpers+
            '\n\nCALENDAR='+repr(calendar)+'\nSYMBOLS='+repr(sorted(symbols))+'\n'+wrapper)
