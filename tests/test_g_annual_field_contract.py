"""Source-role/byte boundaries, without price or economic observations."""
from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from panda_alpha.financial_statements import flow_spans, source_page
from panda_alpha.g_annual_field_contract import bound_annual_G_fields, bound_H1_assets_from_native, current_comparability
from panda_alpha.trade_accrual import _balance_sections, trade_stock_periods


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(*, literal_opening=False, scope="□适用 不适用\n报告期无合并报表范围变化。"):
    assets_header = "\n2025 年 12 月 31 日\n单位：元\n项目 " + ("2025 年 12 月 31 日 2024 年 12 月 31 日" if literal_opening else "期末余额 期初余额") + "\n"
    income_header = "\n2025 年度\n单位：元\n项目 2025 年度 2024 年度\n"
    assets_row, revenue_row, cost_row = "资产总计 1,200.00 1,000.00", "其中：营业收入 800.00 700.00", "其中：营业成本 500.00 450.00"
    text = ("公司2025年年度报告\n===SOURCE_PAGE:10===\n1、合并资产负债表" + assets_header + assets_row
            + "\n2、母公司资产负债表\n===SOURCE_PAGE:12===\n3、合并利润表" + income_header + revenue_row + "\n" + cost_row
            + "\n4、母公司利润表\n===SOURCE_PAGE:20===\n（1）重要会计政策变更\n□适用 不适用\n"
            + "（2）重要会计估计变更\n□适用 不适用\n是否需追溯调整或重述以前年度会计数据\n□是 否\n"
            + "与上年度财务报告相比，合并报表范围发生变化的情况说明\n" + scope + "\n八、聘任会计师事务所\n")
    period = trade_stock_periods(assets_header, "2025-12-31", text[:2500])
    assets = {"status": "same_report_current_and_opening_amounts", "statement_scope": "consolidated", "currency": "CNY",
              "printed_unit": "元", "cells": ["1,200.00", "1,000.00"], "amount_yuan": "1200.00", "comparative_amount_yuan": "1000.00",
              "stock_asof": "2025-12-31", "comparative_stock_asof": period["opening_stock_asof"], "period_evidence": period, "source_page": 10}
    stock = {"code": "300564", "symbol": "300564", "announcement_id": "current", "report_date": "2025-12-31", "published_at": "2026-04-28",
             "edition": "ORIGINAL_FULL_REPORT", "annual_signal_eligible": True, "excluded_source_kind": None,
             "comparison_change_barriers": [], "pdf_sha256": "a" * 64,
             "values": {"total_assets": {"current": "1200.00", "opening": "1000.00"}},
             "field_evidence": {"total_assets": assets},
             "statement_evidence": [{"status": "bounded_consolidated_statement", "header": assets_header, "period": period, "source_page": 10}],
             "provenance": {"pdf_sha256": "a" * 64}}
    e = {"status": "original_current_and_comparative_amounts_bound", "actual_table_header": assets_header,
         "source_label": assets_row, "source_cells": assets["cells"], "current_yuan": "1200.00", "comparative_yuan": "1000.00",
         "statement_scope": "consolidated", "currency": "CNY", "printed_unit": "元", "period_evidence": period, "source_page": 10,
         "source_row_offset": text.index(assets_row), "statement_heading": "1、合并资产负债表",
         "statement_start_offset": text.index(assets_header), "statement_end_offset": text.index("2、母公司资产负债表"), "end_boundary_heading": "2、母公司资产负债表"}
    fields = {"total_assets": e}
    for name, row, cells in [("operating_revenue", revenue_row, ["800.00", "700.00"]), ("operating_cost", cost_row, ["500.00", "450.00"])]:
        fields[name] = {"statement_scope": "consolidated", "currency": "CNY", "printed_unit": "元", "source_cells": cells,
                        "current_yuan": cells[0], "comparative_yuan": cells[1], "actual_table_header": income_header,
                        "source_row_offset": text.index(row), "source_label": row, "source_page": 12,
                        "period_evidence": flow_spans("项目2025年度2024年度", "2025-12-31"), "statement_heading": "3、合并利润表",
                        "statement_start_offset": text.index(income_header), "statement_end_offset": text.index("4、母公司利润表"), "end_boundary_heading": "4、母公司利润表"}
    return {"stock": stock, "fields": fields}, text


