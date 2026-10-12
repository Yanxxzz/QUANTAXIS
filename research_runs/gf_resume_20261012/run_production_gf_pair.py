"""One frozen production-style proxy migration review; no remote runs or tuning."""
from datetime import datetime, timezone
import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from panda_alpha import study
from panda_alpha.registry import ResearchRegistry
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(study._clean(value), ensure_ascii=False,
                                     indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def previous_observed(series, n):
    return series.groupby(level='symbol', sort=False).pct_change(periods=n, fill_method=None)


def build_inputs(p, out):
    print('READ_EXISTING_FULL_PEER_PRICE_VIEW_ONCE', flush=True)
    frame = read_mongo_gf_fixture(p['bindings']['descriptor']['path'])
    source_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
    peers, rows = frame.symbol.nunique(), len(frame)
    frame['date'] = pd.to_datetime(frame.date)
    price = pd.Series(frame.close.to_numpy(), index=pd.MultiIndex.from_frame(frame[['date', 'symbol']]), name='close')
    ns = {'np': np, 'pd': pd, 'Factor': object, 'RETURNS': previous_observed}
    exec(compile(Path(p['bindings']['production_code']['path']).read_text(encoding='utf-8'), '<owned-f141>', 'exec'), ns)
    print('CALCULATE_PRODUCTION_CODE_PROXY_BEFORE_FINANCIAL_MASK', flush=True)
    native_f = ns['F141ResidualVarianceShareTransfer']().calculate({'close': price})
    keep = native_f.index.get_level_values('date').isin(pd.to_datetime(p['anchors']))
    native_f = native_f.loc[keep]
    del frame, price, ns
    gc.collect()
    ns = {'np': np, 'pd': pd, 'Factor': object}
    exec(compile(Path(p['bindings']['old_g_code']['path']).read_text(encoding='utf-8'), '<bound-old-g-source>', 'exec'), ns)
    cls = ns['WC03GFConservative']
    intervals = cls.SOURCE
    qualified = pd.read_parquet(p['bindings']['source_qualification']['path'],
                                columns=['date', 'symbol', 'source_qualified', 'expected_finite'],
                                filters=[('date', 'in', p['anchors'])])
    qualified['G_pct'] = np.nan
    for symbol, group in qualified.groupby('symbol', sort=False):
        for first, last, value in intervals.get(symbol, []):
            use = group.date.ge(first) & group.date.le(last)
            qualified.loc[group.index[use], 'G_pct'] = value
    if qualified.loc[qualified.source_qualified.eq(True), 'G_pct'].isna().any():
        raise ValueError('Original qualified source identity has no bound G interval')
    helper = load_module(p['bindings']['signal_builder']['path'], 'production_gf_signals')
    signals, support = helper.build_signals(native_f, qualified, p['anchors'])
    if isinstance(signals.index, pd.MultiIndex):
        signals = signals.reset_index()
    signals['date'] = pd.to_datetime(signals.date).dt.strftime('%Y-%m-%d')
    signals = signals[['date', 'symbol', 'GF', 'F141_CONTROL']].sort_values(['date', 'symbol']).reset_index(drop=True)
    if sorted(signals.date.unique()) != p['anchors'] or signals.duplicated(['date', 'symbol']).any():
        raise ValueError('Fixed formation dates/identities incomplete')
    # Old values are only an exposed migration reference, never the new price input.
    old = pd.read_parquet(p['bindings']['old_formation_signals']['path'])
    comparisons = []
    for day in p['anchors']:
        a = signals[signals.date.eq(day)].set_index('symbol')
        b = old[old.date.eq(day)].set_index('symbol')
        joined = a.join(b, how='inner', lsuffix='_new', rsuffix='_old')
        differences = {'date': day, 'new_support': len(a), 'old_support': len(b),
                       'intersection': len(joined), 'new_only': len(a.index.difference(b.index)),
                       'old_only': len(b.index.difference(a.index))}
        for name, low in [('F141_CONTROL', True), ('GF', False)]:
            groups_a, status_a = study.quantile_groups(a.reset_index()[['symbol', name]].rename(columns={name: 'value'}), p['groups'], p['minimum_assets'])
            groups_b, status_b = study.quantile_groups(b.reset_index()[['symbol', name]].rename(columns={name: 'value'}), p['groups'], p['minimum_assets'])
            if status_a != 'groups_available' or status_b != 'groups_available':
                raise ValueError('Exact migration target groups pending: '+day+':'+name)
            group = p['held_groups'][name]
            sa, sb = set(groups_a[group]), set(groups_b[group])
            differences[name] = {'common_rank_rho': joined[name+'_new'].corr(joined[name+'_old'], method='spearman'),
                                 'full_decile_overlap_count': len(sa & sb), 'new_decile_count': len(sa),
                                 'old_decile_count': len(sb), 'full_decile_jaccard': len(sa & sb) / len(sa | sb)}
        comparisons.append(differences)
    receipt = {'price_source_rows': rows, 'price_source_peers': int(peers), 'mutable_price_view_sha256': source_hash,
               'formation_rows': len(signals), 'formation_days': len(p['anchors']), 'support': support,
               'migration_value_comparisons': comparisons, 'RETURNS': p['RETURNS_assumption'],
               'all_peers_before_financial_filter': True, 'new_native_actual_values_certified': False,
               'old_history_labels_already_exposed': True, 'price_snapshot_saved': False,
               'new_financial_facts_used': False, 'official_calls': 0}
    signals.to_parquet(out/'formation_signals.parquet', index=False, compression='zstd')
    receipt['formation_signals_sha256'] = sha(out/'formation_signals.parquet')
    write(out/'input_receipt.json', receipt)
    print('FORMATION_SUPPORT_AND_TARGET_INPUT_FROZEN', len(signals), flush=True)
    return signals, receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--runtime-repair', type=Path)
    parser.add_argument('--repair-sha')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if sha(args.protocol) != args.sha:
        raise ValueError('Frozen protocol changed')
    p = json.loads(args.protocol.read_text(encoding='utf-8'))
    out = args.protocol.parent
    if args.runtime_repair:
        if sha(args.runtime_repair) != args.repair_sha:
            raise ValueError('Explicit runtime repair changed')
        repair = json.loads(args.runtime_repair.read_text(encoding='utf-8'))
        if repair['protocol_sha256'] != args.sha or repair['old_runtime'] != p['bindings']['runtime']:
            raise ValueError('Runtime repair does not bind original protocol')
        p['bindings']['runtime'] = repair['new_runtime']
    if (out/'summary.json').exists() and json.loads((out/'summary.json').read_text(encoding='utf-8')).get('status') == 'ALL_SIX_PRODUCTION_PROXY_MATCHED_PATHS_COMPLETE':
        raise ValueError('Complete owned unit exists; no duplicate execution')
    if (out/'checkpoint.json').exists() and not args.resume:
        raise ValueError('Owned unit already started/completed; inspect it rather than duplicate execution')
    for binding in p['bindings'].values():
        if sha(binding['path']) != binding['sha256']:
            raise ValueError('Frozen binding changed: ' + binding['path'])
    registry = ResearchRegistry(p['registry'])
    if not any(e['event_id'] == 'gf-production-freeze:'+args.sha for e in registry.events()):
        raise ValueError('Unit not frozen/registered before computation')
    registry.db.close()
    checkpoint = (json.loads((out/'checkpoint.json').read_text(encoding='utf-8')) if args.resume else
                  {'protocol_sha256': args.sha, 'completed': [], 'payloads': {}, 'errors': []})
    if checkpoint['protocol_sha256'] != args.sha:
        raise ValueError('Checkpoint belongs to another unit')
    checkpoint.update(status='RUNNING', pid=os.getpid(), runtime_sha256=sha(__file__), errors=[])
    write(out/'checkpoint.json', checkpoint)
    try:
        if (out/'input_receipt.json').exists():
            receipt = json.loads((out/'input_receipt.json').read_text(encoding='utf-8'))
            if sha(out/'formation_signals.parquet') != receipt['formation_signals_sha256']:
                raise ValueError('Stored formation signals changed')
            signals = pd.read_parquet(out/'formation_signals.parquet')
        else:
            signals, receipt = build_inputs(p, out)
        cost = load_module(p['bindings']['cost_helpers']['path'], 'existing_cost_helpers')
        days, schedules = cost.make_schedules(p, signals)
        write(out/'target_receipt.json', {'target_sha256': study.object_hash({'days': days, 'schedules': schedules}),
                                         'holding_days': len(days), 'forward_quotes_not_yet_read': True})
        print('READ_EXISTING_BOUND_EXECUTION_QUOTES', flush=True)
        book = cost.quote_book(p, set(signals.symbol))
        results, daily = [], []
        for fee in p['costs']:
            for name in cost.NAMES:
                key = cost.path_key(name, fee)
                if key in checkpoint['completed']:
                    payload = checkpoint['payloads'][key]
                    results.append(payload['record'])
                    daily.extend(payload['daily'])
                    print('REUSE_COMPLETED_PATH_WITHOUT_REPLAY', key, flush=True)
                    continue
                try:
                    run = study.replay_portfolio(name, schedules[name], days, book, fee)
                    record, ledger = cost.compact_run(run, days)
                    results.append(record)
                    daily.extend(ledger)
                    checkpoint['completed'].append(key)
                    checkpoint['payloads'][key] = {'record': record, 'daily': ledger}
                    del run
                except ValueError as error:
                    checkpoint['errors'].append({'path': key, 'error': type(error).__name__+': '+str(error),
                                                 'economic_failure_claimed': False})
                write(out/'checkpoint.json', checkpoint)
                print('COST_PATH_COMPLETED_OR_PENDING', key, flush=True)
        if checkpoint['errors']:
            checkpoint['status'] = 'EXECUTION_PENDING'
            write(out/'checkpoint.json', checkpoint)
            write(out/'summary.json', {'status': 'EXECUTION_PENDING', 'errors': checkpoint['errors'],
                                       'economic_failure_claimed': False, 'partial_metrics_withheld': True})
            return
        daily = pd.DataFrame(daily)
        daily.to_parquet(out/'daily_net_ledger.parquet', index=False, compression='zstd')
        comparisons = []
        for fee in p['costs']:
            a = daily[daily.name.eq('GF') & daily.cost.eq(fee)].set_index('date')
            ma = next(r['metrics'] for r in results if r['name']=='GF' and r['cost']==fee)
            for control in ['F141_CONTROL', 'COMMON_MARKET']:
                b = daily[daily.name.eq(control) & daily.cost.eq(fee)].set_index('date')
                assert a.index.equals(b.index)
                mb = next(r['metrics'] for r in results if r['name']==control and r['cost']==fee)
                comparisons.append({'cost': fee, 'control': control,
                                    'net_return_difference': ma['compounded_return']-mb['compounded_return'],
                                    'sharpe_difference': ma['sharpe']-mb['sharpe'],
                                    'drawdown_difference': ma['max_drawdown']-mb['max_drawdown'],
                                    'daily_net_return_correlation': a.net_return.corr(b.net_return)})
        summary = {'status': 'ALL_SIX_PRODUCTION_PROXY_MATCHED_PATHS_COMPLETE', 'protocol_sha256': args.sha,
                   'completed_at_utc': datetime.now(timezone.utc).isoformat(), 'window': p['window'],
                   'formation_days': len(p['anchors']), 'holding_days': len(days), 'results': results,
                   'comparisons': comparisons, 'errors': [], 'ledger_sha256': sha(out/'daily_net_ledger.parquet'),
                   'study_kind': 'exposed_historical_production_style_proxy_migration',
                   'new_economic_contexts': p['new_contexts'], 'official_runs': 0, 'official_spent': 0,
                   'formal_admission': False, 'native_actual_input_parity': False, 'full_PIT': False,
                   'unchanged_G_source_and_equal_Z_weight': True, 'old_results_overwritten': False,
                   'limitations': p['limitations']}
        write(out/'summary.json', summary)
        checkpoint.update(status=summary['status'], pid=None, completed_at_utc=summary['completed_at_utc'])
        checkpoint.pop('payloads', None)
        write(out/'checkpoint.json', checkpoint)
        print('ALL_SIX_FIXED_COST_PATHS_COMPLETE', flush=True)
    except Exception as error:
        checkpoint.update(status='EXECUTION_PENDING', error=type(error).__name__+': '+str(error),
                          economic_failure_claimed=False)
        write(out/'checkpoint.json', checkpoint)
        raise


if __name__ == '__main__':
    main()
