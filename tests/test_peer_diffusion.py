from fractions import Fraction
import numpy as np
import pandas as pd

from panda_alpha.peer_diffusion import exact_positive_peers, exact_peer_gap, peer_diffusion, native_code


def test_exact_correlation_order_and_ties_against_fraction_oracle():
    rng=np.random.default_rng(20261020)
    history=rng.integers(-5,6,size=(60,12)).astype(float)
    history[:,1]=history[:,0];history[:,2]=2*history[:,0]
    symbols=[f'{i:06d}' for i in range(12)]
    actual=exact_positive_peers(history,symbols)
    expected={}
    for i in range(12):
        x=[Fraction(float(v)) for v in history[:,i]]
        vx=60*sum(v*v for v in x)-sum(x)**2
        scores=[]
        for j in range(12):
            y=[Fraction(float(v)) for v in history[:,j]]
            vy=60*sum(v*v for v in y)-sum(y)**2
            cov=60*sum(a*b for a,b in zip(x,y))-sum(x)*sum(y)
            if i!=j and vx>0 and vy>0 and cov>0:scores.append((cov*cov/(vx*vy),symbols[j],j))
        top=sorted(scores,key=lambda v:(-v[0],v[1]))[:5]
        if len(top)==5:expected[i]=[x[2] for x in top]
    assert actual==expected
    assert actual[0][:2]==[1,2]


def test_exact_zero_and_gap_match_independent_fraction_sum():
    repeated=np.full((5,6),.1)
    assert exact_peer_gap(repeated,0,[1,2,3,4,5])==0
    rng=np.random.default_rng(45)
    for _ in range(40):
        values=rng.normal(0,.01,(5,6))
        oracle=sum(Fraction(float(v)) for v in values[:,1:].T.ravel())/5-sum(Fraction(float(v)) for v in values[:,0])
        assert exact_peer_gap(values,0,[1,2,3,4,5])==float(oracle)
    assert not exact_positive_peers(repeated,[str(i) for i in range(6)])


def test_prefix_order_missing_calendar_and_no_input_mutation():
    rng=np.random.default_rng(20261020)
    dates=pd.bdate_range('2024-01-01',periods=72)
    codes=[f'{i:06d}' for i in range(62)]
    close=pd.DataFrame(np.exp(rng.normal(0,.01,(72,62)).cumsum(axis=0))*10,index=dates,columns=codes)
    original=close.copy(deep=True)
    whole=peer_diffusion(close,dates,codes)
    assert whole.iloc[:65].isna().all().all() and whole.iloc[65:].notna().any().any()
    pd.testing.assert_frame_equal(whole.iloc[:70],peer_diffusion(close.iloc[:70],dates[:70],codes))
    pd.testing.assert_frame_equal(whole,peer_diffusion(close.iloc[::-1,::-1],dates,codes[::-1]))
    missing=close.drop(index=dates[66])
    result=peer_diffusion(missing,dates,codes)
    assert result.iloc[66:].isna().all().all()
    pd.testing.assert_frame_equal(close,original)


def test_native_identity_aliases_and_compact_dates_restore_original_stock_keys():
    rng=np.random.default_rng(15)
    dates=pd.bdate_range('2024-01-01',periods=67)
    codes=[f'{i:06d}' for i in range(62)]
    prices=pd.DataFrame(np.exp(rng.normal(0,.01,(67,62)).cumsum(axis=0))*10,index=dates,columns=codes)
    local=peer_diffusion(prices,dates,codes).stack(dropna=False)
    source=prices.rename_axis(index='date',columns='symbol').stack(dropna=False)
    source.index=pd.MultiIndex.from_arrays([source.index.get_level_values('date').strftime('%Y%m%d'),
        source.index.get_level_values('symbol')+'.XSHE'],names=['date','symbol'])
    namespace={'Factor':object};exec(native_code(dates,codes),namespace)
    actual=namespace['FixedPeerDiffusionFactor']().calculate({'close':source})
    assert actual.index.equals(source.index) and actual.name=='value'
    np.testing.assert_array_equal(actual.to_numpy(),local.to_numpy())
