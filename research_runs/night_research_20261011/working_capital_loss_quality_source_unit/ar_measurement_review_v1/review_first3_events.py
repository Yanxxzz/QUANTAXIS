"""First-three whole-AR source event inventory; no monetary recomputation."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT))
from review_events import bound,canonical,consume_cases,sha

KEY_FIELDS=['code','announcement_id','report_date','source_record_sha256']
FIXED_REPORTS={('000157','1225055609'),('000403','1225245931'),('000409','1225084580')}
AMOUNT_STATUS='COMPLETED_FIXED6_NOTE_CONFIRMED_AND_PARTIAL_PRINTED_FACTS'


def report_key(record):return {key:record[key] for key in KEY_FIELDS}


def select_first3_whole_AR(scope,selected_keys,plan):
    """Validate only already-bound pair metadata, never use cell amounts."""
    if scope.get('status')!=AMOUNT_STATUS:raise ValueError('first3_amount_source_receipt_incomplete')
    if len(selected_keys)!=3 or {(r['code'],r['announcement_id']) for r in selected_keys}!=FIXED_REPORTS:
        raise ValueError('fixed_first3_AR_reports_required')
    if any(r['report_date']!='2025-12-31' for r in selected_keys):raise ValueError('fixed_FY2025_reports_required')
    selected={canonical(r) for r in selected_keys}
    if len(selected)!=3:raise ValueError('duplicate_first3_report_KEY')
    records={canonical(report_key(r)):r for r in scope['records']}
    if len(records)!=len(scope['records']) or not selected<=records.keys():raise ValueError('first3_amount_exact_KEY_pending')
    candidates={canonical(report_key(r)):r for r in plan['selected_cases']}
    if not selected<=candidates.keys():raise ValueError('first3_original_plan_KEY_pending')
    cases=[];pair_metadata={}
    for key in selected_keys:
        encoded=canonical(key);record=records[encoded];case=candidates[encoded]
        if record['retained_text_sha256']!=case['retained_text_sha256']:raise ValueError('first3_amount_native_text_identity_changed')
        stock=[t for t in record['tables'] if t['asset']=='AR' and t['kind']=='stock']
        periods={}
        for period in ['closing','opening']:
            matches=[t for t in stock if t['period_role']==period]
            if len(matches)!=1:raise ValueError('unique_whole_AR_period_pair_pending')
            table=matches[0]
            if (table['population_kind']!='whole_AR_classification_total'
                or table['status']!='EXACT_SOURCE_FACTS_ONLY_NOT_ELIGIBILITY'
                or set(table['cells'])!={'gross','allowance_disclosed','net'}):
                raise ValueError('whole_AR_three_printed_roles_pending')
            periods[period]={field:table.get(field) for field in ['table_id','printed_period_label','literal_date_printed','period_context','unit_status']}
        pair_metadata[encoded]={'whole_AR_printed_pair_complete_for_source_selection_only':True,
            'periods':periods,'amounts_or_movement_recomputed':False,'AR_measurement_or_financial_eligibility_cleared':False,
            'currency_calendar_policy_and_version_questions_resolved_here':False}
        cases.append(case)
    return cases,pair_metadata


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--protocol',type=Path,default=OUT/'protocol_first3.json')
    parser.add_argument('--protocol-sha');parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    raw=args.protocol.read_bytes();p=json.loads(raw)
    if not args.execute:
        print(json.dumps({'status':'DEFERRED_FIRST3_AR_EVENT_INVENTORY_NO_BODY_AMOUNTS_Q_OR_LABEL_READ','protocol_sha256':sha(raw)}));return
    if (not args.protocol_sha or sha(raw)!=args.protocol_sha or sha(Path(__file__).read_bytes())!=p['runtime_sha256']
        or p['status']!='FROZEN_FIRST3_AR_SOURCE_EVENT_INVENTORY_AWAIT_ROOT_EXECUTION'):
        raise ValueError('root_first3_AR_inventory_protocol_required')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']):raise ValueError('human_research_deadline')
    output=Path(p['output_path'])
    if output.exists():raise ValueError('first3_AR_inventory_exists_no_replay')
    with sqlite3.connect(Path(p['registry_path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?",(p['event_prefix']+args.protocol_sha,p['registration_subject'])).fetchone()
        if not row or json.loads(row[0]).get('new_contexts')!=0:raise ValueError('root_first3_source_only_registration_required')
    for item in p['bindings'].values():bound(item)
    scope=json.loads(bound(p['bindings']['first3_amount_receipt'],True))
    plan=json.loads(bound(p['bindings']['source_plan'],True))
    cases,pair_metadata=select_first3_whole_AR(scope,p['selected_report_keys'],plan)
    del scope
    idx=plan['bindings']['source_indices'];indexes={ref['path']:json.loads(bound(ref,True)) for ref in idx.values()}
    spec=importlib.util.spec_from_file_location('bound_first3_AR_event_reader',p['bindings']['source_reader']['path'])
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    with sqlite3.connect(Path(p['bindings']['capture_db']['path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        consumed=consume_cases(cases,reader,db,indexes,idx)
    for record in consumed['records']:record['prior_amount_pair_selection_metadata']=pair_metadata[canonical(record['source_identity'])]
    result={'status':('PARTIAL_FIRST3_AR_SOURCE_EVENT_INVENTORY_SOURCE_PENDING_STOPPED' if consumed['errors'] else
        'COMPLETED_FIRST3_AR_SOURCE_EVENT_INVENTORY_PENDING_IMPACT_REVIEW'),'protocol_sha256':args.protocol_sha,**consumed,
        'purpose':'AR_measurement_group_estimate_opening_restatement_and_version_short_locator_only',
        'source_only_selection_not_economic_sample_or_OOS_qualification':True,'new_contexts':0,
        'stock_amounts_or_movement_recomputed':False,'Q_computed':False,'price_or_label_read':False,
        'new_factor_or_mask_defined':False,'AR_measurement_or_financial_eligibility_cleared':False,'official_calls':0,'new_report_copies':0}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'records':len(consumed['records']),'errors':len(consumed['errors']),'output_sha256':sha(output.read_bytes())}))


if __name__=='__main__':main()
