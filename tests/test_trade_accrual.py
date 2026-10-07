import gzip
import hashlib
from pathlib import Path

import pytest

from panda_alpha.trade_accrual import (
    parse_trade_accrual_report, load_trade_accrual_report,
    select_trade_accrual_asof, trade_accrual_score, trade_stock_periods,
)


def source(period="2024-12-31", header=None, rows=None, title=None):
    year = period[:4]
    title = title or f"{year}年年度报告"
    header = header or f"{year}年12月31日\n单位：元\n项目 期末余额 期初余额"
    rows = rows or "应收账款 30.00 20.00\n存货 20.00 10.00\n应付账款 15.00 10.00\n资产总计 200.00 100.00"
    text = f"===SOURCE_PAGE:1===\n{title}\n===SOURCE_PAGE:20===\n1、合并资产负债表\n{header}\n流动资产：\n{rows}\n2、母公司资产负债表\n应收账款 900 800\n存货 900 800\n应付账款 0 0\n资产总计 999 888\n"
    meta = {"code": "000008", "announcement_id": "ID1", "report_date": period,
            "published_at": "2025-04-19", "title": title, "edition": "ORIGINAL_FULL_REPORT"}
    provenance = {"pdf_sha256": "a"*64, "text_sha256": "b"*64,
                  "pdf_hash_reverified": True, "text_hash_reverified": True}
    return text, meta, provenance


def report(**kwargs):
    return parse_trade_accrual_report(*source(**kwargs))


def test_same_annual_original_stock_roles_and_score():
    r = report()
    assert r["status"] == "source_values_verified"
    assert r["field_evidence"]["accounts_receivable"]["comparative_stock_asof"] == "2024-01-01"
    assert r["field_evidence"]["accounts_receivable"]["source_page"] == 20
    assert r["values"]["accounts_receivable"] == {"current": "30.00", "opening": "20.00"}
    assert trade_accrual_score(r["values"]) == pytest.approx(-.1)
    assert not r["full_pit_certified"]


@pytest.mark.parametrize("opening", ["2023年12月31日", "2024年1月1日"])
def test_explicit_opening_stock_date_retained(opening):
    p = trade_stock_periods(f"2024年12月31日\n2024年12月31日 {opening}", "2024-12-31", "2024年年度报告")
    assert p["status"] == "same_report_stock_columns_bound"
    assert p["repeated_current_caption_collapsed"]
    assert p["opening_stock_asof"] in {"2023-12-31", "2024-01-01"}


@pytest.mark.parametrize("header,title", [
    ("2025年12月31日 项目 期末余额 期初余额", "2024年年度报告"),
    ("项目 期末余额 期初余额", "2024年年度报告"),
    ("2024年12月31日 2022年12月31日", "2024年年度报告"),
    ("2024年12月31日 2024年6月30日", "2024年年度报告"),
    ("2024年12月31日 项目 本期 上期", "2024年年度报告"),
    ("2024年12月31日 项目 期末余额 期初余额", "2025年年度报告"),
    ("2024年13月31日 项目 期末余额 期初余额", "2024年年度报告"),
])
def test_unbound_or_contradictory_column_period_stays_pending(header, title):
    assert trade_stock_periods(header, "2024-12-31", title)["status"] != "same_report_stock_columns_bound"


@pytest.mark.parametrize("row", ["应收账款 — 20.00", "应收账款 30.00", "应收账款", "应收账款 30.00 20.00 10.00"])
def test_missing_dash_or_ambiguous_column_not_zero(row):
    r = report(rows=row+"\n存货 20 10\n应付账款 15 10\n资产总计 200 100")
    assert r["status"] == "source_values_pending"
    assert r["values"]["accounts_receivable"]["current"] is None


def test_numeric_zero_is_valid_and_trade_labels_are_exact():
    r = report(rows="应收票据 900 800\n应收账款 0.00 20.00\n存货 20 10\n其他应付款 999 888\n应付账款 15 10\n资产总计 200 100")
    assert r["status"] == "source_values_verified"
    assert r["values"]["accounts_receivable"]["current"] == "0.00"
    assert r["values"]["accounts_payable"]["current"] == "15"


