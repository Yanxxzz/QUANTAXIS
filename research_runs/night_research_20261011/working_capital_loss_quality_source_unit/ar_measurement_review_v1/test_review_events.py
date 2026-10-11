"""Synthetic inventory semantics; no actual report/body/score reads."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent))
from review_events import inventory,impact_inventory,consume_cases


def test_explicit_negative_same_section_is_inventory_not_AR_ready():
    text='===SOURCE_PAGE:1===\n重要会计政策变更\n□适用 ☑不适用\n重要会计估计变更\n□适用 ☑不适用\n前期会计差错更正\n无\n'
    r=inventory(text,{})
    policy=next(e for e in r['events'] if e['kind']=='accounting_policy')
    assert policy['choice_inventory']=='explicit_negative_choice_inventory_only'
    assert r['AR_source_ready_certified'] is False and policy['AR_fields_resolved']==[]
    assert policy['source_page']==1 and len(policy['span_sha256'])==64


def test_negative_in_later_section_cannot_clear_positive_policy_scope():
    text='重要会计政策变更\n☑适用 □不适用\n本期修改收入条款。\n重要会计估计变更\n□适用 ☑不适用\n'
    r=inventory(text,{})
    p=next(e for e in r['events'] if e['kind']=='accounting_policy')
    assert p['choice_inventory']!='explicit_negative_choice_inventory_only'
    assert p['impact_state']=='field_effect_unresolved_pending'


def test_composition_is_competing_explanation_not_automatic_measurement_break():
    assert impact_inventory('本年收购子公司，客户结构发生变化。')=='state_composition_event_not_automatic_measurement_block'
    r=inventory('合并范围的变更\n本年并购子公司。\n研发支出\n说明\n',{})
    assert r['composition_events_automatically_blocked'] is False
    assert r['cash_unknown_used_as_gate'] is False


def test_AR_method_or_classification_change_is_pending_and_normal_definition_is_not_change():
    assert impact_inventory('本年度调整应收账款预期信用损失计提方法与组合划分。')=='AR_measurement_change_phrase_scope_pending'
    assert impact_inventory('应收账款坏账准备采用预期信用损失，按信用风险组合计量。')=='AR_method_or_group_definition_inventory_unreviewed'
    r=inventory('应收账款预期信用损失计量方法本年未发生变化。\n',{})
    assert r['AR_source_ready_certified'] is False
    assert all(event['AR_fields_resolved']==[] for event in r['events'])


def test_emptyflags_oldready_and_revision_title_do_not_certify_field_scope():
    r=inventory('',{'flags':[],'G_ready':True,'income_ready':True,'known_revision_or_later_report_metadata':[{'title':'年度报告更正公告'}]})
    assert r['source_flags_empty_is_AR_ready'] is False and r['existing_G_trade_or_income_ready_copied'] is False
    assert 'known_revision_or_later_report_body_effect_scope_unreviewed' in r['specific_gaps']


def test_second_reader_error_preserves_first_record_and_exact_resume_KEY(monkeypatch):
    cases=[{'code':code,'announcement_id':code+'a','report_date':'2025-12-31',
            'source_record_sha256':code+'sha','source_index_binding_key':'index'} for code in ['000001','000002','000003']]
    reader=SimpleNamespace(read_case=None);attempted=[]
    def fake_read(case,db,indexes):
        attempted.append(case['code'])
        if case['code']=='000002':raise RuntimeError('synthetic_capture_metadata_mismatch')
        return {},{},'重要会计政策变更\n□适用 ☑不适用\n'
    monkeypatch.setattr(reader,'read_case',fake_read)
    result=consume_cases(cases,reader,None,{}, {'index':{'path':'synthetic_index'}})
    assert attempted==['000001','000002'] and len(result['records'])==1
    assert result['records'][0]['source_identity']['code']=='000001'
    assert result['errors'][0]['error_type']=='RuntimeError'
    assert result['errors'][0]['status']=='SOURCE_CASE_EXCEPTION_PENDING_NOT_ECONOMIC_FAILURE'
    assert result['resume_position']['failed_report_key']==result['errors'][0]['source_identity']
    assert result['resume_position']['failed_case_index']==1 and result['resume_position']['completed_record_count']==1
    assert result['resume_position']['unattempted_case_count']==1 and result['resume_position']['automatic_resume'] is False
