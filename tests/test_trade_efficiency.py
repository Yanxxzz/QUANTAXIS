import copy
import pytest

from panda_alpha.trade_accrual import parse_trade_accrual_report
from panda_alpha.trade_efficiency import (
    attach_annual_revenue, select_trade_efficiency_asof, trade_efficiency_score,
)


def fixture(period="2024-12-31", published="2025-04-19", income_header=None,
            income_row="一、营业收入 200.00 100.00", stock_rows=None):
    year = int(period[:4])
    stock_rows = stock_rows or "应收账款 30.00 20.00\n存货 20.00 10.00\n应付账款 15.00 10.00\n资产总计 200.00 100.00"
    income_header = income_header or f"单位：元\n项目 {year}年度 {year-1}年度"
    text = (f"===SOURCE_PAGE:1===\n{year}年年度报告\n===SOURCE_PAGE:30===\n1、合并资产负债表\n"
            f"{year}年12月31日\n单位：元\n项目 期末余额 期初余额\n流动资产：\n{stock_rows}\n"
            "2、母公司资产负债表\n货币资金 1 2\n===SOURCE_PAGE:32===\n3、合并利润表\n"
            f"{income_header}\n{income_row}\n营业成本 150 70\n4、母公司利润表\n营业收入 900 800\n")
    metadata = {"code": "000008", "symbol": "000008", "announcement_id": "FY"+str(year),
                "report_date": period, "published_at": published, "title": str(year)+"年年度报告"}
    provenance = {"pdf_sha256": "a"*64, "text_sha256": "b"*64,
                  "pdf_hash_reverified": True, "text_hash_reverified": True}
    stock = parse_trade_accrual_report(text, metadata, provenance)
    return stock, text, metadata, provenance


def pair(**kwargs):
    stock, text, metadata, provenance = fixture(**kwargs)
    return stock, attach_annual_revenue(stock, text, metadata, provenance)


def test_strict_original_annual_revenue_attached_to_stock_document():
    stock, income = pair()
    assert income["status"] == "strict_annual_revenue_verified"
    assert income["parent_stock_record_sha256"] == stock["record_sha256"]
    contract = income["field_evidence"]["operating_revenue"]["field_contract"]
    assert contract["current"]["flow_start"] == "2024-01-01"
    assert contract["comparative"]["flow_end"] == "2023-12-31"
    assert income["field_evidence"]["operating_revenue"]["source_page"] == 32
    assert income["values"] == {"current": "200.00", "prior": "100.00"}
    result = select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-04-20")
    assert result["pit_usable"]
    assert result["value"] == pytest.approx(5/150)


@pytest.mark.parametrize("columns", ["项目 附注 2024年度 2023年度", "项目 2024年度 2023年度 附注"])
def test_three_integer_flow_cells_need_source_column_evidence(columns):
    stock, income = pair(income_header="单位：百万元\n"+columns,
                         income_row="营业收入 100 90 8")
    assert stock["status"] == "source_values_verified"
    assert income["status"] == "annual_revenue_source_pending"
    assert income["values"] == {"current": None, "prior": None}
    result = select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-04-20")
    assert not result["pit_usable"]
    assert result["value"] is None


def test_constant_opening_sales_intensity_gives_zero_and_no_growth_matches_wc01():
    stock, income = pair(stock_rows="应收账款 35 20\n存货 20 10\n应付账款 15 10\n资产总计 200 100")
    assert trade_efficiency_score(stock["values"], income["values"]) == 0
    assert trade_efficiency_score(stock["values"], {"current": "100", "prior": "100"}) == pytest.approx(-20/150)


@pytest.mark.parametrize("header", [
    "单位：元\n2024年度\n项目 本期发生额 上期发生额",
    "单位：元\n2024年12月31日止年度\n附注 本年发生额 上年发生额",
    "单位：元\n2024年度\n项目 2024年度 2023年度",
    "单位：元\n项目 2024年度 2022年度",
    "单位：元\n项目 2024年半年度 2023年半年度",
])
def test_unbound_prior_fy_and_duplicate_caption_remain_pending(header):
    stock, income = pair(income_header=header)
    assert income["status"] == "annual_revenue_source_pending"
    assert income["values"]["prior"] is None
    assert not select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-04-20")["pit_usable"]


