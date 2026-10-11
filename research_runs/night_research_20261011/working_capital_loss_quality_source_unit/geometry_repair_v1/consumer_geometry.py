"""Fixed three-source note geometry repair; headers only, no amount solver."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3

OUT=Path(__file__).resolve().parent


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def bound(item,retain=False):
    h=hashlib.sha256()
    with Path(item['path']).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    if h.hexdigest()!=item['sha256']:raise ValueError('frozen_geometry_binding_changed:'+Path(item['path']).name)
    return Path(item['path']).read_bytes() if retain else None


def page(text,offset):
    found=re.findall(r'===SOURCE_PAGE:(\d+)===',text[:offset])
    return int(found[-1]) if found else None


def span(text,start,end):
    return {'start':start,'end':end,'source_page':page(text,start),'span_sha256':sha(text[start:end].encode('utf-8'))}


def checked_span(text,item):
    start,end=item['start'],item['end']
    if not (0<=start<end<=len(text)) or sha(text[start:end].encode('utf-8'))!=item['sha256']:
        raise ValueError('exact_diagnosed_chapter_span_pending')
    if 'source_page' in item and page(text,start)!=item['source_page']:
        raise ValueError('exact_diagnosed_chapter_page_pending')
    return text[start:end]


TOP=re.compile(r'^[ \t]*(\d+)([、.．])[ \t]*([^\d\s][^\n]*)[ \t]*$')
TOP_LINES=re.compile(r'(?m)^[ \t]*\d+[、.．][ \t]*[^\d\s][^\n]*$')
CHILD=re.compile(r'(?m)^[ \t]*[（(][ \t]*(\d+)[ \t]*[）)][^\n]*$')
WORDS=['期末余额','期初余额','账面余额','账面价值','坏账准备','跌价准备','金额','比例','计提','收回或转回','转销或核销','转回或转销','本期变动','本期增加','本期减少','其他','单位','币种']


def line_at(text,start):
    end=text.find('\n',start)
    return text[start:end if end!=-1 else len(text)],end if end!=-1 else len(text)


def geometry(text,contract):
    parent=contract['chapter_start'];following=contract['chapter_end'];scope=contract['chapter_range']
    p=checked_span(text,parent);q=checked_span(text,following);checked_span(text,scope)
    if (scope['start']!=parent['start'] or scope['end']!=following['start']
            or re.sub(r'\s+','',p)!=contract['chapter_start_caption_dense']
            or re.sub(r'\s+','',q)!=contract['chapter_end_caption_dense']):
        raise ValueError('diagnosed_named_parent_chapter_identity_pending')
    records=[]
    for note in contract['notes']:
        start,end=note['start'],note['next_parent_start']
        if not parent['end']<=start<end<following['start']:raise ValueError('fixed_note_outside_diagnosed_parent_chapter')
        current,caption_end=line_at(text,start);nxt,next_end=line_at(text,end)
        a,b=TOP.fullmatch(current),TOP.fullmatch(nxt)
        if (not a or not b or int(a[1])!=note['ordinal'] or int(b[1])!=note['next_ordinal']
                or a[2]!=b[2] or re.sub(r'\s+','',a[3])!=note['title']):
            raise ValueError('fixed_parent_and_next_same_level_caption_pending')
        # Bracket children are kept. Any unexpected bare numbered caption
        # remains a conservative boundary, including unknown titles/templates.
        unknown=TOP_LINES.search(text,caption_end,end)
        stop=unknown.start() if unknown else end
        children=[{**span(text,m.start(),m.end()),'brief_caption':m.group().strip()[:200]}
                  for m in CHILD.finditer(text,caption_end,stop)]
        clues=[]
        for row in re.compile(r'(?m)^.*$').finditer(text,caption_end,stop):
            literal=row.group();words=[word for word in WORDS if word in literal]
            exact_single=literal.strip() in WORDS or bool(re.fullmatch(r'\s*比例[（(]?[%％][）)]?\s*',literal))
            if len(words)<2 and not exact_single and not re.search(r'单位|币种|20\d{2}\s*年\s*\d{1,2}\s*月',literal):continue
            # Do not emit body-row monetary values. Percentage header wording
            # and multirow column labels are retained verbatim, never flattened.
            if re.search(r'\d[\d,，]*\.\d{2,}',literal):continue
            clues.append({**span(text,row.start(),row.end()),'brief_native_header':literal.strip()[:200],
                          'literal_keywords':words,'roles_interpreted':False})
            if len(clues)==24:break
        records.append({'asset':note['asset'],'parent_ordinal':note['ordinal'],'next_parent_ordinal':note['next_ordinal'],
                        'parent_caption':{**span(text,start,caption_end),'brief_caption':current.strip()[:200]},
                        'next_same_level_caption':{**span(text,end,next_end),'brief_caption':nxt.strip()[:200]},
                        'note_range':span(text,start,stop),'known_bracket_child_captions':children,'native_header_clues':clues,
                        'geometry_status':'unknown_same_level_boundary_pending' if unknown else 'exact_diagnosed_parent_note_range_bound',
                        'unknown_boundary':({**span(text,unknown.start(),unknown.end()),'brief_caption':unknown.group().strip()[:200]} if unknown else None),
                        'amount_roles_units_period_population_status':'unparsed_native_multilevel_layout_pending',
                        'combined_movements_split':False,'percentage_columns_removed':False,'amounts_solved':0,'financial_eligibility':False})
    return {'chapter_start':span(text,parent['start'],parent['end']),'chapter_end':span(text,following['start'],following['end']),
            'chapter_range':span(text,scope['start'],scope['end']),'notes':records,
            'chapter_reference_or_subline_used_as_end':False,'no_amount_or_financial_mask_created':True}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--protocol',type=Path,default=OUT/'protocol.json')
    parser.add_argument('--protocol-sha');parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    raw=args.protocol.read_bytes();p=json.loads(raw)
    if not args.execute:
        print(json.dumps({'status':'DEFERRED_GEOMETRY_ONLY_NO_NATIVE_BODY_READ','protocol_sha256':sha(raw)}));return
    if not args.protocol_sha or sha(raw)!=args.protocol_sha or sha(Path(__file__).read_bytes())!=p['runtime_sha256']:
        raise ValueError('exact_root_geometry_protocol_runtime_required')
    if p['status']!='FROZEN_FIXED3_GEOMETRY_ONLY_AWAIT_ROOT_EXECUTION' or datetime.now(timezone.utc)>=datetime.fromisoformat(p['deadline_utc']):
        raise ValueError('geometry_freeze_or_deadline_pending')
    output=Path(p['output_path'])
    if output.exists():raise ValueError('owned_geometry_receipt_exists_no_replay')
    with sqlite3.connect(Path(p['registry_path']).resolve().as_uri()+'?mode=ro',uri=True) as registry:
        event=registry.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?",(p['event_prefix']+args.protocol_sha,p['registration_subject'])).fetchone()
        if not event or json.loads(event[0]).get('new_contexts')!=0:raise ValueError('root_geometry_source_only_registration_required')
    for item in p['bindings'].values():bound(item)
    plan=json.loads(bound(p['bindings']['source_plan'],retain=True))
    cases=[case for case in plan['selected_cases'] if case['code'] in p['selected_codes']]
    if [case['code'] for case in cases]!=p['selected_codes']:raise ValueError('exact_fixed_three_source_selection_pending')
    idx=plan['bindings']['source_indices'];indexes={item['path']:json.loads(bound(item,retain=True)) for item in idx.values()}
    spec=importlib.util.spec_from_file_location('bound_geometry_source_reader',p['bindings']['source_reader']['path'])
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    records=[]
    with sqlite3.connect(Path(p['bindings']['capture_db']['path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        for original in cases:
            case=dict(original);case['source_index_ref']=idx[case['source_index_binding_key']]
            if case['announcement_id']!=p['geometry_contracts'][case['code']]['announcement_id']:raise ValueError('exact_diagnosed_report_version_pending')
            try:
                document,binding,text=reader.read_case(case,db,indexes)
                record=geometry(text,p['geometry_contracts'][case['code']]);del document,binding,text
                record.update(code=case['code'],source_record_sha256=case['source_record_sha256'],retained_text_sha256=case['retained_text_sha256'],
                              known_revision_or_later_report_metadata=case.get('known_revision_or_later_report_metadata'),
                              scope_policy_restatement_version_status=case.get('scope_policy_restatement_version_status'),
                              policy_scope_versions_cleared=False)
            except ValueError as exc:record={'code':case['code'],'status':'source_geometry_pending','reason':str(exc),'financial_eligibility':False}
            records.append(record)
    result={'status':'COMPLETED_FIXED3_GEOMETRY_ONLY_SOURCE_REPAIR','protocol_sha256':args.protocol_sha,'runtime_sha256':p['runtime_sha256'],
            'records':records,'original_receipt_replaced':False,'amounts_solved':0,'new_contexts':0,'official_calls':0,'new_full_text_copies':0,
            'new_factor_price_label_mask_or_shared_state_written':False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'records':len(records),'output_sha256':sha(output.read_bytes())}))


if __name__=='__main__':main()
