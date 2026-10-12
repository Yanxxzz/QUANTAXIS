"""One fixed industry-relative GF risk hypothesis; reuse completed controls."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from panda_alpha import study
from panda_alpha.registry import ResearchRegistry


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda: stream.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(study._clean(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(path)


def relative_rank(frame):
    if frame.duplicated(['date', 'symbol']).any() or frame.sector.isna().any() or frame.sector.eq('UNKNOWN').any():
        raise ValueError('Unique known industry identities required')
    grouped = frame.groupby(['date', 'sector'], sort=False)
    # Midrank keeps a one-stock sector at0.5 rather than incorrectly at1.
    return (grouped.GF.rank(method='average')-.5)/grouped.GF.transform('size')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--sha',required=True)
    args=parser.parse_args()
    if sha(args.protocol)!=args.sha:
        raise ValueError('Frozen protocol changed')
    p=json.loads(args.protocol.read_text(encoding='utf-8'));out=args.protocol.parent
    if (out/'checkpoint.json').exists():
        raise ValueError('Owned review already started; inspect checkpoint, do not repeat')
    for b in p['bindings'].values():
        if sha(b['path'])!=b['sha256']:
            raise ValueError('Bound input changed: '+b['path'])
    r=ResearchRegistry(p['registry'])
    assert any(e['event_id']=='gf-sector-freeze:'+args.sha for e in r.events())
    r.db.close()
    cp={'status':'RUNNING','pid':os.getpid(),'completed':[],'payloads':{},'errors':[],'protocol_sha256':args.sha}
    write(out/'checkpoint.json',cp)
    try:
        signals=pd.read_parquet(p['bindings']['base_signals']['path'])
        sectors=pd.read_parquet(p['bindings']['components']['path'],
                                columns=['date','symbol','sector','sector_asof','sector_pit_usable'],
                                filters=[('date','in',p['anchors'])])
        signals=signals.merge(sectors,on=['date','symbol'],how='left',validate='one_to_one')
        known=signals.sector_pit_usable.eq(True)&pd.to_datetime(signals.sector_asof,errors='coerce').le(pd.to_datetime(signals.date))
        if not known.all() or signals.sector.isna().any() or signals.sector.eq('UNKNOWN').any():
            raise ValueError('Original cohort industry asof missing; no silent universe trimming')
        signals['SECTOR_GF']=relative_rank(signals)
        base_key=pd.MultiIndex.from_frame(signals[['date','symbol']])
        if len(signals)!=p['expected_formation_rows'] or base_key.has_duplicates:
            raise ValueError('Original common cohort changed')
        spec=importlib.util.spec_from_file_location('cost_helpers',p['bindings']['cost_helpers']['path'])
        cost=importlib.util.module_from_spec(spec);spec.loader.exec_module(cost)
        parent=json.loads(Path(p['bindings']['parent']['path']).read_text(encoding='utf-8'))
        calendar=parent['calendar'];positions={d:i for i,d in enumerate(calendar)}
        days=[d for d in calendar if p['window']['holding_start']<=d<=p['window']['holding_end']]
        schedule={};target_reviews=[]
        for day in p['anchors']:
            rows=signals[signals.date.eq(day)]
            groups,status=study.quantile_groups(rows[['symbol','SECTOR_GF']].rename(columns={'SECTOR_GF':'value'}),10,10)
            if status!='groups_available':raise ValueError('Ten industry-relative groups pending: '+day)
            selected=groups['10'];schedule[calendar[positions[day]+1]]={'decision_date':day,'codes':selected}
            selected_sectors=rows.set_index('symbol').loc[selected,'sector'].value_counts(normalize=True)
            target_reviews.append({'date':day,'cohort':len(rows),'selected':len(selected),
                                   'max_industry_target_fraction':float(selected_sectors.max()),'industries_selected':len(selected_sectors)})
        small=signals[['date','symbol','SECTOR_GF']]
        small.to_parquet(out/'formation_signals.parquet',index=False,compression='zstd')
        write(out/'input_receipt.json',{'base_cohort_exact':True,'formation_rows':len(signals),
                                      'source_and_windows_unchanged':True,'signals_sha256':sha(out/'formation_signals.parquet'),
                                      'targets_sha256':study.object_hash({'days':days,'schedule':schedule}),
                                      'target_reviews':target_reviews,'new_OHLC_not_yet_read':True,
                                      'formula':p['formula'],'price_snapshot_saved':False})
        print('INDUSTRY_RELATIVE_TARGETS_FROZEN',flush=True)
        book=cost.quote_book(p,set(signals.symbol))
        results=[];daily=[]
        for fee in p['costs']:
            key=cost.path_key('SECTOR_GF',fee)
            try:
                run=study.replay_portfolio('SECTOR_GF',schedule,days,book,fee)
                record,ledger=cost.compact_run(run,days)
                cp['payloads'][key]={'record':record,'daily':ledger};cp['completed'].append(key)
                results.append(record);daily.extend(ledger);del run
            except ValueError as e:
                cp['errors'].append({'path':key,'error':str(e),'economic_failure_claimed':False})
            write(out/'checkpoint.json',cp)
            print('FIXED_SECTOR_COST_PATH_FINISHED_OR_PENDING',key,flush=True)
        if cp['errors']:
            cp.update(status='EXECUTION_PENDING',pid=None);write(out/'checkpoint.json',cp)
            write(out/'summary.json',{'status':'EXECUTION_PENDING','errors':cp['errors'],'partial_metrics_withheld':True})
            return
        daily=pd.DataFrame(daily);daily.to_parquet(out/'daily_net_ledger.parquet',index=False,compression='zstd')
        controls=json.loads(Path(p['bindings']['base_summary']['path']).read_text(encoding='utf-8'))
        old_daily=pd.read_parquet(p['bindings']['base_ledger']['path']);comparison=[]
        for result in results:
            fee=result['cost'];a=daily[daily.cost.eq(fee)].set_index('date')
            for name in ['GF','F141_CONTROL','COMMON_MARKET']:
                b=old_daily[old_daily.cost.eq(fee)&old_daily.name.eq(name)].set_index('date')
                assert a.index.equals(b.index)
                control=next(x for x in controls['results'] if x['name']==name and x['cost']==fee)
                comparison.append({'cost':fee,'control':name,
                                   'return_difference':result['metrics']['compounded_return']-control['metrics']['compounded_return'],
                                   'sharpe_difference':result['metrics']['sharpe']-control['metrics']['sharpe'],
                                   'drawdown_difference':result['metrics']['max_drawdown']-control['metrics']['max_drawdown'],
                                   'daily_return_correlation':a.net_return.corr(b.net_return)})
        summary={'status':'TWO_FIXED_SECTOR_RANK_PATHS_COMPLETE','completed_at_utc':datetime.now(timezone.utc).isoformat(),
                 'protocol_sha256':args.sha,'window':p['window'],'formation_days':51,'holding_days':len(days),
                 'results':results,'comparisons':comparison,'errors':[],
                 'base_controls_replayed':False,'cohort_unchanged':True,'new_contexts':1,'official_spent':0,
                 'formal_admission':False,'independent_alpha_certified':False,'full_PIT':False,
                 'labels_already_exposed':True,'sector_ranking_not_exact_sector_weight_neutrality':True,
                 'limitations':p['limitations'],'ledger_sha256':sha(out/'daily_net_ledger.parquet')}
        write(out/'summary.json',summary);cp.update(status=summary['status'],pid=None);cp.pop('payloads',None);write(out/'checkpoint.json',cp)
        print('FIXED_SECTOR_RANK_REVIEW_COMPLETE',flush=True)
    except Exception as e:
        cp.update(status='EXECUTION_PENDING',pid=None,error=type(e).__name__+': '+str(e),economic_failure_claimed=False)
        write(out/'checkpoint.json',cp);raise


if __name__=='__main__':main()
