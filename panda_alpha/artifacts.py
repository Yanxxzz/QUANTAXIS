"""Small research results by default; full execution traces are opt-in."""
from __future__ import annotations

DAILY_FIELDS=('date','nav','net_return','gross_return','fees','cash_after','known_suspension_mark')

def compact_study_result(result: dict) -> dict:
    """Change storage only; preserve quality evidence and the caller's result."""
    out={k:v for k,v in result.items() if k!='runs'}
    runs=[]
    for run in result.get('runs',[]):
        row={k:v for k,v in run.items() if k not in {'daily','orders','trades','exposures'}}
        if 'daily' in run:
            row['daily']=[{k:d[k] for k in DAILY_FIELDS if k in d} for d in run['daily']]
        if 'exposures' in run:
            row['exposures']={k:v for k,v in run['exposures'].items() if k!='daily'}
        runs.append(row)
    out['runs']=runs
    out['artifact_storage']={'format':'compact_study_v1','execution_details_retained':False,
                             'metrics_costs_folds_source_gates_and_failure_reasons_retained':True}
    return out
