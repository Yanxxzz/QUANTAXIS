"""Synthetic native note roles and ambiguity regressions; no real reports."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from consumer import consume_text,STOCK_HEADERS,ROLL_HEADERS


CASE={"code":"000001","announcement_id":"synthetic","report_date":"2025-12-31","source_record_sha256":"a"*64,"pdf_sha256":"b"*64,"retained_text_sha256":"c"*64,"edition":"ORIGINAL","original_metadata_flags":[]}


def fixture(stock="100.00 10.00 90.00 120.00 8.00 112.00",roll="8.00 5.00 1.00 2.00 0.00 0.00 10.00"):
    parts=["===SOURCE_PAGE:1===\n测试公司2025年年度报告\n===SOURCE_PAGE:2===\n七、合并财务报表项目注释"]
    for number,kind,title in [(3,"AR","应收账款"),(4,"INV","存货")]:
        parts += [str(number)+"、"+title,"单位：元 币种：人民币",STOCK_HEADERS[kind],"合计 "+stock,
                  "坏账准备的情况" if kind=="AR" else "存货跌价准备","单位：元 币种：人民币",ROLL_HEADERS[kind],"合计 "+roll]
    parts += ["5、其他流动资产","八、合并范围的变更"]
    return "\n".join(parts)+"\n"


def test_same_table_gross_provision_net_and_explicit_precision_roles():
    result=consume_text(fixture(),CASE)
    for kind in ["AR","INV"]:
        row=result['source_fact_tables'][kind]['tables']['stock']
        assert row['status']=='printed_amount_facts_bound' and row['currency']=='CNY'
        assert row['facts']['closing_gross']['amount_yuan']=='100.00'
        assert row['facts']['closing_gross']['quantum_yuan']=='0.01'
        assert row['opening_stock_asof']=='2025-01-01' and row['opening_literal_date_printed'] is False
        assert all(v['status']=='source_display_intervals_overlap' for v in row['source_reconciliations'].values())
        assert row['row']['source_page']==2 and len(row['row']['span_sha256'])==64
    assert 'net_decline_cause_and_actual_cash_release_unverified' in result['pending_reasons']
    assert result['empty_flags_mean_clean'] is False


def test_AR_writeoff_and_inventory_transfer_out_have_distinct_unclassified_semantics():
    result=consume_text(fixture(),CASE)
    ar=result['source_fact_tables']['AR']['tables']['roll']
    inv=result['source_fact_tables']['INV']['tables']['roll']
    assert 'writeoff' in ar['facts'] and 'inventory_disposal_or_consumption' in inv['facts']
    for row in [ar,inv]:
        assert row['source_reconciliations']['provision_rollforward']['status']=='source_display_intervals_overlap'
        assert row['cash_collection_or_true_inventory_release_certified'] is False
        assert row['inventory_transfer_out_classified_as_bad'] is False


@pytest.mark.parametrize('bad',['8.00 5.00 — 2.00 0.00 0.00 10.00','8.00 5.00 2.00 10.00','8.00 -5.00 1.00 2.00 0.00 0.00 0.00'])
def test_missing_blank_or_negative_movement_is_not_imputed_or_direction_guessed(bad):
    result=consume_text(fixture(roll=bad),CASE)
    assert result['source_fact_tables']['AR']['tables']['roll']['status']=='pending'


def test_arithmetic_conflict_is_retained_and_not_called_release():
    result=consume_text(fixture(stock='100.00 10.00 85.00 120.00 8.00 112.00'),CASE)
    assert result['source_fact_tables']['AR']['tables']['stock']['source_reconciliations']['closing']['status']=='source_arithmetic_conflict_pending'
    assert 'AR_source_arithmetic_conflict_pending' in result['pending_reasons']


def test_other_scope_duplicate_rows_unknown_currency_and_foreign_year_stay_pending():
    text=fixture().replace('七、合并财务报表项目注释','七、母公司财务报表项目注释')
    assert not consume_text(text,CASE)['source_fact_tables']
    text=fixture().replace('合计 100.00 10.00 90.00 120.00 8.00 112.00','合计 100.00 10.00 90.00 120.00 8.00 112.00\n合计 100.00 10.00 90.00 120.00 8.00 112.00',1)
    assert consume_text(text,CASE)['source_fact_tables']['AR']['tables']['stock']['status']=='pending'
    assert consume_text(fixture().replace(' 币种：人民币',''),CASE)['source_fact_tables']['AR']['tables']['stock']['status']=='pending'
    with pytest.raises(ValueError,match='current_annual_year'):
        consume_text(fixture().replace('2025年年度报告','2024年年度报告'),CASE)


def test_percent_columns_and_combined_recovery_reversal_are_not_silently_retyped():
    text=fixture().replace(STOCK_HEADERS['AR'],STOCK_HEADERS['AR']+'比例%').replace('本期转回本期核销','收回或转回本期核销',1)
    result=consume_text(text,CASE)
    assert result['source_fact_tables']['AR']['tables']['stock']['status']=='pending'
    assert result['source_fact_tables']['AR']['tables']['roll']['status']=='pending'
    assert result['source_fact_tables']['AR']['tables']['stock']['uninterpreted_native_header_clues'][0]['brief_text']==STOCK_HEADERS['AR']+'比例%'


def test_explicit_foreign_table_year_is_pending_but_year_shaped_amount_is_money():
    result=consume_text(fixture(stock='2,024.00 10.00 2,014.00 2,100.00 8.00 2,092.00'),CASE)
    assert result['source_fact_tables']['AR']['tables']['roll']['status']=='printed_amount_facts_bound'
    text=fixture().replace('3、应收账款\n单位','3、应收账款\n2024年度\n单位',1)
    assert consume_text(text,CASE)['source_fact_tables']['AR']['tables']['stock']['reason']=='table_printed_year_conflict_pending'


def test_unknown_numbered_note_never_supplies_its_table_to_inventory():
    text=fixture().replace('4、存货\n','4、存货\n暂未列示\n5、未知资产\n',1)
    result=consume_text(text,CASE)
    assert all(table['status']=='pending' for table in result['source_fact_tables']['INV']['tables'].values())


@pytest.mark.parametrize('caption',['2025年6月30日','2025年3月31日','2024-12-31','2025-12-31'])
def test_explicit_unmapped_dates_never_become_FY_closing_or_Jan1_opening(caption):
    text=fixture().replace('3、应收账款\n单位','3、应收账款\n'+caption+'\n单位',1)
    row=consume_text(text,CASE)['source_fact_tables']['AR']['tables']['stock']
    assert row['status']=='pending' and 'date_or_nonannual' in row['reason']


@pytest.mark.parametrize('unit',['单位：人民币元 币种：美元','单位：元 币种：人民币及美元'])
def test_currency_prefix_does_not_override_foreign_or_mixed_declaration(unit):
    text=fixture().replace('单位：元 币种：人民币',unit,1)
    assert consume_text(text,CASE)['source_fact_tables']['AR']['tables']['stock']['reason']=='table_local_CNY_unit_pending'


@pytest.mark.parametrize('caption',['（1）按组合计提坏账准备','按单项计提坏账准备','某局部类别'])
def test_partial_or_unknown_population_total_is_not_whole_receivables(caption):
    text=fixture().replace('3、应收账款\n单位','3、应收账款\n'+caption+'\n单位',1)
    row=consume_text(text,CASE)['source_fact_tables']['AR']['tables']['stock']
    assert row['status']=='pending'
    assert 'facts' not in row


def test_spaced_nonannual_date_cannot_hide_behind_valid_whole_asset_caption():
    text=fixture().replace('3、应收账款\n单位','3、应收账款\n2025 年 6 月 30 日\n应收账款分类披露\n单位',1)
    row=consume_text(text,CASE)['source_fact_tables']['AR']['tables']['stock']
    assert row['reason']=='table_explicit_date_or_nonannual_period_mapping_pending'


@pytest.mark.parametrize('role',['stock','roll'])
def test_generic_valid_caption_never_resets_known_partial_ancestor(role):
    text=fixture()
    if role=='stock':
        text=text.replace('3、应收账款\n单位','3、应收账款\n按组合计提坏账准备\n应收账款分类披露\n单位',1)
    else:
        text=text.replace('坏账准备的情况','按单项计提坏账准备\n坏账准备的情况',1)
    row=consume_text(text,CASE)['source_fact_tables']['AR']['tables'][role]
    assert row['reason']=='known_partial_population_ancestor_scope_pending' and 'facts' not in row