@pytest.mark.parametrize("bad_label,field,original", [
    ("存货跌价准备", "inventory", "存货"),
    ("应收账款坏账准备", "accounts_receivable", "应收账款"),
    ("应付账款预提", "accounts_payable", "应付账款"),
    ("存货 跌价准备", "inventory", "存货"),
])
def test_balance_field_name_suffix_cannot_become_exact_trade_stock(bad_label, field, original):
    text, meta, provenance = source()
    text = text.replace(original, bad_label)
    r = parse_trade_accrual_report(text, meta, provenance)
    assert r["status"] == "source_values_pending"
    assert r["values"][field]["current"] is None


@pytest.mark.parametrize("unit", ["万元", "百万元", "千元", "亿元"])
def test_source_units_map_both_columns(unit):
    r = report(header=f"2024年12月31日\n单位：{unit}\n项目 期末余额 期初余额")
    multiplier = {"万元": 10000, "百万元": 1000000, "千元": 1000, "亿元": 100000000}[unit]
    assert float(r["values"]["total_assets"]["current"]) == 200*multiplier
    assert trade_accrual_score(r["values"]) == pytest.approx(-.1)


def test_parent_only_foreign_currency_and_bad_intro_fail():
    text, m, p = source()
    assert parse_trade_accrual_report(text.replace("合并资产负债表", "母公司资产负债表"), m, p)["status"] == "source_values_pending"
    assert report(header="2024年12月31日\n单位：美元\n项目 期末余额 期初余额")["status"] == "source_values_pending"
    assert parse_trade_accrual_report(text.replace("2024年年度报告", "2025年年度报告"), m, p)["status"] == "source_values_pending"


