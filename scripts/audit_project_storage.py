"""Read-only Windows storage inventory; ignores directory reparse targets."""
from pathlib import Path
from collections import defaultdict
import argparse,ctypes,json,os,sqlite3,time
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 root=a.root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);dbpath=out/'file_inventory.sqlite3';assert not dbpath.exists()
 db=sqlite3.connect(dbpath);db.execute('CREATE TABLE files(path TEXT PRIMARY KEY, relative_path TEXT, logical_bytes INTEGER, physical_bytes INTEGER, mtime_ns INTEGER, inode TEXT, links INTEGER)')
 getsize=ctypes.windll.kernel32.GetCompressedFileSizeW;getsize.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_ulong)];getsize.restype=ctypes.c_ulong
 counts=defaultdict(lambda:[0,0,0]);errors=[];skipped=[];batch=[];seen=set();start=time.monotonic();total=0
 for current,dirs,files in os.walk(root,followlinks=False):
  for name in list(dirs):
   p=Path(current)/name
   try:s=p.lstat()
   except OSError as e:errors.append({'path':str(p),'error':type(e).__name__});dirs.remove(name);continue
   if getattr(s,'st_file_attributes',0)&0x400:skipped.append(str(p));dirs.remove(name)
  for name in files:
   p=Path(current)/name
   if out==p or out in p.parents:continue
   try:
    s=p.stat();high=ctypes.c_ulong();ctypes.windll.kernel32.SetLastError(0);low=getsize(str(p),ctypes.byref(high));physical=(high.value<<32)|low
    if low==0xffffffff and ctypes.windll.kernel32.GetLastError():physical=s.st_size
    ident=(s.st_dev,s.st_ino);unique=physical if ident not in seen else 0;seen.add(ident);rel=p.relative_to(root).as_posix()
    batch.append((str(p),rel,s.st_size,unique,s.st_mtime_ns,str(s.st_ino),s.st_nlink));total+=1
    parts=Path(rel).parts
    for depth in [1,2,3,4]:
     if len(parts)>=depth:tag='/'.join(parts[:depth]);c=counts[tag];c[0]+=1;c[1]+=s.st_size;c[2]+=unique
    if len(batch)>=5000:db.executemany('INSERT INTO files VALUES(?,?,?,?,?,?,?)',batch);db.commit();batch=[]
   except OSError as e:errors.append({'path':str(p),'error':type(e).__name__})
 if batch:db.executemany('INSERT INTO files VALUES(?,?,?,?,?,?,?)',batch);db.commit()
 summary={'root':str(root),'file_count':total,'logical_bytes':db.execute('SELECT SUM(logical_bytes) FROM files').fetchone()[0],
  'physical_bytes_unique_files':db.execute('SELECT SUM(physical_bytes) FROM files').fetchone()[0],
  'directory_sizes':[{ 'path':k,'files':v[0],'logical_bytes':v[1],'physical_bytes':v[2]} for k,v in sorted(counts.items(),key=lambda kv:kv[1][2],reverse=True)],
  'largest_files':[{'path':p,'logical_bytes':l,'physical_bytes':c} for p,l,c in db.execute('SELECT relative_path,logical_bytes,physical_bytes FROM files ORDER BY physical_bytes DESC LIMIT 40')],
  'skipped_reparse_directories':skipped,'errors':errors,'seconds':round(time.monotonic()-start,1),'no_files_deleted':True}
 (out/'storage_audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');db.close()
 print(json.dumps({k:summary[k] for k in ['root','file_count','logical_bytes','physical_bytes_unique_files','seconds']},ensure_ascii=False))
 print(json.dumps({'top_directories':[r for r in summary['directory_sizes'] if '/' not in r['path']][:12],'largest_files':summary['largest_files'][:8],'errors':len(errors),'skipped_reparse_directories':skipped},ensure_ascii=False))
if __name__=='__main__':main()
