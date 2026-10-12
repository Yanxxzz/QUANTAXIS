"""One frozen source-domain expansion, unchanged cost-beta definition."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import os
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from panda_alpha import study
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
from panda_alpha.registry import ResearchRegistry
from panda_alpha.quote_labels import fixed_forward_open_labels
from panda_alpha.execution_status import add_suspension_statuses


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda:stream.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path,obj):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(study._clean(obj),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8');tmp.replace(path)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--protocol',type=Path,required=True);ap.add_argument('--sha',required=True);ap.add_argument('--resume',action='store_true');ap.add_argument('--runtime-repair',type=Path);ap.add_argument('--repair-sha');a=ap.parse_args()
    if sha(a.protocol)!=a.sha:raise ValueError('Frozen liquidity pilot protocol changed')
    p=read(a.protocol);out=a.protocol.parent
    repair = None
    if a.runtime_repair:
        if sha(a.runtime_repair)!=a.repair_sha:raise ValueError('Runtime repair hash changed')
        repair=read(a.runtime_repair)
        if repair['protocol_sha256']!=a.sha or repair['original_runtime']!=p['bindings']['runtime']:
            raise ValueError('Runtime repair does not bind the original frozen unit')
        p['bindings']['runtime']=repair['new_runtime']
        for name in ('status_sidecar', 'status_consumer'):
            if name in repair:
                p['bindings'][name] = repair[name]
    if (out/'summary.json').exists() and read(out/'summary.json')['status']=='ALL_FOUR_FIXED_DOMAIN_PATHS_COMPLETE':raise ValueError('Completed pilot must not be replayed')
    for b in p['bindings'].values():
        if sha(b['path'])!=b['sha256']:raise ValueError('Bound input changed: '+b['path'])
    r=ResearchRegistry(ROOT/'research_runs/research_trials.sqlite3')
    if not any(e['event_id']=='liquidity-domain-freeze:'+a.sha for e in r.events()):raise ValueError('Pilot not registered before inputs/labels')
    r.db.close()
    cp_path=out/'checkpoint.json'
    if cp_path.exists() and not a.resume:raise ValueError('Check old PID/checkpoint before explicit resume')
    cp=read(cp_path) if cp_path.exists() else {'completed':{},'errors':[]}
    cp.update(status='RUNNING',pid=os.getpid());write(cp_path,cp)
    try:
        if (out/'input_receipt.json').exists():
            receipt=read(out/'input_receipt.json')
            if sha(out/'formation_signals.parquet')!=receipt['formation_signals_sha256']:raise ValueError('Frozen signals changed')
            signals=pd.read_parquet(out/'formation_signals.parquet')
        else:
            print('READ_EXISTING_FULL_PEER_PRICE_AND_AMOUNT_ONCE',flush=True)
            frame=read_mongo_gf_fixture(p['bindings']['descriptor']['path'])
            actual=hashlib.sha256(pd.util.hash_pandas_object(frame,index=True).values.tobytes()).hexdigest()
            if actual!=p['expected_price_view_hash']:raise ValueError('Existing accepted price view changed')
            amounts=pd.read_parquet(p['bindings']['technical_bars']['path'],columns=['date','code','amount'],filters=[('date','>=',20240603),('date','<=',20260903)])
            amounts['date']=pd.to_datetime(amounts.date.astype(str),format='%Y%m%d').dt.strftime('%Y-%m-%d');amounts['code']=amounts.code.astype(str).str.zfill(6)
            frame=frame.merge(amounts.rename(columns={'code':'symbol'}),on=['date','symbol'],how='left',validate='one_to_one')
            frame['date']=pd.to_datetime(frame.date);frame=frame.set_index(['date','symbol']).sort_index()
            env={'Factor':object,'np':np,'pd':pd}
            exec(compile(Path(p['bindings']['native_code']['path']).read_text(encoding='utf-8'),'<frozen-native-cost-beta>','exec'),env)
            candidate=env['MarketCostShockProtection']()
            values=candidate.calculate({'close':frame.close,'amount':frame.amount})
            formed=values[values.index.get_level_values('date').isin(pd.to_datetime(p['anchors']))].rename('LQ01').reset_index()
            formed['date']=formed.date.dt.strftime('%Y-%m-%d')
            original_formed=len(formed);valid=formed.symbol.isin(p['fixed_scope_codes'])&np.isfinite(formed.LQ01)
            signals=formed[valid].copy().sort_values(['date','symbol']).reset_index(drop=True)
            if sorted(signals.date.unique())!=p['anchors']:raise ValueError('Frozen anchor support pending')
            signals.to_parquet(out/'formation_signals.parquet',index=False,compression='zstd')
            receipt={'all_peer_formation_rows':original_formed,'fixed_scope_codes':len(p['fixed_scope_codes']),
                'formed_rows':len(signals),'finite_assets_by_date':signals.groupby('date').size().to_dict(),
                'input_amount_missing_rows':int(frame.amount.isna().sum()),'input_frame_rows':len(frame),
                'formation_signals_sha256':sha(out/'formation_signals.parquet'),'native_financial_source_required':False,
                'new_labels_read':0,'source_scope':f"Original fixed{len(p['fixed_scope_codes'])}SHSZ source-parent universe; no future quote gate; historical PIT membership unverified",
                'native_remote_input_value_parity_certified':False,'forward_quote_availability_selection':False}
            write(out/'input_receipt.json',receipt)
        parent=read(p['bindings']['parent']['path']);cal=parent['calendar'];pos={d:n for n,d in enumerate(cal)}
        days=[d for d in cal if p['window']['holding_start']<=d<=p['window']['holding_end']]
        schedules={'LQ01':{},'COMMON_MARKET':{}}
        for day in p['anchors']:
            rows=signals[signals.date==day];entry=cal[pos[day]+1]
            groups,status=study.quantile_groups(rows[['symbol','LQ01']].rename(columns={'LQ01':'value'}),10,30)
            if status!='groups_available':raise ValueError('Exact10groups pending: '+day)
            schedules['LQ01'][entry]={'decision_date':day,'codes':groups['10']}
            schedules['COMMON_MARKET'][entry]={'decision_date':day,'codes':sorted(rows.symbol)}
        write(out/'target_receipt.json',{'target_sha256':study.object_hash(schedules),'holding_days':len(days),'prices_for_forward_fills_not_yet_read':True})
        spec=importlib.util.spec_from_file_location('existing_cost_helpers',p['bindings']['cost_helpers']['path']);helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        book=helper.quote_book(p,set(signals.symbol))
        if repair and 'status_sidecar' in repair:
            sidecar=read(repair['status_sidecar']['path'])
            if sidecar['protocol_sha256'] != a.sha:
                raise ValueError('Historical status sidecar belongs to another unit')
            for source in sidecar['evidence_bindings']:
                if sha(source['path']) != source['sha256']:
                    raise ValueError('Bound suspension evidence changed')
            added=add_suspension_statuses(book, sidecar['rows'])
            print('BOUND_HISTORICAL_SUSPENSION_KEYS_ADDED',added,flush=True)
        for fee in p['costs']:
            for name in schedules:
                key=helper.path_key(name,fee)
                if key in cp['completed']:continue
                run=study.replay_portfolio(name,schedules[name],days,book,fee)
                record,ledger=helper.compact_run(run,days);cp['completed'][key]={'record':record,'daily':ledger};write(cp_path,cp)
                print('COMPLETE_COST_PATH',key,flush=True)
        daily=pd.DataFrame([row for result in cp['completed'].values() for row in result['daily']]);daily.to_parquet(out/'daily_net_ledger.parquet',index=False,compression='zstd')
        reviews=[]
        for fee in p['costs']:
            c=daily[(daily.name=='LQ01')&(daily.cost==fee)];b=daily[(daily.name=='COMMON_MARKET')&(daily.cost==fee)]
            if not c.date.reset_index(drop=True).equals(b.date.reset_index(drop=True)):raise ValueError('Comparator calendar mismatch')
            reviews.append({'cost':fee,'relative_terminal_wealth':c.nav.iloc[-1]/b.nav.iloc[-1]-1,
                'annualized_relative_wealth':(c.nav.iloc[-1]/b.nav.iloc[-1])**(252/len(c))-1,
                'metrics_recomputed':study.performance(c.net_return.reset_index(drop=True))})
        # Bound previously exposed five-day labels; no partial-path tuning.
        old_labels=pd.read_parquet(p['bindings']['labels']['path'],columns=['date','symbol','forward_return','label_available'])
        label_quotes=pd.read_parquet(p['bindings']['quotes']['path'],columns=['date','symbol','open'])
        labels=fixed_forward_open_labels(signals,label_quotes,cal,preserve_unavailable=old_labels)
        checked=labels.merge(old_labels.rename(columns={'forward_return':'old_return','label_available':'old_available'}),on=['date','symbol'],how='left',validate='one_to_one')
        matched=checked.old_available.eq(True)
        if (not checked.loc[matched,'label_available'].all() or
                not np.array_equal(checked.loc[matched,'forward_return'].to_numpy(),checked.loc[matched,'old_return'].to_numpy())):
            raise ValueError('Same fixed label source no longer reproduces existing matched labels')
        joined=signals.merge(labels,on=['date','symbol'],how='left',validate='one_to_one');usable=joined.label_available.eq(True)&np.isfinite(joined.forward_return)
        write(out/'label_receipt.json',{'old_true_matched':int(matched.sum()),'old_true_numeric_exact':True,
            'source_label_keys':len(labels),'original_formed_denominator':len(signals),'available':int(usable.sum()),
            'missing_or_nonpositive_open':int((~usable).sum()),'old_false_preserved':True,'horizon':'t+1open to t+6open'})
        information=[{'date':d,'assets':len(g),'RankIC':g.LQ01.corr(g.forward_return,method='spearman')} for d,g in joined[usable].groupby('date')]
        summary={'status':'ALL_FOUR_FIXED_DOMAIN_PATHS_COMPLETE','completed_at_utc':datetime.now(timezone.utc).isoformat(),
            'protocol_sha256':a.sha,'results':[x['record'] for x in cp['completed'].values()],
            'cost_comparisons':reviews,'formation_periods':len(p['anchors']),'holding_days':len(days),
            'mean_RankIC':float(np.mean([x['RankIC'] for x in information])),
            'information_periods':information,'usable_labels':int(usable.sum()),'original_formed_label_denominator':len(signals),
            'missing_or_unusable_labels':int((~usable).sum()),'labels_previously_exposed':True,
            'new_contexts':2,'official_spent':0,'formal_admission':False,'full_PIT':False,'errors':[],
            'ledger_sha256':sha(out/'daily_net_ledger.parquet')}
        write(out/'summary.json',summary);cp.update(status='COMPLETED',pid=None,completed_paths=list(cp['completed']),completed={});write(cp_path,cp)
    except Exception as e:
        cp.update(status='SOURCE_OR_EXECUTION_PENDING',pid=None,error=type(e).__name__+': '+str(e));write(cp_path,cp);raise


if __name__=='__main__':main()
