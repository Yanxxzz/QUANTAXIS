"""One frozen current-balance source proxy; no production/PIT certification."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from panda_alpha import study
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
from panda_alpha.native_preflight import FactorSeriesFixture, _FactorFixture
from panda_alpha.quote_labels import fixed_forward_open_labels
from panda_alpha.registry import ResearchRegistry


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, obj):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(study._clean(obj), ensure_ascii=False, indent=2,
                                    allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if sha(args.protocol) != args.sha:
        raise ValueError('Frozen balance-stock protocol changed')
    p, out = read(args.protocol), args.protocol.parent
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p['deadline_utc']):
        raise ValueError('Human cutoff reached; no new unit dispatch')
    if (out / 'summary.json').exists():
        raise ValueError('Completed pilot cannot be repeated')
    for binding in p['bindings'].values():
        if sha(binding['path']) != binding['sha256']:
            raise ValueError('Bound source changed: ' + binding['path'])
    registry = ResearchRegistry(ROOT / 'research_runs/research_trials.sqlite3')
    if not any(e['event_id'] == 'balance-stock-pilot-freeze:' + args.sha for e in registry.events()):
        raise ValueError('Register all contexts before values and labels')
    registry.db.close()
    cp_path = out / 'checkpoint.json'
    if cp_path.exists() and not args.resume:
        raise ValueError('Existing execution requires inspection and explicit resume')
    cp = read(cp_path) if cp_path.exists() else {'completed': {}, 'errors': []}
    cp.update(status='RUNNING', pid=os.getpid(), protocol_sha256=args.sha)
    write(cp_path, cp)
    try:
        source = read(p['bindings']['source_receipt']['path'])
        records = source['cases']
        codes = [x['symbol'] for x in records]
        if codes != p['fixed_scope_codes'] or len(set(codes)) != 200:
            raise ValueError('Fixed source200 changed')
        if (out / 'input_receipt.json').exists():
            receipt = read(out / 'input_receipt.json')
            if receipt['signals_sha256'] != sha(out / 'formation_signals.parquet'):
                raise ValueError('Frozen input signals changed')
            signals = pd.read_parquet(out / 'formation_signals.parquet')
        else:
            print('READ_BOUND_ALL_PRICE_PEERS_ONCE', flush=True)
            frame = read_mongo_gf_fixture(p['bindings']['descriptor']['path'])
            received_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
            if received_hash != p['expected_received_price_hash']:
                raise ValueError('Frozen accepted price input view changed')
            frame['date'] = pd.to_datetime(frame.date)
            price_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
            price = frame.set_index(['date', 'symbol']).sort_index().close
            ns = {'np': np, 'pd': pd}
            exec(compile(Path(p['bindings']['F_observed_math']['path']).read_text(encoding='utf-8'), '<bound-observed-price-control>', 'exec'), ns)
            f_all = ns['ObservedPriceShareControl']()._production_f({'close': price})
            peer_count = frame.symbol.nunique()
            del frame
            index = pd.MultiIndex.from_product([pd.to_datetime(p['anchors']), codes], names=['date', 'symbol'])
            values = {name: pd.Series(np.nan, index=index, dtype=float) for name in p['financial_fields']}
            amount_eligible = 0
            for record in records:
                if not record.get('finite_same_current_pair', record['status'] == 'current_balance_source_amounts_bound'):
                    continue
                parsed = record.get('parsed', record)
                if (parsed.get('statement_scope', 'consolidated') != 'consolidated' or parsed['report_date'] != p['source_stock_date'] or parsed['candidate_current_statement_count'] != 1):
                    raise ValueError('Frozen same-current-balance role changed')
                amount_eligible += 1
                available = parsed['publication']['available_date']
                for field in p['financial_fields']:
                    fact = parsed['fields'][field]
                    if (fact['stock_asof'] != parsed['report_date'] or fact['currency'] != 'CNY'
                            or fact['current_amount_yuan'] is None):
                        raise ValueError('Current amount role changed')
                    for day in p['anchors']:
                        if day >= available:
                            values[field].loc[(pd.Timestamp(day), record['symbol'])] = float(fact['current_amount_yuan'])
            values['close'] = price.reindex(index)
            env = {'Factor': _FactorFixture, '__name__': 'frozen-balance-stock-known-inputs'}
            exec(compile(Path(p['bindings']['native_code']['path']).read_text(encoding='utf-8'), '<frozen-balance-stock-native>', 'exec'), env)
            fraction = env[p['native_class']]().calculate({k: FactorSeriesFixture(v) for k, v in values.items()})
            if not isinstance(fraction, pd.Series) or not fraction.index.equals(index) or fraction.name != 'value':
                raise ValueError('Known-interface balance-stock output changed')
            f = f_all.reindex(index)
            # Negative values align the predeclared raw direction0; no sign search.
            raw = pd.concat([-fraction.rename('BALANCE'), -f.rename('F_OBSERVED')], axis=1).reset_index()
            raw['date'] = raw.date.dt.strftime('%Y-%m-%d')
            signals = raw[np.isfinite(raw.BALANCE) & np.isfinite(raw.F_OBSERVED)].copy()
            signals.to_parquet(out / 'formation_signals.parquet', index=False, compression='zstd')
            receipt = {
                'protocol_sha256': args.sha, 'signals_sha256': sha(out / 'formation_signals.parquet'),
                'all_price_peers_before_financial_mask': peer_count, 'price_input_hash': price_hash,
                'source_denominator': len(codes), 'current_amount_pair_bound': amount_eligible,
                'common_assets_by_date': signals.groupby('date').size().to_dict(),
                'distinct_candidate_values_by_date': signals.groupby('date').BALANCE.nunique().to_dict(),
                'explicit_zero_source_ratio_count': int((signals.BALANCE == 0).sum()),
                'native_known_interface_execution': True, 'remote_native_values_or_PIT_certified': False,
                'financial_production_qualification_overridden': False,
                'source_kind': 'Explicit original FY25 current-stock amount proxy; no certification of live default report or complete versions',
                'return_labels_read': 0,
            }
            write(out / 'input_receipt.json', receipt)
        positions = {d: n for n, d in enumerate(p['calendar'])}
        days = p['calendar'][1:]
        schedules = {name: {} for name in ('BALANCE', 'F_OBSERVED', 'COMMON_MARKET')}
        for day in p['anchors']:
            rows = signals[signals.date.eq(day)]
            entry = p['calendar'][positions[day] + 1]
            for name in ('BALANCE', 'F_OBSERVED'):
                groups, status = study.quantile_groups(rows[['symbol', name]].rename(columns={name: 'value'}), 10, 30)
                if status != 'groups_available':
                    raise ValueError('Exact ten groups pending: ' + day + ':' + name + ':' + status)
                schedules[name][entry] = {'decision_date': day, 'codes': groups['10']}
            schedules['COMMON_MARKET'][entry] = {'decision_date': day, 'codes': sorted(rows.symbol)}
        target_hash = study.object_hash(schedules)
        target_path = out / 'target_receipt.json'
        if target_path.exists() and read(target_path)['target_sha256'] != target_hash:
            raise ValueError('Frozen targets changed')
        if not target_path.exists():
            write(target_path, {'target_sha256': target_hash, 'holding_days': len(days), 'labels_not_yet_read': True})
        spec = importlib.util.spec_from_file_location('existing_cost_helpers', p['bindings']['cost_helpers']['path'])
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        book = helper.quote_book(p, set(signals.symbol))
        for fee in p['costs']:
            for name, schedule in schedules.items():
                key = helper.path_key(name, fee)
                if key in cp['completed']:
                    continue
                run = study.replay_portfolio(name, schedule, days, book, fee)
                record, ledger = helper.compact_run(run, days)
                cp['completed'][key] = {'record': record, 'daily': ledger}
                write(cp_path, cp)
                print('COMPLETE_COST_PATH', key, flush=True)
        daily = pd.DataFrame([row for result in cp['completed'].values() for row in result['daily']])
        daily.to_parquet(out / 'daily_net_ledger.parquet', index=False, compression='zstd')
        quotes = pd.read_parquet(p['bindings']['quotes']['path'], columns=['date', 'symbol', 'open'])
        old_labels = pd.read_parquet(p['bindings']['labels']['path'], columns=['date', 'symbol', 'forward_return', 'label_available'])
        labels = fixed_forward_open_labels(signals, quotes, p['calendar'], preserve_unavailable=old_labels)
        joined = signals.merge(labels, on=['date', 'symbol'], how='left', validate='one_to_one')
        valid = joined.label_available.eq(True) & np.isfinite(joined.forward_return)
        info = [{'date': day, 'assets': len(g),
                 'BALANCE_RankIC': g.BALANCE.corr(g.forward_return, method='spearman'),
                 'F_OBSERVED_RankIC': g.F_OBSERVED.corr(g.forward_return, method='spearman')}
                for day, g in joined[valid].groupby('date')]
        comparisons = []
        for fee in p['costs']:
            c = daily[(daily.name == 'BALANCE') & (daily.cost == fee)].set_index('date')
            for name in ('F_OBSERVED', 'COMMON_MARKET'):
                b = daily[(daily.name == name) & (daily.cost == fee)].set_index('date')
                comparisons.append({'cost': fee, 'control': name,
                    'relative_terminal_wealth': c.nav.iloc[-1] / b.nav.iloc[-1] - 1,
                    'daily_net_return_correlation': c.net_return.corr(b.net_return)})
        summary = {
            'status': 'ALL_SIX_FIXED_BALANCE_PROXY_PATHS_COMPLETE',
            'protocol_sha256': args.sha, 'completed_at_utc': datetime.now(timezone.utc).isoformat(),
            'results': [item['record'] for item in cp['completed'].values()], 'comparisons': comparisons,
            'source_denominator': 200, 'formation_periods': len(p['anchors']), 'holding_days': len(days),
            'usable_labels': int(valid.sum()), 'formed_label_denominator': len(signals),
            'missing_labels': int((~valid).sum()), 'information_by_period': info,
            'RankIC': {name: float(np.mean([x[name + '_RankIC'] for x in info])) for name in ('BALANCE', 'F_OBSERVED')},
            'control_label': 'Observed GF price component proxy; formal C16 remote RETURNS/input parity unverified',
            'signal_rank_correlation': float(signals.groupby('date').apply(lambda g: g.BALANCE.corr(g.F_OBSERVED, method='spearman')).median()),
            'ledger_sha256': sha(out / 'daily_net_ledger.parquet'),
            'source_limited_preliminary': True, 'full_PIT': False, 'remote_native_values_certified': False,
            'formal_admission': False, 'official_spent': 0, 'new_contexts': 3, 'errors': [],
        }
        write(out / 'summary.json', summary)
        cp.update(status='COMPLETED', pid=None, completed_paths=list(cp['completed']), completed={})
        write(cp_path, cp)
        print('ALL_SIX_FIXED_BALANCE_PROXY_PATHS_COMPLETE', flush=True)
    except Exception as error:
        cp.update(status='SOURCE_OR_EXECUTION_PENDING', pid=None, error=type(error).__name__ + ': ' + str(error))
        write(cp_path, cp)
        raise


if __name__ == '__main__':
    main()
