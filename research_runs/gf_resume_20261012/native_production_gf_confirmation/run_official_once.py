"""One concrete authorized native confirmation; never retry create/start."""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from panda_alpha.platform import PandaClient, ExperimentLedger, dispatch, resume, fingerprint
from panda_alpha.registry import ResearchRegistry
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_production_gf_pair import sha, write

UNIT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def bound(b):
    if sha(b['path']) != b['sha256']:
        raise ValueError('Frozen artifact changed: '+str(b['path']))
    return Path(b['path'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['dispatch','collect'])
    args=parser.parse_args()
    p=read(UNIT/'protocol.json')
    assert sha(UNIT/'protocol.json')=='a0fff9fffdea3541e26bbe2259ff2e9ad396f730b86410c51a1ba35b39f81a67'
    c=read(bound(p['candidate']));c['code']=bound(p['code']).read_bytes().decode('utf-8')
    key=fingerprint(c,p['creation_window'],5,10);assert key==p['fingerprint']
    cfg=read(ROOT/'config/panda-alpha.local.json')
    client=PandaClient(cfg['platform']);ledger=ExperimentLedger(ROOT/'research_runs/experiments.sqlite3')
    registry=ResearchRegistry(ROOT/'research_runs/research_trials.sqlite3')
    try:
        existing=ledger.get(key)
        if args.action=='dispatch':
            if existing:
                print('Exact job already exists; collect it, do not dispatch twice')
                return
            authority=read(UNIT/'authorization.json')
            if authority.get('status')!='APPROVED_BY_HUMAN':
                raise ValueError('Concrete funding/estimate scope has not been confirmed')
            grant=authority['grant']
            if (grant['fingerprints']!=[key] or grant['max_runs']!=1 or grant.get('accept_estimated_charge_risk') is not True):
                raise ValueError('Single-run scope/fingerprint mismatch')
            compute=dict(cfg['compute'])
            compute.update(planning_credit_estimate=8,
                           planning_cost_source='Conservative8-point reservation; owned prior successful source runs observed2, historical single runs2-8; not a service cap',
                           estimate_verified_at=authority['confirmed_at_utc'][:10],
                           daily_credit_limit=grant['max_total_credits'],
                           recharge_credit_limit=grant['max_recharge_credits'],
                           estimated_billing_batch_authorization=grant)
            if grant['max_recharge_credits']>0:
                compute['recharge_batch_authorization']=grant
            cfg['compute']=compute
            # Also check raw gift expiry conservatively when its timezone is unspecified.
            wallet=client.balance();expiry=wallet.get('gift_expires')
            if wallet['gift']<8 or not expiry:
                raise ValueError('Current gift headroom/expiry insufficient')
            deadline=datetime.fromisoformat(str(expiry));now=datetime.now(timezone.utc)
            zones=(timezone(timedelta(hours=8)),timezone.utc)
            if not all((deadline if deadline.tzinfo else deadline.replace(tzinfo=z))>now for z in zones):
                raise ValueError('Gift may already have expired')
            def before_dispatch():
                assert any(e['event_id']=='gf-native-confirm-freeze:'+sha(UNIT/'protocol.json') for e in registry.events())
                registry.append_event('evaluation',p['id'],{'phase':'about_to_dispatch_exact_native_context','fingerprint':key,'new_contexts':0,'funding_authority':{'path':str(UNIT/'authorization.json'),'sha256':sha(UNIT/'authorization.json')}},event_id='gf-native-dispatch:'+key)
            job=dispatch(c,p['creation_window'],cfg,ledger,client,'source_probe',
                         before_dispatch=before_dispatch,native_fixture_reader=read_mongo_gf_fixture)
        else:
            if not existing:
                raise ValueError('No existing official Run to collect')
            if existing['state']=='SETTLED':
                job=existing
            else:
                job=resume(key,ledger,client,UNIT/'official_results')
        compact={k:job.get(k) for k in ['candidate_id','fingerprint','state','reserved','actual','factor_id','run_id']}
        write(UNIT/'official_progress.json',{'action':args.action,'observed_at_utc':datetime.now(timezone.utc).isoformat(),'job':compact,'duplicate_dispatches':0})
        print(json.dumps(compact,ensure_ascii=False),flush=True)
    finally:
        registry.db.close();ledger.db.close()


if __name__=='__main__':main()
