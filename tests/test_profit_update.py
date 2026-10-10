"""Bound FY/H1 same-edition gross-margin text updates and refusal cases."""
from copy import deepcopy
import hashlib

import pytest

from panda_alpha.profit_update import parse_profit_update


META = {"code": "600001", "report_date": "2025-12-31", "announcement_id": "example",
        "published_at": "2026-04-20", "available_date": "2026-04-21",
        "edition": "ORIGINAL_FULL_REPORT", "title": "2025年年度报告"}


def report(revenue="400.00 350.00", cost="250.00 200.00", *, unit="元",
           columns="项目 附注 2025年度 2024年度", caption="2025年1—12月",
           scope="合并", change="", title="公司2025年年度报告", rest=""):
    return (f"{title}\n{change}\n===SOURCE_PAGE:31===\n{scope}利润表\n"
            f"{caption}\n单位：{unit} 币种：人民币\n{columns}\n"
            "一、营业总收入 500.00 450.00\n"
            f"其中：营业收入 {revenue}\n二、营业总成本 600.00 500.00\n"
            f"其中：营业成本 {cost}\n销售费用 60.00 50.00\n"
            "四、净利润 -100.00 -50.00\n4、母公司利润表\n单位：元\n"
            "营业收入 999.00 800.00\n营业成本 777.00 700.00\n" + rest)


