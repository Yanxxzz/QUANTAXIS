import numpy as np
import pandas as pd
import pytest
from panda_alpha.factor_blend import equal_signal


def fixture():
    return pd.DataFrame({'date':['2026-01-05']*5+['2026-01-12']*5,
                         'symbol':list('abcde')*2,
                         'profit':[1,2,3,4,5,10,20,30,40,50],
                         'risk':[8,6,4,2,0,80,60,40,20,0]})


def test_direction_zero_preserves_complementary_order():
    frame=fixture();result=equal_signal(frame,{'profit':1,'risk':0})
    assert result.groupby(frame.date).apply(lambda r:r.is_monotonic_increasing).all()
    assert np.allclose(result.iloc[:5],result.iloc[5:])
    assert np.allclose(result.groupby(frame.date).mean(),0)


def test_units_and_offsets_do_not_change_equal_weights():
    frame=fixture();a=equal_signal(frame,{'profit':1,'risk':0})
    frame['profit']=frame.profit*1e9+300;frame['risk']=frame.risk*.001-12
    assert np.allclose(a,equal_signal(frame,{'profit':1,'risk':0}))


def test_missing_never_reweights_remaining_component():
    frame=fixture();frame.loc[0,'risk']=np.nan
    with pytest.raises(ValueError,match='no automatic reweighting'):equal_signal(frame,{'profit':1,'risk':0})


def test_constant_is_not_manufactured_as_information():
    frame=fixture();frame.loc[:4,'risk']=1
    with pytest.raises(ValueError,match='Constant component'):equal_signal(frame,{'profit':1,'risk':0})


def test_direction_boolean_rejected():
    with pytest.raises(ValueError,match='direction'):equal_signal(fixture(),{'profit':True})


def test_duplicate_identity_rejected():
    frame=fixture();frame.loc[1,'symbol']='a'
    with pytest.raises(ValueError,match='Duplicate'):equal_signal(frame,{'profit':1})


def test_rows_permuted_without_changing_stock_signal():
    frame=fixture();a=equal_signal(frame,{'profit':1,'risk':0})
    b=equal_signal(frame.iloc[::-1],{'profit':1,'risk':0})
    assert np.allclose(a,b.reindex(a.index))
