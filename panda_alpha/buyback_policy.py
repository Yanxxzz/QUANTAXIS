"""PIT source-score classification; peer announcements after formation stay out.

This only repairs policy timing. It does not make a retrospective source sample
complete or certify returns already inspected during an exploratory event study.
"""
from __future__ import annotations
import numpy as np


def expanding_participation_groups(events: list[dict], minimum_prior: int = 20) -> list[dict]:
    if minimum_prior < 3:
        raise ValueError('At least three prior source observations required')
    ordered=sorted(events,key=lambda r:(r['decision_date'],r['symbol'],r['announcement_id']))
    ids=[r['announcement_id'] for r in ordered]
    if len(set(ids))!=len(ids):
        raise ValueError('Duplicate source event IDs')
    for r in ordered:
        if not np.isfinite(r['score']) or r['score']<0:
            raise ValueError('Invalid source score')
    out=[]
    for current in ordered:
        # Whole formation-day batch shares the same strictly prior history.
        history=[r for r in ordered if r['decision_date']<current['decision_date']]
        row={**current,'historical_event_ids':[r['announcement_id'] for r in history],
             'historical_observations':len(history),'history_last_day':max((r['decision_date'] for r in history),default=None),
             'policy_arm':'CALIBRATION_PENDING','policy_point_in_time':True}
        if len(history)>=minimum_prior:
            lower,upper=np.quantile([r['score'] for r in history],[1/3,2/3],method='linear')
            row.update(low_cutoff=float(lower),high_cutoff=float(upper))
            if lower>=upper:
                row['policy_arm']='NONDISCRIMINATING_HISTORY'
            elif current['score']>=upper:row['policy_arm']='HIGH'
            elif current['score']<=lower:row['policy_arm']='LOW'
            else:row['policy_arm']='MIDDLE'
        out.append(row)
    return out
