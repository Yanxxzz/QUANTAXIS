import copy
import pytest

from panda_alpha.trade_accrual import parse_trade_accrual_report, trade_accrual_score
from panda_alpha.trade_efficiency import attach_annual_revenue, trade_efficiency_score
from panda_alpha.component_efficiency import (
    attach_component_cost, component_efficiency_score, select_component_efficiency_asof,
)


def fixture(year=2024, published="2025-04-19", header=None, cells=None):
    header = header or f"单位：元\n项目 {year}年度 {year-1}年度"
    text = (f"===SOURCE_PAGE:1===\n{year}年年度报告\n1、合并资产负债表\n{year}年12月31日\n单位：元\n项目 期末余额 期初余额\n流动资产：\n"
            "应收账款 35 20\n存货 30 15\n应付账款 12 5\n资产总计 200 100\n2、母公司资产负债表\n货币资金 1 2\n"
            f"3、合并利润表\n单位：元\n项目 {year}年度 {year-1}年度\n营业收入 200 100\n营业成本 150 100\n4、母公司利润表\n营业收入 1 2")
    metadata = {"code": "000008", "symbol": "000008", "announcement_id": str(year), "report_date": f"{year}-12-31",
                "published_at": published, "title": f"{year}年年度报告"}
    provenance = {"pdf_sha256": "a"*64, "text_sha256": "b"*64, "pdf_hash_reverified": True, "text_hash_reverified": True}
    stock = parse_trade_accrual_report(text, metadata, provenance)
    revenue = attach_annual_revenue(stock, text, metadata, provenance)
    source = {"code": stock["code"], "report_date": stock["report_date"], "announcement_id": stock["announcement_id"],
              "stock_record_sha256": stock["record_sha256"], "pdf_sha256": stock["pdf_sha256"], "text_sha256": provenance["text_sha256"],
              "actual_income_header": header, "current_cost_yuan": "999999", "prior_cost_yuan": "999999",
              "bounded_consolidated_income_proof":{"status":"source_row_inside_bounded_consolidated_income","statement_scope":"consolidated","table_type":"income",
                  "pdf_sha256":"a"*64,"text_sha256":"b"*64,"announcement_id":str(year),"source_label":"营业成本 150 100",
                  "source_cells":cells or ["150","100"],"column_source":"150 100","actual_income_header":header,
                  "statement_start_offset":100,"statement_end_offset":200,"source_row_offset":150},
              "cost_candidates": [{"source_label": "营业成本 150 100", "source_cells": cells or ["150", "100"],
                                   "column_source": "150 100", "source_page": 31}]}
    return stock, revenue, source


def test_cost_attachment_rederives_amounts_and_explicit_dates_from_original_evidence():
    s, r, source = fixture()
    c = attach_component_cost(s, r, source)
    assert c["status"] == "strict_annual_cost_verified"
    assert c["values"] == {"current": "150", "prior": "100"}
    assert c["parent_stock_record_sha256"] == s["record_sha256"]
    assert c["parent_revenue_attachment_sha256"] == r["record_sha256"]
    contract = c["field_evidence"]["operating_cost"]["field_contract"]
    assert contract["current"]["flow_start"] == "2024-01-01"
    assert contract["comparative"]["flow_end"] == "2023-12-31"
    result = select_component_efficiency_asof([s], [r], [c], code="000008", decision_date="2025-04-20")
    assert result["value"] == pytest.approx(2/150)


def test_constant_component_intensities_produce_zero_investment_gap():
    s, r, source = fixture()
    v = copy.deepcopy(s["values"])
    v["accounts_receivable"]["current"]="40"
    v["inventory"]["current"]="22.5"
    v["accounts_payable"]["current"]="7.5"
    assert component_efficiency_score(v,r["values"],{"current":"150","prior":"100"}) == 0


def test_parent_identities_when_flow_growths_match_or_zero():
    s, r, source = fixture()
    assert component_efficiency_score(s["values"],r["values"],r["values"]) == trade_efficiency_score(s["values"],r["values"])
    flat={"current":"100","prior":"100"}
    assert component_efficiency_score(s["values"],flat,flat) == trade_accrual_score(s["values"])
    values=copy.deepcopy(s["values"]);values["accounts_payable"]["opening"]=values["inventory"]["opening"]
    assert component_efficiency_score(values,r["values"],{"current":"350","prior":"100"}) == trade_efficiency_score(values,r["values"])


def test_parent_difference_is_opening_net_inventory_credit_times_cost_vs_sales_growth():
    s, r, source = fixture();cost={"current":"150","prior":"100"}
    delta=component_efficiency_score(s["values"],r["values"],cost)-trade_efficiency_score(s["values"],r["values"])
    assert delta == pytest.approx((15-5)*(1.5-2)/150)


@pytest.mark.parametrize("header", ["单位：元\n2024年度\n项目 本期发生额 上期发生额",
    "单位：元\n2024年度\n项目 2024年度 2023年度", "单位：元\n项目 2024年度 2022年度",
    "单位：美元\n项目 2024年度 2023年度"])