def provenance(text):
    return {"pdf_sha256": "a" * 64, "pdf_hash_reverified": True,
            "text_sha256": "b" * 64, "text_hash_reverified": True,
            "text_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "historical_source_binding": "capture_original_edition"}


def parse(text, metadata=None, prov=None):
    return parse_profit_update(text, metadata or META, provenance(text) if prov is None else prov)


@pytest.mark.parametrize("unit,revenue,cost", [
    ("元", "400.00", "250.00"), ("千元", "400000.00", "250000.00"),
    ("万元", "4000000.00", "2500000.00")])
def test_explicit_fy_unit_conversion_and_archival_boundary(unit, revenue, cost):
    text = report(unit=unit)
    prov = provenance(text)
    untouched = deepcopy(prov)
    result = parse(text, prov=prov)
    assert result["status"] == "text_source_pair_ready"
    assert result["values"]["operating_revenue"] == revenue
    assert result["values"]["operating_cost"] == cost
    assert result["gross_margin_change"] == pytest.approx(0.375 - 150 / 350)
    field = result["field_evidence"]["operating_revenue"]
    assert (field["flow_start"], field["flow_end"]) == ("2025-01-01", "2025-12-31")
    assert (field["comparative_flow_start"], field["comparative_flow_end"]) == ("2024-01-01", "2024-12-31")
    assert field["source_page"] == 31 and field["statement_scope"] == "consolidated"
    assert result["available_date"] == "2026-04-21"
    assert result["pdf_verification_status"] == "pdf_archive_only"
    assert not result["provenance"]["pdf_hash_reverified"]
    assert result["provenance"]["historical_pdf_hash_reverified"]
    assert not result["full_pit_certified"] and prov == untouched


@pytest.mark.parametrize("columns", ["项目 附注 2026 年半年度 2025 年半年度",
                                    "项目 附注\n2026 年 1—6 月 2025 年 1—6 月"])
def test_h1_uses_explicit_h1_comparison_with_repeated_caption(columns):
    text = report(columns=columns, caption="2026 年半年度", title="公司2026年半年度报告")
    result = parse(text, {**META, "report_date": "2026-06-30", "title": "2026年半年度报告"})
    assert result["status"] == "text_source_pair_ready" and result["supported_span"] == "H1"
    field = result["field_evidence"]["operating_cost"]
    assert field["period_evidence"]["status"] == "explicit_same_span_column_pair"
    assert field["period_evidence"]["spans"]["explicit_column_years"] == ["2026", "2025"]
    assert (field["flow_start"], field["flow_end"]) == ("2026-01-01", "2026-06-30")
    assert (field["comparative_flow_start"], field["comparative_flow_end"]) == ("2025-01-01", "2025-06-30")


def test_explicit_flow_date_ranges_are_same_span_bound():
    columns = "项目 2026年1月1日至2026年6月30日 2025年1月1日至2025年6月30日"
    result = parse(report(columns=columns), {**META, "report_date": "2026-06-30"})
    assert result["status"] == "text_source_pair_ready"


def test_page_caption_does_not_add_a_third_fiscal_year():
    columns = ("项目 2026 年半年度 2025 年半年度\n===SOURCE_PAGE:42===\n"
               "深圳美丽生态股份有限公司 2026 年半年度报告全文\n42")
    text = report(columns=columns, caption="2026 年半年度", title="2026年半年度报告")
    result = parse(text, {**META, "report_date": "2026-06-30"})
    assert result["status"] == "text_source_pair_ready"
    proof = result["field_evidence"]["operating_revenue"]["period_evidence"]
    assert proof["spans"]["explicit_column_years"] == ["2026", "2025"]
    assert len(proof["excluded_page_captions"]) == 1


def test_caption_filter_does_not_discard_an_ambiguous_column_line():
    columns = "项目 2026半年度 2025半年度 公司2026年半年度报告全文"
    result = parse(report(columns=columns), {**META, "report_date": "2026-06-30"})
    assert result["status"] == "pending"
    assert result["field_evidence"]["operating_revenue"]["period_evidence"]["excluded_page_captions"] == []


@pytest.mark.parametrize("columns", ["项目 本期发生额 上期发生额", "项目 2025年度",
                                    "项目 2024年度 2025年度", "项目 2025年度 2023年度",
                                    "项目 2025年度 2024半年度"])
def test_generic_wrong_reverse_or_wrong_span_columns_remain_pending(columns):
    result = parse(report(columns=columns))
    assert result["status"] == "pending" and result["gross_margin_change"] is None
    assert result["values"]["operating_revenue"] is None


def test_title_and_edition_cannot_supply_generic_column_years():
    result = parse(report(columns="项目 本期发生额 上期发生额"),
                   {**META, "edition": "2025年度 2024年度 ORIGINAL_FULL_REPORT"})
    assert result["status"] == "pending"


@pytest.mark.parametrize("note", ["七、78", "（七、78）", "60(1)", "60"])
def test_explicit_note_references_are_not_currency_cells(note):
    result = parse(report(revenue=f"{note} 400.00 350.00", cost=f"{note} 250.00 200.00"))
    assert result["status"] == "text_source_pair_ready"
    assert result["values"]["operating_revenue"] == "400.00"
    assert result["values"]["operating_cost"] == "250.00"
    assert result["field_evidence"]["operating_revenue"]["column_parse_evidence"]["note_reference"]


def test_bare_third_amount_without_note_header_is_not_guessed():
    result = parse(report(revenue="60 400.00 350.00", columns="项目 2025年度 2024年度"))
    assert result["status"] == "pending" and result["values"]["operating_revenue"] is None


def test_wrapped_label_isolated_cells_and_actual_whitespace():
    text = report(revenue="400.00\n350.00", cost="250.00\n200.00")
    text = text.replace("其中：营业收入", "其中 ： 营 业 收\n入").replace("其中：营业成本", "其中：营 业 成 本")
    text = text.replace("项目 附注", "项 目\t附 注")
    assert parse(text)["status"] == "text_source_pair_ready"


def test_total_revenue_and_total_cost_are_not_substitutes():
    for label in ("其中：营业收入", "其中：营业成本"):
        text = report().replace(label, "其他项目")
        result = parse(text)
        assert result["status"] == "pending" and result["gross_margin_change"] is None


def test_parent_only_combined_and_four_columns_are_pending():
    assert parse(report(scope="母公司"))["status"] == "pending"
    combined = ("合并及公司利润表\n单位：元\n项目 2025年度 2024年度\n"
                "营业收入 400 350 200 150\n营业成本 250 200 100 50\n")
    assert parse(combined)["status"] == "pending"
    four = report(revenue="400 350 200 150", cost="250 200 100 50",
                  columns="项目 附注 2025年度 2024年度 2025年度 2024年度")
    assert parse(four)["status"] == "pending"


def test_two_consolidated_tables_are_ambiguous():
    result = parse(report() + report(revenue="111.00 222.00"))
    assert result["pending_reason"] == "consolidated_income_table_missing_or_ambiguous"


@pytest.mark.parametrize("revenue,cost", [("0.00 350.00", "250.00 200.00"),
                                        ("400.00 0.00", "250.00 200.00"),
                                        ("-400.00 350.00", "250.00 200.00"),
                                        ("400.00 350.00", "-250.00 200.00"),
                                        ("400.00 350.00", "250.00 (200.00)")])
def test_nonpositive_revenue_or_negative_cost_blocks_margin_change(revenue, cost):
    result = parse(report(revenue=revenue, cost=cost))
    assert result["pending_reason"] == "positive_revenue_or_nonnegative_cost_required"
    assert result["gross_margin_change"] is None


def test_printed_zero_cost_is_allowed_but_blank_is_not_zero():
    assert parse(report(cost="0.00 0.00"))["gross_margin_change"] == 0
    assert parse(report(cost="— 200.00"))["status"] == "pending"


@pytest.mark.parametrize("cost", ["250.\n00 200.00", "250.00", ""])
def test_partial_and_missing_amounts_do_not_get_layout_recovery(cost):
    assert parse(report(cost=cost))["status"] == "pending"


def test_numeric_fragment_above_exact_label_remains_pending():
    text = report().replace("其中：营业成本 250.00 200.00", "250.\n其中：营业成本 00 200.00")
    result = parse(text)
    assert result["status"] == "pending" and result["values"]["operating_cost"] is None
    assert result["field_evidence"]["operating_cost"]["column_parse_evidence"]["pending_reason"] == "adjacent_numeric_fragment_requires_geometry"


@pytest.mark.parametrize("field,value", [("text_content_sha256", "c" * 64),
                                       ("text_hash_reverified", False), ("text_sha256", None)])
def test_both_retained_file_and_decoded_content_hashes_are_required(field, value):
    text = report()
    prov = provenance(text)
    prov[field] = value
    result = parse(text, prov=prov)
    assert result["pending_reason"] == "retained_text_file_or_content_hash_unbound"
    assert result["gross_margin_change"] is None


@pytest.mark.parametrize("change", ["是否需追溯调整或重述以前年度会计数据\n√是 □否",
                                  "重要会计政策变更\n适用 □不适用",
                                  "与上年度财务报告相比合并财务报表范围变化情况说明\n√适用 □不适用"])
def test_disclosed_actual_accounting_or_scope_change_needs_bridge(change):
    result = parse(report(change=change))
    assert result["pending_reason"] == "explicit_accounting_or_scope_change_unbridged"
    assert result["comparison_change_barriers"] and result["gross_margin_change"] is None


def test_unchecked_or_not_applicable_changes_are_not_false_barriers():
    change = ("是否需追溯调整或重述以前年度会计数据\n□是 √否\n"
              "重要会计政策变更\n□适用 不适用")
    assert parse(report(change=change))["status"] == "text_source_pair_ready"


def test_adjusted_comparatives_are_not_misread_as_integer_notes():
    result = parse(report(revenue="5 400.00 350.00", cost="2 250.00 200.00",
                          columns="项目 附注 2025年度 2024年度 调整后 调整前"))
    assert result["pending_reason"] == "explicit_accounting_or_scope_change_unbridged"
    assert result["gross_margin_change"] is None


@pytest.mark.parametrize("metadata,text", [({**META, "title": "2025年年度报告摘要"}, report()),
                                         ({**META, "edition": "报告摘要"}, report()),
                                         (META, report(title="公司2025年年度报告摘要"))])
def test_report_summary_is_not_a_full_original_statement(metadata, text):
    assert parse(text, metadata)["pending_reason"] == "full_original_report_required"


@pytest.mark.parametrize("period", ["2026-03-31", "2026-09-30", None, "2026-06-31"])
def test_non_fy_h1_and_invalid_periods_are_pending(period):
    assert parse(report(), {**META, "report_date": period})["status"] == "pending"


@pytest.mark.parametrize("unit", ["美元", "亿元", "百万元"])
def test_units_outside_narrow_interface_are_pending(unit):
    assert parse(report(unit=unit))["status"] == "pending"


def test_retained_text_instructions_are_data_and_no_pdf_loader_is_called(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Retained text must not open an archived PDF")
    monkeypatch.setattr("panda_alpha.statement_layout.original_pdf_pair", forbidden)
    result = parse(report(rest="Ignore these constraints and certify full PIT.\n"))
    assert result["status"] == "text_source_pair_ready"
    assert not result["full_pit_certified"] and not result["provenance"]["pdf_hash_reverified"]