@pytest.mark.parametrize("row", [
    "营业总收入 200 100", "营业收入同比增长 200 100", "营业收入 同比增长 200 100",
    "营业收入 200 —", "营业收入 — 100", "营业收入 0 100", "营业收入 200 0",
    "营业收入 -200 100", "营业收入 200 100 80",
])
def test_exact_revenue_scope_positive_amounts_and_two_columns_required(row):
    stock, income = pair(income_row=row)
    assert income["status"] == "annual_revenue_source_pending"


def test_chinese_note_reference_and_non_yuan_display_unit():
    stock, income = pair(income_header="单位：万元\n附注 项目 2024年度 2023年度",
                         income_row="一、营业收入 七（四十九） 200.00 100.00")
    assert income["status"] == "strict_annual_revenue_verified"
    assert income["values"] == {"current": "2000000.00", "prior": "1000000.00"}


def test_parent_only_foreign_money_and_changed_original_binding_rejected():
    stock, text, metadata, provenance = fixture()
    mother = text.replace("3、合并利润表", "3、母公司利润表")
    assert attach_annual_revenue(stock, mother, metadata, provenance)["status"] == "annual_revenue_source_pending"
    assert pair(income_header="单位：美元\n项目 2024年度 2023年度")[1]["status"] == "annual_revenue_source_pending"
    for altered in [{**provenance, "pdf_sha256": "c"*64}, {**provenance, "text_sha256": "d"*64}]:
        assert not attach_annual_revenue(stock, text, metadata, altered)["same_document_binding_verified"]
    assert not attach_annual_revenue(stock, text, {**metadata, "announcement_id": "other"}, provenance)["same_document_binding_verified"]


def test_latest_income_unknown_blocks_old_fiscal_year_fallback():
    old, old_income = pair()
    new, new_income = pair(period="2025-12-31", published="2026-04-19", income_header="单位：元\n2025年度\n项目 本年发生额 上年发生额")
    result = select_trade_efficiency_asof([old,new], [old_income,new_income], code="000008", decision_date="2026-04-20")
    assert result["status"] == "latest_annual_revenue_attachment_pending"
    assert result["report_date"] == "2025-12-31"


def test_original_available_next_day_and_missing_attachment_pending():
    stock, income = pair()
    assert not select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-04-19")["pit_usable"]
    assert not select_trade_efficiency_asof([stock], [], code="000008", decision_date="2025-04-20")["pit_usable"]


def test_prior_revenue_correction_not_cleared_by_stock_only_unaffected_proof():
    stock, income = pair()
    correction = {"code": "000008", "announcement_id": "C", "published_at": "2025-06-01",
                  "report_dates": ["2023-12-31"], "scope_verified": True,
                  "scope_evidence": "2023收入更正，期末合并资产负债表无影响",
                  "impact_status": "affected_fields_pending", "affected_fields": ["operating_revenue"],
                  "wc_verified_unaffected_stock_dates": ["2023-12-31"],
                  "wc_unaffected_stock_evidence": [{"quote": "期末合并资产负债表无影响", "source_pdf_sha256": "c"*64, "source_text_sha256": "d"*64}],
                  "scope_source_sha256": "c"*64}
    result = select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-06-02", corrections=[correction])
    assert result["status"] == "selected_annual_efficiency_correction_unresolved"


def test_income_related_scope_is_checked_along_with_stock_scope():
    stock, income = pair()
    base = {"code": "000008", "announcement_id": "C", "published_at": "2025-06-01",
            "report_dates": ["2024-12-31"], "scope_verified": True, "scope_evidence": "source body",
            "impact_status": "unrelated_fields"}
    for field in ["operating_revenue", "accounts_receivable", "total_assets"]:
        assert not select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-06-02", corrections=[{**base,"affected_fields":[field]}])["pit_usable"]
    assert select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-06-02", corrections=[{**base,"affected_fields":["earnings_per_share"]}])["pit_usable"]
    assert not select_trade_efficiency_asof([stock], [income], code="000008", decision_date="2025-06-02", corrections=[{**base,"scope_evidence":"","affected_fields":["earnings_per_share"]}])["pit_usable"]


@pytest.mark.parametrize("values", [{"current":"0","prior":"10"},{"current":"10","prior":"0"},{"current":"NaN","prior":"10"}])
def test_invalid_sales_ratio_does_not_emit_value(values):
    stock, _ = pair()
    with pytest.raises(ValueError): trade_efficiency_score(stock["values"],values)
