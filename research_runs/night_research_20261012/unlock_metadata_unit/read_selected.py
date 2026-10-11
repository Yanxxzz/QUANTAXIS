"""Read the frozen six new notices once; retain facts, not PDF/text copies."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from queue import Queue, Empty
import sqlite3
import sys
from threading import Thread


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--protocol-sha', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print('DEFERRED_NO_SOURCE_READ'); return
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError('frozen protocol changed')
    p = json.loads(args.protocol.read_text(encoding='utf-8'))
    deadline = datetime.fromisoformat(p['deadline_utc'])
    remaining = lambda: (deadline-datetime.now(timezone.utc)).total_seconds()
    if remaining() <= 0:
        raise ValueError('human deadline reached')
    output = Path(p['output'])
    if output.exists():
        raise ValueError('existing receipt; no source replay')
    for ref in p['bindings'].values():
        if sha(ref['path']) != ref['sha256']:
            raise ValueError('source/runtime binding changed')
    metadata = json.loads(Path(p['bindings']['metadata']['path']).read_text(encoding='utf-8'))
    cases = p['selected_cases']
    if cases != metadata['next_body_first6'] or len(cases) != 6:
        raise ValueError('frozen six-case source selection changed')
    with sqlite3.connect(Path(p['registry_path']).resolve().as_uri()+'?mode=ro', uri=True) as db:
        r = db.execute('SELECT payload FROM research_events WHERE event_id=? AND subject=?',
                       ('unlock-new-six-body-freeze:'+args.protocol_sha,p['id'])).fetchone()
        if not r or json.loads(r[0]).get('new_contexts') != 0:
            raise ValueError('registered source-only freeze required')
    spec=importlib.util.spec_from_file_location('frozen_prior_pdf_reader',p['bindings']['PDF_reader']['path'])
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    # Reuse read_once only, never its old exact-three execute/consume paths.
    import requests
    records, documents, failed = [], [], None
    with requests.Session() as session:
        for case in cases:
            try:
                record, document=reader.read_once(case,session,seconds_remaining=remaining)
            except Exception as error:
                failed={'symbol':case['symbol'],'announcement_id':case['announcement_id'],
                        'error_type':type(error).__name__,'error':str(error)[:512]};break
            records.append(record);documents.append(document)
    result={'status':'SIX_FULL_NATIVE_NOTICE_READS_FIELDS_PENDING' if len(records)==6 else 'SOURCE_PENDING_COMPLETED_READS_RETAINED',
            'protocol_sha256':args.protocol_sha,'records':records,'failed_source':failed,
            'HTTP_attempts':len(records)+(failed is not None),'unattempted_sources':len(cases)-len(records)-(failed is not None),
            'PDF_or_fulltext_file_saved':False,'new_contexts':0,'official_calls':0,
            'prices_values_or_labels_read':False,'manual_field_review':'PENDING',
            'completed_at_utc':datetime.now(timezone.utc).isoformat()}
    with output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({'status':result['status'],'records':len(records),'receipt_sha256':sha(output)}),flush=True)
    commands=Queue()
    def read_commands():
        for line in sys.stdin:
            commands.put(line.strip())
        commands.put('QUIT')
    Thread(target=read_commands,daemon=True).start()
    for index, document in enumerate(documents):
        if remaining() <= 0:break
        print(json.dumps({'native_document_index':index,'symbol':document['metadata']['symbol'],
                          'announcement_id':document['metadata']['announcement_id'],
                          'PDF_sha256':document['PDF_sha256'],'text_sha256':document['text_sha256']},ensure_ascii=False),flush=True)
        print(document['full_text'],flush=True)
        if index==len(documents)-1:break
        print('NEXT_DOCUMENT_PENDING: send NEXT or QUIT; no further source requests',flush=True)
        try: command=commands.get(timeout=max(0,remaining()))
        except Empty:break
        if command!='NEXT':break
    print('READER_SESSION_CLOSED_NO_SOURCE_DOCUMENT_COPIES',flush=True)


if __name__=='__main__':main()
