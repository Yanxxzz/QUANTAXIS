"""One already-located AR ECL item: structure and method evidence only."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys

OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT.parent))
from review_events import bound,canonical,location,sha

KEY_FIELDS=['code','announcement_id','report_date','source_record_sha256']
LETTER=re.compile(r'(?m)^[ \t]*(?P<left>[（(])(?P<letter>[a-z])(?P<right>[）)])[ \t]*(?P<title>[^\n]+?)[ \t]*$')
NUMBER=re.compile(r'^[ \t]*[（(](?P<number>\d+)[）)][ \t]*(?P<title>[^\n]+?)[ \t]*$')
UNKNOWN_NUMBER=re.compile(r'(?m)^[ \t]*(?:[（(]\d+[）)]|\d+[、.．]|[一二三四五六七八九十]+[、.．])[^\n]*$')
UNKNOWN_ALPHA=re.compile(r'(?m)^[ \t]*[（(][A-Za-z]+[）)][ \t]*[^\n]+$')
PHRASES={
    'group_AR_subject':r'本集团[^\n。]{0,100}应收账款',
    'individual_assessment':r'单项(?:计提|评估|评价|测试)|单独(?:计提|评估|评价|测试)',
    'portfolio_assessment':r'组合|共同信用风险|信用风险特征',
    'lifetime_ECL':r'整个存续期[^\n。]{0,20}预期信用损失',
    'age_and_loss_rate':r'账龄|预期信用损失率',
    'historical_loss_experience':r'过去年度的实际信用损失经验|历史信用损失经验',
    'forward_credit_judgment':r'前瞻性|未来经济状况|当前及未来|经济状况',
    'explicit_change_or_restatement_wording':r'会计政策变更|会计估计变更|计提方法变更|计量方法变更|追溯调整|重述|更正'}


def verify_span(text,span):
    a,b=span['start'],span['end']
    if a<0 or b<=a or sha(text[a:b].encode('utf-8'))!=span['span_sha256']:
        raise ValueError('existing_named_AR_source_span_changed')
    return a,b


def locate_item(text,contract):
    a,b=verify_span(text,contract['AR_note'])
    parent_a,parent_b=verify_span(text,contract['enclosing_parent'])
    next_a,next_b=verify_span(text,contract['next_enclosing_sibling'])
    named_a,named_b=verify_span(text,contract['named_item'])
    if not a<=parent_a<parent_b<=named_a<named_b<next_a<next_b<=b:
        raise ValueError('existing_named_AR_span_order_changed')
    parent=NUMBER.fullmatch(text[parent_a:parent_b]);successor=NUMBER.fullmatch(text[next_a:next_b])
    if not parent or not successor or (parent['number'],successor['number'])!=('3','4'):
        return {'status':'NAMED_AR_ITEM_BOUNDARY_PENDING','reason':'exact_enclosing_numbered_sibling_not_supported'}
    headings=list(LETTER.finditer(text,parent_b,next_a))
    named=[h for h in headings if h.start()==named_a and h.end()==named_b and h['letter']=='b']
    if len(named)!=1 or re.sub(r'\s+','',named[0]['title'])!='应收账款预期信用损失的评估：':
        return {'status':'NAMED_AR_ITEM_BOUNDARY_PENDING','reason':'exact_named_b_heading_not_unique'}
    labels=[h['letter'] for h in headings]
    if labels!=list('abcdefghijklmnopqrstuvwxyz'[:len(labels)]) or labels[:2]!=['a','b']:
        return {'status':'NAMED_AR_ITEM_BOUNDARY_PENDING','reason':'letter_sibling_order_or_ancestor_unknown'}
    if any((h['left'],h['right'])!=(named[0]['left'],named[0]['right']) for h in headings):
        return {'status':'NAMED_AR_ITEM_BOUNDARY_PENDING','reason':'letter_sibling_delimiter_level_unknown'}
    end=headings[2].start() if len(headings)>2 else next_a
    if UNKNOWN_NUMBER.search(text,named_b,end):
        return {'status':'NAMED_AR_ITEM_BOUNDARY_PENDING','reason':'unclassified_numbered_heading_inside_item'}
    if UNKNOWN_ALPHA.search(text,named_b,end):
        return {'status':'NAMED_AR_ITEM_BOUNDARY_PENDING','reason':'unclassified_parenthesized_alphabet_heading_inside_item'}
    markers=list(re.finditer(r'===SOURCE_PAGE:(\d+)===' ,text))
    prior=[m for m in markers if m.start()<=named_a]
    pages=([int(prior[-1][1])] if prior else [])+[int(m[1]) for m in markers if named_a<m.start()<end]
    if not pages or pages!=list(range(pages[0],pages[-1]+1)):
        return {'status':'NAMED_AR_ITEM_BOUNDARY_PENDING','reason':'native_cross_page_relation_unknown'}
    evidence=[]
    for kind,expression in PHRASES.items():
        for hit in list(re.finditer(expression,text[named_b:end]))[:4]:
            x,y=named_b+hit.start(),named_b+hit.end()
            evidence.append({'kind':kind,**location(text,x,y),'brief_source_phrase':text[x:y][:100],
                'fragment_only_not_complete_effect_or_period_proof':True})
    return {'status':'NAMED_AR_ITEM_COMPLETE_BOUNDARY_SHORT_METHOD_INVENTORY_ONLY',
        'item':location(text,named_a,end),'named_header':contract['named_item'],
        'ending_kind':'next_contiguous_letter_sibling' if len(headings)>2 else 'known_enclosing_numbered_sibling_after_last_letter_child',
        'ending_caption':location(text,headings[2].start(),headings[2].end()) if len(headings)>2 else contract['next_enclosing_sibling'],
        'retained_contiguous_pages':pages,'short_method_evidence':evidence,
        'whole_group_AR_object_and_single_portfolio_scope':'candidate_phrases_only_complete_semantics_pending',
        'current_opening_method_applicability':'unreviewed_pending',
        'normal_forward_credit_judgment_is_automatic_regime_block':False,
        'missing_change_words_or_ECL_negative_clears_measurement':False,'AR_measurement_or_financial_eligibility_cleared':False,
        'stock_or_movement_amounts_recomputed':False,'Q_computed':False}


def consume_one(case,read_case,contract,deadline_reached):
    key={field:case[field] for field in KEY_FIELDS}
    try:
        if deadline_reached():raise TimeoutError('human_deadline_reached_before_named_item_read')
        document,binding,text=read_case(case)
        result=locate_item(text,contract)
        del document,binding,text
        return {'records':[{'source_identity':key,**result,
            'known_revision_or_later_report_metadata':case.get('known_revision_or_later_report_metadata'),
            'full_version_chain_certified':False}],'errors':[],'resume_position':None}
    except Exception as error:
        return {'records':[],'errors':[{'status':'SOURCE_NAMED_ITEM_EXECUTION_PENDING_NOT_ECONOMIC_FAILURE',
            'source_identity':key,'error_type':type(error).__name__,'error':str(error)[:512]}],
            'resume_position':{'failed_report_key':key,'completed_records':0,'automatic_resume':False}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--protocol',type=Path,default=OUT/'protocol.json')
    parser.add_argument('--protocol-sha');parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    raw=args.protocol.read_bytes();p=json.loads(raw)
    if not args.execute:
        print(json.dumps({'status':'DEFERRED_NAMED_AR_ECL_ITEM_NO_BODY_AMOUNTS_Q_OR_LABEL_READ','protocol_sha256':sha(raw)}));return
    if (not args.protocol_sha or sha(raw)!=args.protocol_sha or sha(Path(__file__).read_bytes())!=p['runtime_sha256']):
        raise ValueError('exact_root_named_AR_protocol_required')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']):raise ValueError('human_deadline_reached')
    output=Path(p['output_path'])
    if output.exists():raise ValueError('named_AR_item_receipt_exists_no_replay')
    with sqlite3.connect(Path(p['registry_path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        event=db.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?",(p['event_prefix']+args.protocol_sha,p['registration_subject'])).fetchone()
        if not event or json.loads(event[0]).get('new_contexts')!=0:raise ValueError('root_named_AR_zero_context_registration_required')
    for artifact in p['bindings'].values():bound(artifact)
    plan=json.loads(bound(p['bindings']['source_plan'],True))
    cases=[case for case in plan['selected_cases'] if canonical({field:case[field] for field in KEY_FIELDS})==canonical(p['source_identity'])]
    if len(cases)!=1 or (cases[0]['code'],cases[0]['announcement_id'])!=('000403','1225245931'):
        raise ValueError('fixed_named_AR_report_identity_required')
    prior=json.loads(bound(p['bindings']['first3_inventory'],True))
    if len([r for r in prior['records'] if r['source_identity']==p['source_identity']])!=1:
        raise ValueError('existing_first3_AR_inventory_identity_pending')
    idx=plan['bindings']['source_indices'];indexes={ref['path']:json.loads(bound(ref,True)) for ref in idx.values()}
    spec=importlib.util.spec_from_file_location('bound_named_AR_reader',p['bindings']['source_reader']['path'])
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    case=dict(cases[0]);case['source_index_ref']=idx[case['source_index_binding_key']]
    with sqlite3.connect(Path(p['bindings']['capture_db']['path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        consumed=consume_one(case,lambda c:reader.read_case(c,db,indexes),p['item_contract'],
            lambda:datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']))
    result={'status':'SOURCE_NAMED_AR_ITEM_PENDING_STOPPED' if consumed['errors'] else 'COMPLETED_NAMED_AR_ITEM_SOURCE_INVENTORY_ONLY',
        'protocol_sha256':args.protocol_sha,**consumed,'new_contexts':0,'new_factor_or_mask':False,
        'Q_price_or_labels_computed':False,'amount_consumer_replayed':False,'AR_eligibility_cleared':False,
        'official_calls':0,'new_native_or_PDF_copies':0}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'records':len(consumed['records']),'errors':len(consumed['errors']),'output_sha256':sha(output.read_bytes())}))


if __name__=='__main__':main()
