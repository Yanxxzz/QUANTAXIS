"""One bounded Q1 metadata unit on the already frozen company order."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--protocol-sha', required=True)
    args = parser.parse_args()
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError('protocol changed')
    p = json.loads(args.protocol.read_text(encoding='utf-8'))
    deadline = datetime.fromisoformat(p['deadline_utc'])
    def remaining():
        return (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining() <= 0:
        raise ValueError('human deadline reached')
    out = Path(p['output'])
    if out.exists():
        raise ValueError('receipt already exists; no repeat requests')
    for ref in p['bindings'].values():
        if sha(ref['path']) != ref['sha256']:
            raise ValueError('binding changed')
    from panda_alpha.registry import ResearchRegistry
    registry = ResearchRegistry(p['registry'])
    ev = [e for e in registry.events() if e['event_id'] == 'q1-source20-freeze:' + args.protocol_sha]
    registry.db.close()
    if len(ev) != 1 or ev[0]['payload'].get('new_contexts') != 0:
        raise ValueError('source-only registration missing')
    codes = p['codes']
    evidence = {code: [] for code in codes}
    mapping = json.loads(Path(p['bindings']['org_map']['path']).read_text(encoding='utf-8'))
    for row in mapping['mapping']:
        if row['code'] in evidence:
            evidence[row['code']].extend(x['url'] for x in row.get('evidence', []) if 'url' in x)
    # Existing own-account local index, projected metadata only; no price or amount reads.
    from pymongo import MongoClient
    cfg = json.loads(Path(p['config_path']).read_text(encoding='utf-8'))
    if cfg['data']['database'] != 'quantaxis':
        raise ValueError('configured index database changed')
    with MongoClient(cfg['data']['mongo_uri'], serverSelectionTimeoutMS=5000) as client:
        for code in codes:
            if remaining() <= 0:
                raise ValueError('deadline before index read')
            rows = client['quantaxis'].stock_filing_index.find(
                {'code': code}, {'_id': 0, 'detail_url': 1}).sort('announcement_id', -1).limit(20)
            evidence[code].extend(r['detail_url'] for r in rows if r.get('detail_url'))
    orgs = {}
    for code, urls in evidence.items():
        values = set()
        valid = []
        for url in urls:
            parsed = urlsplit(url); query = parse_qs(parsed.query)
            ids = query.get('announcementId', [])
            if (parsed.scheme == 'https' and parsed.netloc == 'www.cninfo.com.cn'
                and parsed.path == '/new/disclosure/detail' and query.get('stockCode') == [code]
                and len(ids) == 1 and re.fullmatch(r'\d+', ids[0])
                and len(query.get('orgId', [])) == 1):
                values.add(query['orgId'][0]); valid.append(url)
        orgs[code] = {'org_id': next(iter(values)) if len(values) == 1 else None,
                      'candidates': sorted(values), 'URL_evidence': sorted(set(valid))[:2]}
    sys.path.insert(0, p['external_workspace'])
    source = importlib.import_module('factor_workflow.filings_market_index')
    if Path(source.__file__).resolve() != Path(p['bindings']['index']['path']).resolve():
        raise ValueError('index runtime changed')
    result = {'status': 'METADATA_RUNNING', 'protocol_sha256': args.protocol_sha,
              'org_bindings': orgs, 'companies': [], 'metadata': [], 'attempts': [],
              'new_contexts': 0, 'PDF_calls': 0, 'official_calls': 0,
              'prices_factor_values_or_labels_read': False, 'full_channel_certified': False}
    def checkpoint():
        result['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    import requests
    with requests.Session() as session:
        for code in codes:
            info = {'symbol': code, 'status': 'PENDING', 'records': 0}
            result['companies'].append(info)
            if remaining() <= 0:
                info['reason'] = 'human deadline reached'; break
            if not orgs[code]['org_id']:
                info['reason'] = 'unique explicit orgId unavailable'; checkpoint(); continue
            request = source._payload(source._day('20260401'), source._day('20260430'), '一季报', 1)
            request['stock'] = code + ',' + orgs[code]['org_id']
            attempt = {'symbol': code, 'request_sha256': digest(request)}
            result['attempts'].append(attempt); checkpoint()
            try:
                response = session.post(source.QUERY_URL, data=request,
                                        timeout=min(15, remaining()), allow_redirects=False)
                attempt.update(http_status=response.status_code,
                               response_sha256=hashlib.sha256(response.content).hexdigest())
                if 300 <= response.status_code < 400:
                    raise ValueError('redirect not followed')
                response.raise_for_status(); packet = response.json()
                total, rows = packet.get('totalAnnouncement'), packet.get('announcements') or []
                if type(total) is not int or not isinstance(rows, list) or total != len(rows) or len(rows) > 30:
                    raise ValueError('single page incomplete or invalid; no automatic expansion')
                records = [asdict(source._record(r, source._day('20260401'), source._day('20260430'), '一季报')) for r in rows]
                if any(r['symbol'] != code for r in records) or len({r['announcement_id'] for r in records}) != len(records):
                    raise ValueError('identity mismatch or duplicate')
                result['metadata'].extend(records)
                info.update(status='BOUNDED_METADATA_COMPLETE', records=len(records))
            except Exception as error:
                info.update(reason=str(error)[:384], error_type=type(error).__name__)
            checkpoint()
    order = {c: i for i, c in enumerate(codes)}
    result['metadata'].sort(key=lambda r: (order[r['symbol']], r['published_at'], r['announcement_id']))
    primary = [r for r in result['metadata'] if re.search(r'2026\s*年.*第一季度报告', re.sub(r'<[^>]+>', '', r['title']))
               and not re.search(r'摘要|英文|更正|修订|取消', r['title'])]
    first = {}
    for row in primary:
        first.setdefault(row['symbol'], row)
    result['planned_body_first3'] = list(first.values())[:3]
    result['selection'] = 'first3 available original-title reports by original company order, date and ID; version chains pending'
    result['status'] = 'BOUNDED_Q1_METADATA_COMPLETE' if len(result['companies']) == 20 and all(x['status'] == 'BOUNDED_METADATA_COMPLETE' for x in result['companies']) else 'BOUNDED_Q1_METADATA_SOURCE_PENDING'
    checkpoint()
    print(json.dumps({'status': result['status'], 'POST_calls': len(result['attempts']),
                      'metadata': len(result['metadata']), 'first3': result['planned_body_first3'],
                      'receipt_sha256': sha(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
