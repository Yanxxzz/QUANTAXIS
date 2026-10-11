"""Deferred fixed three-source/six-note printed amount facts; no alpha/mask."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from amount_adapter import table_facts,evidence

OUT=Path(__file__).resolve().parent
def sha(raw):return hashlib.sha256(raw).hexdigest()
def bound(item,retain=False):
    h=hashlib.sha256()
    with Path(item['path']).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    if h.hexdigest()!=item['sha256']:raise ValueError('frozen_amount_binding_changed:'+Path(item['path']).name)
    return Path(item['path']).read_bytes() if retain else None


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--protocol',type=Path,default=OUT/'protocol.json')
    parser.add_argument('--protocol-sha');parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    raw=args.protocol.read_bytes();p=json.loads(raw)
    if not args.execute:
        print(json.dumps({'status':'DEFERRED_PRINTED_SOURCE_FACTS_NO_NATIVE_BODY_READ','protocol_sha256':sha(raw)}));return
    if not args.protocol_sha or sha(raw)!=args.protocol_sha or sha(Path(__file__).read_bytes())!=p['runner_sha256']:
        raise ValueError('root_exact_amount_protocol_runtime_required')
    if p['status']!='FROZEN_FIXED6_NOTE_AMOUNT_SOURCE_FACTS_AWAIT_ROOT_EXECUTION' or datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']):
        raise ValueError('amount_source_freeze_or_deadline_pending')
    output=Path(p['output_path'])
    if output.exists():raise ValueError('owned_amount_receipt_exists_no_replay')
    with sqlite3.connect(Path(p['registry_path']).resolve().as_uri()+'?mode=ro',uri=True) as registry:
        row=registry.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?",(p['event_prefix']+args.protocol_sha,p['registration_subject'])).fetchone()
        if not row or json.loads(row[0]).get('new_contexts')!=0:raise ValueError('root_amount_source_registration_required')
    for item in p['bindings'].values():bound(item)
    plan=json.loads(bound(p['bindings']['source_plan'],True));geometry=json.loads(bound(p['bindings']['geometry_receipt'],True))
    cases=[case for case in plan['selected_cases'] if case['code'] in p['selected_codes']]
    if [case['code'] for case in cases]!=p['selected_codes']:raise ValueError('fixed_three_source_identity_pending')
    oldnotes={record['code']:{note['asset']:note for note in record['notes']} for record in geometry['records']}
    idx=plan['bindings']['source_indices'];indexes={item['path']:json.loads(bound(item,True)) for item in idx.values()}
    spec=importlib.util.spec_from_file_location('bound_amount_source_reader',p['bindings']['source_reader']['path'])
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    records=[]
    with sqlite3.connect(Path(p['bindings']['capture_db']['path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        for original in cases:
            case=dict(original);case['source_index_ref']=idx[case['source_index_binding_key']]
            contract=p['annotations'][case['code']]
            if case['announcement_id']!=contract['announcement_id']:raise ValueError('fixed_original_report_version_pending')
            document,binding,text=reader.read_case(case,db,indexes)
            # Calendar convention and cover are source facts, not invented
            # literal dates in a note that prints only closing/opening labels.
            for ref in contract['fiscal_source_spans']:evidence(text,ref)
            tables=[]
            for table in contract['tables']:
                note=oldnotes[case['code']][table['asset']]
                if note['geometry_status']!='exact_diagnosed_parent_note_range_bound':raise ValueError('known_note_geometry_pending')
                a,b=note['note_range']['start'],note['note_range']['end']
                if not a<=table['header_span']['start']<table['header_span']['end']<=b or not a<=table['row_span']['start']<table['row_span']['end']<=b:
                    raise ValueError('annotated_table_outside_fixed_six_notes')
                try:tables.append(table_facts(text,table))
                except ValueError as exc:tables.append({'table_id':table['table_id'],'asset':table['asset'],'kind':table['kind'],'status':'source_amount_role_pending','reason':str(exc),'financial_eligibility':False})
            records.append({'code':case['code'],'announcement_id':case['announcement_id'],'report_date':case['report_date'],
                            'source_record_sha256':case['source_record_sha256'],'retained_text_sha256':case['retained_text_sha256'],
                            'tables':tables,'known_revision_or_later_report_metadata':case.get('known_revision_or_later_report_metadata'),
                            'latest_annual_version_metadata':case.get('latest_annual_version_metadata'),
                            'scope_policy_restatement_version_status':case.get('scope_policy_restatement_version_status'),
                            'policy_scope_version_or_cash_comparability_cleared':False})
            del document,binding,text
    result={'status':'COMPLETED_FIXED6_NOTE_CONFIRMED_AND_PARTIAL_PRINTED_FACTS','protocol_sha256':args.protocol_sha,'runner_sha256':p['runner_sha256'],
            'records':records,'new_contexts':0,'new_factor_or_mask_defined':False,'actual_cash_release_certified':False,
            'movement_unknown_did_not_drop_independent_stock':True,'AR_writeoff_mechanically_adjusted_net_change':False,
            'INV_transfer_out_blanket_bad':False,'old_receipts_changed':False,'official_calls':0,'new_full_report_copies':0}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'records':len(records),'output_sha256':sha(output.read_bytes())}))


if __name__=='__main__':main()