def test_original_bytes_reverified_and_tampered_source_rejected(tmp_path):
    text, m, _ = source()
    pdf = tmp_path/"original.pdf"; pdf.write_bytes(b"%PDF-original-source")
    gz = tmp_path/"original.txt.gz"
    with gzip.open(gz, "wt", encoding="utf8") as f: f.write(text)
    document = {**m, "path": str(pdf), "text_path": str(gz),
                "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "text_sha256": hashlib.sha256(gz.read_bytes()).hexdigest()}
    assert load_trade_accrual_report(document)["status"] == "source_values_verified"
    pdf.write_bytes(b"%PDF-tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_trade_accrual_report(document)


def test_date_only_availability_and_halfyear_not_signal():
    r = report()
    assert not select_trade_accrual_asof([r], code="000008", decision_date="2025-04-19")["pit_usable"]
    assert select_trade_accrual_asof([r], code="000008", decision_date="2025-04-20")["pit_usable"]
    h = report(period="2025-06-30", header="2025年6月30日\n单位：元\n项目 期末余额 期初余额", title="2025年半年度报告")
    assert h["status"] == "source_values_verified"
    assert not select_trade_accrual_asof([h], code="000008", decision_date="2025-08-31")["pit_usable"]


def test_latest_unparsed_and_same_day_ambiguity_blocks_fallback():
    old = report()
    new = report(period="2025-12-31", rows="应收账款\n存货 20 10\n应付账款 15 10\n资产总计 200 100")
    new.update(announcement_id="ID2", published_at="2026-04-01", available_date="2026-04-02")
    assert select_trade_accrual_asof([old,new], code="000008", decision_date="2026-04-02")["status"] == "latest_annual_source_values_pending"
    alt = {**old, "announcement_id": "ID3"}
    assert select_trade_accrual_asof([old,alt], code="000008", decision_date="2025-04-20")["status"] == "ambiguous_same_day_annual_editions"


def test_correction_scope_proof_and_actual_field_mapping():
    r = report()
    event = {"code": "000008", "announcement_id": "C", "published_at": "2025-06-01", "report_dates": ["2024-12-31"]}
    assert not select_trade_accrual_asof([r], code="000008", decision_date="2025-06-02", corrections=[event])["pit_usable"]
    unrelated = {**event, "scope_verified": True, "scope_evidence": "Original paragraph: EPS correction only",
                 "impact_status": "unrelated_fields", "affected_fields": ["earnings_per_share"]}
    assert select_trade_accrual_asof([r], code="000008", decision_date="2025-06-02", corrections=[unrelated])["pit_usable"]
    no_proof = {**unrelated, "scope_evidence": ""}
    assert not select_trade_accrual_asof([r], code="000008", decision_date="2025-06-02", corrections=[no_proof])["pit_usable"]
    ar = {**unrelated, "affected_fields": ["accounts_receivable"]}
    assert not select_trade_accrual_asof([r], code="000008", decision_date="2025-06-02", corrections=[ar])["pit_usable"]


def test_explicit_unaffected_year_end_proof_for_opening_stock():
    r = report()
    event = {"code": "000008", "announcement_id": "C", "published_at": "2025-06-01", "report_dates": ["2023-09-30"],
             "scope_verified": True, "scope_evidence": "consolidated source correction",
             "wc_verified_unaffected_stock_dates": ["2023-12-31"]}
    assert not select_trade_accrual_asof([r], code="000008", decision_date="2025-06-02", corrections=[event])["pit_usable"]
    event.update(wc_unaffected_stock_evidence=[{"quote": "2023年度合并资产负债表期末无影响",
                                               "source_pdf_sha256": "c"*64, "source_text_sha256": "d"*64}],
                 scope_source_sha256="c"*64)
    assert select_trade_accrual_asof([r], code="000008", decision_date="2025-06-02", corrections=[event])["pit_usable"]
    event["scope_source_sha256"] = "f"*64
    assert not select_trade_accrual_asof([r], code="000008", decision_date="2025-06-02", corrections=[event])["pit_usable"]


def test_original_index_summary_and_governance_document_do_not_block_real_fy():
    original = report()
    text, meta, provenance = source(title="2024年年度报告摘要")
    summary = parse_trade_accrual_report(text, {**meta, "announcement_id": "summary"}, provenance)
    assert summary["excluded_source_kind"] == "annual_summary"
    assert not summary["annual_signal_eligible"]
    governance = parse_trade_accrual_report("===SOURCE_PAGE:1===\n公司年度报告工作制度", {**meta, "announcement_id": "governance"}, provenance)
    assert governance["excluded_source_kind"] == "governance_annual_report_work_rules"
    assert select_trade_accrual_asof([original, summary, governance], code="000008", decision_date="2025-04-20")["pit_usable"]


def test_later_halfyear_restatement_detected_only_after_public():
    r = report()
    h = report(period="2025-06-30", title="2025年半年度报告", header="2025年6月30日\n单位：元\n项目 期末余额 期初余额",
               rows="应收账款 35.00 31.00\n存货 25.00 20.00\n应付账款 16.00 15.00\n资产总计 220.00 200.00")
    h.update(announcement_id="H1", published_at="2025-08-25", available_date="2025-08-26")
    assert select_trade_accrual_asof([r,h], code="000008", decision_date="2025-08-25")["pit_usable"]
    result=select_trade_accrual_asof([r,h], code="000008", decision_date="2025-08-26")
    assert result["status"] == "later_report_selected_fy_trade_comparison_mismatch"
    assert result["affected_field"] == "accounts_receivable"


def test_later_halfyear_revised_comparison_supersedes_old_edition_after_public():
    original = report()
    bad = report(period="2025-06-30", title="2025年半年度报告", header="2025年6月30日\n单位：元\n项目 期末余额 期初余额",
                 rows="应收账款 35.00 31.00\n存货 25.00 20.00\n应付账款 16.00 15.00\n资产总计 220.00 200.00")
    bad.update(announcement_id="H1", published_at="2025-08-25", available_date="2025-08-26")
    good = report(period="2025-06-30", title="2025年半年度报告", header="2025年6月30日\n单位：元\n项目 期末余额 期初余额",
                  rows="应收账款 35.00 30.00\n存货 25.00 20.00\n应付账款 16.00 15.00\n资产总计 220.00 200.00")
    good.update(announcement_id="H1-revised", published_at="2025-08-28", available_date="2025-08-29")
    assert not select_trade_accrual_asof([original,bad,good], code="000008", decision_date="2025-08-28")["pit_usable"]
    assert select_trade_accrual_asof([original,bad,good], code="000008", decision_date="2025-08-29")["pit_usable"]


def test_later_display_rounding_and_missing_comparison_not_fabricated():
    r = report()
    h = report(period="2025-06-30", title="2025年半年度报告", header="2025年6月30日\n单位：元\n项目 期末余额 期初余额",
               rows="应收账款 35.00 30.004\n存货 25.00 20.00\n应付账款 16.00 15.00\n资产总计 220.00 200.00")
    h.update(announcement_id="H1", published_at="2025-08-25", available_date="2025-08-26")
    assert select_trade_accrual_asof([r,h], code="000008", decision_date="2025-08-26")["pit_usable"]
    h["field_evidence"]["accounts_receivable"].update(comparative_amount_yuan=None, comparative_stock_asof=None)
    assert select_trade_accrual_asof([r,h], code="000008", decision_date="2025-08-26")["pit_usable"]


@pytest.mark.parametrize("checkbox,blocked", [("√是 □否", True), ("☑是 □否", True), ("□是 √否", False), ("□适用 √不适用", False)])
def test_later_affirmative_prior_year_restatement_blocks_unknown_stock_scope(checkbox, blocked):
    original = report()
    text, metadata, provenance = source(period="2025-06-30", title="2025年半年度报告",
        header="2025年6月30日\n单位：元\n项目 期末余额 期初余额",
        rows="应收账款 35.00 30.00\n存货 25.00 20.00\n应付账款 16.00 15.00\n资产总计 220.00 200.00")
    text = text.replace("===SOURCE_PAGE:20===", f"公司是否需追溯调整或重述以前年度会计数据\n{checkbox}\n" + "其他业务说明\n"*70 + "===SOURCE_PAGE:20===")
    metadata.update(announcement_id="H1", published_at="2025-08-25")
    later = parse_trade_accrual_report(text, metadata, provenance)
    result = select_trade_accrual_asof([original, later], code="000008", decision_date="2025-08-26")
    assert result["pit_usable"] is not blocked


@pytest.mark.parametrize("boilerplate", [
    "前期会计差错更正\n无\n应收账款的期初余额按账龄披露。",
    "在报告期内，若因同一控制下企业合并增加子公司，则调整合并资产负债表期初数。",
    "报告期内增减子公司的处理方法：同一控制下企业合并增加的业务，编制合并资产负债表时调整期初。",
    "公司主营业务数据统计口径调整后的主营业务数据\n□适用 √不适用\n主要计提存货跌价准备。",
])
def test_boilerplate_or_negated_other_data_adjustment_is_not_actual_stock_restatement(boilerplate):
    text, metadata, provenance = source()
    parsed = parse_trade_accrual_report(text+boilerplate, metadata, provenance)
    assert parsed["comparison_change_barriers"] == []


def test_actual_balance_adjusted_comparison_header_retained_as_basis_evidence():
    parsed = report(header="2024年12月31日\n单位：元\n项目 期末余额 期初余额（调整后）")
    assert parsed["comparison_change_barriers"][0]["kind"] == "actual_balance_comparative_adjusted_header"


def test_nonpositive_assets_and_negative_carrying_stock_invalid():
    assert report(rows="应收账款 -1 20\n存货 20 10\n应付账款 15 10\n资产总计 200 100")["status"] == "source_values_pending"
    r = report()
    r["values"]["total_assets"]["opening"] = "0"
    with pytest.raises(ValueError): trade_accrual_score(r["values"])
