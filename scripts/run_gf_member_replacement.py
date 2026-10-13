"""Four frozen local GF replacement/removal diagnostics; no official mutations.

The root freezes this source/protocol and registers four contexts before invoking
the runner. Old pool signals/results are read-only controls, never replayed.
The two four-member removals are research diagnostics, not admission proposals.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha import study
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture

SCENARIOS = {
    'RETAINED_REPLACE_C03_GF': {'removed_member': 'C03', 'insert_GF': True,
                                'member_count': 5, 'diagnostic_only': False},
    'RETAINED_REPLACE_C04_GF': {'removed_member': 'C04', 'insert_GF': True,
                                'member_count': 5, 'diagnostic_only': False},
    'RETAINED_REMOVE_C03_DIAGNOSTIC': {'removed_member': 'C03', 'insert_GF': False,
                                      'member_count': 4, 'diagnostic_only': True},
    'RETAINED_REMOVE_C04_DIAGNOSTIC': {'removed_member': 'C04', 'insert_GF': False,
                                      'member_count': 4, 'diagnostic_only': True},
}
OLD_CONTROLS = ('RETAINED_POOL', 'RETAINED_ADD_GF', 'RETAINED_REPLACE_C16_GF')
SOURCE_LIMIT = (
    'Same exposed local GF source support and original received 5608 price peers, '
    'including 308 Beijing peers, are preserved. C03 turnover on those peers can '
    'be unavailable while C04 close RANK includes them. Official definitions say '
    'Shanghai/Shenzhen A; peer/field/native value parity and historical PIT are '
    'not certified. No peer or source mask is changed to rescue performance.'
)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def bound(item):
    path = Path(item['path']).resolve()
    if sha(path) != item['sha256']:
        raise ValueError('Frozen artifact changed: ' + str(path))
    return path


def artifact(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path)}


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(study._clean(value), ensure_ascii=False,
                                    indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def parquet(path, frame):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    frame.to_parquet(temporary, index=False, compression='zstd')
    temporary.replace(path)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identities(frame):
    return pd.MultiIndex.from_frame(frame[['date', 'symbol']])


def normalized_keys(frame):
    frame = frame.copy()
    frame['date'] = pd.to_datetime(frame.date, errors='raise').dt.strftime('%Y-%m-%d')
    frame['symbol'] = frame.symbol.astype(str).str.zfill(6)
    if frame.duplicated(['date', 'symbol']).any():
        raise ValueError('Duplicate frozen formation identities')
    return frame


def registered(protocol, digest):
    path = ROOT / 'research_runs/research_trials.sqlite3'
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT payload FROM research_events WHERE event_id=?',
                                 ('gf-member-replacement-freeze:' + digest,)).fetchone()
    if not row:
        raise ValueError('Root must register the frozen four contexts before execution')
    payload = json.loads(row[0])
    if (payload.get('protocol', {}).get('sha256') != digest
            or payload.get('new_contexts') != 4 or len(protocol.get('contexts', [])) != 4):
        raise ValueError('Frozen registration does not bind exactly four new contexts')


def validate_protocol(protocol, digest):
    for item in protocol['bindings'].values():
        bound(item)
    if protocol['bindings']['runtime'] != artifact(__file__):
        raise ValueError('The root must freeze this exact runner source')
    old = read(bound(protocol['bindings']['original_protocol']))
    for item in [*old['bindings'].values(), *old['definitions'].values()]:
        bound(item)
    if (protocol['anchors'] != old['anchors'] or protocol['window'] != old['window']
            or protocol['costs'] != old['costs'] or protocol['costs'] != [.003, .005]
            or protocol.get('cycle') != 5 or protocol.get('groups') != 10
            or protocol.get('direction') != 1 or protocol.get('expected_shared_rows') != 32892):
        raise ValueError('Replacement diagnostics must retain the original calendar/scope/costs')
    if protocol.get('scenarios', SCENARIOS) != SCENARIOS:
        raise ValueError('Only the four fixed member replacement/removal scenarios are supported')
    expected_members = {}
    retained = old['pools']['RETAINED_POOL']
    if retained != ['C03', 'C04', 'C16', 'C17', 'F253']:
        raise ValueError('Original planned retained membership changed')
    for name, spec in SCENARIOS.items():
        expected_members[name] = ([('GF' if member == spec['removed_member'] else member)
                                    for member in retained] if spec['insert_GF']
                                  else [member for member in retained if member != spec['removed_member']])
    if protocol.get('pools') != expected_members:
        raise ValueError('Frozen member lists differ from the four implemented scenarios')
    original_receipt = read(bound(protocol['bindings']['original_input_receipt']))
    if (original_receipt['shared_pool_rows'] != 32892
            or protocol['bindings']['original_pool_signals']['sha256'] != original_receipt['pool_signals_sha256']):
        raise ValueError('Original shared-support signal binding is inconsistent')
    old_summary = read(bound(protocol['bindings']['original_summary']))
    if (old_summary.get('status') != 'ALL_REMAINING_LOCAL_PATHS_COMPLETE'
            or old_summary.get('errors') or old_summary['ledger_sha256'] !=
            protocol['bindings']['original_daily_ledger']['sha256']):
        raise ValueError('Original controls must be complete and bound, not regenerated')
    registered(protocol, digest)
    return old, original_receipt, old_summary


def component_signals(protocol, old, original_receipt, output, digest):
    receipt_path, signal_path = output / 'input_receipt.json', output / 'formation_signals.parquet'
    if receipt_path.exists():
        receipt = read(receipt_path)
        if receipt['protocol_sha256'] != digest:
            raise ValueError('Signal cache belongs to another frozen protocol')
        signals = pd.read_parquet(bound(receipt['formation_signals']))
        return signals, receipt
    if signal_path.exists():
        raise ValueError('Uncommitted signal cache exists; inspect before resuming')

    old_runner = load(old['bindings']['runtime']['path'], 'bound_old_gf_member_math')
    base = normalized_keys(pd.read_parquet(bound(protocol['bindings']['original_pool_signals'])))
    if len(base) != 32892 or sorted(base.date.unique()) != protocol['anchors']:
        raise ValueError('Frozen original shared formation support changed')
    raw_gf = normalized_keys(pd.read_parquet(old['bindings']['gf_signals']['path']))
    if len(raw_gf) != original_receipt['original_GF_rows']:
        raise ValueError('Original GF parent support changed')
    base = base.merge(raw_gf[['date', 'symbol', 'GF']], on=['date', 'symbol'],
                      how='left', validate='one_to_one')
    if not np.isfinite(base['GF']).all():
        raise ValueError('Frozen shared support is no longer finite for original GF')

    print('CALCULATE_ONLY_C03_C04_ON_UNCHANGED_FULL_RECEIVED_PEERS', flush=True)
    frame = read_mongo_gf_fixture(old['bindings']['descriptor']['path'])
    price_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
    if price_hash != old['expected_price_view_hash']:
        raise ValueError('Original mutable peer close view changed')
    bars = pd.read_parquet(old['bindings']['technical_bars']['path'],
                           columns=['date', 'code', 'turnover'],
                           filters=[('date', '>=', 20240603), ('date', '<=', 20260903)])
    bars['date'] = pd.to_datetime(bars.date.astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    bars['code'] = bars.code.astype(str).str.zfill(6)
    bars = bars.rename(columns={'code': 'symbol'})
    if bars.duplicated(['date', 'symbol']).any():
        raise ValueError('Conflicting original turnover identities')
    frame = frame.merge(bars, on=['date', 'symbol'], how='left', validate='one_to_one')
    frame['date'] = pd.to_datetime(frame.date, errors='raise')
    frame = frame.sort_values(['symbol', 'date']).set_index(['date', 'symbol'])
    env = {'CLOSE': frame.close, 'TURNOVER': frame.turnover,
           'RETURNS': old_runner.returns, 'RANK': old_runner.rank,
           'MA': old_runner.rolling, 'CORR': old_runner.corr,
           'TS_MAX': lambda series, n: old_runner.rolling(series, n, 'max')}
    keep = frame.index.get_level_values('date').isin(pd.to_datetime(protocol['anchors']))
    values = {}
    for name in ('C03', 'C04'):
        definition = read(old['definitions'][name]['path'])
        if definition.get('mode') != 'formula' or definition.get('factor_direction') != '0':
            raise ValueError('Frozen C03/C04 formula or direction changed')
        value = eval(compile(definition['content'], '<bound-owned-' + name + '>', 'eval'), env)
        values[name] = value.reindex(frame.index)[keep]
    components = pd.DataFrame(values).reset_index()
    components['date'] = components.date.dt.strftime('%Y-%m-%d')
    signals = base.merge(components, on=['date', 'symbol'], how='left', validate='one_to_one')
    if not identities(signals).equals(identities(base)) or not np.isfinite(signals[['C03', 'C04']]).all(axis=None):
        raise ValueError('C03/C04 recomputation changed the frozen common finite support')
    checks = []
    for day, rows in signals.groupby('date', sort=True):
        recovered = 6 * rows.RETAINED_ADD_GF - 5 * rows.RETAINED_POOL
        direct = old_runner.z(rows.GF)
        error = float((recovered - direct).abs().max())
        if not np.isfinite(error) or error > 1e-10:
            raise ValueError('Recovered GF direction-Z disagrees with original direct-Z: ' + day)
        signals.loc[rows.index, 'GF_Z'] = recovered
        for member in ('C03', 'C04'):
            signals.loc[rows.index, member + '_Z'] = old_runner.z(-rows[member])
        checks.append({'date': day, 'assets': len(rows), 'GF_Z_max_absolute_error': error})
    baseline = signals.RETAINED_POOL
    for name, spec in SCENARIOS.items():
        removed = signals[spec['removed_member'] + '_Z']
        signals[name] = (baseline + (signals.GF_Z - removed) / 5 if spec['insert_GF']
                         else (5 * baseline - removed) / 4)
    if not np.isfinite(signals[list(SCENARIOS)]).all(axis=None):
        raise ValueError('A fixed replacement/removal signal is unavailable')
    # Thin evaluated signal cache permits resume without repeating rolling math.
    fields = ['date', 'symbol', *OLD_CONTROLS, 'GF_Z', 'C03_Z', 'C04_Z', *SCENARIOS]
    signals = signals[fields]
    parquet(signal_path, signals)
    receipt = {'status': 'FOUR_SIGNALS_ON_UNCHANGED_COMMON_SUPPORT', 'protocol_sha256': digest,
               'formation_signals': artifact(signal_path), 'shared_rows': len(signals),
               'price_view_sha256': price_hash, 'GF_Z_equivalence': checks,
               'all_peers_before_financial_mask': True, 'recomputed_members': ['C03', 'C04'],
               'old_pool_or_control_paths_replayed': False,
               'source_scope_limit': SOURCE_LIMIT, 'forward_quotes_used_for_formation': False,
               'new_source_masks_or_weights_selected': False}
    write(receipt_path, receipt)
    del frame, bars, components, base, raw_gf, env, values
    gc.collect()
    return signals, receipt


def make_schedules(protocol, old, signals, output, digest):
    calendar = read(old['bindings']['parent']['path'])['calendar']
    position = {day: n for n, day in enumerate(calendar)}
    days = [day for day in calendar if protocol['window']['holding_start'] <= day <= protocol['window']['holding_end']]
    if len(days) != 256 or len(protocol['anchors']) != 51:
        raise ValueError('Original holding and formation calendar changed')
    schedules = {name: {} for name in SCENARIOS}
    diagnostics, correlations = [], []
    for day, rows in signals.groupby('date', sort=True):
        entry = calendar[position[day] + 1]
        groups, status = study.quantile_groups(rows[['symbol', 'RETAINED_POOL']].rename(
            columns={'RETAINED_POOL': 'value'}), 10, 30)
        if status != 'groups_available':
            raise ValueError('Original baseline grouping unavailable: ' + day)
        baseline = set(groups['10'])
        for member in ('C03_Z', 'C04_Z', 'RETAINED_POOL'):
            correlations.append({'date': day, 'comparison': 'GF_Z_vs_' + member,
                                 'assets': len(rows), 'pearson': rows.GF_Z.corr(rows[member]),
                                 'spearman': rows.GF_Z.corr(rows[member], method='spearman')})
        for name in SCENARIOS:
            groups, status = study.quantile_groups(rows[['symbol', name]].rename(columns={name: 'value'}), 10, 30)
            if status != 'groups_available':
                raise ValueError('Fixed new grouping unavailable: ' + name + ':' + day)
            selected = set(groups['10'])
            schedules[name][entry] = {'decision_date': day, 'codes': sorted(selected)}
            union = selected | baseline
            diagnostics.append({'date': day, 'name': name, 'baseline_selected': len(baseline),
                                'new_selected': len(selected), 'retained_count': len(selected & baseline),
                                'added_count': len(selected - baseline), 'removed_count': len(baseline - selected),
                                'jaccard_vs_baseline': len(selected & baseline) / len(union),
                                'score_pearson_vs_baseline': rows[name].corr(rows.RETAINED_POOL),
                                'score_spearman_vs_baseline': rows[name].corr(rows.RETAINED_POOL, method='spearman')})
    target_hash = study.object_hash(schedules)
    receipt_path = output / 'target_receipt.json'
    if receipt_path.exists() and read(receipt_path)['target_sha256'] != target_hash:
        raise ValueError('Frozen new target sets changed on resume')
    write(receipt_path, {'protocol_sha256': digest, 'days': len(days), 'target_sha256': target_hash,
                         'all_targets_frozen_before_forward_quote_read': True,
                         'selection_changes': diagnostics, 'factor_correlations': correlations,
                         'old_baseline_targets_used_only_for_set_diagnostics': True})
    return days, schedules


def run_with_attribution(name, schedule, days, book, fee, helper):
    sectors = defaultdict(lambda: defaultdict(float))
    sector_codes, sector_days = defaultdict(set), defaultdict(set)
    original_account = study.account_day

    def observe_account(**kwargs):
        result = original_account(**kwargs)
        for row in result['security_rows']:
            sector, _ = book.risk_fact(row['code'], result['date'], 'sector')
            key = str(sector) if sector is not None else 'UNKNOWN_SOURCE_SECTOR'
            for field in ('price_pnl', 'fee', 'net_contribution', 'traded_value'):
                sectors[key][field] += row[field]
            sector_codes[key].add(row['code'])
            sector_days[key].add(result['date'])
        return result  # Accounting/executions and returned data are unchanged.

    study.account_day = observe_account
    try:
        run = study.replay_portfolio(name, schedule, days, book, fee)
    finally:
        study.account_day = original_account
    record, daily = helper.compact_run(run, days)
    totals = run['accounting']
    sector_rows = [{'sector': key, **dict(values), 'contributing_stock_count': len(sector_codes[key]),
                    'contributing_days': len(sector_days[key]), 'sector_evidence_known': key != 'UNKNOWN_SOURCE_SECTOR'}
                   for key, values in sectors.items()]
    sector_rows.sort(key=lambda row: (-row['net_contribution'], row['sector']))
    errors = {'price_pnl': abs(sum(row['price_pnl'] for row in sector_rows) - totals['price_pnl']),
              'fees': abs(sum(row['fee'] for row in sector_rows) - totals['fees']),
              'net_contribution': abs(sum(row['net_contribution'] for row in sector_rows) - totals['nav_change'])}
    if max(errors.values(), default=0) > 1e-8:
        raise ValueError('Known/unknown industry contribution buckets do not reconcile')
    record['scenario'] = SCENARIOS[name]
    record['formal_admission'] = False
    record['attribution'] = {
        'price_pnl_initial_equity_units': totals['price_pnl'], 'fees_initial_equity_units': totals['fees'],
        'net_nav_change_initial_equity_units': totals['nav_change'],
        'securities': totals['securities'], 'industry_contributions': sector_rows,
        'industry_reconciliation_max_absolute_errors': errors,
        'industry_method': 'Aggregate actual accounting-day security PnL/fees using that day risk_fact sector; unknown metadata retained in a separate bucket.',
        'interpretation': 'Contribution accounting is descriptive, not causal industry/independent-alpha attribution; market/beta PnL projection was not supplied.'}
    del run
    return record, daily


def finalize(protocol, output, checkpoint, old_summary, old_daily, digest):
    expected = {name + ':' + format(fee, '.3f') for name in SCENARIOS for fee in protocol['costs']}
    if set(checkpoint['completed']) != expected:
        raise ValueError('Do not summarize partial path outcomes')
    results, daily_rows = [], []
    for fee in protocol['costs']:
        for name in SCENARIOS:
            item = checkpoint['completed'][name + ':' + format(fee, '.3f')]
            results.append(item['record'])
            daily_rows.extend(item['daily'])
    daily = pd.DataFrame(daily_rows)
    comparisons = []
    for result in results:
        fee, name = result['cost'], result['name']
        current = daily[daily.name.eq(name) & daily.cost.eq(fee)].set_index('date')
        for control in OLD_CONTROLS:
            prior = old_daily[old_daily.name.eq(control) & old_daily.cost.eq(fee)].set_index('date')
            if not current.index.equals(prior.index):
                raise ValueError('New and existing control ledger calendars differ')
            previous = next(row for row in old_summary['results'] if row['name'] == control and row['cost'] == fee)
            cm, bm = result['metrics'], previous['metrics']
            pnl_delta = float(current.price_pnl.sum() - prior.price_pnl.sum())
            fee_delta = float(current.fees.sum() - prior.fees.sum())
            net_delta = cm['compounded_return'] - bm['compounded_return']
            if abs(net_delta - (pnl_delta - fee_delta)) > 1e-8:
                raise ValueError('Incremental price PnL minus incremental fees does not reconcile')
            comparisons.append({'candidate': name, 'baseline': control, 'cost': fee,
                                'baseline_result_reused_not_replayed': True,
                                'net_return_delta': net_delta,
                                'sharpe_delta': cm['sharpe'] - bm['sharpe'],
                                'max_drawdown_delta': cm['max_drawdown'] - bm['max_drawdown'],
                                'relative_terminal_wealth': current.nav.iloc[-1] / prior.nav.iloc[-1] - 1,
                                'price_pnl_delta_initial_equity_units': pnl_delta,
                                'fee_delta_initial_equity_units': fee_delta,
                                'daily_net_return_correlation': current.net_return.corr(prior.net_return)})
    ledger_path = output / 'daily_net_ledger.parquet'
    parquet(ledger_path, daily)
    result_refs = []
    for record in results:
        path = output / (record['name'].lower() + '_' + str(round(record['cost'] * 10000)) + 'bp.json')
        if path.exists() and study.object_hash(read(path)) != study.object_hash(record):
            raise ValueError('A compact completed path artifact changed before finalization')
        write(path, record)
        result_refs.append({'name': record['name'], 'cost': record['cost'],
                            'scenario': record['scenario'], 'metrics': record['metrics'],
                            'result': artifact(path), 'fees': record['fees'],
                            'attribution_totals': {key: record['attribution'][key] for key in
                               ('price_pnl_initial_equity_units', 'fees_initial_equity_units',
                                'net_nav_change_initial_equity_units')},
                            'risk_metadata_status': record['risk_metadata_status'],
                            'blocked_orders': record['blocked_orders'],
                            'terminal_unliquidated_fraction': record['terminal_unliquidated_fraction']})
    summary = {'status': 'ALL_EIGHT_MEMBER_REPLACEMENT_PATHS_COMPLETE', 'protocol_sha256': digest,
               'completed_at_utc': datetime.now(timezone.utc).isoformat(), 'results': result_refs,
               'comparisons': comparisons, 'formation_periods': 51, 'holding_days': 256,
               'shared_formation_rows': 32892, 'new_contexts': 4,
               'daily_net_ledger': artifact(ledger_path), 'targets': artifact(output / 'target_receipt.json'),
               'input_receipt': artifact(output / 'input_receipt.json'),
               'controls_reused': {key: protocol['bindings'][key] for key in ('original_summary', 'original_daily_ledger')},
               'four_member_removals_diagnostic_only': True,
               'source_scope_limit': SOURCE_LIMIT, 'historical_vintage_verified': False,
               'native_field_parity': False, 'full_PIT': False, 'formal_admission': False,
               'statistically_significant_pool_increment_claimed': False,
               'official_runs': 0, 'official_spent': 0, 'pool_operations': 0, 'errors': []}
    write(output / 'summary.json', summary)
    checkpoint.update(status='COMPLETED', pid=None, active_path=None,
                      completed_paths=sorted(checkpoint['completed']), completed={})
    write(output / 'checkpoint.json', checkpoint)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    protocol_path = Path(args.protocol).resolve()
    protocol_path.parent.relative_to(ROOT / 'research_runs')
    if sha(protocol_path) != args.sha:
        raise ValueError('Frozen replacement protocol changed')
    output = protocol_path.parent
    if (output / 'summary.json').exists() and read(output / 'summary.json').get('status') == 'ALL_EIGHT_MEMBER_REPLACEMENT_PATHS_COMPLETE':
        raise ValueError('Complete replacement tests exist; no duplicate execution')
    protocol = read(protocol_path)
    old, original_receipt, old_summary = validate_protocol(protocol, args.sha)
    checkpoint_path = output / 'checkpoint.json'
    if checkpoint_path.exists() and not args.resume:
        raise ValueError('Checkpoint exists; inspect actual PID before using --resume')
    checkpoint = read(checkpoint_path) if checkpoint_path.exists() else {
        'protocol_sha256': args.sha, 'completed': {}, 'errors': []}
    if checkpoint['protocol_sha256'] != args.sha:
        raise ValueError('Checkpoint belongs to another protocol')
    if checkpoint.get('pid') and checkpoint.get('status') == 'RUNNING':
        import psutil
        if psutil.pid_exists(checkpoint['pid']):
            raise ValueError('Recorded process still exists; do not start a duplicate')
    checkpoint.update(status='RUNNING', pid=os.getpid(), active_path=None)
    write(checkpoint_path, checkpoint)
    try:
        signals, _ = component_signals(protocol, old, original_receipt, output, args.sha)
        days, schedules = make_schedules(protocol, old, signals, output, args.sha)
        # The old ledger is only a completed comparison artifact, never a replay target.
        old_daily = pd.read_parquet(bound(protocol['bindings']['original_daily_ledger']))
        helper = load(old['bindings']['cost_helpers']['path'], 'bound_gf_replacement_cost_helpers')
        book = helper.quote_book(old, set(signals.symbol))
        for fee in protocol['costs']:
            for name in SCENARIOS:
                key = helper.path_key(name, fee)
                if key in checkpoint['completed']:
                    completed = checkpoint['completed'][key]
                    if (completed['record']['name'] != name or completed['record']['cost'] != fee
                            or [row['date'] for row in completed['daily']] != days):
                        raise ValueError('Completed checkpoint path/calendar differs from the freeze')
                    continue
                checkpoint['active_path'] = key
                write(checkpoint_path, checkpoint)
                record, daily = run_with_attribution(name, schedules[name], days, book, fee, helper)
                # One atomic checkpoint owns both the complete record and daily
                # rows. Compact per-path files are emitted only after all eight
                # paths finish; an interrupted finalize never repeats a replay.
                checkpoint['completed'][key] = {'record': record, 'daily': daily}
                write(checkpoint_path, checkpoint)
                print('COMPLETED_FIXED_PATH', key, flush=True)
                del record, daily
                gc.collect()
        finalize(protocol, output, checkpoint, old_summary, old_daily, args.sha)
    except Exception as error:
        checkpoint.update(status='SOURCE_OR_EXECUTION_PENDING', pid=None, active_path=None,
                          error=type(error).__name__ + ': ' + str(error))
        write(checkpoint_path, checkpoint)
        raise


if __name__ == '__main__':
    main()
