import math
from panda_alpha.limit_credit_crowding import credit_crowding

def m():return {'amount':10000.,'volume':1000,'low':9.9,'high':10.1,'close':10.,'turnover':10.}
def test_exact_fixed_boundaries_and_explicit_zero_buy():
    r=credit_crowding(5000,[0,2000],[m(),m()],10000)
    assert r['known'] and r['stock_ratio']==.05 and r['buy_ratio']==.1
    assert r['stock_pass'] and r['buy_pass']
def test_distinct_stock_and_participation_signals():
    r=credit_crowding(6000,[1,1],[m(),m()],10000)
    assert r['known'] and not r['stock_pass'] and r['buy_pass']
    r=credit_crowding(1000,[2000,2000],[m(),m()],10000)
    assert r['stock_pass'] and not r['buy_pass']
def test_missing_not_zero():
    for b in [None,float('nan'),float('inf'),0,-1,True]:
        assert not credit_crowding(b,[0,0],[m(),m()],10000)['known']
def test_unit_and_float_role_mismatch_are_pending():
    a=m();a['amount']/=1000
    assert not credit_crowding(1,[0,0],[a,m()],10000)['known']
    assert not credit_crowding(1,[0,0],[m(),m()],20000)['known']
def test_market_total_bounds_credit_and_invalid_order():
    assert not credit_crowding(1,[11000,0],[m(),m()],10000)['known']
    a=m();a['low']=11
    assert not credit_crowding(1,[0,0],[a,m()],10000)['known']
def test_scale_equivalence_of_monetary_units():
    a=m();b=m()
    r=credit_crowding(1234,[12,34],[a,b],10000)
    for x in [a,b]:
        for k in ['amount','low','high','close']:x[k]*=10
    s=credit_crowding(12340,[120,340],[a,b],10000)
    assert r==s