def bind(doc, text):
    rawtext = gzip.compress(text.encode(), mtime=0)
    doc["stock"]["provenance"]["text_sha256"] = digest(rawtext)
    doc["stock"]["record_sha256"] = digest(json.dumps({k: v for k, v in doc["stock"].items() if k != "record_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    raw = gzip.compress(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode(), mtime=0)
    return {"capture_gzip": raw, "capture_sha256": digest(raw), "text_gzip": rawtext}


def crossed_asset_pages(gap=1, continuation=""):
    doc, text = fixture()
    assets_row = doc["fields"]["total_assets"]["source_label"]
    markers = "".join(f"===SOURCE_PAGE:{11 + n}===\n" for n in range(gap))
    text = text.replace(assets_row, markers + continuation + assets_row)
    text = text.replace("===SOURCE_PAGE:12===\n3、合并利润表", "===SOURCE_PAGE:15===\n3、合并利润表")
    for name, e in doc["fields"].items():
        e["source_row_offset"] = text.index(e["source_label"])
        e["source_page"] = source_page(text, e["source_row_offset"])
        e["statement_start_offset"] = text.index(e["actual_table_header"])
        e["statement_end_offset"] = text.index(e["end_boundary_heading"])
    doc["stock"]["field_evidence"]["total_assets"]["source_page"] = 10 + gap
    return doc, text


@pytest.mark.parametrize("gap", [1, 2])
def test_actual_header_row_page_separation_uses_one_original_bounded_statement(gap):
    # Actual000609 has header62/totalassets63;300564 header83/row85.
    # This small source fixture preserves their one-/two-page separation.
    doc, text = crossed_asset_pages(gap)
    assets = bound_annual_G_fields(doc, bind(doc, text))["amounts"]["total_assets"]
    assert assets["source_header_page"] == 10
    assert assets["source_page"] == 10 + gap
    assert assets["current_yuan"] == "1200.00" and assets["opening_stock_asof"] == "2025-01-01"
    assert assets["opening_literal_date_printed"] is False


@pytest.mark.parametrize("bad", ["header_page", "duplicate_section", "outside_statement", "continuation_unit"])
def test_cross_page_binding_rejects_ambiguous_table_scope_or_changed_unit(bad):
    doc, text = crossed_asset_pages(1, "单位：万元\n" if bad == "continuation_unit" else "")
    if bad == "header_page": doc["stock"]["statement_evidence"][0]["source_page"] = 11
    elif bad == "duplicate_section": doc["stock"]["statement_evidence"].append(deepcopy(doc["stock"]["statement_evidence"][0]))
    elif bad == "outside_statement": doc["fields"]["total_assets"]["statement_end_offset"] = doc["fields"]["total_assets"]["source_row_offset"] - 1
    with pytest.raises(ValueError): bound_annual_G_fields(doc, bind(doc, text))


def geometry_continuation_fixture(*, spaced_dates=False):
    doc, text = fixture(literal_opening=True)
    row = doc["fields"]["total_assets"]["source_label"]
    text = text.replace(row, "流动资产：\n货币资金 100.00 200.00\n===SOURCE_PAGE:11===\n" + row)
    section = _balance_sections(text, "2025-12-31", text[:2500])[0]
    doc["stock"]["statement_evidence"] = [{k: v for k, v in section.items() if k not in {"body", "body_start"}}]
    old = doc["stock"]["field_evidence"]["total_assets"]
    old.update(source_page=11, period_evidence=section["period"], source_label=row)
    doc["fields"]["total_assets"] = {
        "status": "original_consolidated_dated_grid_two_stock_amounts_bound", "currency": "CNY", "printed_unit": "元",
        "bounded_native_and_actual_grid_row_text_match": True, "same_table_grid_x_edges_verified": True,
        "native_source_row_offset": text.index(row), "native_source_label": row, "source_page": 11,
        "current_yuan": "1200.00", "opening_yuan": "1000.00", "current_stock_asof": "2025-12-31", "opening_stock_asof": "2024-12-31",
        "original_header_date_cells": ["2025 年12 月31 日", "2024 年12 月31 日"] if spaced_dates else ["2025年12月31日", "2024年12月31日"],
        "dated_header_source_page": 10}
    for field in ["operating_revenue", "operating_cost"]:
        e = doc["fields"][field]
        e.update(source_row_offset=text.index(e["source_label"]), statement_start_offset=text.index(e["actual_table_header"]), statement_end_offset=text.index(e["end_boundary_heading"]))
    return doc, text


@pytest.mark.parametrize("spaced_dates", [False, True])
def test_retained_geometry_continuation_binds_original_native_range_and_only_date_spacing(spaced_dates):
    # Actual600338/601231 header-parent precedes originalgridrow byonepage;
    # revised603729 additionally prints spaced date cells.
    doc, text = geometry_continuation_fixture(spaced_dates=spaced_dates)
    assets = bound_annual_G_fields(doc, bind(doc, text))["amounts"]["total_assets"]
    assert assets["source_header_page"] == 10 and assets["source_page"] == 11
    assert assets["statement_start_offset"] <= assets["source_row_offset"] < assets["statement_end_offset"]
    assert assets["opening_stock_asof"] == "2024-12-31" and assets["retained_geometry_reused"]
    assert not assets["fresh_PDF_geometry_verified"]


@pytest.mark.parametrize("bad", ["outside_table", "wrong_date", "grid_flag", "role", "dated_page", "unit"])
def test_geometry_reuse_cannot_borrow_other_table_year_grid_or_unit(bad):
    doc, text = geometry_continuation_fixture()
    e = doc["fields"]["total_assets"]
    if bad == "outside_table": e["native_source_row_offset"] = text.index("2、母公司资产负债表")
    elif bad == "wrong_date": e["original_header_date_cells"][1] = "2023年12月31日"
    elif bad == "grid_flag": e["same_table_grid_x_edges_verified"] = False
    elif bad == "role": e["opening_stock_asof"] = "2025-01-01"
    elif bad == "dated_page": e["dated_header_source_page"] = 20
    elif bad == "unit": e["printed_unit"] = "万元"
    with pytest.raises(ValueError): bound_annual_G_fields(doc, bind(doc, text))


def test_geometry_amount_binding_does_not_clear_revised_edition():
    doc, text = geometry_continuation_fixture(spaced_dates=True)
    doc["stock"]["edition"] = "REVISED_FULL_REPORT"
    out = bound_annual_G_fields(doc, bind(doc, text))
    assert out["amounts_status"] == "three_G_same_report_amounts_bound"
    assert "current_revised_edition_G_version_bridge_pending" in out["current_comparability"]["pending_reasons"]


@pytest.mark.parametrize("literal_opening", [False, True])
def test_three_fields_do_not_require_AR_INV_AP_and_preserve_real_opening_role(literal_opening):
    doc, text = fixture(literal_opening=literal_opening)
    result = bound_annual_G_fields(doc, bind(doc, text))
    assets = result["amounts"]["total_assets"]
    assert result["amounts_status"] == "three_G_same_report_amounts_bound"
    assert result["current_comparability"]["status"] == "current_G_comparability_ready"
    assert assets["opening_stock_asof"] == ("2024-12-31" if literal_opening else "2025-01-01")
    assert assets["opening_literal_date_printed"] == literal_opening
    assert assets["opening_fiscal_year"] == 2025
    assert result["non_G_fields_required"] == [] and not result["full_PIT_certified"]


@pytest.mark.parametrize("scope", ["□适用 □不适用", "2025年7月7日新设子公司。", "适用 □不适用\n本期收购子公司。"])
def test_unchecked_or_narrative_scope_never_borrows_empty_raw_flags(scope):
    doc, text = fixture(scope=scope)
    out = bound_annual_G_fields(doc, bind(doc, text))
    assert out["amounts_status"] == "three_G_same_report_amounts_bound"
    assert "current_consolidation_scope_G_field_scope_pending" in out["current_comparability"]["pending_reasons"]


def test_income_no_effect_never_resolves_unknown_asset_policy():
    doc, text = fixture()
    text = text.replace("（1）重要会计政策变更\n□适用 不适用", "（1）重要会计政策变更\n适用 □不适用\n该项政策对营业收入和营业成本无影响。")
    out = bound_annual_G_fields(doc, bind(doc, text))
    assert "current_accounting_policy_G_income_and_assets_bridge_pending" in out["current_comparability"]["pending_reasons"]
    assert not out["income_bridge_used_as_assets_bridge"]


def test_same_line_policy_negative_is_bound_to_its_heading():
    doc, text = fixture()
    text = text.replace("（1）重要会计政策变更\n□适用 不适用", "（1）重要会计政策变更：□适用 不适用")
    out = bound_annual_G_fields(doc, bind(doc, text))
    assert out["current_comparability"]["status"] == "current_G_comparability_ready"


@pytest.mark.parametrize("body,ready", [
    ("1、其他\n无。", True),
    ("1、其他\n无重大影响 0.00", False),
    ("1、其他\n新设子公司；除此之外无变化。", False),
    ("1、其他\n无。\n2、收购\n本期收购子公司。", False),
    ("", False),
])
def test_bound_H1_scope_note_accepts_only_complete_explicit_none(body, ready):
    doc, text = fixture()
    q = "与上年度财务报告相比，合并报表范围发生变化的情况说明\n□适用 不适用\n报告期无合并报表范围变化。"
    text = text.replace(q, "九、合并范围的变更\n" + body + "\n十、在其他主体中的权益")
    doc["stock"]["report_date"] = "2026-06-30"
    out = current_comparability(text, "", doc["stock"])
    assert (out["status"] == "current_G_comparability_ready") == ready
    if ready:
        note = next(x for x in out["locators"] if x["kind"] == "consolidation_scope")
        assert note["method"] == "bounded_H1_note_explicit_none"


def test_H1_note_cannot_clear_annual_scope_or_a_separate_unchecked_question():
    doc, text = fixture(scope="□适用 □不适用")
    text += "\n九、合并范围的变更\n1、其他\n无。\n十、在其他主体中的权益\n"
    for period in ["2025-12-31", "2026-06-30"]:
        doc["stock"]["report_date"] = period
        out = current_comparability(text, "", doc["stock"])
        assert "current_consolidation_scope_G_field_scope_pending" in out["pending_reasons"]


@pytest.mark.parametrize("bad", [None, "missing_item", "blank", "material", "subsidiary", "extra_item"])
def test_exact_six_scope_note_requires_every_named_item_explicit_none(bad):
    doc, text = fixture()
    labels = ["非同一控制下企业合并", "同一控制下企业合并", "反向购买", "处置子公司", "其他原因的合并范围变动", "其他"]
    items = [f"{i}、{label}\n无\n" for i, label in enumerate(labels, 1)]
    if bad == "missing_item": items.pop(2)
    elif bad == "blank": items[2] = "3、反向购买\n\n"
    elif bad == "material": items[2] = "3、反向购买\n无重大影响\n"
    elif bad == "subsidiary": items[4] = "5、其他原因的合并范围变动\n新设子公司\n"
    elif bad == "extra_item": items.append("7、其他变化\n本期收购。\n")
    q = "与上年度财务报告相比，合并报表范围发生变化的情况说明\n□适用 不适用\n报告期无合并报表范围变化。"
    text = text.replace(q, "九、合并范围的变更 \n" + "".join(items) + "十、在其他主体中的权益")
    doc["stock"]["report_date"] = "2026-06-30"
    out = current_comparability(text, "", doc["stock"])
    assert (out["status"] == "current_G_comparability_ready") == (bad is None)
    if bad is None:
        assert any(e.get("method") == "bounded_H1_note_explicit_all_six_none" for e in out["locators"])


def native_H1_fixture():
    doc, text = fixture()
    text = text.replace("公司2025年年度报告", "公司2026年半年度报告").replace("2025 年 12 月 31 日", "2026 年 06 月 30 日")
    row = doc["fields"]["total_assets"]["source_label"]
    text = text.replace(row, "流动资产：\n货币资金 100.00 200.00\n" + row)
    section = _balance_sections(text, "2026-06-30", text[:2500])[0]
    stock = doc["stock"]
    stock.update(report_date="2026-06-30", annual_signal_eligible=False,
                 statement_evidence=[{k: v for k, v in section.items() if k not in {"body", "body_start"}}])
    e = stock["field_evidence"]["total_assets"]
    e.update(stock_asof="2026-06-30", comparative_stock_asof="2026-01-01", period_evidence=section["period"], source_label=row)
    doc["fields"] = {}  # Actual300564H1 archive has no annual supplement fields.
    return doc, text


def test_H1_native_stock_schema_does_not_require_annual_supplement_and_keeps_real_role():
    doc, text = native_H1_fixture()
    before = deepcopy(doc)
    assets = bound_H1_assets_from_native(doc, bind(doc, text))
    assert assets["current_stock_asof"] == "2026-06-30"
    assert assets["opening_stock_asof"] == "2026-01-01" and assets["opening_fiscal_year"] == 2026
    assert assets["opening_literal_date_printed"] is False
    assert assets["current_yuan"] == "1200.00" and assets["opening_yuan"] == "1000.00"
    assert doc["fields"] == {} and not assets["original_capture_fields_modified"]
    assert not assets["fresh_PDF_geometry_verified"]


@pytest.mark.parametrize("bad", ["capture_hash", "stock_values", "duplicate_row", "period", "unit"])
def test_H1_native_projection_keeps_byte_amount_period_unit_and_unique_row_gates(bad):
    doc, text = native_H1_fixture()
    if bad == "stock_values": doc["stock"]["values"]["total_assets"]["opening"] = "9999"
    elif bad == "duplicate_row":
        row = doc["stock"]["field_evidence"]["total_assets"]["source_label"]
        text = text.replace(row, row + "\n" + row)
    elif bad == "period": doc["stock"]["field_evidence"]["total_assets"]["comparative_stock_asof"] = "2025-01-01"
    elif bad == "unit": doc["stock"]["field_evidence"]["total_assets"]["printed_unit"] = "万元"
    binding = bind(doc, text)
    if bad == "capture_hash": binding["capture_sha256"] = "0" * 64
    with pytest.raises(ValueError): bound_H1_assets_from_native(doc, binding)


def test_income_pair_cannot_borrow_two_independently_bound_statements():
    doc, text = fixture()
    doc["fields"]["operating_cost"]["statement_start_offset"] += 1
    with pytest.raises(ValueError, match="not_one_same_consolidated_statement"):
        bound_annual_G_fields(doc, bind(doc, text))


def test_income_declared_unit_cannot_override_original_header_even_if_scaled_values_rebind():
    doc, text = fixture()
    for e in [doc["fields"]["operating_revenue"], doc["fields"]["operating_cost"]]:
        e["printed_unit"] = "万元"
        e["current_yuan"] = str(float(e["current_yuan"]) * 10000)
        e["comparative_yuan"] = str(float(e["comparative_yuan"]) * 10000)
    with pytest.raises(ValueError, match="original_header_unit_mismatch"):
        bound_annual_G_fields(doc, bind(doc, text))


@pytest.mark.parametrize("values", [{"current": "9999", "opening": "8888"}, {}, {"current": "1200.00"}])
def test_stock_operand_amounts_must_match_same_report_asset_prints(values):
    doc, text = fixture()
    doc["stock"]["values"]["total_assets"] = values
    with pytest.raises(ValueError, match="original_stock_values_mismatch"):
        bound_annual_G_fields(doc, bind(doc, text))


@pytest.mark.parametrize("bad", ["capture_hash", "asset_row_offset", "opening_period", "unit", "income_scope"])
def test_source_or_three_field_role_mismatch_is_not_a_ready_contract(bad):
    doc, text = fixture()
    if bad == "asset_row_offset": doc["fields"]["total_assets"]["source_row_offset"] = 0
    elif bad == "opening_period": doc["stock"]["field_evidence"]["total_assets"]["comparative_stock_asof"] = "2023-12-31"
    elif bad == "unit": doc["fields"]["total_assets"]["printed_unit"] = "万元"
    elif bad == "income_scope": doc["fields"]["operating_cost"]["statement_heading"] = "1、合并资产负债表"
    binding = bind(doc, text)
    if bad == "capture_hash": binding["capture_sha256"] = "0" * 64
    with pytest.raises(ValueError): bound_annual_G_fields(doc, binding)


def test_revised_edition_remains_separate_even_with_bound_unchanged_amounts():
    doc, text = fixture()
    doc["stock"]["edition"] = "REVISED_FULL_REPORT"
    out = bound_annual_G_fields(doc, bind(doc, text))
    assert out["amounts_status"] == "three_G_same_report_amounts_bound"
    assert "current_revised_edition_G_version_bridge_pending" in out["current_comparability"]["pending_reasons"]
