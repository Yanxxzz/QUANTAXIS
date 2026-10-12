"""Frozen value-only input-grid diagnosis; no return labels or paid requests."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
import pandas as pd


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def previous_observed(series, n):
    return series.groupby(level='symbol', sort=False).pct_change(periods=n, fill_method=None)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    args = parser.parse_args()
    if sha(args.protocol) != args.sha:
        raise ValueError('frozen source diagnostic changed')
    p = json.loads(args.protocol.read_text(encoding='utf-8'))
    out = Path(p['output'])
    if out.exists():
        raise ValueError('completed input diagnosis exists; no repeat source read')
    for b in p['bindings'].values():
        if sha(b['path']) != b['sha256']:
            raise ValueError('source/runtime binding changed')
    from panda_alpha.registry import ResearchRegistry
    r = ResearchRegistry(p['registry'])
    assert any(e['event_id'] == 'gf-input-freeze:' + args.sha for e in r.events())
    r.db.close()
    print('READING_EXISTING_PRICE_ONLY_FIXTURE', flush=True)
    from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
    frame = read_mongo_gf_fixture(p['bindings']['descriptor']['path'])
    frame['date'] = pd.to_datetime(frame['date'])
    index = pd.MultiIndex.from_frame(frame[['date', 'symbol']])
    price = pd.Series(frame.close.to_numpy(), index=index, name='close').astype(float)
    calendar = pd.to_datetime(p['calendar'])
    matrix = price.unstack('symbol').reindex(calendar)
    matrix.index.name = 'date'; matrix.columns.name = 'symbol'
    namespace = {'np': np, 'pd': pd, 'Factor': object, 'RETURNS': previous_observed}
    exec(compile(Path(p['bindings']['production_code']['path']).read_text(encoding='utf-8'), '<owned-F141>', 'exec'), namespace)
    factor = namespace['F141ResidualVarianceShareTransfer']()
    print('CALCULATING_OBSERVED_BAR_CONTRACT_PROXY', flush=True)
    sparse = factor.calculate({'close': price})
    print('CALCULATING_RECTANGULAR_CALENDAR_CONTRACT_PROXY', flush=True)
    rectangular_price = matrix.stack(dropna=False)
    rectangular = factor.calculate({'close': rectangular_price})
    spec = importlib.util.spec_from_file_location('frozen_F141_math', p['bindings']['research_math']['path'])
    math = importlib.util.module_from_spec(spec); spec.loader.exec_module(math)
    math.np = np; math.pd = pd  # Frozen extracted function relies on caller globals.
    print('CALCULATING_EXISTING_RESEARCH_CALENDAR_REFERENCE', flush=True)
    research = math.peer_share(matrix).rank(axis=1, method='average', pct=True).stack(dropna=False)
    rows = []
    for day in pd.to_datetime(p['dates']):
        a, b, c = [x.xs(day, level='date') for x in (sparse, rectangular, research)]
        common = pd.concat([a.rename('observed'), b.rename('calendar'), c.rename('research')], axis=1)
        finite = common.replace([np.inf, -np.inf], np.nan).dropna()
        n = max(1, int(np.ceil(len(finite) * .1)))
        def overlap(x, y):
            return len(set(finite[x].nsmallest(n).index) & set(finite[y].nsmallest(n).index)) / n
        rows.append({'date': day.strftime('%Y-%m-%d'), 'received_price_peers': int(matrix.loc[day].notna().sum()),
                     'observed_finite': int(np.isfinite(a).sum()), 'calendar_finite': int(np.isfinite(b).sum()),
                     'research_finite': int(np.isfinite(c).sum()), 'joint_finite': len(finite),
                     'observed_calendar_max_rank_error': float((finite.observed-finite.calendar).abs().max()),
                     'calendar_research_max_rank_error': float((finite.calendar-finite.research).abs().max()),
                     'observed_research_rank_correlation': float(finite.observed.corr(finite.research, method='spearman')),
                     'observed_research_low_decile_overlap': overlap('observed','research'),
                     'calendar_research_low_decile_overlap': overlap('calendar','research')})
    result = {'status':'COMPLETE_VALUE_ONLY_F141_INPUT_GRID_DIAGNOSIS',
              'protocol_sha256':args.sha, 'source_rows':len(frame), 'symbols':int(frame.symbol.nunique()),
              'calendar_days':len(calendar), 'price_frame_hash':hashlib.sha256(pd.util.hash_pandas_object(frame,index=True).values.tobytes()).hexdigest(),
              'dates':rows, 'operator_assumption':'previous observed per-symbol close, no fill; same rule on rectangular calendar. Actual platform RETURNS remains unverified.',
              'native_platform_values_parity_certified':False, 'old_GF_F141_outputs_replaced':False,
              'new_economic_contexts':0, 'forward_return_labels_read':False, 'official_calls':0,
              'price_snapshot_saved':False, 'completed_at_utc':datetime.now(timezone.utc).isoformat()}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
