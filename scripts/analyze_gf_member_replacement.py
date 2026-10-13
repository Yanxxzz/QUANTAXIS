"""Review only complete frozen member comparisons; never create new portfolios."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def bound(item):
    path = Path(item['path'])
    if sha(path) != item['sha256']:
        raise ValueError('Comparison evidence changed: ' + str(path))
    return path


def paired_review(current, prior, entry_dates):
    if not current.date.equals(prior.date):
        raise ValueError('Paired holding dates are different')
    dates = current.date.to_numpy()
    blocks = np.searchsorted(np.asarray(entry_dates), dates, side='right') - 1
    if blocks.min() != 0 or blocks.max() != 50 or len(np.unique(blocks)) != 51:
        raise ValueError('All 51 original holding blocks are required')
    log_difference = np.log1p(current.net_return.to_numpy()) - np.log1p(prior.net_return.to_numpy())
    totals = np.bincount(blocks, weights=log_difference, minlength=51)
    relative = float(current.nav.iloc[-1] / prior.nav.iloc[-1] - 1)
    if abs(np.expm1(totals.sum()) - relative) > 1e-10:
        raise ValueError('Block log differences do not reconcile to relative wealth')
    # Frozen seed/number of draws. This is descriptive, not a new family test.
    sampled = np.random.default_rng(20261013).integers(0, 51, size=(10000, 51))
    interval = np.quantile(np.expm1(totals[sampled].sum(axis=1)), [.025, .975])
    price = float(current.price_pnl.sum() - prior.price_pnl.sum())
    fee = float(current.fees.sum() - prior.fees.sum())
    net = float(current.nav.iloc[-1] - prior.nav.iloc[-1])
    if abs(net - (price - fee)) > 1e-9:
        raise ValueError('Price/fee difference does not reconcile to NAV difference')
    return {
        'net_return_delta_initial_equity_units': net,
        'price_pnl_delta_initial_equity_units': price,
        'fee_delta_initial_equity_units': fee,
        'relative_terminal_wealth': relative,
        'paired_51_block_relative_wealth_bootstrap_95pct': interval.tolist(),
        'bootstrap_interval_contains_zero': bool(interval[0] <= 0 <= interval[1]),
        'positive_paired_blocks': int((totals > 0).sum()),
        'negative_paired_blocks': int((totals < 0).sum()),
        'bootstrap_draws': 10000, 'bootstrap_seed': 20261013,
        'bootstrap_scope': 'Paired whole rebalance blocks, preserving within-block daily structure. Cross-block dependence/stationarity and search-family adjustment are not certified; this is retrospective uncertainty, not causal alpha proof.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--sha', required=True)
    args = parser.parse_args()
    path = Path(args.protocol).resolve()
    if sha(path) != args.sha:
        raise ValueError('Frozen protocol changed')
    out = path.parent
    review_path = out / 'cause_review.json'
    if review_path.exists():
        raise ValueError('Complete cause review exists; do not produce duplicates')
    protocol = read(path)
    summary = read(out / 'summary.json')
    if (summary['status'] != 'ALL_EIGHT_MEMBER_REPLACEMENT_PATHS_COMPLETE'
            or summary['protocol_sha256'] != args.sha or summary['errors']):
        raise ValueError('All eight frozen paths must complete before review')
    old_protocol = read(bound(protocol['bindings']['original_protocol']))
    old_summary = read(bound(protocol['bindings']['original_summary']))
    new_daily = pd.read_parquet(bound(summary['daily_net_ledger']))
    old_daily = pd.read_parquet(bound(protocol['bindings']['original_daily_ledger']))
    calendar = read(old_protocol['bindings']['parent']['path'])['calendar']
    position = {day: n for n, day in enumerate(calendar)}
    entries = [calendar[position[day] + 1] for day in protocol['anchors']]
    targets = read(bound(summary['targets']))
    input_receipt = read(bound(summary['input_receipt']))
    for item in summary['results']:
        bound(item['result'])
    names = list(protocol['pools']) + ['RETAINED_ADD_GF', 'RETAINED_REPLACE_C16_GF']
    comparisons = []
    for cost in protocol['costs']:
        prior = old_daily[(old_daily.name == 'RETAINED_POOL') & (old_daily.cost == cost)].sort_values('date').reset_index(drop=True)
        base = next(r for r in old_summary['results'] if r['name'] == 'RETAINED_POOL' and r['cost'] == cost)
        for name in names:
            is_new = name in protocol['pools']
            frame = new_daily if is_new else old_daily
            current = frame[(frame.name == name) & (frame.cost == cost)].sort_values('date').reset_index(drop=True)
            item = next(r for r in (summary['results'] if is_new else old_summary['results']) if r['name'] == name and r['cost'] == cost)
            record = read(item['result']['path']) if is_new else item
            calculation = paired_review(current, prior, entries)
            comparisons.append({'candidate': name, 'baseline': 'RETAINED_POOL', 'cost': cost,
                'baseline_metrics': base['metrics'], 'candidate_metrics': item['metrics'],
                'sharpe_delta': item['metrics']['sharpe'] - base['metrics']['sharpe'],
                'max_drawdown_delta': item['metrics']['max_drawdown'] - base['metrics']['max_drawdown'],
                'annual_partial_year_returns': {year: values['compounded_return'] for year, values in record['annual'].items()},
                'baseline_annual_partial_year_returns': {year: values['compounded_return'] for year, values in base['annual'].items()},
                'terminal_unliquidated_fraction': record['terminal_unliquidated_fraction'],
                'risk_metadata_status': record['risk_metadata_status'],
                'mean_one_side_turnover_proxy': record['exposure_summary']['mean_one_side_turnover_proxy'],
                'maximum_industry_wealth_fraction': record['exposure_summary']['maximum_industry_wealth_fraction'],
                **calculation})
    selection = []
    frame = pd.DataFrame(targets['selection_changes'])
    for name, rows in frame.groupby('name', sort=False):
        selection.append({'name': name, 'formation_periods': len(rows),
            'mean_jaccard_vs_baseline': float(rows.jaccard_vs_baseline.mean()),
            'mean_baseline_stock_retention_fraction': float((rows.retained_count / rows.baseline_selected).mean()),
            'mean_added_count': float(rows.added_count.mean()),
            'mean_score_spearman_vs_baseline': float(rows.score_spearman_vs_baseline.mean())})
    correlations = []
    frame = pd.DataFrame(targets['factor_correlations'])
    for name, rows in frame.groupby('comparison', sort=False):
        correlations.append({'comparison': name, 'formation_periods': len(rows),
                             'mean_pearson': float(rows.pearson.mean()),
                             'mean_spearman': float(rows.spearman.mean())})
    review = {
        'status': 'COMPLETE_PAIRED_PRICE_FEE_AND_MEMBER_CAUSE_REVIEW',
        'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
        'protocol': {'path': str(path), 'sha256': args.sha},
        'analysis_runtime': {'path': str(Path(__file__).resolve()), 'sha256': sha(__file__)},
        'summary': {'path': str(out / 'summary.json'), 'sha256': sha(out / 'summary.json')},
        'comparisons': comparisons, 'selection_changes': selection,
        'factor_correlations': correlations,
        'GF_Z_max_equivalence_error': max(r['GF_Z_max_absolute_error'] for r in input_receipt['GF_Z_equivalence']),
        'formal_admission': False, 'official_runs': 0, 'official_spent': 0,
        'old_controls_replayed': False, 'new_contexts_from_analysis': 0,
        'limits': [
            'Same exposed source-limited historical support; not a new independent OOS window.',
            summary['source_scope_limit'],
            'Old baseline stock/industry identity contribution details were not retained and are not reconstructed; only old total price/fee differences can be paired exactly.',
            'New stock/industry contribution is descriptive. PricePnL also incorporates fee-driven position scaling; no causal market/alpha projection is claimed.',
            'Four-member removals are research diagnostics; no formal pool removal or sub-five-member launch is authorized.',
        ],
    }
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2,
                                     allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': review['status'], 'review_sha256': sha(review_path),
                      'paired_comparisons': len(comparisons)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
