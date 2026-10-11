"""Consume one registered source-limited event pilot; never replay labels."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from panda_alpha.event_study import evaluate_event_windows
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key
from panda_alpha.sources import provider_from_config, resolve_source


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    cli=argparse.ArgumentParser();cli.add_argument('--protocol',type=Path,required=True)
    cli.add_argument('--protocol-sha',required=True);cli.add_argument('--execute',action='store_true')
    args=cli.parse_args()
    if not args.execute:print('DEFERRED_NO_PRICE_OR_LABEL_READ');return
    if sha(args.protocol)!=args.protocol_sha:raise ValueError('frozen event protocol changed')
    p=json.loads(args.protocol.read_text(encoding='utf-8'))
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']):
        raise ValueError('human deadline reached before labels')
    output=Path(p['output'])
    if output.exists():raise ValueError('complete or pending receipt exists; no implicit label replay')
    for b in p['bindings'].values():
        if sha(b['path'])!=b['sha256']:raise ValueError('source/runtime binding changed')
    r=ResearchRegistry(p['registry_path'])
    key=trial_key(strategy_definition(p['candidate'],p['window'],5,2))
    if key not in r.registrations():raise ValueError('registration before prices/labels required')
    peers=json.loads(Path(p['bindings']['peers']['path']).read_text(encoding='utf-8'))['peer_codes']
    events=p['candidate']['strategy_spec']['event_sources']
    cfg=json.loads((ROOT/'config/panda-alpha.local.json').read_text(encoding='utf-8'))
    profile=resolve_source(cfg['data'],p['price_profile'])
    public={k:profile[k] for k in ('name','database','price_source','reference_database','calendar_source')}
    if public!=p['public_price_profile']:raise ValueError('declared source selection changed')
    provider=provider_from_config(cfg,p['price_profile'])
    try:
        codes=sorted({e['symbol'] for e in events}|set(peers))
        daily=provider.daily(codes,p['calendar'][0],p['calendar'][-1],adjustment='qfq',expected_dates=p['calendar'])
        if daily.coverage['daily']['duplicate_rows']:
            raise ValueError('raw duplicate quote identities require source reconciliation')
        result=evaluate_event_windows(daily.frame,events,p['calendar'],peers,horizon=5,costs=(0.003,0.005))
        quoted=daily.frame[['date','symbol','open','close','adjustment','source']].sort_values(['date','symbol'])
        result['quote_subset_sha256']=hashlib.sha256(quoted.to_json(orient='records',date_format='iso',double_precision=15).encode()).hexdigest()
        result['price_source_review']={'profile':p['price_profile'],'adjustment':'same-source qfq',
            'positive_bar_contract':'Existing accepted StockDB/AXIS provider; fills, price limits and contemporaneous executable liquidity not certified',
            'rows':len(daily.frame),'duplicates':daily.coverage['daily']['duplicate_rows'],
            'invalid_rows':daily.coverage['daily']['invalid_rows'],'known_suspended_rows':daily.coverage['daily']['known_suspended_rows'],
            'missing_by_code':daily.coverage['daily']['missing_by_code']}
    except Exception as error:
        result={'status':'SOURCE_OR_EXECUTION_PENDING_NO_COMPLETE_EVENT_RESULT','source_limited_preliminary':True,
                'error_type':type(error).__name__,'error':str(error)[:1024],'economic_failure':False,
                'eligible_for_review':False,'automatic_replay':False}
    result.update(protocol_sha256=args.protocol_sha,registration_key=key,registry_total=r.total,
                  completed_at_utc=datetime.now(timezone.utc).isoformat(),official_calls=0,
                  new_registered_contexts=1,original_source_files_or_market_store_changed=False,
                  source_limits=p['limits'])
    with output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    r.append_event('evaluation' if result['status']=='COMPLETE_FIXED_EVENT_DIAGNOSTIC' else 'source_pending',key,
                   {'result':{'path':str(output),'sha256':sha(output)},'status':result['status'],
                    'new_contexts':0,'eligible_for_review':False},event_id='unlock-event-pilot-result:'+sha(output))
    r.db.close()
    print(json.dumps({'status':result['status'],'events':result.get('complete_event_paths',0),
                      'RankIC':result.get('direction_aligned_event_RankIC'),'result_sha256':sha(output),'total':result['registry_total']}))


if __name__=='__main__':main()
