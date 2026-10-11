"""One fixed20 retained-text source unit; no HTTP, score or return execution."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from panda_alpha.cash_investment import parse_current_annual_investment_cash


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    cli=argparse.ArgumentParser();cli.add_argument('--protocol',type=Path,required=True)
    cli.add_argument('--protocol-sha',required=True);cli.add_argument('--execute',action='store_true');args=cli.parse_args()
    if not args.execute:print('DEFERRED_NO_SOURCE_CONSUMPTION');return
    if sha(args.protocol)!=args.protocol_sha:raise ValueError('frozen protocol changed')
    p=json.loads(args.protocol.read_text(encoding='utf-8'));end=datetime.fromisoformat(p['deadline_utc'])
    output=Path(p['output'])
    if output.exists():raise ValueError('complete or pending receipt exists; no automatic replay')
    for b in p['bindings'].values():
        if sha(b['path'])!=b['sha256']:raise ValueError('source/runtime binding changed')
    with sqlite3.connect(Path(p['registry']).resolve().as_uri()+'?mode=ro',uri=True) as d:
        row=d.execute('SELECT payload FROM research_events WHERE event_id=? AND subject=?',
             ('cash-investment-source20-freeze:'+args.protocol_sha,p['id'])).fetchone()
        if not row or json.loads(row[0]).get('new_contexts')!=0:raise ValueError('source-only registration required')
    records=[]
    with sqlite3.connect(Path(p['captures']).resolve().as_uri()+'?mode=ro',uri=True) as d:
        for c in p['selected20']:
            if datetime.now(timezone.utc)>=end:break
            result={'symbol':c['symbol'],'announcement_id':c['announcement_id'],'report_date':c['report_date']}
            try:
                row=d.execute('SELECT period,original_json_gzip,source_sha256 FROM captures WHERE code=? AND annid=?',
                              (c['symbol'],c['announcement_id'])).fetchone()
                if not row or row[0]!=c['report_date'] or row[2]!=c['capture_sha256'] or hashlib.sha256(row[1]).hexdigest()!=row[2]:
                    raise ValueError('exact captured identity/blob changed or absent')
                raw=json.loads(gzip.decompress(row[1]));stock=raw['stock'];prov=dict(stock['provenance'])
                if stock['record_sha256']!=c['source_record_sha256'] or prov['text_sha256']!=c['text_sha256']:
                    raise ValueError('stock/text binding changed')
                textpath=Path(c['text_path']);compressed=textpath.read_bytes()
                if hashlib.sha256(compressed).hexdigest()!=c['text_sha256']:
                    raise ValueError('retained text gzip changed')
                content=gzip.decompress(compressed);prov.update(text_hash_reverified=True,
                     text_content_sha256=hashlib.sha256(content).hexdigest(),pdf_hash_reverified=False)
                result.update(parse_current_annual_investment_cash(content.decode('utf-8'),stock,prov),
                              capture_sha256=row[2],stock_record_sha256=stock['record_sha256'],
                              text_path=str(textpath),text_sha256=c['text_sha256'],published_at=stock.get('published_at'),
                              stored_available_date=stock.get('available_date'),
                              comparison_change_barriers_preserved=stock.get('comparison_change_barriers'),
                              monetary_source_facts_only=True,financial_or_production_qualified=False)
            except Exception as error:
                result.update(status='pending',error_type=type(error).__name__,reason=str(error)[:384])
            records.append(result)
    result={'status':'FIXED20_CASH_INVESTMENT_SOURCE_COMPLETED' if len(records)==20 else 'SOURCE_PENDING_DEADLINE_PARTIAL_RETAINED',
            'records':records,'counts':dict(Counter(r['status'] for r in records)),
            'protocol_sha256':args.protocol_sha,'completed_at_utc':datetime.now(timezone.utc).isoformat(),
            'new_contexts':0,'new_scores_or_price_labels':False,'HTTP_or_PDF_calls':0,'official_calls':0,
            'complete_text_or_database_copies_saved':False,'production_qualified':False,
            'limits':'Same-period three monetary source facts only; no native field-value parity, full versions, PIT, financial qualification or economic claim'}
    with output.open('x',encoding='utf-8') as s:json.dump(result,s,ensure_ascii=False,indent=2,allow_nan=False);s.write('\n')
    print(json.dumps({'status':result['status'],'counts':result['counts'],'receipt_sha256':sha(output),'new_contexts':0}))


if __name__=='__main__':main()
