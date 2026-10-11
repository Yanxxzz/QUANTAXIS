"""AR source-event locators only; no inherited G-ready or AR certificate."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
sys.path.insert(0,str(ROOT))
from panda_alpha.income_basis import policy_change_sections,POLICY_HEADING


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def bound(item,retain=False):
    h=hashlib.sha256()
    with Path(item['path']).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    if h.hexdigest()!=item['sha256']:raise ValueError('frozen_AR_event_binding_changed:'+Path(item['path']).name)
    return Path(item['path']).read_bytes() if retain else None


AR=re.compile(r'应收账款|坏账准备|预期信用损失|信用减值|金融资产|金融工具|信用风险组合|组合划分')
COMPOSITION=re.compile(r'合并范围|收购|并购|处置子公司|新设子公司|客户结构|客户构成|客户组合')
CHANGE=re.compile(r'变更|改变|调整|修订|更正|追溯|重述|重新分类')
MEASUREMENT=re.compile(r'会计政策|会计估计|计量方法|计提方法|计提比例|预期信用损失|前瞻性|组合划分|风险组合')
PREFIX=r'(?:[（(]?[一二三四五六七八九十\d]+[）)]?[、.．]?[ \t]*)?'
SECTION=re.compile(r'(?m)^[ \t]*'+PREFIX+r'(?P<title>(?:重要)?会计估计变更|前期会计差错更正|是否需追溯调整或重述以前年度会计数据|合并范围的变更|合并范围变更|研发支出|在其他主体中的权益|重要会计政策和会计估计)[ \t]*[：:]?[ \t]*(?=[□☑√✓■]|$)')


def location(text,start,end):
    pages=re.findall(r'===SOURCE_PAGE:(\d+)===',text[:start])
    return {'start':start,'end':end,'source_page':int(pages[-1]) if pages else None,'span_sha256':sha(text[start:end].encode('utf-8'))}


def negative_choice(body):
    """Inventory only: same bounded first meaningful choice line, never ready."""
    for line in body.splitlines():
        line=re.sub(r'\s+','',line)
        if not line:continue
        yes=bool(re.search(r'[☑√✓■](?:适用|是)',line));no=bool(re.search(r'[☑√✓■](?:不适用|否)',line))
        return 'explicit_negative_choice_inventory_only' if no and not yes else 'positive_or_unchecked_or_ambiguous_pending'
    return 'choice_missing_pending'


def impact_inventory(body):
    ar=bool(AR.search(body));composition=bool(COMPOSITION.search(body));measurement=bool(MEASUREMENT.search(body))
    if ar and measurement and CHANGE.search(body):return 'AR_measurement_change_phrase_scope_pending'
    if composition:return 'state_composition_event_not_automatic_measurement_block'
    if ar:return 'AR_method_or_group_definition_inventory_unreviewed'
    return 'field_effect_unresolved_pending'


def inventory(text,metadata):
    events=[];covered=[]
    for section in policy_change_sections(text):
        start,end=section['start'],section['end'];raw=text[start:end]
        first=raw.splitlines()[0] if raw.splitlines() else ''
        heading=POLICY_HEADING.search(raw)
        choice=negative_choice(raw[heading.end():] if heading else raw[len(first):])
        events.append({'kind':'accounting_policy','evidence_scope':'existing_bounded_policy_section',**location(text,start,end),
                       'choice_inventory':choice,'impact_state':impact_inventory(raw),'AR_fields_resolved':[],
                       'period_binding':'current_report_context_only_item_periods_unreviewed','brief_heading':first.strip()[:160]})
        covered.append((start,end))
    headings=list(SECTION.finditer(text))
    for number,hit in enumerate(headings):
        if hit['title'] in {'研发支出','在其他主体中的权益','重要会计政策和会计估计'}:continue
        end=headings[number+1].start() if number+1<len(headings) else len(text)
        raw=text[hit.start():end]
        events.append({'kind':hit['title'],'evidence_scope':'recognized_heading_to_next_recognized_heading',**location(text,hit.start(),end),
                       'choice_inventory':negative_choice(text[hit.end():end]),'impact_state':impact_inventory(raw),
                       'AR_fields_resolved':[],'period_binding':'current_report_context_only_effect_period_unreviewed','brief_heading':hit['title']})
        covered.append((hit.start(),end))
    # A local paragraph locator helps the next field-specific review. It is
    # never promoted to a complete policy-item or measurement-effect proof.
    for paragraph in re.finditer(r'(?m)^[^\n]+$',text):
        raw=paragraph.group()
        if not (AR.search(raw) and MEASUREMENT.search(raw)) or any(a<=paragraph.start()<b for a,b in covered):continue
        events.append({'kind':'AR_ECL_or_group_definition_local_locator','evidence_scope':'native_line_only_complete_item_scope_pending',
                       **location(text,paragraph.start(),paragraph.end()),'impact_state':impact_inventory(raw),'AR_fields_resolved':[],
                       'period_binding':'unreviewed','brief_heading':raw.splitlines()[0].strip()[:160]})
        if len(events)>=30:break
    kinds={event['kind'] for event in events}
    gaps=['AR_item_specific_measurement_effect_not_certified','opening_comparative_basis_and_whole_AR_match_require_separate_source_facts']
    if 'accounting_policy' not in kinds:gaps.append('bounded_accounting_policy_section_not_located')
    if not any('估计' in kind for kind in kinds):gaps.append('bounded_accounting_estimate_change_section_not_located')
    known=metadata.get('known_revision_or_later_report_metadata')
    if known:gaps.append('known_revision_or_later_report_body_effect_scope_unreviewed')
    return {'status':'AR_SOURCE_EVENT_INVENTORY_ONLY_MEASUREMENT_REVIEW_PENDING','events':events,'specific_gaps':gaps,
            'known_revision_or_later_report_metadata':known,'latest_annual_version_metadata':metadata.get('latest_annual_version_metadata'),
            'source_flags_empty_is_AR_ready':False,'existing_G_trade_or_income_ready_copied':False,'AR_source_ready_certified':False,
            'cash_unknown_used_as_gate':False,'composition_events_automatically_blocked':False,'future_daily_availability_and_versions_still_required':True}


def consume_cases(cases,reader,db,indexes,source_indices):
    """Stop at one case error, retaining completed evidence and its fixed KEY."""
    fields=['code','announcement_id','report_date','source_record_sha256']
    records=[]
    for number,original in enumerate(cases):
        identity={key:original.get(key) for key in fields}
        try:
            case=dict(original);case['source_index_ref']=source_indices[case['source_index_binding_key']]
            document,binding,text=reader.read_case(case,db,indexes)
            result=inventory(text,case);result['source_identity']=identity;records.append(result)
            del document,binding,text
        except Exception as exc:
            return {'records':records,'errors':[{'status':'SOURCE_CASE_EXCEPTION_PENDING_NOT_ECONOMIC_FAILURE',
                    'source_identity':identity,'error_type':type(exc).__name__,'error':str(exc)[:512]}],
                    'resume_position':{'failed_case_index':number,'failed_report_key':identity,
                    'completed_record_count':len(records),'next_unattempted_case_index':number+1,
                    'unattempted_case_count':len(cases)-number-1,'automatic_resume':False}}
    return {'records':records,'errors':[],'resume_position':None}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--protocol',type=Path,default=OUT/'protocol_draft.json')
    parser.add_argument('--protocol-sha');parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    raw=args.protocol.read_bytes();p=json.loads(raw)
    if not args.execute:
        print(json.dumps({'status':'DEFERRED_AR_EVENT_LOCATORS_NO_BODY_Q_OR_LABEL_READ','protocol_sha256':sha(raw)}));return
    if (not args.protocol_sha or sha(raw)!=args.protocol_sha or sha(Path(__file__).read_bytes())!=p['runtime_sha256']
            or p['status']!='FROZEN_AR_SPECIFIC_EVENT_INVENTORY_AWAIT_ROOT_EXECUTION'):
        raise ValueError('root_final_AR_inventory_scope_protocol_required')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']):raise ValueError('human_research_deadline')
    output=Path(p['output_path'])
    if output.exists():raise ValueError('owned_AR_inventory_exists_no_replay')
    with sqlite3.connect(Path(p['registry_path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?",(p['event_prefix']+args.protocol_sha,p['registration_subject'])).fetchone()
        if not row or json.loads(row[0]).get('new_contexts')!=0:raise ValueError('root_source_only_AR_inventory_registration_required')
    for item in p['bindings'].values():bound(item)
    # Root supplies the fixed KEYs from the completed remaining17 receipt;
    # never discover cases from monetary values, prices or this consumer.
    scope=json.loads(bound(p['bindings']['selected_AR_scope_receipt'],True))
    if not p['selected_report_keys'] or scope.get('status')!=p['required_scope_receipt_status']:raise ValueError('final_whole_AR_amount_period_scope_pending')
    plan=json.loads(bound(p['bindings']['source_plan'],True));idx=plan['bindings']['source_indices']
    fields=['code','announcement_id','report_date','source_record_sha256']
    selected={canonical(key) for key in p['selected_report_keys']}
    cases=[case for case in plan['selected_cases'] if canonical({key:case[key] for key in fields}) in selected]
    if len(cases)!=len(selected):raise ValueError('fixed_AR_report_KEY_set_pending')
    indexes={ref['path']:json.loads(bound(ref,True)) for ref in idx.values()}
    spec=importlib.util.spec_from_file_location('bound_AR_event_reader',p['bindings']['source_reader']['path'])
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    with sqlite3.connect(Path(p['bindings']['capture_db']['path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        consumed=consume_cases(cases,reader,db,indexes,idx)
    result={'status':('PARTIAL_AR_SPECIFIC_SOURCE_EVENT_INVENTORY_SOURCE_PENDING_STOPPED' if consumed['errors'] else
            'COMPLETED_AR_SPECIFIC_SOURCE_EVENT_INVENTORY_PENDING_IMPACT_REVIEW'),'protocol_sha256':args.protocol_sha,**consumed,
            'new_contexts':0,'Q_values_computed':False,'new_factor_or_mask':False,'official_calls':0,'cash_or_AR_ready_certificates':0,'new_report_copies':0}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'records':len(consumed['records']),'errors':len(consumed['errors']),'output_sha256':sha(output.read_bytes())}))


if __name__=='__main__':main()
