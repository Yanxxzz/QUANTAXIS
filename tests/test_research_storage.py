"""Exercise the destructive maintenance seam in an isolated fake checkout."""
import importlib.util,json,sys
from pathlib import Path
import pytest
PATH=Path(__file__).resolve().parents[1]/'scripts/prune_research_storage.py'
spec=importlib.util.spec_from_file_location('prune_research_storage',PATH);maintenance=importlib.util.module_from_spec(spec);spec.loader.exec_module(maintenance)

def test_path_outside_root_and_primary_database_are_protected(tmp_path):
 root=tmp_path/'repo';root.mkdir();outside=tmp_path/'unrelated.json';outside.write_text('valuable')
 with pytest.raises(ValueError,match='outside'):maintenance.guarded_path(root,outside)
 protected=root/'.runtime/mongodb/data/collection.wt';protected.parent.mkdir(parents=True);protected.write_bytes(b'market')
 with pytest.raises(ValueError,match='Protected'):maintenance.guarded_path(root,protected)

def test_cleanup_preserves_changed_files_and_registry(tmp_path,monkeypatch):
 root=tmp_path/'repo';root.mkdir();out=root/'research_runs/maintenance';out.mkdir(parents=True)
 ledger=root/'research_runs/research_trials.sqlite3';ledger.write_bytes(b'unchanged registry')
 memory=root/'research_runs/live_memory.json';memory.write_text('{}')
 removable=root/'research_runs/old_progress.json';removable.write_text('old checkpoint');s=removable.stat()
 changed=root/'research_runs/changed_progress.json';changed.write_text('earlier');old=changed.stat();changed.write_text('user added a new result')
 plan={'root':str(root),'output':str(out),'user_authorized':True,'research_paused':True,'financial_capsules_to_preserve':[],'source_index_bindings':[],'source_catalog':[],
  'delete_actions':[{'path':str(p),'relative_path':p.relative_to(root).as_posix(),'logical_bytes':st.st_size,'mtime_ns':st.st_mtime_ns,'physical_bytes':st.st_size,'kind':'temporary_progress'} for p,st in [(removable,s),(changed,old)]]}
 path=out/'plan.json';path.write_text(json.dumps(plan));monkeypatch.setattr(maintenance,'ROOT',root)
 monkeypatch.setattr(sys,'argv',['prune','--plan',str(path),'--apply']);maintenance.main()
 assert not removable.exists();assert changed.read_text()=='user added a new result'
 assert ledger.read_bytes()==b'unchanged registry' and memory.read_text()=='{}'
 receipt=json.loads((out/'cleanup_receipt.json').read_text());assert len(receipt['removed'])==1 and len(receipt['skipped'])==1

def test_preview_never_deletes_files(tmp_path,monkeypatch):
 root=tmp_path/'repo';root.mkdir();source=root/'old.json';source.write_text('retained')
 plan=root/'plan.json';plan.write_text(json.dumps({'root':str(root),'output':str(root),'user_authorized':True,'research_paused':True,'delete_actions':[{'path':str(source)}],'categories':{}}))
 monkeypatch.setattr(maintenance,'ROOT',root);monkeypatch.setattr(sys,'argv',['prune','--plan',str(plan)]);maintenance.main()
 assert source.read_text()=='retained'


def test_compact_output_preserves_metrics_errors_and_curve_without_mutating_math():
 from panda_alpha.artifacts import compact_study_result
 full={'status':'evaluated_research','source_coverage':{'full_PIT':False},'errors':[],
       'runs':[{'name':'ARINV_G10','cost':.005,'metrics':{'compounded_return':.16},
                'accounting':{'folds':[{'net_return':.1}],'fees':.02},
                'daily':[{'date':'2026-01-05','nav':1.1,'net_return':.1,'positions_after':{'000001':1},'security_rows':[{'symbol':'000001'}]}],
                'orders':[{'symbol':'000001'}],'trades':[{'symbol':'000001'}],
                'exposures':{'summary':{'mean_cash_fraction':.1},'daily':[{'positions':[{'symbol':'000001'}]}]}}]}
 compact=compact_study_result(full);run=compact['runs'][0]
 assert run['metrics']==full['runs'][0]['metrics'] and run['accounting']==full['runs'][0]['accounting']
 assert run['daily']==[{'date':'2026-01-05','nav':1.1,'net_return':.1}]
 assert 'orders' not in run and 'trades' not in run and 'daily' not in run['exposures']
 assert compact['source_coverage']==full['source_coverage'] and compact['errors']==full['errors']
 assert full['runs'][0]['daily'][0]['positions_after']=={'000001':1}
