"""Synthetic exact nested-item contracts; no retained native body reads."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from review_named_item import locate_item,consume_one,sha


def fixture(body=None):
    text=('===SOURCE_PAGE:1===\n4、应收账款\n（3） 按坏账计提方法分类披露\n（a）单项计提\n说明\n'
        '（b）应收账款预期信用损失的评估：\n'+(body or '本集团按照整个存续期内预期信用损失计量应收账款。\n')+
        '===SOURCE_PAGE:2===\n依据历史信用损失经验及经济状况进行前瞻性判断。\n（4） 本期坏账准备情况\n5、应收款项融资\n')
    def span(s,end=None):
        a=text.index(s);b=a+len(s) if end is None else text.index(end)
        return {'start':a,'end':b,'span_sha256':sha(text[a:b].encode())}
    c={'AR_note':span('4、应收账款','5、应收款项融资'),
       'enclosing_parent':span('（3） 按坏账计提方法分类披露'),
       'next_enclosing_sibling':span('（4） 本期坏账准备情况'),
       'named_item':span('（b）应收账款预期信用损失的评估：')}
    return text,c


def test_last_b_item_closes_only_at_bound_enclosing_sibling_across_complete_pages():
    text,c=fixture();r=locate_item(text,c)
    assert r['status']=='NAMED_AR_ITEM_COMPLETE_BOUNDARY_SHORT_METHOD_INVENTORY_ONLY'
    assert r['retained_contiguous_pages']==[1,2] and r['item']['end']==c['next_enclosing_sibling']['start']
    assert any(e['kind']=='lifetime_ECL' for e in r['short_method_evidence'])
    assert r['AR_measurement_or_financial_eligibility_cleared'] is False


def test_duplicate_or_skipped_letter_scope_stays_pending():
    text,c=fixture('（d）不明层级\n');assert locate_item(text,c)['status']=='NAMED_AR_ITEM_BOUNDARY_PENDING'
    text,c=fixture('（b）重复子项\n');assert locate_item(text,c)['status']=='NAMED_AR_ITEM_BOUNDARY_PENDING'


def test_unknown_numbered_heading_cannot_be_keyword_chapter_ending():
    text,c=fixture('八、研发支出\n')
    assert locate_item(text,c)['reason']=='unclassified_numbered_heading_inside_item'


def test_unknown_uppercase_letter_heading_cannot_be_swallowed_or_normalized_to_c():
    text,c=fixture('（C）另一不明同级事项\n')
    r=locate_item(text,c)
    assert r['status']=='NAMED_AR_ITEM_BOUNDARY_PENDING'
    assert r['reason']=='unclassified_parenthesized_alphabet_heading_inside_item'


def test_normal_forward_credit_judgment_is_inventory_not_regime_block_or_negative_certificate():
    text,c=fixture();r=locate_item(text,c)
    assert r['normal_forward_credit_judgment_is_automatic_regime_block'] is False
    assert r['missing_change_words_or_ECL_negative_clears_measurement'] is False
    assert r['current_opening_method_applicability']=='unreviewed_pending'
    assert all(e['fragment_only_not_complete_effect_or_period_proof'] for e in r['short_method_evidence'])


def test_unexpected_reader_error_has_fixed_KEY_pending_receipt_without_automatic_resume():
    case={'code':'000403','announcement_id':'1225245931','report_date':'2025-12-31','source_record_sha256':'synthetic'}
    def reader(case):raise RuntimeError('synthetic_complete_capture_binding_error')
    r=consume_one(case,reader,{},lambda:False)
    assert r['records']==[] and r['errors'][0]['error_type']=='RuntimeError'
    assert r['resume_position']['failed_report_key']==case and r['resume_position']['automatic_resume'] is False
