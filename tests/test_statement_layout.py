"""Physical cells, rather than the magnitude of amounts, bind fiscal columns."""
import pytest
import base64, gzip, hashlib, json
from pathlib import Path

from panda_alpha.statement_layout import monetary_grid_pair


def grid(*, current="4,335,202,621.\n92", prior="3,523,285,791.68",
         headers=None, label="其中：营业收入", kind="income", period="2025-12-31",
         scope="consolidated", unit="元"):
    rows = [headers or ["项目", "附注", "2025年度", "2024年度"],
            [label, "七、32", current, prior]]
    boxes = [[(10 + i * 100, 10 + j * 30, 100 + i * 100, 35 + j * 30)
              for i in range(4)] for j in range(2)]
    return monetary_grid_pair(rows, boxes, label="营业收入", table_kind=kind,
                              period=period, statement_scope=scope,
                              unit_evidence={"status": "unit_verified", "currency": "CNY",
                                             "printed_unit": unit})


@pytest.mark.parametrize("current,prior", [
    ("4,335,202,621.\n92", "3,523,285,791.68"),
    ("22,246,423,224\n.05", "19,759,215,060.\n69"),
    ("7,486,062,467.\n65", "8,226,716,564.40"),
])
def test_wrapped_amounts_recover_only_within_physical_year_cells(current, prior):
    result = grid(current=current, prior=prior)
    assert result["status"] == "physical_grid_columns_bound"
    assert result["current_printed"] == current.replace("\n", "")
    assert result["comparative_printed"] == prior.replace("\n", "")
    assert result["raw_current_cell"] == current
    assert result["current_bbox"] != result["comparative_bbox"]
    assert result["period_evidence"]["comparative_start"] == "2024-01-01"


def test_small_real_amount_and_display_unit_are_preserved():
    result = grid(current="100", prior="2", unit="百万元")
    assert result["current_yuan"] == "100000000"
    assert result["comparative_yuan"] == "2000000"


def test_reordered_columns_use_explicit_dates():
    result = grid(current="2", prior="100", headers=["项目", "附注", "2024年度", "2025年度"])
    assert result["current_yuan"] == "100"
    assert result["comparative_yuan"] == "2"


@pytest.mark.parametrize("kwargs", [
    {"current": "100\n200"},
    {"current": "100 200"},
    {"current": "100.00\n200.00"},
    {"current": "12,34.00"},
    {"current": "100."},
    {"prior": "—"},
    {"prior": None},
    {"scope": "parent"},
    {"unit": "USD"},
    {"label": "营业总收入"},
    {"headers": ["项目", "附注", "本年金额", "上年金额"]},
    {"headers": ["项目", "附注", "2025年度", "2023年度"]},
    {"headers": ["项目", "附注", "2025半年度", "2024年度"], "period": "2025-06-30"},
    {"headers": ["项目", "附注", "2025年度", "2024年度（调整前）/2024年度（调整后）"]},
])
def test_ambiguous_layout_or_source_contract_stays_pending(kwargs):
    result = grid(**kwargs)
    assert result["status"] != "physical_grid_columns_bound"
    assert "current_yuan" not in result


def test_negative_and_explicit_zero_are_values_not_missing():
    result = grid(current="(100.00)", prior="0.00")
    assert result["current_yuan"] == "-100.00"
    assert result["comparative_yuan"] == "0.00"


def test_duplicate_exact_rows_cannot_be_selected_by_amount():
    rows = [["项目", "2025年度", "2024年度"], ["营业收入", "100", "2"], ["营业收入", "101", "3"]]
    boxes = [[(i * 100, j * 30, i * 100 + 90, j * 30 + 25) for i in range(3)] for j in range(3)]
    result = monetary_grid_pair(rows, boxes, label="营业收入", table_kind="income",
                               period="2025-12-31", statement_scope="consolidated",
                               unit_evidence={"status": "unit_verified", "currency": "CNY", "printed_unit": "元"})
    assert result["status"] == "grid_exact_row_missing_or_ambiguous"


def test_stock_cells_require_explicit_current_and_opening_dates():
    result = grid(headers=["项目", "附注", "2025年12月31日", "2024年12月31日"], kind="balance")
    assert result["status"] == "physical_grid_columns_bound"
    assert result["period_evidence"]["comparative_stock_asof"] == "2024-12-31"


