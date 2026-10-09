"""Apply an explicit, reviewed research-storage manifest; preview by default."""
from pathlib import Path
from urllib.parse import urlsplit
import argparse,gzip,hashlib,json,os,sqlite3,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.runtime/python'))
PROTECTED=('.git','panda_alpha','tests','config','research_bootstrap','.runtime/mongodb/data',
           'research_runs/research_trials.sqlite3','research_runs/live_memory.json')
PUBLIC_HOSTS={'static.cninfo.com.cn','static.cninfo.cn','www.capco.org.cn','sp.capco.org.cn','www.csrc.gov.cn'}
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
 return h.hexdigest()
def guarded_path(root,path):
 root=Path(root).resolve();p=Path(path)
 resolved=p.resolve(strict=True)
 if root not in resolved.parents:raise ValueError('Target outside named project root')
 rel=resolved.relative_to(root).as_posix()
 if any(rel==v or rel.startswith(v+'/') for v in PROTECTED):raise ValueError('Protected project data')
 cursor=p
 while cursor!=root and cursor.parent!=cursor:
  if getattr(cursor.lstat(),'st_file_attributes',0)&0x400:raise ValueError('Reparse target not accepted')
  cursor=cursor.parent
 if not resolved.is_file():raise ValueError('Only explicit individual files may be removed')
 return resolved
def compact_wealth(source,target):
 import ijson
 rows=[]
 with Path(source).open('rb') as f:
  for r in ijson.items(f,'runs.item',use_float=True):
   account=r.get('accounting',{})
   rows.append({k:r.get(k) for k in ['name','cost','status','error','metrics','blocked_orders','terminal_liquidation_pending']}
               |{'accounting':{k:account.get(k) for k in ['net_return','fees','folds','concentration','max_absolute_accounting_error']},
                 'exposure_summary':r.get('exposures',{}).get('summary')})
 if not rows:raise ValueError('Backtest summary has no runs; original retained')
 payload={'source_sha256':sha(source),'original_relative_path':str(Path(source).relative_to(ROOT)),
          'runs':rows,'daily_quantity_order_and_position_arrays_discarded_by_user_request':True}
 target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_suffix('.tmp')
 temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');temporary.replace(target)
 assert json.loads(target.read_text(encoding='utf-8'))['runs']==rows
 return {'path':str(target),'sha256':sha(target),'run_count':len(rows)}
