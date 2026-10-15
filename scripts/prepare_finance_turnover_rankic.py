"""Freeze/replay a new public-source score; never infer native financing access."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,hashlib,sys
ROOT=Path(__file__).resolve().parents[1]
UNIT=ROOT/'research_runs/finance_turnover_rankic_20261015'
OLD=ROOT/'research_runs/limit_turnover_margin_20261015'
sys.path.insert(0,str(ROOT))

def read(p):return json.loads(Path(p).read_text('utf-8-sig'))
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def bind(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}
def now():return datetime.now(timezone.utc).isoformat()

def freeze():
    from panda_alpha.registry import ResearchRegistry,strategy_definition,trial_key
    if (UNIT/'protocol.json').exists():raise RuntimeError('Already frozen; collect existing unit')
    UNIT.mkdir(exist_ok=True)
    common=ROOT/'research_runs/finance_domain_validation_20261015/credit_crowding/values.json'
    codes=sorted({c for d,c in read(common)['masks']['control']})
    symbols=[]
    for pre in ['00','60']:
        part=[c for c in codes if c.startswith(pre)][:30];assert len(part)==30;symbols+=part
    meta=ROOT/'research_runs/limit_reversal_20261014/source_metadata.json';cal=read(meta)['calendar']
    inputs=[d for d in cal if '2024-06-03'<=d<='2026-08-28']
    dates=[d for d in cal if '2026-05-06'<=d<='2026-08-28'];assert len(dates)==82
    p={'scope':'FINANCE_TURNOVER_RANKIC_20261015','frozen_at':now(),
       'human_instructions':['把新的版本放到官网测试一下，看看rankic','还有之前高收益的版本也回测一下看看','融资＋换手变化的新评分'],
       'source_symbols':symbols,'formation_dates':dates,'calendar':bind(meta),
       'input_window':{'start':inputs[0],'end':inputs[-1]},'evaluation_window':{'start':dates[0],'end':dates[-1]},
       'cycle':5,'groups':10,'direction':1,
       'definitions':{'target':'0.5*(average_percentile_rank(net_flow_5)+average_percentile_rank(turnover_ratio_5_20))',
          'control':'average_percentile_rank(turnover_ratio_5_20)'},
       'source_features':{'net_flow_5':'sum(RZMRE-RZCHE)/sum(RZMRE+RZCHE),5reference reports endingt-2,positivegross;6full nonnegativeRZYE/RZMRE/RZCHE observations required by retained source helper',
          'turnover_ratio_5_20':'mean(last5)/mean(last20),20exactfinite nonnegative turnover observations endingt-2,mean20positive'},
       'clock':'Score date t uses only finance/turnover through t-2. Official normal holding labels start no earlier than t+1. No holding-state/exit-age/realized-return input.',
       'common_domain':'Fixed60 archived identities; both finite metrics,turnover ratio>=0; retain missing nominal rows and actualties. Rank each day on same valid domain for bothroles. Nozero,hold,decay,jitter or income-to-financing substitutes.',
       'source_selection':'First30 lexicographic00 and first30lexicographic60 from frozen1444 original finance-disclosed opportunitymask. No amount/price/return selection. This is archived observed-financing-support scope, not historical eligible-universe/PIT certification.',
       'source_scope':{'max_new_requests':600,'no_auto_retry':True,'finance_endpoint':'https://datacenter-web.eastmoney.com/api/data/v1/get','report':'RPTA_WEB_RZRQ_GGMX','no_old_success_or_known_absence_requery':True},
       'economic_hypothesis':'Positive financing support together with renewed trading participation may distinguish positive future returns. Financing can also crowd momentum, and high turnover can distribute risk; fixed positive direction is falsifiable.',
       'comparison':'Does financing contribute beyond the same-support turnoverchange score? Preset bothroles before any newlabels/reports; launchcontrol before readingtargetreturns. No intermediategroup/sign/weight/window selection.',
       'research_question':'Observe official5day RankIC/ICIR/groups for this fixed external-input continuous score, compared with its predeterminedturnover-only control.',
       'decision_if_pass':'Keep only an exploratory lead if report/source/codeecho/support are complete andtarget shows coherent directedIC/group evidence; then separate later event-domain and nativeproduction work, no automaticadmission.',
       'decision_if_fail':'Close this exact direction/window/weight; no flip,retune,groupcherry-pick or automaticretry. Service/input failure stays technical, not economic rejection.',
       'sampling_note':'82score dates are real daily financing/turnover windows, not82independent periods. Publicworkflow5Dgrid expected16formation observations before6sessionholding tail. No200period claim.',
       'not_the_old_strategy':'New continuous score across fixed60 names, not limited to realizedtwo-board trades, not original10%TP orcontinuous exit account. Prior90.93/70.40/33.78accounts only reusedcontext and do not becomeRankIC.',
       'official_source_role':'Pythonliteral public-data replay using officialprice labels; close input only carries index. No nativefinance field orproduction/PIT claim. Existing failedmargin_balanceRun remainslocked.',
       'formal_eligible':False,'all_history_exposed':True,'paid_scope':'FINANCE_DOMAIN_VALIDATION20_20261015','max_total_official_credits':20,'max_new_runs':2,'conservative_per_run_reserve':8,
       'bindings':[bind(x) for x in [__file__,UNIT/'source_io.py',ROOT/'panda_alpha/finance_turnover_source_factor.py',ROOT/'tests/test_finance_turnover_source_factor.py',
          common,meta,OLD/'finance_protocol.json',OLD/'turnover_protocol.json',ROOT/'panda_alpha/native_preflight.py',ROOT/'panda_alpha/platform.py']]}
    save(UNIT/'protocol.json',p)
    authority={'status':'APPROVED_BY_HUMAN','source':'direct_user_reply_in_current_chat','human_instructions':p['human_instructions'],
       'selected_new_factor':True,'parent_batch_id':p['paid_scope'],'parent_authority':bind(ROOT/'research_runs/finance_domain_validation_20261015/authorization.json'),
       'max_total_credits':20,'accept_estimated_charge_risk':True,'recorded_at_utc':now(),
       'risk_authority_note':'Earlier direct paid/free estimated-risk consent persists; current20scope and latestofficial-test request authorize thisboundedbatch. Not new20.',
       'execution_valid_until_utc':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),'automation':'PAUSED'}
    save(UNIT/'authorization.json',authority)
    reg=ResearchRegistry(ROOT/'research_runs/research_trials.sqlite3');before=reg.total;regs=[]
    for role,graph in p['definitions'].items():
        c={'candidate_id':'FINANCE_TURNOVER_SOURCE60_'+role.upper()+'_20261015','direction':1,
           'strategy_spec':{'graph':graph,'source_features':p['source_features'],'clock':p['clock'],'source_symbols':symbols,
             'common_domain':p['common_domain'],'source_selection':p['source_selection'],'evaluation_window':p['evaluation_window'],
             'price_label_source':'Official stockworker prices, actualcleaner/tradeability unknown beforeRun; externalfixedfeatures',
             'template':bind(ROOT/'panda_alpha/finance_turnover_source_factor.py')}}
        reg.record(c,p['evaluation_window'],5,10);definition=strategy_definition(c,p['evaluation_window'],5,10);key=trial_key(definition)
        regs.append({'role':role,'candidate_id':c['candidate_id'],'economic_identity_key':key,'economic_definition':definition})
        reg.append_event('evaluation',key,{'phase':'FROZEN_BEFORE_NEW_FEATURES_OR_RESULTS','protocol':bind(UNIT/'protocol.json'),'new_contexts':0},event_id='finance-turnover-freeze:'+key)
    save(UNIT/'registration.json',{'before':before,'total':reg.total,'new_contexts':reg.total-before,'registrations':regs,'head':reg.head})
    state={'status':'FROZEN_SOURCE_PENDING','process':None,'registry_total':reg.total,'registry_head':reg.head,'new_contexts':2,'official_runs':0,'official_spent':0,'automation':'PAUSED','formal_eligible':False}
    save(UNIT/'state.json',state);reg.close();print(json.dumps({'registered':state['registry_total'],'symbols':len(symbols),'dates':len(dates),'protocol':sha(UNIT/'protocol.json')}))

def read_retained_close_fixture(descriptor):
    """Only named retained monthly3column quotes; never Mongo/network or labels."""
    import pandas as pd
    import pyarrow.parquet as pq
    d=read(descriptor);parts=[];symbols=set(d['symbols'])
    for b in d['sources']:
        assert sha(b['path'])==b['sha256'],b['path']
        frame=pq.read_table(b['path'],columns=['date','code','close']).to_pandas()
        frame['symbol']=frame.code.astype(str).str.zfill(6);frame=frame[frame.symbol.isin(symbols)].copy()
        ds=frame.date.astype(str);frame['date']=ds.str[:4]+'-'+ds.str[4:6]+'-'+ds.str[6:8]
        frame=frame[frame.date.between(d['input_window']['start'],d['input_window']['end'])]
        parts.append(frame[['date','symbol','close']])
    out=pd.concat(parts,ignore_index=True).sort_values(['date','symbol']).reset_index(drop=True)
    assert not out.duplicated(['date','symbol']).any()
    return out

def build():
    import pandas as pd, numpy as np
    from panda_alpha.finance_turnover_source_factor import render_finance_turnover_source_factor,renderer_metadata
    from panda_alpha.native_preflight import build_native_preflight,verify_native_preflight
    from panda_alpha.platform import fingerprint
    from panda_alpha.registry import ResearchRegistry
    p=read(UNIT/'protocol.json');regspec=read(UNIT/'registration.json')
    if (UNIT/'build_complete.json').exists():raise RuntimeError('Completedbuild; no replay')
    for b in p['bindings']:
        if sha(b['path'])!=b['sha256']:
            delta=read(UNIT/'source_binding_runtime_delta.json')
            assert delta['kind']=='input_binding_metadata_only' and b==delta['original'] and bind(b['path'])==delta['repaired'],b['path']
    src=read(UNIT/'source_literal.json');raw=read(UNIT/'source_features.json')['rows']
    assert len(raw)==60*82
    cal=read(p['calendar']['path'])['calendar'];inputcal=[d for d in cal if p['input_window']['start']<=d<=p['input_window']['end']]
    save(UNIT/'input_calendar.json',{'dates':inputcal,'source':p['calendar'],'independent_reference_calendar':True})
    sources=[bind(b['path']) for b in read(OLD/'turnover_protocol.json')['sources'] if p['input_window']['start'][:7]<=Path(b['path']).name[:7]<=p['input_window']['end'][:7]]
    d={'format':'fixed60_retained_close_index_carrier_v1','symbols':p['source_symbols'],'input_window':p['input_window'],'sources':sources,
       'price_used_in_score':False,'not_remote_input_export':True};save(UNIT/'fixture_descriptor.json',d)
    frame=read_retained_close_fixture(UNIT/'fixture_descriptor.json')
    assert sorted(frame.date.unique())==inputcal
    featuremap={(x['code'],x['date']):x for x in raw};mask=[]
    for row in frame[frame.date.between(p['evaluation_window']['start'],p['evaluation_window']['end'])].itertuples():
        f=featuremap[row.symbol,row.date];a,b=f.get('net_flow'),f.get('turnover_relative')
        good=isinstance(a,(int,float)) and isinstance(b,(int,float)) and np.isfinite(a) and np.isfinite(b) and b>=0
        mask.append({'date':row.date,'symbol':row.symbol,'source_qualified':bool(good),'expected_finite':bool(good)})
    pd.DataFrame(mask).to_parquet(UNIT/'source_qualification.parquet',index=False,compression='zstd')
    evalcal=p['formation_dates'];forms=evalcal[:-6:5];assert len(forms)==16
    contract={'input_window':p['input_window'],'evaluation_window':p['evaluation_window'],'holding_end':p['evaluation_window']['end'],
       'formation_dates':forms,'warmup':{'minimum_prior_price_rows':258},'input_calendar':bind(UNIT/'input_calendar.json'),'source_qualification':bind(UNIT/'source_qualification.parquet')}
    reg=ResearchRegistry(ROOT/'research_runs/research_trials.sqlite3');out=[]
    for role in ['target','control']:
        folder=UNIT/role;folder.mkdir(exist_ok=True)
        code=render_finance_turnover_source_factor(src,p['evaluation_window'],mode=role,markets={c:'sz' if c.startswith('00') else 'sh' for c in p['source_symbols']})
        file=folder/'FactorSource.py';file.write_text(code,encoding='utf-8',newline='\n')
        registration=next(x for x in regspec['registrations'] if x['role']==role)
        c={'candidate_id':registration['candidate_id'],'code':code,'code_path':str(file),'direction':1,
           'native_preflight_purpose':'source_qualified_projection_research','native_evaluation_contract':contract,
           'official_task':'native_screen','budget_mechanism_id':'financing_turnover_support_rank60',
           'research_question':p['research_question']+' Role='+role,'decision_if_pass':p['decision_if_pass'],'decision_if_fail':p['decision_if_fail'],
           'official_information_gap':'Officialprice/cleaner basedRankIC/group report for this exact externally sourcedscore. No nativefinanceworker/PIT certification claimed.',
           'data_requirements':['formal_admission_pending','full_a_coverage_pending','official_pool_increment_pending'],
           'scope_limitations':{'external_public_source_replay':True,'native_finance_verified':False,'PIT_certified':False,'includes_old_exit_strategy':False},
           'metadata':renderer_metadata(role)}
        pf=build_native_preflight(c,p['input_window'],5,10,UNIT/'fixture_descriptor.json',
            [UNIT/'protocol.json',UNIT/'source_literal.json',UNIT/'source_features.json',UNIT/'input_calendar.json',UNIT/'source_qualification.parquet'],
            [ROOT/'panda_alpha/native_gf_transfer.py',ROOT/'panda_alpha/native_preflight.py'],folder/'preflight.json',fields=['close'],fixture_reader=read_retained_close_fixture)
        c['native_preflight_path']=str(folder/'preflight.json');save(folder/'candidate.json',c)
        if not pf.get('research_replay_passed'):raise RuntimeError('Unpaidrealpreflightfailed: '+str(pf.get('failure')))
        verify_native_preflight(c,p['input_window'],5,10,fixture_reader=read_retained_close_fixture)
        key=fingerprint(c,p['input_window'],5,10)
        plan={'role':role,'candidate':bind(folder/'candidate.json'),'code_file':bind(file),'protocol':bind(UNIT/'protocol.json'),'preflight':bind(folder/'preflight.json'),
          'window':p['input_window'],'evaluation_window':p['evaluation_window'],'cycle':5,'groups':10,'fingerprint':key,'max_new_runs':1,'reserve_estimate':8,
          'economic_identity_key':registration['economic_identity_key'],'economic_definition':registration['economic_definition'],'external_source_replay':True}
        save(folder/'batch_plan.json',plan);out.append({'role':role,'fingerprint':key,'finite':pf['proof']['finite_evaluation_rows'] if 'finite_evaluation_rows' in pf['proof'] else pf['proof'].get('finite_window_rows'),
           'preflight':bind(folder/'preflight.json')})
        reg.append_event('execution_repair',registration['economic_identity_key'],{'code_file':bind(file),'code_includes_frozen_source_literal':bind(UNIT/'source_literal.json'),
           'fingerprint':key,'same_registered_mathematical_graph_source_and_window':True,'new_contexts':0},event_id='finance-turnover-materialized:'+key)
    save(UNIT/'build_complete.json',{'at':now(),'roles':out,'input_rows':len(frame),'input_symbols':frame.symbol.nunique(),'formation_count':len(forms),
       'research_only':True,'native_finance_certified':False,'code_template_test_count':8})
    s=read(UNIT/'state.json');s.update(status='REAL_PREFLIGHT_COMPLETE_BOTH_ROLES',registry_head=reg.head);save(UNIT/'state.json',s);reg.close();print(json.dumps(out))

if __name__=='__main__':{'freeze':freeze,'build':build}[sys.argv[1]]()
