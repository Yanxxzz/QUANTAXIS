"""Freeze the exact pool/size comparisons before signal or return evaluation."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from panda_alpha.registry import ResearchRegistry
from run_remaining_tests import sha, write, read

OUT = Path(__file__).parent


def binding(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path)}


def main():
    path = OUT/'test_protocol.json'
    if path.exists():
        raise ValueError('Frozen protocol already exists')
    original = read(OUT.parent/'production_pair_v1/protocol.json')
    ids = {'C01':'6ab09183ecb163ea7228de5d','C02':'6ab09185a7f535324660d47a',
           'C03':'6ab091868b01f62dc5147aee','C04':'6ab091888b01f62dc5147aef',
           'C16':'6ab159288b01f62dc5147bf6','C17':'6ab290616df2a192a47e98b7',
           'F253':'6abbd3fda8ba1ed343bf85bf'}
    definitions = {name: binding(OUT/('definition_'+wid+'.json')) for name,wid in ids.items()}
    pool = read(OUT/'owned_pool_snapshot.json')['content']['data']
    effective = {f['workflow_id'] for f in pool['factors'] if f['status'] in ['active','pending_delete']}
    if effective != {ids[n] for n in ['C01','C02','C03','C04','C16','C17']}:
        raise ValueError('Observed own pool changed; do not silently test a different plan')
    scheduled = next(f for f in pool['factors'] if f['workflow_id']==ids['F253'])
    if scheduled['status']!='pending_effective' or scheduled['effective_trade_date']!='20261015':
        raise ValueError('Observed scheduled F253 transition changed')
    pools = {'CURRENT_POOL':['C01','C02','C03','C04','C16','C17'],
             'CURRENT_ADD_GF':['C01','C02','C03','C04','C16','C17','GF'],
             'RETAINED_POOL':['C03','C04','C16','C17','F253'],
             'RETAINED_ADD_GF':['C03','C04','C16','C17','F253','GF'],
             'RETAINED_REPLACE_C16_GF':['C03','C04','C17','F253','GF']}
    bindings = {n: original['bindings'][n] for n in ['parent','quotes','risk_cache','descriptor','cost_helpers','study_engine']}
    bindings.update(gf_signals=binding(OUT.parent/'production_pair_v1/formation_signals.parquet'),
                    technical_bars=binding('D:/PandaAI-Alpha/stockdb_factor_eval/cache/execution_bars_20210922_20260921.parquet'),
                    technical_manifest=binding('D:/PandaAI-Alpha/stockdb_factor_eval/cache/execution_bars_20210922_20260921.parquet.manifest.json'),
                    snapshot=binding(OUT/'owned_pool_snapshot.json'),
                    runtime=binding(OUT/'run_remaining_tests.py'),
                    freeze_runtime=binding(__file__),
                    source_reader=binding(ROOT/'panda_alpha/native_gf_transfer.py'))
    contract = {'members':definitions, 'source':bindings,
                'full_peer_before_financial_mask':True,
                'prices':'Existing5608-peer as-of HFQ close, unchanged exact mutable frame SHA; other same date/code raw StockDB bars mapped by close/rawClose scale',
                'C17':'AMOUNT/VOLUME is existing raw StockDB amount/volume price ratio scaled by same close adjustment, no100-lot multiplier',
                'rolling':'Observed symbol rows, full-window min_periods, first return NaN, unshrunk Pearson CORR, per-date average percentile RANK',
                'pool':'Each native member direction aligned;1/99percentile winsor,population sd Z within shared support;equal signal average, exactqcut10 topdecile',
                'cohort':'OriginalGF source formed identities intersect all7 exact own member finite fields, unknown inputs excluded before targets and counted, no forward quote selection',
                'size':'OriginalGF formed identities with positive contemporaneous cached total_mv strictly above full received positive-size20pct cutoff; no new signal neutralization',
                'limits':['Historical-vintage/native field parity unverified','Same historical window already exposed','No actual future production source certification','No200period expansion','Prospective scheduled pool backcast is not the actual historical competition pool'],
                'calendar':original['anchors'], 'window':original['window'], 'costs':[.003,.005]}
    contexts = [{'candidate_id':'GF_ADMISSION_'+name+'_20261012','direction':1,
                 'strategy_spec':{'kind':'fixed_exposed_pool_or_size_comparison','contract':contract,
                                  'members':members,'comparison_name':name}}
                for name,members in pools.items()]
    for name in ['SIZE_GF','SIZE_MARKET']:
        contexts.append({'candidate_id':'GF_ADMISSION_'+name+'_20261012','direction':1,
                         'strategy_spec':{'kind':'size_source_stress','contract':contract,'name':name}})
    p = {'id':'GF_REMAINING_ADMISSION_TESTS_20261012','frozen_at_utc':datetime.now(timezone.utc).isoformat(),
         'purpose':'Complete current/planned retained pool marginal and size pressure tests without selecting weights or rescuing a definition',
         'definitions':definitions,'bindings':bindings,'pools':pools,'contexts':contexts,
         'anchors':original['anchors'],'window':original['window'],'costs':[.003,.005],
         'expected_price_view_hash':read(OUT.parent/'production_pair_v1/input_receipt.json')['mutable_price_view_sha256'],
         'direction':1,'cycle':5,'groups':10,'formal_admission':False,'official_runs_authorized':0,
         'existing_GF_control_results_not_replayed':True,'new_contexts':7,'protocol_contract':contract}
    write(path,p)
    digest = sha(path)
    registry = ResearchRegistry(ROOT/'research_runs/research_trials.sqlite3')
    prior = registry.total
    for c in contexts:
        registry.record(c,{'start':original['window']['holding_start'],'end':original['window']['holding_end']},5,10)
    registry.append_event('evaluation',p['id'],{'status':'FROZEN_BEFORE_SIGNAL_AND_RETURN_EVALUATION',
         'protocol':binding(path),'new_contexts':registry.total-prior,'source_proxy_not_PIT_or_native':True,
         'official_runs':0,'pool_operations':0},event_id='gf-admission-tests-freeze:'+digest)
    print(json.dumps({'protocol_sha256':digest,'before':prior,'total':registry.total,'new_contexts':registry.total-prior,'registry_head':registry.head}))
    registry.db.close()


if __name__=='__main__':
    main()
