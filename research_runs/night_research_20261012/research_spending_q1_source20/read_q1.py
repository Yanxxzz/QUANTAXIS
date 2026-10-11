"""Read three frozen quarter originals once; full native text stays in memory."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from queue import Queue, Empty
import re
import sys
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def primary(row):
    title = re.sub(r'<[^>]+>', '', row['title'])
    return bool(re.search(r'2026\s*年.*(?:第一季度|一季度)报告', title)
                and not re.search(r'摘要|英文|更正|修订|取消', title))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--protocol-sha', required=True)
    args = parser.parse_args()
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError('body protocol changed')
    p = json.loads(args.protocol.read_text(encoding='utf-8'))
    deadline = datetime.fromisoformat(p['deadline_utc'])
    remaining = lambda: (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining() <= 0:
        raise ValueError('human deadline reached')
    out = Path(p['output'])
    if out.exists():
        raise ValueError('existing body receipt; no repeat source request')
    for ref in p['bindings'].values():
        if sha(ref['path']) != ref['sha256']:
            raise ValueError('source/runtime binding changed')
    metadata = json.loads(Path(p['bindings']['metadata']['path']).read_text(encoding='utf-8'))
    first = {}
    for row in metadata['metadata']:
        if primary(row):
            first.setdefault(row['symbol'], row)
    if list(first.values())[:3] != p['selected_cases']:
        raise ValueError('frozen primary original selection changed')
    from panda_alpha.registry import ResearchRegistry
    r = ResearchRegistry(p['registry'])
    ev = [e for e in r.events() if e['event_id'] == 'q1-source3-body-freeze:' + args.protocol_sha]
    r.db.close()
    if len(ev) != 1 or ev[0]['payload'].get('new_contexts') != 0:
        raise ValueError('source-only freeze missing')
    spec = importlib.util.spec_from_file_location('q1_bound_native_reader', p['bindings']['reader']['path'])
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    reader.LOCATOR = re.compile(r'合并利润表|营业收入|研发费用|2026年1|2025年1|会计政策|追溯调整|重述|合并范围|审计|单位')
    result = {'status': 'Q1_ORIGINAL_READS_RUNNING', 'protocol_sha256': args.protocol_sha,
              'records': [], 'attempts': [], 'failed_source': None, 'new_contexts': 0,
              'official_calls': 0, 'prices_factor_values_or_labels_read': False,
              'PDF_or_fulltext_file_saved': False, 'manual_field_review': 'PENDING',
              'native_financial_period_or_production_certified': False}
    def checkpoint():
        result['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    documents = []
    import requests
    with requests.Session() as session:
        for case in p['selected_cases']:
            if remaining() <= 0:
                result['failed_source'] = {'symbol': case['symbol'], 'reason': 'deadline before source request'}; break
            result['attempts'].append({'symbol': case['symbol'], 'announcement_id': case['announcement_id']})
            checkpoint()
            try:
                receipt, document = reader.read_once(case, session, seconds_remaining=remaining)
                receipt['unknown_fields'] = ['current_quarter_income_roles', 'currency_unit_precision',
                                             'accounting_restatements_scope', 'latest_version_chain', 'complete_PIT']
                receipt['status'] = 'FULL_NATIVE_Q1_ORIGINAL_READ_FIELD_REVIEW_PENDING'
                result['records'].append(receipt); documents.append(document)
                checkpoint()
            except Exception as error:
                result['failed_source'] = {'symbol': case['symbol'], 'error_type': type(error).__name__, 'reason': str(error)[:384]}; break
    result['status'] = 'THREE_Q1_NATIVE_ORIGINAL_READS_COMPLETE_FIELDS_PENDING' if len(documents) == 3 else 'Q1_SOURCE_PENDING_COMPLETED_RECORDS_RETAINED'
    checkpoint()
    print(json.dumps({'status': result['status'], 'records': len(documents), 'receipt_sha256': sha(out)}), flush=True)
    commands = Queue()
    def read_commands():
        for line in sys.stdin:
            commands.put(line.strip())
        commands.put('QUIT')
    Thread(target=read_commands, daemon=True).start()
    for i, document in enumerate(documents):
        print(json.dumps({'index': i, 'symbol': document['metadata']['symbol'],
                          'announcement_id': document['metadata']['announcement_id'],
                          'pages': len(document['page_texts']), 'PDF_sha256': document['PDF_sha256'],
                          'text_sha256': document['text_sha256']}, ensure_ascii=False), flush=True)
        print(document['full_text'], flush=True)
        if i + 1 == len(documents):
            break
        print('NEXT_NATIVE_DOCUMENT: send NEXT or QUIT; no more HTTP', flush=True)
        try:
            command = commands.get(timeout=min(55, max(0, remaining())))
        except Empty:
            break
        if command != 'NEXT':
            break
    print('READER_SESSION_CLOSED_NO_FULLTEXT_OR_PDF_COPIES', flush=True)


if __name__ == '__main__':
    main()
