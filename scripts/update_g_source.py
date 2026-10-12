"""Incremental local G contract sync and bounded CNInfo metadata refresh.

No PDF, market prices, labels, paid jobs or pool operations. Missing new report
bodies enter the pending queue. Updating this store is NOT an official worker
deployment; competition data-channel certification remains separate.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, parse_qs
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from panda_alpha.g_continuity import GSourceStore, annual_delta, canonical, digest, TZ


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def verify(binding):
    path=Path(binding['path']).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest()!=binding['sha256']:
        raise ValueError('Bound G input changed: '+str(path))
    return path


def sync(store, manifest, binding):
    cases={}
    for item in manifest['contracts']:
        packet=read(verify(item))
        for case in packet['cases']:
            ident=case['source_identity'];key=(ident['code'],ident['announcement_id'])
            cases[key]=case
    for item in manifest.get('continuations',[]):
        packet=read(verify(item));ident=packet['annual_source_identity'];key=(ident['code'],ident['announcement_id'])
        if key not in cases:raise ValueError('Continuation has no exact annual leaf')
        if cases[key]['source_identity']['source_record_sha256']!=ident['source_record_sha256']:
            raise ValueError('Continuation changes annual identity')
        cases[key]=dict(cases[key],formations=packet['formations'])
    ready=[]
    for (code,annid),case in sorted(cases.items()):
        contract=case['current_field_contract'];ident=case['source_identity']
        identity=contract.get('source_identity',{})
        published=ident.get('published_at') or identity.get('published_at')
        if not published:raise ValueError('Missing original annual publication date: '+code)
        latest=max(case['formations'],key=lambda r:r['date'])
        payload={'announcement_id':annid,'report_date':ident['report_date'],'published_at':published,
            'source_record_sha256':ident['source_record_sha256'],'contract':contract,
            'approved_through':latest['date'],'pending_reasons':latest.get('pending_reasons',[]),
            'source_binding':binding}
        store.append('annual',code,payload)
        try:annual_delta(contract,ident['report_date'])
        except (ValueError,KeyError,TypeError):continue
        if latest['source_contract_proposed_ready'] and not payload['pending_reasons']:
            ready.append({'code':code,'since':latest['date'],
                          'known_annual_id':annid,'known_publication_date':str(published)[:10]})
    return {'contract_leaves':len(cases),'ready_for_metadata_refresh':ready}


def refresh(store, ready, cfg, *, through, maximum_pages=4, session=None):
    """All-announcement bounded paging for already reviewed annual sources."""
    import requests
    from pymongo import MongoClient
    result=[]
    endpoint='https://www.cninfo.com.cn/new/hisAnnouncement/query'
    session=session or requests.Session()
    now=datetime.now(timezone.utc).isoformat()
    with MongoClient(cfg['data']['mongo_uri'],serverSelectionTimeoutMS=5000) as client:
        for case in ready:
            code,since=case['code'],case['since']
            urls=[r.get('detail_url','') for r in client[cfg['data']['database']].stock_filing_index.find(
                {'code':code},{'_id':0,'detail_url':1}).sort('announcement_id',-1).limit(20)]
            orgs=set()
            for url in urls:
                u=urlsplit(url);q=parse_qs(u.query)
                if (u.scheme=='https' and u.netloc=='www.cninfo.com.cn' and u.path=='/new/disclosure/detail'
                        and q.get('stockCode')==[code] and len(q.get('orgId',[]))==1):orgs.add(q['orgId'][0])
            receipt={'code':code,'since':since,'through':through,'complete':False,'rows':[],
                'endpoint':endpoint,'pages':[],'observed_at':now,'scope':'supplied_CNInfo_issuer_metadata_only'}
            try:
                if len(orgs)!=1:raise ValueError('Unique source-bound orgId missing')
                # A server's empty response is not credible absence evidence if
                # the same bound issuer query cannot find its known annual.
                probe={'pageNum':1,'pageSize':30,'column':'szse','tabName':'fulltext','plate':'',
                    'stock':code+','+next(iter(orgs)),'searchkey':'','secid':'','category':'','trade':'',
                    'seDate':case['known_publication_date']+'~'+case['known_publication_date'],
                    'sortName':'','sortType':'','isHLtitle':'true'}
                control=session.post(endpoint,data=probe,timeout=15,allow_redirects=False)
                control.raise_for_status();control_packet=control.json()
                receipt['known_annual_control']={'request_sha256':digest(probe),
                    'response_sha256':hashlib.sha256(control.content).hexdigest(),'http_status':control.status_code}
                if not any(str(r.get('announcementId'))==case['known_annual_id'] and str(r.get('secCode'))==code
                           for r in control_packet.get('announcements') or []):
                    raise ValueError('Known annual metadata control missing; empty tail is not certified')
                total=None
                for page in range(1,maximum_pages+1):
                    request={'pageNum':page,'pageSize':30,'column':'szse','tabName':'fulltext','plate':'',
                        'stock':code+','+next(iter(orgs)),'searchkey':'','secid':'','category':'','trade':'',
                        'seDate':since+'~'+through,'sortName':'','sortType':'','isHLtitle':'true'}
                    response=session.post(endpoint,data=request,timeout=15,allow_redirects=False)
                    response.raise_for_status();packet=response.json()
                    count=packet.get('totalAnnouncement');rows=packet.get('announcements') or []
                    receipt['pages'].append({'request_sha256':digest(request),'response_sha256':hashlib.sha256(response.content).hexdigest(),'http_status':response.status_code})
                    if type(count)!=int or not isinstance(rows,list) or count>maximum_pages*30:
                        raise ValueError('Metadata bound or shape exceeded')
                    if total is not None and count!=total:raise ValueError('Unstable pagination total')
                    total=count
                    for row in rows:
                        if str(row['secCode'])!=code:raise ValueError('Foreign issuer metadata')
                        stamp=datetime.fromtimestamp(row['announcementTime']/1000,TZ).date().isoformat()
                        if not since<=stamp<=through:raise ValueError('Announcement outside query window')
                        receipt['rows'].append({'announcement_id':str(row['announcementId']),
                            'title':re.sub(r'<[^>]+>','',row['announcementTitle']),'published_at':stamp})
                    if len(receipt['rows'])>=total:break
                if len(receipt['rows'])!=total or len({r['announcement_id'] for r in receipt['rows']})!=total:
                    raise ValueError('Incomplete or duplicate metadata pages')
                receipt['complete']=True
            except Exception as error:
                receipt['error']=type(error).__name__+': '+str(error)[:250]
            proof={'sha256':digest(receipt),'source':endpoint}
            # An incomplete last refresh invalidates the freshness lease.
            store.append('refresh',code,dict(receipt,source_binding=proof),observed_at=now)
            for row in receipt['rows']:
                title=row['title']
                relevant=bool(re.search(r'年度报告|半年度报告|季度报告|更正|修订|会计政策|会计差错|追溯|重述',title))
                if not relevant:continue
                annual=re.search(r'(20\d{2})\s*年.*年度报告',title)
                payload=dict(row,source_binding=proof)
                if annual and not re.search(r'摘要|英文',title):
                    payload['report_date']=annual[1]+'-12-31'
                    store.append('report_metadata',code,payload,observed_at=now)
                else:
                    # Unknown new event is a source task, never filled with zero
                    # or interpreted as an economic failure.
                    store.append('blocker',code,dict(payload,report_dates=[],reason='new_financial_metadata_requires_G_scope_review'),observed_at=now)
            result.append({k:v for k,v in receipt.items() if k!='rows'} | {'metadata_rows':len(receipt['rows'])})
    return result


def sync_industry(store,cfg,codes):
    """Bind the latest accepted GLOBAL edition, including missing issuer rows."""
    from pymongo import MongoClient
    with MongoClient(cfg['data']['mongo_uri'],serverSelectionTimeoutMS=5000) as client:
        db=client[cfg['data']['database']]
        editions=list(db.stock_industry_editions.find({'status':'verified_scoped'},{'_id':0}))
        latest=max((e['publication_date'] for e in editions),default=None)
        same=[e for e in editions if e['publication_date']==latest]
        if len(same)!=1:raise ValueError('Latest global industry edition missing or ambiguous')
        edition=same[0];rows=list(db.stock_industry_pit.find({'vintage_id':edition['vintage_id'],
            'source_sha256':edition['source_sha256'],'code':{'$in':codes}},{'_id':0}))
        records={}
        for row in rows:
            code=row['code']
            if (code in records or row['publication_date']!=latest
                    or row['parse_receipt_sha256']!=edition['parse_receipt_sha256']):
                raise ValueError('Industry row/edition identity mismatch')
            records[code]=row['category_code']+row['major_code']
        store.append('industry','*',{'published_at':latest,'publication_date':latest,
            'vintage_id':edition['vintage_id'],'source_sha256':edition['source_sha256'],
            'records':records,'source_scope_count':len(edition['source_scope_codes']),
            'source_binding':{'sha256':digest({'edition':edition,'rows':rows}),
                              'source_sha256':edition['source_sha256'],'source':'AXIS accepted industry collections'}})
    return {'publication_date':latest,'requested':len(codes),'rows':len(records),'global_latest_edition_selected':True}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--refresh-ready',action='store_true')
    ap.add_argument('--config',type=Path,default=ROOT/'config/panda-alpha.local.json')
    ap.add_argument('--through')
    ap.add_argument('--decision-date')
    ap.add_argument('--sync-industry',action='store_true')
    args=ap.parse_args();manifest=read(args.manifest)
    binding={'path':str(args.manifest.resolve()),'sha256':hashlib.sha256(args.manifest.read_bytes()).hexdigest()}
    if not args.execute:
        print(canonical({'status':'PLAN_ONLY','source':binding,'store':manifest['store'],'contracts':len(manifest['contracts']),'HTTP_calls':0}));return
    store=GSourceStore(manifest['store'])
    try:
        status=sync(store,manifest,binding)
        if args.sync_industry:
            status['industry']=sync_industry(store,read(args.config),[r['code'] for r in status['ready_for_metadata_refresh']])
        if args.refresh_ready:
            if not args.through:raise ValueError('Refresh needs explicit inclusive --through')
            if args.through>datetime.now(TZ).date().isoformat():raise ValueError('No future metadata completeness claim')
            status['refreshes']=refresh(store,status['ready_for_metadata_refresh'],read(args.config),through=args.through)
        if args.decision_date:
            rows=store.asof([r['code'] for r in status['ready_for_metadata_refresh']],args.decision_date)
            status['source_result']=rows.astype(object).where(pd.notna(rows),None).to_dict('records')
        status.update(official_runs=0,official_spent=0,new_economic_contexts=0,official_production_channel_certified=False)
        print(json.dumps(status,ensure_ascii=False,default=str))
    finally:store.close()


if __name__=='__main__':main()