def test_generic_stock_roles_require_the_same_original_period_proof():
    rows = [["项目", "期末余额", "期初余额"], ["应付账款", "254\n,165\n,206.20", "100.00"]]
    boxes = [[(i*100,j*40,i*100+90,j*40+35) for i in range(3)] for j in range(2)]
    options = dict(label='应付账款',table_kind='balance',period='2024-12-31',statement_scope='consolidated',
                   unit_evidence={'status':'unit_verified','currency':'CNY','printed_unit':'元'})
    assert monetary_grid_pair(rows,boxes,**options)['status'] == 'grid_period_columns_pending'
    proof = {'status':'same_report_stock_columns_bound','current_stock_asof':'2024-12-31','opening_stock_asof':'2024-01-01'}
    result = monetary_grid_pair(rows,boxes,stock_periods=proof,**options)
    assert result['status'] == 'physical_grid_columns_bound'
    assert result['current_yuan'] == '254165206.20'
    assert result['period_evidence']['comparative_stock_asof'] == '2024-01-01'
    proof['current_stock_asof']='2025-12-31'
    assert monetary_grid_pair(rows,boxes,stock_periods=proof,**options)['status'] == 'grid_period_columns_pending'


def test_corrupt_pdf_is_pending_instead_of_crashing_parser(tmp_path):
    from panda_alpha.statement_layout import original_pdf_pair
    data, provenance = original_fixture(tmp_path)
    corrupt = Path(provenance['pdf_path']); corrupt.write_bytes(b'Not a PDF')
    provenance['pdf_sha256'] = hashlib.sha256(corrupt.read_bytes()).hexdigest()
    result = original_pdf_pair(data['native_text'],provenance,label='营业收入',table_kind='income',period='2025-12-31')
    assert result['status'] == 'original_layout_pdf_unreadable'


def test_missing_or_overlapping_cell_coordinates_do_not_prove_two_columns():
    rows = [["项目", "2025年度", "2024年度"], ["营业收入", "100", "2"]]
    for boxes in ([[None, None, None]] * 2,
                  [[(0, 0, 90, 20)] * 3, [(0, 30, 90, 50)] * 3]):
        result = monetary_grid_pair(rows, boxes, label="营业收入", table_kind="income",
                                   period="2025-12-31", statement_scope="consolidated",
                                   unit_evidence={"status": "unit_verified", "currency": "CNY", "printed_unit": "元"})
        assert result["status"] != "physical_grid_columns_bound"


def original_fixture(tmp_path, name='statement_layout_original.json'):
    data = json.loads((Path(__file__).parent/'fixtures'/name).read_text(encoding='utf-8'))
    pdf = tmp_path/'original.pdf'; pdf.write_bytes(base64.b64decode(data['pdf_base64']))
    native = tmp_path/'original.txt.gz'; native.write_bytes(gzip.compress(data['native_text'].encode('utf-8')))
    provenance = {'pdf_path':str(pdf),'text_path':str(native),
                  'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
                  'text_sha256':hashlib.sha256(native.read_bytes()).hexdigest(),
                  'pdf_hash_reverified':True,'text_hash_reverified':True}
    return data, provenance


def test_blank_margin_columns_and_merged_amount_cells_use_coordinates():
    rows = [['','项目','','2025年度','','2024年度',''],
            ['','营业收入','100.00',None,'2.00',None,None]]
    header = [(0,0,5,25),(5,0,100,25),(100,0,110,25),(110,0,200,25),
              (200,0,210,25),(210,0,300,25),(300,0,305,25)]
    body = [(0,30,5,55),(5,30,100,55),(100,30,205,55),None,(205,30,305,55),None,None]
    result = monetary_grid_pair(rows,[header,body],label='营业收入',table_kind='income',period='2025-12-31',
        statement_scope='consolidated',unit_evidence={'status':'unit_verified','currency':'CNY','printed_unit':'元'})
    assert result['status'] == 'physical_grid_columns_bound'
    assert result['amount_column_indices'] == [2,4]
    assert result['current_yuan'] == '100.00'
    assert result['comparative_yuan'] == '2.00'


def test_original_continuation_uses_same_statement_header_and_grid_edges(tmp_path):
    from panda_alpha.statement_layout import original_pdf_pair
    data, provenance = original_fixture(tmp_path,'statement_layout_continuation.json')
    result = original_pdf_pair(data['native_text'],provenance,label='应付账款',table_kind='balance',
                               period='2024-12-31',source_page_hint=2)
    assert result['status'] == 'physical_grid_columns_bound'
    assert result['current_yuan'] == '254165206.73'
    assert result['comparative_yuan'] == '178046909.06'
    assert result['header_source_page'] == 1
    assert result['source_page'] == 2