def captured_financial_store(folders,path):
 if path.exists():
  with sqlite3.connect(path) as db:return db.execute('SELECT COUNT(*) FROM captures').fetchone()[0]
 temporary=path.with_suffix('.tmp');db=sqlite3.connect(temporary)
 db.execute('CREATE TABLE captures(code TEXT,annid TEXT,period TEXT,original_json_gzip BLOB,source_sha256 TEXT,capture_state TEXT,PRIMARY KEY(code,annid))')
 count=0
 for folder in folders:
  for p in sorted(Path(folder).glob('*.json.gz')):
   b=p.read_bytes();r=json.loads(gzip.decompress(b));s=r.get('stock')
   if not s:continue
   db.execute('INSERT OR REPLACE INTO captures VALUES(?,?,?,?,?,?)',(s['code'],str(s['announcement_id']),s['report_date'],b,hashlib.sha256(b).hexdigest(),
              'accepted capture before cache pruning; local original must be refetched before any new PDF hash re-verification'))
   count+=1
 db.commit();assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';db.close();temporary.replace(path);return count
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--apply',action='store_true');a=ap.parse_args()
 planpath=a.plan.resolve();plan=json.loads(planpath.read_text(encoding='utf-8-sig'));root=Path(plan['root']).resolve();out=Path(plan['output']).resolve()
 if root!=ROOT:raise ValueError('Manifest is for a different checkout')
 if not plan['user_authorized'] or not plan['research_paused']:raise ValueError('User authorization/pause missing')
 if not a.apply:
  print(json.dumps({'preview':True,'files':len(plan['delete_actions']),'categories':plan['categories'],'no_files_deleted':True},ensure_ascii=False));return
 result=out/'cleanup_receipt.json'
 if result.exists():raise ValueError('Completed cleanup already recorded; inspect it instead of repeating')
 ledger=root/'research_runs/research_trials.sqlite3';ledger_before=sha(ledger);memory=root/'research_runs/live_memory.json';memory_before=sha(memory)
 archive=root/'research_runs/compact_archive';archive.mkdir(exist_ok=True)
 captures=captured_financial_store(plan['financial_capsules_to_preserve'],archive/'captured_financial.sqlite3')
 catalog=archive/'original_source_catalog.json.gz';catalog.write_bytes(gzip.compress(json.dumps({'source_index_bindings':plan['source_index_bindings'],
   'sources':plan['source_catalog'],'native_text_preserved':True,'PDFs_are_retrievable_caches_not_newly_reverified_local_originals':True},ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
 free_before=__import__('shutil').disk_usage(root).free;removed=[];skipped=[];summaries=[];started=time.monotonic()
 for i,item in enumerate(plan['delete_actions']):
  try:
   p=guarded_path(root,item['path']);s=p.stat()
   if s.st_size!=item['logical_bytes'] or s.st_mtime_ns!=item['mtime_ns']:raise ValueError('File changed after reviewed inventory')
   if item['kind']=='verified_public_pdf_cache':
    source=item['source'];url=urlsplit(source['url'])
    if url.scheme not in {'http','https'} or url.hostname not in PUBLIC_HOSTS:raise ValueError('Unrecognized original publisher')
    native=guarded_path(root,source['text_path'])
    if sha(native)!=source['text_sha256'] or sha(p)!=source['pdf_sha256']:raise ValueError('Captured PDF/native digest changed')
    with gzip.open(native,'rb') as f:
     if not f.read(1):raise ValueError('Extracted native text is empty')
   elif item['kind']=='compact_backtest':
    target=archive/'backtests'/item['relative_path'].replace('/','__')
    summaries.append(compact_wealth(p,target))
   digest=item['source']['pdf_sha256'] if item['kind']=='verified_public_pdf_cache' else sha(p)
   p.unlink();removed.append({'path':item['relative_path'],'kind':item['kind'],'sha256':digest,'logical_bytes':item['logical_bytes'],'physical_bytes_in_audit':item['physical_bytes']})
  except (OSError,ValueError,KeyError,EOFError,gzip.BadGzipFile) as e:
   skipped.append({'path':item['relative_path'],'kind':item['kind'],'reason':str(e)})
  if i and i%500==0:
   (out/'cleanup_progress.json').write_text(json.dumps({'processed':i+1,'total':len(plan['delete_actions']),'removed':len(removed),'skipped':len(skipped)}),encoding='utf-8')
   print(json.dumps({'processed':i+1,'total':len(plan['delete_actions']),'removed':len(removed),'skipped':len(skipped)}),flush=True)
 assert sha(ledger)==ledger_before and sha(memory)==memory_before
 receipt={'status':'EXPLICIT_NIGHT_CLEANUP_COMPLETED','plan_sha256':sha(planpath),'removed':removed,'skipped':skipped,'backtest_summaries':summaries,
   'captured_financial_records_preserved':captures,'financial_store':str(archive/'captured_financial.sqlite3'),'original_source_catalog':str(catalog),
   'free_before':free_before,'free_after':__import__('shutil').disk_usage(root).free,'registry_and_hot_memory_unchanged':True,
   'market_database_and_native_financial_text_preserved':True,'no_factor_research_or_network_resumed':True,'official_spent':0,'seconds':round(time.monotonic()-started,1)}
 result.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in receipt.items() if k not in ['removed','skipped','backtest_summaries']},ensure_ascii=False))
if __name__=='__main__':main()
