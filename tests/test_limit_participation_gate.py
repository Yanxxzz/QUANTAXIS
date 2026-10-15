import math
import pytest
from panda_alpha.limit_participation_gate import turnover_gate, financing_gate

@pytest.mark.parametrize('a,b,expected',[(1,1,True),(2,1,False),(1,2,True)])
def test_fixed_turnover_boundary(a,b,expected):
    assert turnover_gate(a,b)['pass'] is expected

@pytest.mark.parametrize('a,b',[(None,1),(1,float('nan')),(0,1),(1,-1),(True,1),(1,float('inf'))])
def test_unknown_not_zero(a,b):
    assert turnover_gate(a,b)=={'known':False,'score':None,'pass':None,'reason':'two_positive_turnovers_required'}

def rows(buy,repay):
    return [dict(eligible=True,fin_value=100,fin_buy_value=buy,fin_refund_value=repay)]*2

def test_financing_units_cancel_and_boundary():
    assert financing_gate(rows(10,10))['pass'] is True
    assert financing_gate(rows(11,10))['pass'] is False
    assert financing_gate(rows(10,20))['score']==financing_gate(rows(10000,20000))['score']

def test_financing_missing_is_not_unlevered():
    x=rows(10,20); x[0]=dict(x[0],eligible=None)
    assert financing_gate(x)['known'] is False
    assert financing_gate(rows(0,0))['known'] is False
