"""One frozen two-kernel replay on real reported amounts; no return labels."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from panda_alpha.native_preflight import FactorSeriesFixture, _FactorFixture
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    cli=argparse.ArgumentParser();cli.add_argument('--protocol',type=Path,required=True)
    cli.add_argument('--protocol-sha',required=True);cli.add_argument('--execute',action='store_true');args=cli.parse_args()
    if not args.execute:print('DEFERRED_NO_AMOUNT_VALUES_OR_PRICE_INPUT_READ');return
    if sha(args.protocol)!=args.protocol_sha:raise ValueError('frozen protocol changed')
    p=json.loads(args.protocol.read_text(encoding='utf-8'))
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']):raise ValueError('human deadline reached')
    out=Path(p['output'])
    if out.exists():raise ValueError('receipt exists; no implicit replay')
    for b in p['bindings'].values():
        if sha(b['path'])!=b['sha256']:raise ValueError('source/runtime binding changed')
    reg=ResearchRegistry(p['registry'])
    for c in p['candidates']:
        if trial_key(strategy_definition(c,p['window'],5,10)) not in reg.registrations():raise ValueError('registration before new scores required')
    receipt=json.loads(Path(p['bindings']['source20']['path']).read_text(encoding='utf-8'))
    records=receipt['records'];codes=[r['symbol'] for r in records];assert len(codes)==20 and len(set(codes))==20
    idx=pd.MultiIndex.from_product([pd.to_datetime(p['calendar']),codes],names=['date','symbol'])
    close=pd.read_parquet(p['bindings']['existing_quotes']['path'],columns=['date','symbol','close'])
    close['date']=pd.to_datetime(close.date)
    close=close[close.symbol.isin(codes)&close.date.isin(pd.to_datetime(p['calendar']))]
    if close.duplicated(['date','symbol']).any():raise ValueError('duplicate source price index')
    close=close.set_index(['date','symbol']).close.reindex(idx)
    values={}
    mapping={'cash_flow_from_operating_activities':'operating_cash_net',
             'cash_paid_for_operation_activities':'operating_cash_outflow','cash_paid_for_asset':'capital_expenditure_cash'}
    for name, field in mapping.items():
        bycode={r['symbol']:float(r['fields'][field]['current_amount_yuan']) for r in records}
        values[name]=pd.Series([bycode[c] for d,c in idx],index=idx)
    for r in records:
        if not r['stored_available_date'] or r['stored_available_date']>p['window']['start']:
            raise ValueError('annual example predates stored source availability')
        if any(f['current_start']!='2025-01-01' or f['current_end']!='2025-12-31' for f in r['fields'].values()):
            raise ValueError('source example current fiscal periods differ')
    values['close']=close
    outputs={};checks={}
    for c in p['candidates']:
        ns={'Factor':_FactorFixture,'__name__':'cash_source_interface_replay'}
        exec(compile(c['code'],'<frozen-cash-kernel>','exec'),ns)
        classes=[v for v in ns.values() if isinstance(v,type) and v is not _FactorFixture and issubclass(v,_FactorFixture)]
        if len(classes)!=1:raise ValueError('one Factor subclass required')
        output=classes[0]().calculate({k:FactorSeriesFixture(v) for k,v in values.items()})
        if not isinstance(output,pd.Series) or output.name!='value' or not output.index.equals(idx):raise ValueError('native source interface/index not preserved')
        if np.isinf(output.to_numpy(dtype=float)).any():raise ValueError('infinite output')
        outputs[c['candidate_id']]=output
        counts=output.notna().groupby(level='date').sum()
        distinct=output.groupby(level='date').nunique()
        checks[c['candidate_id']]={'minimum_finite_assets':int(counts.min()),'maximum_finite_assets':int(counts.max()),
            'minimum_distinct_values':int(distinct.min()),'source_input_rows':len(idx),'output_sha256':hashlib.sha256(pd.util.hash_pandas_object(output,index=True).values.tobytes()).hexdigest()}
    main, control=[outputs[c['candidate_id']] for c in p['candidates']]
    correlations=[];overlap=[]
    for day in pd.to_datetime(p['calendar']):
        pair=pd.concat([main.xs(day,level='date').rename('main'),control.xs(day,level='date').rename('control')],axis=1).dropna()
        if len(pair)>=10:
            correlations.append(float(pair.main.corr(pair.control,method='spearman')))
            n=max(1,int(np.ceil(len(pair)/10)));overlap.append(len(set(pair.main.nlargest(n).index)&set(pair.control.nlargest(n).index))/n)
    result={'status':'SOURCE_AMOUNT_KNOWN_INTERFACE_REPLAY_COMPLETE','protocol_sha256':args.protocol_sha,
        'checks':checks,'source_amount_rows':20,'calendar_sessions':len(p['calendar']),
        'median_main_control_Spearman':float(np.median(correlations)) if correlations else None,
        'mean_extreme_overlap':float(np.mean(overlap)) if overlap else None,'missing_price_input_rows':int(close.isna().sum()),
        'source_vintage':'Original FY25 annual cashflow examples, publicly available before this example grid; not live native latest financial values',
        'source_grid':'fixed20 x independently frozen reference calendar; actual existing price keys, missing rows not removed',
        'all_A_or_live_input_period_parity_certified':False,'production_or_admission_qualified':False,
        'dispatch_native_preflight_passed':False,'wealth_or_return_labels_read':False,'official_calls':0,
        'new_economic_contexts':len(p['candidates']),'registry_total':reg.total,
        'completed_at_utc':datetime.now(timezone.utc).isoformat(),
        'next':'Full dispatch preflight and native field period/value evidence still pending; do not submit on this source-interface receipt alone'}
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    for c in p['candidates']:
        key=trial_key(strategy_definition(c,p['window'],5,10));reg.append_event('evaluation',key,
             {'receipt':{'path':str(out),'sha256':sha(out)},'scope':'source-interface and source-feature redundancy only',
              'new_contexts':0,'no_return_test':True},event_id='cash-kernel-interface-result:'+key+':'+sha(out))
    reg.db.close();print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
