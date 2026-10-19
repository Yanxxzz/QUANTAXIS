from fractions import Fraction

import numpy as np
import pandas as pd
import pytest

from panda_alpha.frontier_factors import (gap_ratio, base_expansion_ratio, rank_ratios,
    market_scores, fixed_capital_scores, asset_acceleration_scores, native_code)


def test_gap_exact_reference_and_null_domain():
    for p,o,c,expected in [(100,101,102,Fraction(2,100)),(100,101,100,Fraction(1,100)),
                           (100,99,98,Fraction(-2,100)),(100,99,100,Fraction(-1,100))]:
        assert Fraction(*gap_ratio(p,o,c)) == expected
    for values in [(100,100,102),(0,101,102),(100,True,102),(100,101,np.inf),(np.nan,1,2)]:
        assert gap_ratio(*values) is None


def test_expansion_does_not_overflow_or_promote_constant_base():
    assert base_expansion_ratio(200,2,100,1) is None
    assert base_expansion_ratio(1e300,1e300,1e200,1e200) is None
    value = base_expansion_ratio(1e300,1e200,1e200,1e200)
    assert value and Fraction(*value) == Fraction(1e300)/Fraction(1e200)
    assert base_expansion_ratio(100,True,100,1) is None


def test_exact_ties_and_adjacent_float_order():
    tiny = np.nextafter(1.,2.)
    assert rank_ratios([(1,1),(2,2),None,(int(tiny.as_integer_ratio()[0]),int(tiny.as_integer_ratio()[1]))]).tolist()[:2] == [.5,.5]
    result = rank_ratios([(1,1),(2,2),(3,2)],centered=True)
    assert result.tolist() == [1/3,1/3,5/6]


def test_five_hundred_random_cases_against_fraction_reference():
    rng=np.random.default_rng(219)
    for _ in range(500):
        p,o,c=(float(x) for x in rng.uniform(.001,1e5,size=3))
        ratio=gap_ratio(p,o,c)
        expected=(Fraction(o)-Fraction(p))/Fraction(p)
        expected*=2 if ((o>p and c>o) or (o<p and c<o)) else 1
        assert Fraction(*ratio)==expected
        v,t,pv,pt=(float(x) for x in rng.uniform(.001,1e10,size=4))
        ratio=base_expansion_ratio(v,t,pv,pt)
        expected=Fraction(v)*Fraction(pt)/(Fraction(t)*Fraction(pv))
        assert (ratio is None)==(expected<=1)
        if ratio:
            assert Fraction(*ratio)==expected


def sample():
    dates = pd.bdate_range('2026-06-01',periods=9)
    index = pd.MultiIndex.from_product([dates,['a','b','c']],names=['date','symbol'])
    frame = pd.DataFrame({'open':101.,'close':102.,'volume':np.arange(len(index))+100.,'turnover':1.},index=index)
    return dates,frame


@pytest.mark.parametrize('kind',['gap','base'])
def test_calendar_prefix_permutation_and_whole_day_missing(kind):
    dates,frame=sample()
    fields={k:frame[k] for k in frame}
    baseline=market_scores(fields,dates,kind)
    shuffled=frame.sample(frac=1,random_state=4)
    reordered=market_scores({k:shuffled[k] for k in shuffled},dates,kind).reindex(frame.index)
    pd.testing.assert_series_equal(baseline,reordered)
    prefix=frame.loc[:dates[6]]
    actual=market_scores({k:prefix[k] for k in prefix},dates[:7],kind)
    pd.testing.assert_series_equal(actual,baseline.reindex(prefix.index))
    missing=frame.drop(dates[2],level='date')
    actual=market_scores({k:missing[k] for k in missing},dates,kind)
    affected=dates[3] if kind=='gap' else dates[7]
    assert actual.xs(affected,level='date').isna().all()


def test_misaligned_and_duplicate_fields_rejected():
    dates,frame=sample()
    with pytest.raises(ValueError,match='key sets'):
        market_scores({'open':frame.open,'close':frame.close.iloc[:-1]},dates,'gap')
    with pytest.raises(ValueError,match='unique'):
        market_scores({'open':pd.concat([frame.open,frame.open]),'close':frame.close},dates,'gap')


def test_power_of_two_units_preserve_gap_and_base_order():
    dates,frame=sample()
    for kind,columns in [('gap',['open','close']),('base',['volume'])]:
        old=market_scores({k:frame[k] for k in frame},dates,kind)
        scaled=frame.copy()
        scaled[columns]*=2.**300
        new=market_scores({k:scaled[k] for k in scaled},dates,kind)
        pd.testing.assert_series_equal(old,new)


def test_fixed_capital_keeps_zero_rejects_bad_domain_before_ranking():
    index=pd.MultiIndex.from_product([[pd.Timestamp('2026-06-18')],list('abcde')],names=['date','symbol'])
    f=pd.Series([0.,50.,100.,101.,True],index=index,dtype=object)
    a=pd.Series(100.,index=index)
    out=fixed_capital_scores({'bs_net_fix_assets':f,'bs_total_assets':a})
    assert out.iloc[:3].tolist()==[1/6,.5,5/6]
    assert out.iloc[3:].isna().all()


def test_asset_acceleration_exact_zero_ties_and_contraction():
    index=pd.MultiIndex.from_product([[pd.Timestamp('2026-09-01')],list('abcde')],names=['date','symbol'])
    states=pd.DataFrame({'20260630':[100.,400.,200.,50.,np.nan],
        '20260331':[100.,200.,100.,100.,100.],'20251231':[100.,100.,100.,100.,100.]},index=index)
    out=asset_acceleration_scores(states)
    assert out.iloc[:3].tolist()==[1/3,1/3,5/6]
    assert out.iloc[3:].isna().all()
    with pytest.raises(ValueError,match='PERIOD_ASOF'):
        native_code('acceleration')
