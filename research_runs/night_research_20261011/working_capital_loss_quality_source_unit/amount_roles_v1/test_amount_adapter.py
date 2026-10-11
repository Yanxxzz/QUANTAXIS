"""Synthetic exact-cell/percentage/blank/combined-role contracts only."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from amount_adapter import table_facts,sha


def fixture():
    header='类别 期末余额 账面余额 坏账准备 账面价值 金额 比例 金额 计提比例'
    row='合计 1,2\n00.00 100.00% 12.00 1.00% 1,188.00'
    text='整体应收账款分类披露\n单位：元\n（除特别注明外，金额单位为人民币元）\n'+header+'\n'+row+'\n'
    def ref(value):
        start=text.index(value);return {'start':start,'end':start+len(value),'sha256':sha(value.encode())}
    tokens=['1,200.00','100.00%','12.00','1.00%','1,188.00'];raw=['1,2\n00.00',*tokens[1:]]
    cells=[{**ref(original),'printed':printed} for original,printed in zip(raw,tokens)]
    table={'table_id':'synthetic_AR_closing','status':'EXACT_NATIVE_ROLES_ANNOTATED','asset':'AR','kind':'stock','period_role':'closing',
           'printed_period_label':'期末余额','literal_date_printed':None,'period_context':{'annual_year':2025,'derived_fiscal_role_date':'2025-12-31'},
           'header_span':ref(header),'required_header_literals':['期末余额','账面余额','坏账准备','账面价值'],
           'row_span':ref(row),'cell_spans':cells,'expected_tokens':tokens,'role_indices':{'gross':0,'allowance_disclosed':2,'net':4},
           'original_role_labels':{'gross':'账面余额','allowance_disclosed':'坏账准备','net':'账面价值'},'percentage_indices':[1,3],
           'population_span':ref('整体应收账款分类披露'),'population_kind':'whole_AR_classification_total',
           'unit_contract':{'status':'SOURCE_CNY_UNIT_BOUND','currency':'CNY','unit':'元','span':ref('（除特别注明外，金额单位为人民币元）'),
                            'required_literal':'金额单位为人民币元','local_unit_span':ref('单位：元'),'table_specific_currency_override_absent_reviewed':True}}
    return text,table


def test_exact_single_cell_wrap_preserves_percent_positions_and_printed_period_role():
    text,t=fixture();result=table_facts(text,t)
    assert result['cells']['gross']['amount_yuan']=='1200.00'
    assert [r['original_token_index'] for r in result['percentage_columns_retained']]==[1,3]
    assert result['cells']['net']['original_token_index']==4
    assert result['gross_allowance_net_reconciliation']['status']=='source_display_intervals_overlap'
    assert result['literal_date_printed'] is None and result['financial_eligibility'] is False


def test_unbound_currency_retains_exact_stock_facts_but_no_yuan_normalization():
    text,t=fixture();t['unit_contract']={'status':'pending'}
    r=table_facts(text,t)
    assert r['cells']['gross']['amount_printed_unit']=='1200.00' and r['cells']['gross']['amount_yuan'] is None
    assert r['unit_status']=='unit_or_currency_pending'


def test_movement_combined_label_and_empty_other_remain_unknown_not_cash_or_zero():
    text,t=fixture();new_header=text[t['header_span']['start']:t['header_span']['end']]+' 期初余额 收回或转回 其他'
    start=t['header_span']['start'];end=t['header_span']['end'];delta=len(new_header)-(end-start)
    text=text[:start]+new_header+text[end:];t['header_span']={'start':start,'end':start+len(new_header),'sha256':sha(new_header.encode())}
    for ref in [t['row_span'],*t['cell_spans']]:ref['start']+=delta;ref['end']+=delta
    t['row_span']['sha256']=sha(text[t['row_span']['start']:t['row_span']['end']].encode())
    t.update(kind='movement',role_indices={'opening':0,'recovery_or_reversal':2,'closing':4},blank_roles=['other'],percentage_indices=[1,3],
        original_role_labels={'opening':'期初余额','recovery_or_reversal':'收回或转回','closing':'期末余额','other':'其他'},
        combined_role_semantics={'recovery_or_reversal':'Originalcombinedsourceword;no cash/reversal split'})
    r=table_facts(text,t)
    assert r['cells']['other']['amount_yuan'] is None and r['cells']['other']['printed'] is None
    assert r['cells']['recovery_or_reversal']['original_header_role']=='收回或转回'
    assert r['cause_components_split'] is False and r['movement_rollforward_status'].startswith('partial')


def test_bad_role_index_or_foreign_period_cannot_be_guessed():
    text,t=fixture();t['role_indices']['gross']=1
    with pytest.raises(ValueError,match='overlap'):table_facts(text,t)
    text,t=fixture();t['printed_period_label']='期初余额'
    with pytest.raises(ValueError,match='closing_opening'):table_facts(text,t)


def test_unknown_sparse_annotation_does_not_become_zero_or_drop_other_stock():
    text,t=fixture();pending={'table_id':'sparse_INV_opening','status':'pending','pending_reason':'5tokens_for6roles_position_unknown'}
    assert table_facts(text,pending)['status']=='source_role_annotation_pending'
    assert table_facts(text,t)['cells']['net']['amount_yuan']=='1188.00'


def test_sparse_printed_tokens_stay_unmapped_and_cannot_fill_missing_opening_allowance():
    text,t=fixture();t.update(status='pending',pending_reason='5printedcells_for6stockroles_column_position_unknown')
    r=table_facts(text,t)
    assert r['unmapped_printed_tokens']==t['expected_tokens'] and 'cells' not in r
    assert r['missing_cell_zero_imputation'] is False and r['financial_eligibility'] is False
