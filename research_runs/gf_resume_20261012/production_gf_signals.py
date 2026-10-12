"""Frozen-source GF composition after full-peer production-style F141 calculation."""
import numpy as np
import pandas as pd


def build_signals(native_f, old_qualification, date_anchors):
    if (not isinstance(native_f.index, pd.MultiIndex)
            or list(native_f.index.names) != ['date', 'symbol'] or not native_f.index.is_unique):
        raise ValueError('Unique date/symbol native F values required')
    required = {'date', 'symbol', 'source_qualified', 'G_pct'}
    if not required <= set(old_qualification.columns):
        raise ValueError('Explicit bound G_pct and source qualification required')
    q = old_qualification.copy()
    q['date'] = pd.to_datetime(q.date)
    if q.duplicated(['date', 'symbol']).any():
        raise ValueError('Duplicate source identity')
    n = native_f.rename('production_F').reset_index()
    n['date'] = pd.to_datetime(n.date)
    merged = q.merge(n, on=['date', 'symbol'], how='left', validate='one_to_one', indicator=True)
    output, support = [], []
    for day in pd.to_datetime(date_anchors):
        dated = merged[merged.date.eq(day)]
        valid_g = dated.source_qualified.eq(True) & np.isfinite(dated.G_pct)
        common = dated[valid_g & np.isfinite(dated.production_F)].copy()
        # G is the original industry percentile. F missingness never re-ranks G.
        f_pct = common.production_F.rank(method='average', pct=True)
        z = []
        for values in [common.G_pct, -f_pct]:
            clipped = values.clip(*values.quantile([.01, .99]).tolist())
            sd = float(clipped.std(ddof=0))
            if not np.isfinite(sd) or sd <= 1e-12:
                raise ValueError('Constant/empty frozen component: '+str(day.date()))
            z.append((clipped-clipped.mean())/sd)
        common['GF'] = (z[0]+z[1])/2
        # Original direction0 comparator; same common support and component transform.
        common['F141_CONTROL'] = -z[1]
        if not np.isfinite(common[['GF', 'F141_CONTROL']]).all().all():
            raise ValueError('Nonfinite fixed GF/control output')
        output.append(common[['date', 'symbol', 'GF', 'F141_CONTROL']])
        native_on_day = n[n.date.eq(day)]
        support.append({'date': str(day.date()), 'all_received_native_rows': len(native_on_day),
                        'all_received_native_finite': int(np.isfinite(native_on_day.production_F).sum()),
                        'original_qualified_G': int(valid_g.sum()), 'common_finite': len(common),
                        'G_qualified_missing_F': int(valid_g.sum())-len(common),
                        'old_expected_finite_used_as_new_mask': False})
    signals = pd.concat(output, ignore_index=True).set_index(['date', 'symbol']).sort_index()
    return signals, support
