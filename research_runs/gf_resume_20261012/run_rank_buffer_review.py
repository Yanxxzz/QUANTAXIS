"""One preregistered 10/20 rank-buffer review on retained GF signals."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import pandas as pd

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parents[1]))
from panda_alpha import study
from panda_alpha.registry import ResearchRegistry
sys.path.insert(0, str(BASE))
from run_production_gf_pair import load_module, sha, write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    args = parser.parse_args()
    if sha(args.protocol) != args.sha:
        raise ValueError('Rank-buffer protocol changed')
    p = json.loads(args.protocol.read_text(encoding='utf-8')); out = args.protocol.parent
    if (out/'checkpoint.json').exists():
        raise ValueError('Owned rank-buffer unit exists; inspect, no duplicate execution')
    for b in p['bindings'].values():
        if sha(b['path']) != b['sha256']:
            raise ValueError('Bound input changed: '+b['path'])
    r = ResearchRegistry(p['registry'])
    assert any(e['event_id']=='gf-buffer-freeze:'+args.sha for e in r.events())
    r.db.close()
    cp = {'status':'RUNNING','pid':os.getpid(),'protocol_sha256':args.sha,'completed':[],'payloads':{},'errors':[]}
    write(out/'checkpoint.json', cp)
    try:
        signals = pd.read_parquet(p['bindings']['base_signals']['path'])
        parent = json.loads(Path(p['bindings']['parent']['path']).read_text(encoding='utf-8'))
        calendar = parent['calendar']
        helper = load_module(p['bindings']['target_builder']['path'], 'rank_buffer_targets')
        prepared = helper.build_rank_buffer_targets(signals, p['anchors'], calendar)
        days = [d for d in calendar if p['window']['holding_start']<=d<=p['window']['holding_end']]
        schedule = prepared['schedule']
        encoded = prepared['encoded_signals']
        if isinstance(encoded.index, pd.MultiIndex):
            encoded = encoded.reset_index()
        encoded.to_parquet(out/'formation_signals.parquet',index=False,compression='zstd')
        write(out/'input_receipt.json', {'formation_rows':len(encoded),'base_cohort_exact':True,
                                       'definition':p['definition'],'target_reviews':prepared['diagnostics'],
                                       'signals_sha256':sha(out/'formation_signals.parquet'),
                                       'targets_sha256':study.object_hash({'days':days,'schedule':schedule}),
                                       'future_quotes_not_yet_read':True,
                                       'state_is_previous_proposed_targets_not_actual_holdings':True})
        print('RANK_BUFFER_TARGETS_FROZEN', flush=True)
        cost = load_module(p['bindings']['cost_helpers']['path'],'existing_cost_helpers')
        book = cost.quote_book(p,set(signals.symbol))
        records, daily = [], []
        for fee in p['costs']:
            key = cost.path_key('BUFFER_GF',fee)
            try:
                run = study.replay_portfolio('BUFFER_GF',schedule,days,book,fee)
                record, ledger = cost.compact_run(run,days)
                cp['payloads'][key]={'record':record,'daily':ledger};cp['completed'].append(key)
                records.append(record);daily.extend(ledger);del run
            except ValueError as error:
                cp['errors'].append({'path':key,'error':str(error),'economic_failure_claimed':False})
            write(out/'checkpoint.json',cp)
            print('RANK_BUFFER_COST_PATH_COMPLETED_OR_PENDING',key,flush=True)
        if cp['errors']:
            cp.update(status='EXECUTION_PENDING',pid=None);write(out/'checkpoint.json',cp)
            write(out/'summary.json',{'status':'EXECUTION_PENDING','errors':cp['errors'],'partial_metrics_withheld':True})
            return
        daily = pd.DataFrame(daily)
        daily.to_parquet(out/'daily_net_ledger.parquet',index=False,compression='zstd')
        baseline = json.loads(Path(p['bindings']['base_summary']['path']).read_text(encoding='utf-8'))
        old_daily = pd.read_parquet(p['bindings']['base_ledger']['path'])
        comparisons = []
        for result in records:
            fee=result['cost'];a=daily[daily.cost.eq(fee)].set_index('date')
            for name in ['GF','F141_CONTROL','COMMON_MARKET']:
                b=old_daily[old_daily.name.eq(name)&old_daily.cost.eq(fee)].set_index('date')
                assert a.index.equals(b.index)
                control=next(x for x in baseline['results'] if x['name']==name and x['cost']==fee)
                comparisons.append({'cost':fee,'control':name,
                                    'net_return_difference':result['metrics']['compounded_return']-control['metrics']['compounded_return'],
                                    'sharpe_difference':result['metrics']['sharpe']-control['metrics']['sharpe'],
                                    'drawdown_difference':result['metrics']['max_drawdown']-control['metrics']['max_drawdown'],
                                    'fee_difference_initial_equity_units':result['fees']-control['fees'],
                                    'daily_net_return_correlation':a.net_return.corr(b.net_return)})
        summary={'status':'TWO_FIXED_RANK_BUFFER_COST_PATHS_COMPLETE','completed_at_utc':datetime.now(timezone.utc).isoformat(),
                 'protocol_sha256':args.sha,'window':p['window'],'formation_days':len(p['anchors']),'holding_days':len(days),
                 'results':records,'comparisons':comparisons,'errors':[],'ledger_sha256':sha(out/'daily_net_ledger.parquet'),
                 'old_controls_replayed':False,'new_price_snapshot':False,'cohort_unchanged':True,
                 'new_economic_contexts':1,'official_runs':0,'official_spent':0,'formal_admission':False,
                 'state_is_previous_proposed_targets_not_actual_holdings':True,'limitations':p['limitations']}
        write(out/'summary.json',summary);cp.update(status=summary['status'],pid=None);cp.pop('payloads',None);write(out/'checkpoint.json',cp)
        print('FIXED_RANK_BUFFER_REVIEW_COMPLETE',flush=True)
    except Exception as error:
        cp.update(status='EXECUTION_PENDING',pid=None,error=type(error).__name__+': '+str(error),economic_failure_claimed=False)
        write(out/'checkpoint.json',cp);raise


if __name__=='__main__':main()
