"""A conditional domain is explicit evidence, never a smaller inferred universe."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from panda_alpha.native_preflight import (build_native_preflight, verify_native_preflight,
    NativePreflightError, CONDITIONAL_RESEARCH_PURPOSE)
from panda_alpha.platform import fingerprint
from panda_alpha.team.evidence import artifact


CODE = '''class ConditionalFactor(Factor):
    def calculate(self,factors):
        x=factors['close'].series
        return x.where(x<.5).rename('value')
'''


def setup_case(tmp_path, code=CODE, mutation=None):
    dates=pd.bdate_range('2025-01-01',periods=10)
    frame=pd.DataFrame([(d,f'{j:06d}',j/40) for d in dates for j in range(1,41)],
                       columns=['date','symbol','close'])
    fixture=tmp_path/'fixture.parquet';frame.to_parquet(fixture,index=False)
    mask=frame[['date','symbol']].copy();mask['eligible']=frame.close.lt(.5)
    if mutation is not None:mask=mutation(mask)
    mask_path=tmp_path/'mask.parquet';mask.to_parquet(mask_path,index=False)
    builder=tmp_path/'mask_builder.py';builder.write_text("eligible = close < .5\n")
    definition=tmp_path/'definition.txt';definition.write_text('Frozen domain close<.5, not output-derived.\n')
    source=tmp_path/'source.txt';source.write_text('Synthetic input fixture for gate regression only.\n')
    contract=tmp_path/'observed.txt';contract.write_text('date/symbol Series(value) interface\n')
    window={'start':str(dates.min().date()),'end':str(dates.max().date())}
    domain={'schema':'native-conditional-domain-v1','window':window,'cycle':5,'groups':10,
        'fixture':artifact(fixture),'eligibility_mask':artifact(mask_path),'mask_builder':artifact(builder),
        'definition_artifacts':[artifact(definition)]}
    candidate={'code':code,'direction':0,'native_preflight_purpose':CONDITIONAL_RESEARCH_PURPOSE,
        'native_conditional_contract':domain,'native_preflight_path':str(tmp_path/'receipt.json')}
    sources=[source,mask_path,builder,definition]
    r=build_native_preflight(candidate,window,5,10,fixture,sources,[contract],tmp_path/'receipt.json')
    return candidate,window,r,(fixture,sources,contract,mask_path)


def test_declared_domain_passes_without_claiming_full_universe(tmp_path):
    c,w,r,paths=setup_case(tmp_path)
    assert r['research_replay_passed'] is True and r['passed'] is False
    assert r['proof']['eligible_point_coverage']==1
    assert r['proof']['outside_domain_finite_points']==0
    assert r['proof']['coverage_evaluation_dates']==10
    assert r['proof']['median_symbol_coverage']<.8
    assert r['scope']['full_A_certified'] is False
    assert verify_native_preflight(c,w,5,10)['research_replay_passed']
    strict={k:v for k,v in c.items() if k not in ['native_preflight_purpose','native_conditional_contract']}
    failed=build_native_preflight(strict,w,5,10,paths[0],paths[1],[paths[2]],tmp_path/'strict.json')
    assert failed['passed'] is False and 'cross-sectional source coverage' in failed['failure']['message']


@pytest.mark.parametrize('code,reason',[
    (CODE.replace("x.where(x<.5)","x"),'outside the frozen domain'),
    (CODE.replace("x.where(x<.5)","x.where((x<.5)&(x>.1))"),'missing inside the frozen domain'),
])
def test_domain_leak_or_suppressed_eligible_point_stops(tmp_path,code,reason):
    _,_,r,_=setup_case(tmp_path,code)
    assert r['research_replay_passed'] is False and reason in r['failure']['message']


@pytest.mark.parametrize('mutation',[
    lambda m:m.iloc[:-1],
    lambda m:pd.concat([m,m.iloc[:1]],ignore_index=True),
    lambda m:m.assign(eligible=m.eligible.astype(float)),
    lambda m:m.assign(eligible=m.eligible.mask(m.index==0)),
])
def test_mask_must_be_boolean_complete_and_unique(tmp_path,mutation):
    _,_,r,_=setup_case(tmp_path,mutation=mutation)
    assert r['research_replay_passed'] is False


def test_mask_change_and_contract_change_invalidate_proof(tmp_path):
    c,w,r,paths=setup_case(tmp_path)
    assert r['research_replay_passed']
    changed=copy.deepcopy(c);changed['native_conditional_contract']['mask_builder']['sha256']='0'*64
    assert fingerprint(changed,w,5,10)!=fingerprint(c,w,5,10)
    with pytest.raises(NativePreflightError,match='parameters changed'):
        verify_native_preflight(changed,w,5,10)
    mask=pd.read_parquet(paths[3]);mask.loc[0,'eligible']=False;mask.to_parquet(paths[3],index=False)
    with pytest.raises(NativePreflightError,match='evidence changed'):
        verify_native_preflight(c,w,5,10)


def test_empty_or_sparse_date_domain_does_not_trim_dates(tmp_path):
    code=CODE.replace("x.where(x<.5)","x.where((x<.5)&(x.index.get_level_values('date')<pd.Timestamp('2025-01-07')))")
    code='import pandas as pd\n'+code
    def sparse(m):return m.assign(eligible=m.eligible & m.date.lt('2025-01-07'))
    _,_,r,_=setup_case(tmp_path,code,mutation=sparse)
    assert r['research_replay_passed'] is False
    assert 'coverage' in r['failure']['message']


def test_contract_requires_exact_dispatch_window(tmp_path):
    c,w,r,_=setup_case(tmp_path)
    assert r['research_replay_passed']
    changed=copy.deepcopy(c);changed['native_conditional_contract']['window']['start']='2025-01-02'
    with pytest.raises(ValueError,match='dispatch parameters'):
        fingerprint(changed,w,5,10)
