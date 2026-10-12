"""Audit completed ledgers, bound size labels and the exact deployment adapter."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

from run_remaining_tests import ROOT, OUT, read, sha, write
from panda_alpha import study


def bind(path):
    path = Path(path).resolve()
    return {'path':str(path),'sha256':sha(path)}


def main():
    protocol = OUT/'audit_protocol.json'
    if (OUT/'completed_test_audit.json').exists():
        raise ValueError('Completed audit exists; no repeat')
    labels = ROOT/'research_runs/wc03_repaired_research_20261009/execution_repair_v2/fixed_forward_quote_labels.parquet'
    native = OUT.parent/'rank_buffer_v1/native_preparation/NativeProductionGF.py'
    inputs = {name:bind(path) for name,path in {
        'summary':OUT/'test_summary.json','new_ledger':OUT/'test_daily_net_ledger.parquet',
        'size_signals':OUT/'size_formation_signals.parquet','old_labels':labels,
        'original_GF_ledger':OUT.parent/'production_pair_v1/daily_net_ledger.parquet',
        'original_protocol':OUT.parent/'production_pair_v1/protocol.json',
        'pool_input_receipt':OUT/'test_input_receipt.json','native_adapter':native,
        'runtime':Path(__file__)}.items()}
    if protocol.exists():
        raise ValueError('Existing audit protocol must be reviewed before retry')
    write(protocol, {'id':'GF_COMPLETED_ADMISSION_TEST_AUDIT_20261012',
        'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'bindings':inputs,
        'new_economic_contexts':0,'label_status':'Previously exposed fixed five-day labels, reused only for already-registered size stress',
        'folds':'Original51 chronologicalanchors split11/10/10/10/10; matched held dates partition by each fold next-openentry through next foldentryexclusive',
        'deployment':'Actual adapter code with finite F stub isolates G/projection availability; synthetic input is software evidence only, no alpha claim'})
    summary = read(inputs['summary']['path'])
    ledger = pd.read_parquet(inputs['new_ledger']['path'])
    checks=[]
    for row in summary['results']:
        d=ledger[(ledger.name==row['name'])&(ledger.cost==row['cost'])].sort_values('date')
        m=study.performance(d.net_return.reset_index(drop=True))
        diffs={k:abs(m[k]-row['metrics'][k]) for k in m}
        recomputed=d.price_pnl-d.fees-d.nav_change
        checks.append({'name':row['name'],'cost':row['cost'],'rows':len(d),
            'metrics_max_absolute_difference':max(diffs.values()),
            'NAV_compound_error':abs((1+d.net_return).prod()-d.nav.iloc[-1]),
            'accounting_residual_max':float(recomputed.abs().max())})
    size=pd.read_parquet(inputs['size_signals']['path'])
    label=pd.read_parquet(labels,columns=['date','symbol','forward_return','label_available'])
    joined=size.merge(label,on=['date','symbol'],how='left',validate='one_to_one',indicator=True)
    usable=joined.label_available.eq(True)&np.isfinite(joined.forward_return)
    ic=[]
    for day,g in joined[usable].groupby('date',sort=True):
        ic.append({'date':day,'assets':len(g),'RankIC':g.GF.corr(g.forward_return,method='spearman')})
    p=read(inputs['original_protocol']['path']);parent=read(p['bindings']['parent']['path'])
    cal=parent['calendar'];pos={d:n for n,d in enumerate(cal)}
    pieces=np.array_split(p['anchors'],5)
    old=pd.read_parquet(inputs['original_GF_ledger']['path'])
    net=[]
    for scope, data, cand, market in [('ORIGINAL_GF',old,'GF','COMMON_MARKET'),('SIZE_GF',ledger,'SIZE_GF','SIZE_MARKET')]:
        for fee in [.003,.005]:
            a=data[(data.name==cand)&(data.cost==fee)].set_index('date').sort_index()
            b=data[(data.name==market)&(data.cost==fee)].set_index('date').sort_index()
            annual=(a.nav.iloc[-1]/b.nav.iloc[-1])**(252/len(a))-1
            folds=[]
            for i,anchors in enumerate(pieces):
                start=cal[pos[str(anchors[0])]+1]
                stop=cal[pos[str(pieces[i+1][0])]+1] if i<4 else '9999-12-31'
                aa=a[(a.index>=start)&(a.index<stop)];bb=b.reindex(aa.index)
                rel=(1+aa.net_return).prod()/(1+bb.net_return).prod()
                folds.append({'fold':i+1,'start':aa.index[0],'end':aa.index[-1],'days':len(aa),
                              'annualized_relative_net_wealth':rel**(252/len(aa))-1})
            net.append({'scope':scope,'cost':fee,'annualized_relative_net_wealth':annual,'folds':folds,
                        'p25_fold_annualized_relative_net_wealth':float(np.quantile([r['annualized_relative_net_wealth'] for r in folds],.25)),
                        'benchmark':'Matched-source equal weighted market, not certified CSIallshare'})
    # Run the actual source/projection logic; a finite F stub isolates transport availability.
    ns={'Factor':object,'np':np,'pd':pd}
    exec(compile(native.read_text(encoding='utf-8'),'<exact-reviewed-adapter>','exec'),ns)
    cls=next(v for k,v in ns.items() if isinstance(v,type) and k.startswith('Native'))
    valid=[]
    for code,intervals in sorted(cls.SOURCE.items()):
        if any(a<='2025-08-14'<=b for a,b,_ in intervals): valid.append(code)
    symbols=valid[:50]
    index=pd.MultiIndex.from_product([pd.to_datetime(['2025-08-14','2026-09-03','2026-10-12']),symbols],names=['date','symbol'])
    instance=cls()
    instance._production_f=lambda factors: pd.Series(np.tile(np.arange(len(symbols))/len(symbols),3),index=factors['close'].index)
    output=instance.calculate({'close':pd.Series(100.,index=index)})
    future=index[index.get_level_values('date')>pd.Timestamp('2026-09-03')]
    deployment={'adapter_sha256':sha(native),'evaluation_start':cls.EVALUATION_START,'evaluation_end':cls.EVALUATION_END,
                'maximum_G_source_interval_end':max(b for intervals in cls.SOURCE.values() for _,b,_ in intervals),
                'historical_synthetic_finite_output':int(np.isfinite(output).sum()),
                'future_supplied_rows':len(future),'future_rows_returned':len(output.index.intersection(future)),
                'future_finite_after_caller_reindex':int(np.isfinite(output.reindex(future)).sum()),
                'finite_F_stub':True,'production_ready':False,
                'blocking_reason':'Fixed past projection excludes future dates; immutable G intervals have no continuous publication/version update source'}
    receipt=read(inputs['pool_input_receipt']['path']);dep=pd.DataFrame(receipt['factor_dependencies'])
    dependencies=[]
    for name,g in dep.groupby('member'):
        dependencies.append({'member':name,'mean_pearson':float(g.pearson.mean()),'mean_spearman':float(g.spearman.mean()),
                             'max_abs_period_pearson':float(g.pearson.abs().max()),'max_abs_period_spearman':float(g.spearman.abs().max())})
    original_receipt=read(OUT.parent/'production_pair_v1/input_receipt.json')
    coverage=[{'date':r['date'],'source_common':r['common_finite'],'received_F_finite_denominator':r['all_received_native_finite'],
               'scoped_ratio':r['common_finite']/r['all_received_native_finite']} for r in original_receipt['support']]
    result={'status':'COMPLETED_LEDGER_AND_DEPLOYMENT_AUDIT','audit_protocol':bind(protocol),'checks':checks,
        'accounting_and_metric_checks_passed':all(r['metrics_max_absolute_difference']<1e-10 and r['NAV_compound_error']<1e-8 and r['accounting_residual_max']<1e-8 for r in checks),
        'size_information':{'original_size_formation_denominator':len(size),'available_label_rows':int(usable.sum()),
            'unusable_label_rows':int((~usable).sum()),'periods':len(ic),'mean_RankIC':float(np.mean([r['RankIC'] for r in ic])),
            'period_metrics':ic,'historical_size_vintage_verified':False},
        'same_source_net_excess_reviews':net,'pool_factor_dependencies':dependencies,
        'source_coverage_observations':coverage,'full_eligible_PIT_denominator_verified':False,
        'deployment':deployment,'new_economic_contexts':0,'official_spent':0,'formal_admission':False}
    write(OUT/'completed_test_audit.json',result)
    print(json.dumps({'status':result['status'],'accounting_passed':result['accounting_and_metric_checks_passed'],
        'size_RankIC':result['size_information']['mean_RankIC'],'deployment':deployment,'net_excess':[(r['scope'],r['cost'],r['annualized_relative_net_wealth'],r['p25_fold_annualized_relative_net_wealth']) for r in net]},ensure_ascii=False))


if __name__=='__main__':
    main()
