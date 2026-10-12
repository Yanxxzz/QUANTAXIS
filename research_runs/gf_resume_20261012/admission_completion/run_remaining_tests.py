"""Frozen retained/current pool and historical-size proxy tests, without tuning."""
import argparse
from datetime import datetime, timezone
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from panda_alpha import study
from panda_alpha.registry import ResearchRegistry
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture

OUT = Path(__file__).parent


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(study._clean(value), ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rolling(s, n, method='mean'):
    result = getattr(s.groupby(level='symbol', sort=False).rolling(n, min_periods=n), method)()
    return result.reset_index(level=0, drop=True).reindex(s.index)


def rank(s):
    return s.groupby(level='date', sort=False).rank(method='average', pct=True)


def returns(s, n=1):
    return s.groupby(level='symbol', sort=False).pct_change(periods=n, fill_method=None)


def corr(a, b, n):
    # Groupwise rolling Pearson correlation, preserving observed per-symbol rows.
    pieces = []
    for symbol, g in a.groupby(level='symbol', sort=False):
        pieces.append(g.rolling(n, min_periods=n).corr(b.reindex(g.index)))
    return pd.concat(pieces).reindex(a.index)


def z(s):
    x = s.clip(*s.quantile([.01, .99]).tolist())
    sd = x.std(ddof=0)
    if not np.isfinite(sd) or sd <= 1e-12:
        raise ValueError('Degenerate pool component')
    return (x-x.mean())/sd


def build_signals(p):
    print('READ_BOUND_EXISTING_PEER_CLOSE_AND_TECHNICAL_FIELDS', flush=True)
    frame = read_mongo_gf_fixture(p['bindings']['descriptor']['path'])
    digest = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
    if digest != p['expected_price_view_hash']:
        raise ValueError('Existing source close view changed; do not silently recalculate')
    bars = pd.read_parquet(p['bindings']['technical_bars']['path'], columns=[
        'date', 'code', 'open', 'high', 'close', 'volume', 'amount', 'turnover', 'total_mv'],
        filters=[('date', '>=', 20240603), ('date', '<=', 20260903)])
    bars['date'] = pd.to_datetime(bars.date.astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    bars['code'] = bars.code.astype(str).str.zfill(6)
    bars = bars.rename(columns={'code': 'symbol', 'close': 'raw_close'})
    if bars.duplicated(['date', 'symbol']).any():
        raise ValueError('Conflicting existing technical input identities')
    frame = frame.merge(bars, on=['date', 'symbol'], how='left', validate='one_to_one')
    raw_valid = frame.raw_close.gt(0) & np.isfinite(frame.raw_close) & frame.close.gt(0)
    scale = (frame.close/frame.raw_close).where(raw_valid)
    # StockDB cache volume is in shares, amount in currency. No QA lots conversion.
    frame['open'] *= scale
    frame['high'] *= scale
    frame['vwap'] = (frame.amount/frame.volume).where(frame.volume.gt(0)) * scale
    frame['date'] = pd.to_datetime(frame.date)
    frame = frame.sort_values(['symbol', 'date']).set_index(['date', 'symbol'])
    factors = {name: frame[name] for name in ['close', 'open', 'turnover']}
    env = {'Factor': object, 'np': np, 'pd': pd, 'RETURNS': returns,
           'RANK': rank, 'MA': rolling, 'CORR': corr,
           'TS_MAX': lambda s, n: rolling(s, n, 'max')}
    definitions = {name: read(b['path']) for name, b in p['definitions'].items()}
    print('CALCULATE_OWN_FROZEN_POOL_COMPONENTS_BEFORE_FINANCIAL_MASK', flush=True)
    fenv = dict(env)
    exec(compile(definitions['C16']['content'], '<owned-C16>', 'exec'), fenv)
    f141 = fenv['F141ResidualVarianceShareTransfer']().calculate(factors)
    values = {'C16': f141}
    for name in ['C01', 'C02', 'C03', 'C04', 'C17']:
        local = dict(env, CLOSE=frame.close, HIGH=frame.high, TURNOVER=frame.turnover,
                     AMOUNT=frame.vwap, VOLUME=pd.Series(1., index=frame.index))
        # For C17, AMOUNT/VOLUME represents already price-adjusted same-source VWAP.
        values[name] = eval(compile(definitions[name]['content'], '<owned-'+name+'>', 'eval'), local)
    fenv = dict(env)
    exec(compile(definitions['F253']['content'], '<owned-F253>', 'exec'), fenv)
    instance = fenv['LocalCandidate']()
    instance._f141 = lambda _: f141  # Exact owned C16 component, computed once.
    values['F253'] = instance.calculate(factors)
    anchors = pd.to_datetime(p['anchors'])
    keep = frame.index.get_level_values('date').isin(anchors)
    panel = pd.DataFrame({name: value.reindex(frame.index)[keep] for name, value in values.items()})
    panel['total_mv'] = frame.total_mv[keep]
    panel = panel.reset_index()
    panel['date'] = panel.date.dt.strftime('%Y-%m-%d')
    cutoffs = panel.loc[panel.total_mv.gt(0)].groupby('date').total_mv.quantile(.2).to_dict()
    gf = pd.read_parquet(p['bindings']['gf_signals']['path'])
    all_source = gf.merge(panel, on=['date', 'symbol'], how='left', validate='one_to_one')
    tech = ['C01', 'C02', 'C03', 'C04', 'C16', 'C17', 'F253']
    finite = np.isfinite(all_source[tech]).all(axis=1)
    shared = all_source[finite].copy()
    size = all_source[all_source.total_mv.gt(0) & all_source.apply(
        lambda r: r.total_mv > cutoffs.get(r.date, np.inf), axis=1)].copy()
    if shared.empty or size.empty:
        raise ValueError('Required shared/size support is unavailable')
    dependencies = []
    for day, rows in shared.groupby('date', sort=True):
        for name in tech:
            signed = rows[name] * (1 if name in ('C17', 'F253') else -1)
            dependencies.append({'date': day, 'member': name, 'assets': len(rows),
                                 'pearson': rows.GF.corr(signed),
                                 'spearman': rows.GF.corr(signed, method='spearman')})
        for name, members in p['pools'].items():
            parts = []
            for member in members:
                value = rows[member] * (1 if member in ('C17', 'F253', 'GF') else -1)
                parts.append(z(value))
            shared.loc[rows.index, name] = sum(parts)/len(parts)
    signals = shared[['date', 'symbol', *p['pools']]].copy()
    signals.to_parquet(OUT/'pool_formation_signals.parquet', index=False, compression='zstd')
    size[['date', 'symbol', 'GF']].to_parquet(OUT/'size_formation_signals.parquet', index=False, compression='zstd')
    receipt = {'original_GF_rows': len(gf), 'shared_pool_rows': len(shared),
               'missing_technical_input_rows': int((~finite).sum()),
               'formation_support': [{ 'date': d, 'original_GF': len(g),
                   'shared_all_pool_inputs': int((shared.date==d).sum()),
                   'positive_size_known': int(g.total_mv.gt(0).sum()),
                   'large80_GF': int((size.date==d).sum()), 'all_received_positive_mv_20pct_cutoff': cutoffs[d] }
                   for d, g in all_source.groupby('date', sort=True)],
               'factor_dependencies': dependencies,
               'proxy_price_view_sha256': digest,
               'field_missing_counts_full_peer_view': frame[['open','high','turnover','vwap','total_mv']].isna().sum().to_dict(),
               'pool_signals_sha256': sha(OUT/'pool_formation_signals.parquet'),
               'size_signals_sha256': sha(OUT/'size_formation_signals.parquet'),
               'historical_vintage_verified': False, 'native_field_parity': False,
               'forward_quotes_used_for_formation': False, 'source_stock_selection_by_future_prices': False}
    write(OUT/'test_input_receipt.json', receipt)
    del frame, panel, values, factors, f141, all_source
    gc.collect()
    return signals, size[['date','symbol','GF']], receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    ppath = OUT/'test_protocol.json'
    if sha(ppath) != args.sha:
        raise ValueError('Frozen test protocol changed')
    p = read(ppath)
    if (OUT/'test_summary.json').exists() and read(OUT/'test_summary.json').get('status') == 'ALL_REMAINING_LOCAL_PATHS_COMPLETE':
        raise ValueError('Complete tests exist; no duplicate execution')
    for item in [*p['bindings'].values(), *p['definitions'].values()]:
        if sha(item['path']) != item['sha256']:
            raise ValueError('Frozen artifact changed: '+item['path'])
    registry = ResearchRegistry(ROOT/'research_runs/research_trials.sqlite3')
    if not any(e['event_id']=='gf-admission-tests-freeze:'+args.sha for e in registry.events()):
        raise ValueError('Tests were not registered before calculation')
    registry.db.close()
    cp_path = OUT/'test_checkpoint.json'
    if cp_path.exists() and not args.resume:
        raise ValueError('Owned checkpoint exists; inspect actual PID before resume')
    cp = read(cp_path) if cp_path.exists() else {'protocol_sha256': args.sha, 'completed': {}, 'errors': []}
    cp.update(status='RUNNING', pid=os.getpid(), active_path=None)
    write(cp_path, cp)
    try:
        if (OUT/'test_input_receipt.json').exists():
            receipt = read(OUT/'test_input_receipt.json')
            for file, key in [('pool_formation_signals.parquet','pool_signals_sha256'),('size_formation_signals.parquet','size_signals_sha256')]:
                if sha(OUT/file) != receipt[key]:
                    raise ValueError('Frozen computed formation signal changed')
            signals = pd.read_parquet(OUT/'pool_formation_signals.parquet')
            size = pd.read_parquet(OUT/'size_formation_signals.parquet')
        else:
            signals, size, receipt = build_signals(p)
        parent = read(p['bindings']['parent']['path'])
        cal = parent['calendar']; pos = {d: n for n,d in enumerate(cal)}
        days = [d for d in cal if p['window']['holding_start'] <= d <= p['window']['holding_end']]
        schedules = {n: {} for n in [*p['pools'], 'SIZE_GF', 'SIZE_MARKET']}
        for day in p['anchors']:
            entry = cal[pos[day]+1]
            for name in schedules:
                rows = (size if name.startswith('SIZE_') else signals)
                rows = rows[rows.date==day]
                if name == 'SIZE_MARKET':
                    codes = sorted(rows.symbol.tolist())
                else:
                    field = 'GF' if name == 'SIZE_GF' else name
                    groups, status = study.quantile_groups(rows[['symbol',field]].rename(columns={field:'value'}),10,30)
                    if status != 'groups_available':
                        raise ValueError('Groups pending: '+name+':'+day+':'+status)
                    codes = groups['10']
                schedules[name][entry] = {'decision_date': day, 'codes': codes}
        write(OUT/'test_target_receipt.json', {'days': len(days), 'target_sha256': study.object_hash(schedules),
                                             'all_targets_frozen_before_forward_quote_read': True})
        helper = load(p['bindings']['cost_helpers']['path'], 'existing_cost_helpers')
        book = helper.quote_book(p, set(signals.symbol)|set(size.symbol))
        for fee in p['costs']:
            for name in schedules:
                key = helper.path_key(name,fee)
                if key in cp['completed']:
                    continue
                cp['active_path'] = key; write(cp_path,cp)
                run = study.replay_portfolio(name,schedules[name],days,book,fee)
                record, daily = helper.compact_run(run,days)
                cp['completed'][key] = {'record':record, 'daily':daily}
                write(cp_path,cp)
                del run
                print('COMPLETED_COST_PATH',key,flush=True)
        daily = pd.DataFrame([row for val in cp['completed'].values() for row in val['daily']])
        daily.to_parquet(OUT/'test_daily_net_ledger.parquet',index=False,compression='zstd')
        comparisons = []
        for fee in p['costs']:
            for cand, base in [('CURRENT_ADD_GF','CURRENT_POOL'),('RETAINED_ADD_GF','RETAINED_POOL'),('RETAINED_REPLACE_C16_GF','RETAINED_POOL'),('SIZE_GF','SIZE_MARKET')]:
                a=daily[(daily.name==cand)&(daily.cost==fee)].set_index('date')
                b=daily[(daily.name==base)&(daily.cost==fee)].set_index('date')
                if not a.index.equals(b.index): raise ValueError('Shared ledger dates diverged')
                am=cp['completed'][helper.path_key(cand,fee)]['record']['metrics']
                bm=cp['completed'][helper.path_key(base,fee)]['record']['metrics']
                relative=a.nav.iloc[-1]/b.nav.iloc[-1]
                comparisons.append({'candidate':cand,'baseline':base,'cost':fee,
                    'net_return_delta':am['compounded_return']-bm['compounded_return'],
                    'sharpe_delta':am['sharpe']-bm['sharpe'],
                    'max_drawdown_delta':am['max_drawdown']-bm['max_drawdown'],
                    'relative_wealth':relative-1,
                    'annualized_relative_wealth':relative**(252/len(days))-1,
                    'daily_return_correlation':a.net_return.corr(b.net_return)})
        write(OUT/'test_summary.json', {'status':'ALL_REMAINING_LOCAL_PATHS_COMPLETE',
            'protocol_sha256':args.sha,'completed_at_utc':datetime.now(timezone.utc).isoformat(),
            'results':[v['record'] for v in cp['completed'].values()], 'comparisons':comparisons,
            'new_contexts':len(p['contexts']),'holding_days':len(days),'formation_periods':len(p['anchors']),
            'ledger_sha256':sha(OUT/'test_daily_net_ledger.parquet'),
            'historical_vintage_verified':False,'native_field_parity':False,'formal_admission':False,
            'official_runs':0,'official_spent':0,'pool_operations':0,'errors':[]})
        cp.update(status='COMPLETED',pid=None,active_path=None,completed_paths=list(cp['completed']),completed={})
        write(cp_path,cp)
    except Exception as error:
        cp.update(status='EXECUTION_PENDING',pid=None,error=type(error).__name__+': '+str(error))
        write(cp_path,cp)
        raise


if __name__ == '__main__':
    main()