def test_parent_scope_breaks_continuation_before_amount_recovery(tmp_path):
    from panda_alpha.statement_layout import original_pdf_pair
    data, provenance = original_fixture(tmp_path,'statement_layout_continuation.json')
    native = data['native_text'].replace('===SOURCE_PAGE:2===','===SOURCE_PAGE:2===\n母公司资产负债表\n')
    raw = gzip.compress(native.encode('utf-8'));Path(provenance['text_path']).write_bytes(raw)
    provenance['text_sha256']=hashlib.sha256(raw).hexdigest()
    result = original_pdf_pair(native,provenance,label='应付账款',table_kind='balance',period='2024-12-31',source_page_hint=2)
    assert result['status'] != 'physical_grid_columns_bound'


def test_original_pdf_to_revenue_attachment_recovers_amount_above_label(tmp_path):
    from panda_alpha.trade_efficiency import attach_annual_revenue
    data, provenance = original_fixture(tmp_path)
    metadata = data['metadata']
    stock = {'code':metadata['code'],'report_date':metadata['report_date'],
             'announcement_id':metadata['announcement_id'],'available_date':'2026-04-02',
             'pdf_sha256':provenance['pdf_sha256'],'provenance':provenance,'record_sha256':'a'*64}
    result = attach_annual_revenue(stock, data['native_text'], metadata, provenance)
    assert result['status'] == 'strict_annual_revenue_verified'
    assert result['values'] == {'current':data['expected_current'],'prior':data['expected_prior']}
    assert result['field_evidence']['operating_revenue']['column_parse_evidence']['status'] == 'physical_grid_columns_bound'
    assert not result['full_pit_certified']


def test_legacy_cost_attachment_recovers_from_same_original_not_cached_values(tmp_path):
    from panda_alpha.component_efficiency import attach_component_cost
    data, provenance = original_fixture(tmp_path)
    meta=data['metadata'];native=data['native_text'];line='营业成本 100.00 2.00'
    stock={**meta,'available_date':'2026-04-02','record_sha256':'a'*64,'pdf_sha256':provenance['pdf_sha256'],'provenance':provenance}
    revenue={**meta,'record_sha256':'b'*64,'parent_stock_record_sha256':stock['record_sha256'],
             'pdf_sha256':provenance['pdf_sha256'],'text_sha256':provenance['text_sha256']}
    candidate={'source_label':line,'source_cells':['100','02'],'column_source':'100.\n02','source_page':1}
    header='单位：元\n项目 2025年度 2024年度'
    boundary={**candidate,'actual_income_header':header,'status':'source_row_inside_bounded_consolidated_income',
        'statement_scope':'consolidated','table_type':'income','pdf_sha256':provenance['pdf_sha256'],
        'text_sha256':provenance['text_sha256'],'announcement_id':meta['announcement_id'],
        'statement_start_offset':native.index('合并利润表'),'statement_end_offset':native.index('母公司利润表'),
        'source_row_offset':native.index(line)}
    source={**meta,'stock_record_sha256':stock['record_sha256'],'pdf_sha256':provenance['pdf_sha256'],
        'text_sha256':provenance['text_sha256'],'actual_income_header':header,
        'bounded_consolidated_income_proof':boundary,'cost_candidates':[candidate]}
    result=attach_component_cost(stock,revenue,source)
    assert result['status']=='strict_annual_cost_verified'
    assert result['values']=={'current':'100.00','prior':'2.00'}
    assert result['field_evidence']['operating_cost']['original_column_source']=='100.\n02'


def test_ambiguous_adjacent_amount_stays_pending_without_original_layout(tmp_path):
    from panda_alpha.financial_statements import resolve_statement_columns
    data, provenance = original_fixture(tmp_path)
    cells, _, proof = resolve_statement_columns('七、32 3,523,285,791.68', ['92'],
        text=data['native_text'], provenance={}, label='营业收入',table_kind='income',
        period='2025-12-31',source_page_hint=1)
    assert cells == []
    assert proof['status'] == 'original_layout_paths_pending'


@pytest.mark.parametrize('mutation',['pdf','native','passed_text','page'])
def test_original_pdf_binding_cannot_accept_changed_bytes_or_text(tmp_path, mutation):
    from panda_alpha.statement_layout import original_pdf_pair
    data, provenance = original_fixture(tmp_path)
    text = data['native_text']; page = 1
    if mutation == 'pdf':Path(provenance['pdf_path']).write_bytes(b'changed original')
    if mutation == 'native':Path(provenance['text_path']).write_bytes(b'changed native')
    if mutation == 'passed_text':text = text.replace('单位：元','单位：万元')
    if mutation == 'page':page = 2
    result = original_pdf_pair(text,provenance,label='营业收入',table_kind='income',
                               period='2025-12-31',source_page_hint=page)
    assert result['status'] != 'physical_grid_columns_bound'
    assert 'current_yuan' not in result
