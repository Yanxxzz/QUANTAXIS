"""Save compact findings and reconcile the unique registry without pool actions."""
from datetime import datetime, timezone
import json
from pathlib import Path
from run_remaining_tests import ROOT, OUT, sha, read, write
from panda_alpha.registry import ResearchRegistry


def artifact(path):
    path=Path(path).resolve()
    return {'path':str(path),'sha256':sha(path)}


def main():
    if (OUT/'final_review.json').exists():
        raise ValueError('Completed final review exists; inspect it instead of repeating')
    summary=read(OUT/'test_summary.json')
    audit=read(OUT/'completed_test_audit.json')
    statistics=read(OUT/'admission_statistics_review.json')
    if summary['status']!='ALL_REMAINING_LOCAL_PATHS_COMPLETE' or not audit['accounting_and_metric_checks_passed']:
        raise ValueError('Complete audited paths are required')
    receipt=read(OUT/'test_input_receipt.json')
    now=datetime.now(timezone.utc).isoformat()
    pool=read(OUT/'owned_pool_snapshot.json')['content']['data']
    review={'status':'REMAINING_TESTS_COMPLETE_CURRENT_ADAPTER_NOT_FOR_LIVE_POOL',
        'completed_at_utc':now,'policy':artifact(ROOT/'config/pool-admission.v2.json'),
        'historical_gate_policy_changed':False,
        'findings':{
            'standalone_GF_net30_sharpe':1.3836303907198282,'minimum':.5,
            'direction_IC_fold_count':5,'direction_IC_fold_interpretation':'Original producer uses five contiguous mean directional RankIC signs; actual IC folds, not net-return folds',
            'scale_proxy':{'net30':.3224355291803813,'sharpe30':1.3027507464295742,
                'net50':.2824221694845801,'sharpe50':1.1715683040551141,
                'RankIC':audit['size_information']['mean_RankIC'],'certified_historical_size_vintage':False},
            'factor_diversity_proxy_max_abs_period_correlation':max(max(x['max_abs_period_pearson'],x['max_abs_period_spearman']) for x in audit['pool_factor_dependencies']),
            'pool_increment':'GF improves current6 member backcast, but addGF and replaceC16withGF both lower retained5 member net return/Sharpe and raise drawdown at both costs',
            'deployment':audit['deployment'],
            'periods':51,'minimum_periods_local_rule':200,'additional_periods_missing':149,
            'BH_q':None,'BH_status':'Missing full one-to-one registered hypothesis family p-values; raw p is not q',
            'full_target_coverage_certified':False,
            'observed_GF_source_vs_received_F_finite_coverage_min':min(r['scoped_ratio'] for r in audit['source_coverage_observations']),
            'observed_GF_source_vs_received_F_finite_coverage_max':max(r['scoped_ratio'] for r in audit['source_coverage_observations']),
            'coverage_denominator':'Received finite price-derived F peers, not certified allA historical eligible PIT universe',
            'complete_forward_validation_certified':False},
        'current_pool_snapshot':{'effective_count':pool['active_factor_count'],'pending_effective_count':pool['pending_effective_count'],
            'next_rebalance':pool['next_rebalance_at'],'modification_window':pool['modification_window']},
        'economic_scope':{'formed_GF_keys':receipt['original_GF_rows'],'shared_all_seven_member_keys':receipt['shared_pool_rows'],
            'missing_technical_keys':receipt['missing_technical_input_rows'],'formation_periods':51,'held_days':256,
            'window':'2025-08-14 formation to2026-09-03 holding end','fields':'Frozen own definitions; existing StockDB bars plus accepted proxy close, with field/runtime parity pending',
            'pool_comparison':'Fixed member versions backcast on common GF source support, not actual historical pool membership/official points',
            'candidate_and_member_signals_combined_before_targets':True,'sleeve_return_average_used':False},
        'completed_cost_paths':14,'registered_contexts_this_unit':7,'new_independent_alpha_claims':0,
        'official_runs_this_unit':0,'official_spent_this_unit':0,'pool_operations':0,
        'decision':'Do not launch this static GF adapter or claim complete eligibility. Keep standalone/size evidence; close these exact add/replace pool proposals as non-improving in this proxy scope; no weight/sign/window scan.',
        'next_valid_work':'Continuous G publication/version and usable-date pipeline, plus genuinely incremental information versus the scheduled retained pool. New source/window testing needs a separately frozen contract; cancelled2023 task remains cancelled.',
        'evidence':{name:artifact(OUT/file) for name,file in {
            'protocol':'test_protocol.json','summary':'test_summary.json','audit':'completed_test_audit.json',
            'statistical_review':'admission_statistics_review.json','own_snapshot':'owned_pool_snapshot.json',
            'definition_receipts':'definition_receipts.json','input_receipt':'test_input_receipt.json',
            'target_receipt':'test_target_receipt.json','ledger':'test_daily_net_ledger.parquet'}.items()}}
    write(OUT/'final_review.json',review)
    registry=ResearchRegistry(ROOT/'research_runs/research_trials.sqlite3')
    registry.append_event('evaluation','GF_REMAINING_ADMISSION_TESTS_20261012',{
        'status':review['status'],'review':artifact(OUT/'final_review.json'),
        'new_contexts':0,'all14_cost_paths_complete':True,'no_partial_result_tuning':True,
        'formal_admission':False,'official_runs':0,'pool_operations':0},
        event_id='gf-admission-complete:'+sha(OUT/'final_review.json'))
    state=read(OUT.parent/'state.json')
    state.update(status=review['status'],active_unit=None,actual_pids=[],updated_at_utc=now,
        research_total=registry.total,registry_head=registry.head,formal_admission=False,
        new_economic_contexts=13,admission_completion=artifact(OUT/'final_review.json'),
        next_unit=review['next_valid_work'],latest_human_instruction='完成后面的测试，然后我会正式上线',
        completed_remaining_tests_at_utc=now,full_pool_proxy_tests_complete=True,
        full_actual_pool_increment_verified=False,live_adapter_ready=False)
    write(OUT.parent/'state.json',state)
    memory=read(ROOT/'research_runs/live_memory.json')
    # Reconcile authoritative cumulative counts; keep all cold/source/install records.
    memory['multiple_testing_denominator']=registry.total
    memory['multiple_testing_total']=registry.total
    memory['denominator_latest'].update(historical_baseline=registry.historical,
        unique_registered=registry.new_tested,new_unique=registry.new_tested,total=registry.total,
        registry_head=registry.head,updated_at_utc=now)
    memory['research_registry'].update(head=registry.head,total=registry.total,unique=registry.new_tested,
        historical=registry.historical,unique_registered=registry.new_tested)
    memory['latest_GF_resume_20261012']=state
    memory['latest_GF_remaining_admission_tests_20261012']={
        'status':review['status'],'review':artifact(OUT/'final_review.json'),
        'new_contexts':7,'research_total':registry.total,'official_spent':0,
        'standalone_GF_retained':True,'planned_retained_pool_increment_proxy':'non-improving at30/50bp',
        'live_adapter_ready':False,'formal_admission':False,'pool_operations':0}
    write(ROOT/'research_runs/live_memory.json',memory)
    registry.db.close()
    print(json.dumps({'status':review['status'],'research_total':state['research_total'],'new_contexts':7,'official_spent':0,'formal_admission':False},ensure_ascii=False))


if __name__=='__main__':main()
