"""Supplement AXIS with BSE identities and direct same-vendor HFQ price panels."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panda_alpha.beijing import MAPPING_URL, HISTORY_URL, official_current_list, history, lifecycles, official_aliases, parse_klines, official_exit_record
from scripts.axis_sync import upsert_many, qa_date_stamp
from panda_alpha.universe import lifecycle_window


def main():
    import requests
    from pymongo import MongoClient
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2019-09-20')
    parser.add_argument('--end', default='2026-09-18')
    parser.add_argument('--uri', default='mongodb://127.0.0.1:27018')
    parser.add_argument('--database', default='quantaxis')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--mapping-file', type=Path, help='Previously saved official mapping response; retained with its hash')
    parser.add_argument('--directory-cache', type=Path, help='Verified source page cache; default is this UTC day under source records')
    parser.add_argument('--metadata-only', action='store_true')
    parser.add_argument('--raw-history-file', nargs='*', type=Path, default=[],
                        help='Previously obtained raw EastMoney response bytes; only present rows are certified')
    parser.add_argument('--exit-manifest', type=Path, help='Previously saved official exit PDF paths/hashes and source URLs')
    parser.add_argument('--history-unavailable-reason', help='Known source connection failure; retain all history checkpoints without network retries')
    parser.add_argument('--history-endpoint', default=HISTORY_URL, help='Explicit public EastMoney history URL; HTTP denials never trigger host hopping')
    parser.add_argument('--report', type=Path, default=Path('research_runs/beijing_market_sync.json'))
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or args.start > args.end or args.limit is not None and args.limit < 1:
        parser.error('Invalid bounded worker count/window')
    from urllib.parse import urlparse
    host = urlparse(args.history_endpoint)
    import re
    if host.scheme != 'https' or not re.fullmatch(r'(?:[0-9]{1,2}\.)?push2his\.eastmoney\.com', host.netloc) or host.path != '/api/qt/stock/kline/get':
        parser.error('History endpoint must be an explicit public EastMoney history host')
    observed = datetime.now(timezone.utc).isoformat()
    client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client[args.database]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = args.report.parent / 'beijing_source_records'
    raw_dir.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bse.cn/'})
        if args.mapping_file:
            mapping_bytes = args.mapping_file.read_bytes()
        else:
            response = session.get(MAPPING_URL, timeout=30)
            response.raise_for_status()
            mapping_bytes = response.content
        aliases = official_aliases(mapping_bytes.decode('utf-8'))
        (raw_dir/'bse_official_mapping.html').write_bytes(mapping_bytes)
        directory_cache = args.directory_cache or raw_dir / 'official_directory' / observed[:10]
        current, directory = official_current_list(session, directory_cache,
            progress=lambda value: print(json.dumps(value), flush=True))
    lifecycle = lifecycles(current, aliases)
    if args.exit_manifest:
        from pypdf import PdfReader
        import io
        by_code = {row['code']: row for row in lifecycle}
        exit_sources = json.loads(args.exit_manifest.read_text(encoding='utf-8'))
        for source in exit_sources:
            url = urlparse(source['source_url'])
            published = re.search(r'/(\d{4}-\d{2}-\d{2})/', url.path)
            if (url.scheme != 'https' or url.netloc not in ('www.bse.cn', 'static.cninfo.com.cn')
                    or not published or published[1] != source['pub_date']):
                raise ValueError('Exit evidence must retain an official publication URL/date')
            original = Path(source['source_path']).read_bytes()
            if hashlib.sha256(original).hexdigest() != source['source_sha256'] or not original.startswith(b'%PDF'):
                raise ValueError('Official exit PDF hash/format mismatch')
            text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(original)).pages)
            old = by_code.get(source['code'], {})
            if old.get('current_directory_present'):
                raise ValueError('Exit evidence conflicts with the current official directory')
            record = official_exit_record(text, source['code'], source, ipo_date=old.get('ipo_date'))
            by_code[record['code']] = {**old, **record, 'source': old.get('source', record['source'])}
        lifecycle = sorted(by_code.values(), key=lambda row: row['code'])
    source_records = {'aliases': aliases, 'current': current, 'lifecycles': lifecycle, 'observed_at': observed,
                      'mapping_source_url': MAPPING_URL, 'mapping_sha256': hashlib.sha256(mapping_bytes).hexdigest(),
                      'mapping_input': 'saved_official_response' if args.mapping_file else 'live_official_response'}
    source_records['directory'] = directory
    encoded = json.dumps(source_records, ensure_ascii=False, sort_keys=True).encode('utf-8')
    (raw_dir/'metadata.json').write_bytes(encoded)
    source_hash = hashlib.sha256(encoded).hexdigest()
    upsert_many(db.stock_lifecycle, [{**r, 'observed_at': observed, 'metadata_sha256': source_hash} for r in lifecycle], ['code', 'source'])
    upsert_many(db.stock_list, lifecycle, ['code'])
    scope = [r for r in lifecycle if r['ipo_date'] <= args.end]
    if args.limit:
        scope = scope[:args.limit]
    report = {'source': 'BSE official identities + direct EastMoney history', 'start': args.start,
              'end': args.end, 'scope': 'beijing_a_lifecycle', 'total': len(scope),
              'metadata_sha256': source_hash, 'success': [], 'skipped': [], 'failures': [],
              'deferred': [], 'directory': directory, 'lifecycle_identities': len(lifecycle),
              'current_identities': len(current), 'alias_identities': len(aliases),
              'historical_identities_absent_current': [r['code'] for r in lifecycle if not r['current_directory_present']],
              'history_endpoint': args.history_endpoint, 'metadata_only': args.metadata_only,
              'pending': ['same_source_hfq_panel', 'historical_suspension_and_ST', 'independent_historical_universe_acceptance'],
              'historical_universe_complete': False,
              'historical_exit_dates': 'pending official delisting decisions', 'can_retire_legacy': False}
    report['verified_exit_identities'] = [{key: row[key] for key in ('code', 'name', 'delisted_date', 'exit_kind')}
        for row in lifecycle if row.get('exit_status') == 'official_effective_exit_verified']
    report['historical_exit_dates'] = ('scoped_official_exits_verified_universe_completeness_pending'
        if report['verified_exit_identities'] else report['historical_exit_dates'])
    report['raw_imports'] = []
    for artifact in args.raw_history_file:
        response_bytes = artifact.read_bytes()
        payload = json.loads(response_bytes.decode('utf-8'))
        if payload.get('rc') != 0 or not isinstance(payload.get('data'), dict):
            raise ValueError('Saved raw source response is unavailable')
        code = payload['data'].get('code')
        listing = next((r for r in lifecycle if r['code'] == code), None)
        if listing is None:
            raise ValueError('Saved raw response identity absent from official lifecycle')
        bars = parse_klines(payload['data'], code, max(args.start, listing['ipo_date']), args.end, 'none')
        if not bars:
            raise ValueError('Saved raw response has no rows in the requested window')
        sha = hashlib.sha256(response_bytes).hexdigest()
        retained = raw_dir / ('saved_raw_' + code + '_' + sha[:16] + '.response')
        retained.write_bytes(response_bytes)
        for bar in bars:
            bar.update(date_stamp=qa_date_stamp(bar['date']), source_response_sha256=sha,
                       source_response_path=str(retained.resolve()),
                       source_observed_at=None, source_observation_time_verified=False,
                       source_url=HISTORY_URL)
        upsert_many(db.stock_day, bars, ['code', 'date_stamp'])
        receipt = {'dataset': 'stock_day', 'code': code, 'source': 'eastmoney',
                   'status': 'source_response_imported', 'rows': len(bars),
                   'first': bars[0]['date'], 'last': bars[-1]['date'], 'observed_at': observed,
                   'artifact_sha256': sha, 'artifact_path': str(retained.resolve()),
                   'source_observation_time_verified': False, 'requested_window_complete': False,
                   'adjusted_panel_verified': False, 'historical_membership': 'exit_dates_pending'}
        db.panda_axis_sync.update_one({'dataset': 'stock_day', 'code': code}, {'$set': receipt}, upsert=True)
        report['raw_imports'].append({key: receipt[key] for key in ('code', 'rows', 'first', 'last', 'artifact_sha256')})
    db.panda_axis_validation.update_one({'_id': 'beijing_current_directory'}, {'$set': {
        'capability': 'beijing_current_directory', 'status': 'verified', 'scope': 'current_snapshot_only',
        'observed_at': observed, 'source_snapshot_date': directory['source_snapshot_date'],
        'current_codes': len(current), 'lifecycle_identities': len(lifecycle),
        'alias_codes_absent_current': report['historical_identities_absent_current'],
        'metadata_sha256': source_hash, 'raw_pages': directory['pages'],
        'historical_universe_complete': False, 'exit_dates_verified': False}}, upsert=True)
    if report['verified_exit_identities']:
        db.panda_axis_validation.update_one({'_id': 'beijing_scoped_exit_evidence'}, {'$set': {
            'capability': 'beijing_scoped_exit_evidence', 'status': 'verified', 'scope': 'supplied_original_notices_only',
            'observed_at': observed, 'records': report['verified_exit_identities'],
            'original_evidence': [row['exit_evidence'] for row in lifecycle if row.get('exit_evidence')],
            'historical_universe_complete': False}}, upsert=True)
    def save():
        temporary = args.report.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(args.report)
    network_lock = threading.Lock()
    network_state = {'consecutive_failures': 0, 'stop': False, 'reason': None}
    def fetch(row):
        code = row['code']
        query_window = lifecycle_window(row, args.start, args.end)
        if query_window is None:
            return 'skipped', {'code': code, 'rows': 0, 'reason': 'outside_effective_BSE_lifecycle'}
        query_start, query_end = query_window
        with network_lock:
            if network_state['stop']:
                return 'deferred', {'code': code, 'reason': network_state['reason']}
        prior = db.panda_axis_sync.find_one({'dataset': 'stock_adjusted_day', 'code': code, 'source': 'eastmoney'})
        if prior and prior.get('status') == 'complete' and prior.get('start', '9999') <= args.start and prior.get('through', '') >= args.end:
            return 'skipped', {'code': code, 'rows': prior['rows']}
        for attempt in range(3):
            try:
                with requests.Session() as session:
                    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
                    raw = history(session, code, query_start, query_end, 'none',
                                  cache_dir=raw_dir/'history_responses', endpoint=args.history_endpoint)
                    adjusted = history(session, code, query_start, query_end, 'hfq',
                                       cache_dir=raw_dir/'history_responses', endpoint=args.history_endpoint)
                if not raw or {r['date'] for r in raw} != {r['date'] for r in adjusted}:
                    raise ValueError('Empty or mismatched raw/HFQ history')
                for bar in raw:
                    bar['date_stamp'] = qa_date_stamp(bar['date'])
                content = {'raw': raw, 'hfq': adjusted}
                data = json.dumps(content, ensure_ascii=False, sort_keys=True).encode('utf-8')
                artifact = raw_dir / (code + '.json')
                artifact.write_bytes(data)
                sha = hashlib.sha256(data).hexdigest()
                upsert_many(db.stock_day, raw, ['code', 'date_stamp'])
                upsert_many(db.stock_adjusted_day, adjusted, ['code', 'date', 'source'])
                receipt = {'code': code, 'source': 'eastmoney', 'start': args.start, 'through': args.end,
                    'status': 'complete', 'rows': len(raw), 'observed_at': observed,
                    'artifact_sha256': sha, 'artifact_path': str(artifact.resolve()),
                    'adjustment': 'source_supplied_hfq_prices', 'factor_origin': 'source_native_hfq_relative_window',
                    'absolute_ipo_anchor_verified': False,
                    'scope': 'successful_requested_query; raw_HFQ_dates_matched; calendar_acceptance_pending',
                    'query_start': query_start, 'query_end': query_end,
                    'historical_membership': 'exit_dates_pending'}
                for dataset in ('stock_day', 'stock_adjusted_day'):
                    db.panda_axis_sync.update_one({'dataset': dataset, 'code': code}, {'$set': {**receipt, 'dataset': dataset}}, upsert=True)
                with network_lock:
                    network_state['consecutive_failures'] = 0
                return 'success', {'code': code, 'rows': len(raw), 'first': raw[0]['date'], 'last': raw[-1]['date'], 'sha256': sha}
            except Exception as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                connection_error = isinstance(exc, (requests.ConnectionError, requests.Timeout))
                denial = status in (401, 403, 429)
                if connection_error or denial:
                    with network_lock:
                        network_state['consecutive_failures'] += 1
                        if denial or network_state['consecutive_failures'] >= 3:
                            network_state.update(stop=True, reason=f'Source unavailable; bounded circuit stopped on {type(exc).__name__}: {status or "connection_failure"}')
                    # Do not retry a permission denial or run one outage through every identity.
                    return 'failures', {'code': code, 'error_type': type(exc).__name__, 'http_status': status, 'error': str(exc)}
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                else:
                    return 'failures', {'code': code, 'error': str(exc)}
    save()
    if args.metadata_only or args.history_unavailable_reason:
        report['status'] = 'current_metadata_synced_history_pending'
        if args.history_unavailable_reason:
            report['deferred'] = [{'code': row['code'], 'reason': args.history_unavailable_reason} for row in scope]
            report['source_connection'] = {'stop': True, 'reason': args.history_unavailable_reason,
                                          'retry_policy': 'wait_for_source_recovery_no_additional_network_attempts'}
        else:
            report['source_connection'] = {'history_status': 'not_attempted_metadata_only'}
        db.panda_axis_validation.update_one({'_id': 'beijing_raw_adjusted_scope'}, {'$set': {
            'capability': 'beijing_raw_adjusted_scope', 'status': 'partial',
            'scope': 'verified_identity_scope_only', 'start': args.start, 'end': args.end,
            'observed_at': observed, 'target_codes': len(scope),
            'available_raw_imports': report['raw_imports'], 'source_connection': report['source_connection'],
            'pending': report['pending'], 'historical_universe_complete': False,
            'exit_dates_verified': False, 'execution_status_history_verified': False,
            'metadata_sha256': source_hash, 'report_path': str(args.report.resolve())}}, upsert=True)
        save()
        client.close()
        print(json.dumps({'current': len(current), 'lifecycle': len(lifecycle), 'status': report['status']}), flush=True)
        return 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, row) for row in scope]
        for future in as_completed(futures):
            kind, item = future.result()
            report[kind].append(item)
            save()
            done = sum(len(report[k]) for k in ('success', 'skipped', 'failures', 'deferred'))
            if done % 20 == 0 or kind == 'failures' or done == len(scope):
                print(json.dumps({'completed': done, 'total': len(scope), 'failures': len(report['failures'])}), flush=True)
    report['source_connection'] = network_state
    report['status'] = 'raw_adjusted_scope_synced' if not report['failures'] and not report['deferred'] else 'partial'
    db.panda_axis_validation.update_one({'_id': 'beijing_raw_adjusted_scope'}, {'$set': {
        'capability': 'beijing_raw_adjusted_scope', 'status': 'partial', 'scope': 'verified_identity_scope_only',
        'start': args.start, 'end': args.end, 'observed_at': observed,
        'completed_codes': len(report['success'])+len(report['skipped']), 'target_codes': len(scope),
        'missing_codes': [r['code'] for r in report['failures']+report['deferred']],
        'historical_universe_complete': False, 'exit_dates_verified': False,
        'execution_status_history_verified': False, 'metadata_sha256': source_hash}}, upsert=True)
    save()
    client.close()
    return int(bool(report['failures']))


if __name__ == '__main__':
    raise SystemExit(main())