def test_unbound_prior_flow_duplicate_caption_foreign_currency_remain_pending(header):
    s,r,source=fixture(header=header)
    assert attach_component_cost(s,r,source)["status"] == "annual_cost_source_pending"


@pytest.mark.parametrize("cells", [["150","0"],["0","100"],["150","—"],["150"],["150","100","80"]])
def test_missing_or_nonpositive_cost_pair_not_substituted(cells):
    s,r,source=fixture(cells=cells)
    assert attach_component_cost(s,r,source)["status"] == "annual_cost_source_pending"


@pytest.mark.parametrize("label", ["营业总成本 150 100","采购流量 150 100","营业成本率 150 100","营业成本 比率 150 100"])
def test_exact_operating_cost_label_required(label):
    s,r,source=fixture();source["cost_candidates"][0]["source_label"]=label
    source["bounded_consolidated_income_proof"]["source_label"]=label
    assert attach_component_cost(s,r,source)["status"] == "annual_cost_source_pending"


def test_original_id_stock_revenue_and_text_binding_rejects_cross_document():
    s,r,source=fixture()
    for key in ["announcement_id","stock_record_sha256","pdf_sha256","text_sha256"]:
        altered=dict(source);altered[key]="other"
        assert attach_component_cost(s,r,altered)["status"] == "annual_cost_source_pending"
    altered=dict(r);altered["pdf_sha256"]="c"*64
    assert attach_component_cost(s,altered,source)["status"] == "annual_cost_source_pending"


def test_same_original_mother_income_table_or_outside_row_cannot_become_consolidated():
    s,r,source=fixture()
    for change in [{"statement_scope":"parent"},{"source_row_offset":250},{"announcement_id":"other"}]:
        modified=copy.deepcopy(source);modified["bounded_consolidated_income_proof"].update(change)
        assert attach_component_cost(s,r,modified)["status"] == "annual_cost_source_pending"
    missing=copy.deepcopy(source);missing.pop("bounded_consolidated_income_proof")
    assert attach_component_cost(s,r,missing)["status"] == "annual_cost_source_pending"


def test_candidate_prefilled_status_and_amount_cannot_overwrite_rejection():
    s,r,source=fixture(header="单位：元\n2024年度\n本年发生额 上年发生额")
    source["cost_candidates"][0].update(status="strict_same_original_annual_cost_pair",current_yuan="150",comparative_yuan="100")
    result=attach_component_cost(s,r,source)
    assert result["status"] == "annual_cost_source_pending" and result["values"] == {"current":None,"prior":None}


def test_printed_cells_or_header_must_match_native_scope_proof():
    s,r,source=fixture()
    for change in [{"source_cells":["999","100"]},{"column_source":"999 100"},{"actual_income_header":"单位：元 项目2024年度2022年度"}]:
        modified=copy.deepcopy(source);modified["bounded_consolidated_income_proof"].update(change)
        assert attach_component_cost(s,r,modified)["status"] == "annual_cost_source_pending"


def test_cost_current_and_prior_corrections_cannot_use_previous_fivefield_clearance():
    s,r,source=fixture();c=attach_component_cost(s,r,source)
    for period in ["2024-12-31","2023-12-31"]:
        event={"code":"000008","announcement_id":"C","published_at":"2025-06-01","report_dates":[period],
               "scope_verified":True,"scope_evidence":"source annual cost revision","impact_status":"unrelated_fields",
               "affected_fields":["operating_cost"]}
        selected=select_component_efficiency_asof([s],[r],[c],code="000008",decision_date="2025-06-02",corrections=[event])
        assert selected["status"] == "selected_annual_component_cost_or_stock_correction_unresolved"


def test_unknown_quarter_title_cannot_prove_disjoint_annual_effect():
    s,r,source=fixture();c=attach_component_cost(s,r,source)
    event={"code":"000008","announcement_id":"C","published_at":"2025-06-01","report_dates":["2025-03-31"],
           "scope_verified":False,"scope_evidence":[]}
    assert not select_component_efficiency_asof([s],[r],[c],code="000008",decision_date="2025-06-02",corrections=[event])["pit_usable"]


def test_latest_cost_pending_blocks_old_annual_fallback_and_next_day_availability():
    s,r,source=fixture();c=attach_component_cost(s,r,source)
    ns,nr,proof=fixture(year=2025,published="2026-04-19",header="单位：元\n2025年度\n项目 本年发生额 上年发生额")
    nc=attach_component_cost(ns,nr,proof)
    assert not select_component_efficiency_asof([s],[r],[c],code="000008",decision_date="2025-04-19")["pit_usable"]
    got=select_component_efficiency_asof([s,ns],[r,nr],[c,nc],code="000008",decision_date="2026-04-20")
    assert got["status"] == "latest_annual_operating_cost_source_pending" and got["report_date"]=="2025-12-31"
