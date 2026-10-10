"""Small original-print fixtures: no report copies and no return labels."""
from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from panda_alpha.financial_statements import flow_spans
from panda_alpha.gross_profitability import _actual_change_evidence
from panda_alpha.income_basis import (
    PAGE_TEXT_JSON_PROJECTION, _bound_capture, _income_fields, resolve_income_basis_change,
)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def bound(year, ID, revenue, cost, policy="", *, code="000020", barriers=()):
    header = f"\n单位：元\n项目 {year} 年度 {year - 1} 年度\n"
    printed = (f"公司{year}年年度报告\n===SOURCE_PAGE:56===\n3、合并利润表"
               + header + f"其中：营业收入 {revenue}\n其中：营业成本 {cost}\n"
               "4、母公司利润表\n===SOURCE_PAGE:114===\n"
               + policy + "\n（2） 重要会计估计变更\n□适用 不适用\n")
    raw_text = gzip.compress(printed.encode(), mtime=0)
    stock = {"code": code, "symbol": code, "announcement_id": ID,
             "report_date": f"{year}-12-31", "edition": "ORIGINAL_FULL_REPORT",
             "title": f"{year}年年度报告", "available_date": f"{year + 1}-04-21",
             "pdf_sha256": "a" * 64,
             "provenance": {"pdf_sha256": "a" * 64, "text_sha256": digest(raw_text)},
             "comparison_change_barriers": list(barriers)}
    stock["record_sha256"] = digest(json.dumps(stock, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    fields = {}
    for field, label, amounts in [("operating_revenue", "营业收入", revenue), ("operating_cost", "营业成本", cost)]:
        cells = amounts.split()
        row = f"其中：{label} {amounts}"
        fields[field] = {"statement_scope": "consolidated", "currency": "CNY", "printed_unit": "元",
                         "source_cells": cells, "current_yuan": cells[0].replace(",", ""),
                         "comparative_yuan": cells[1].replace(",", ""),
                         "actual_table_header": header, "source_row_offset": printed.index(row),
                         "source_label": row, "source_page": 56,
                         "period_evidence": flow_spans(header, f"{year}-12-31")}
    document = {"stock": stock, "fields": fields}
    raw_capture = gzip.compress(json.dumps(document, ensure_ascii=False, sort_keys=True).encode(), mtime=0)
    return document, {"capture_gzip": raw_capture, "capture_sha256": digest(raw_capture), "text_gzip": raw_text}, printed


def pair(policy, *, current_revenue="857,889,252.84 816,682,662.07",
         current_cost="762,947,772.85 725,116,468.05",
         old_revenue="816,682,662.07 726,541,177.76", old_cost="725,116,468.05 651,389,235.77",
         current_year=2025, barriers=()):
    current, cb, text = bound(current_year, "new", current_revenue, current_cost, policy, barriers=barriers)
    prior, pb, _ = bound(current_year - 1, "old", old_revenue, old_cost)
    return current, prior, text, {"current": cb, "prior": pb}


def resolve(args):
    current, prior, text, provenance = args
    return resolve_income_basis_change(current, prior, text=text, provenance=provenance)


def rebound(args, *, side="current"):
    """Rebind an intentionally malformed source, so tests reach field checks."""
    current, prior, text, prov = args
    doc = current if side == "current" else prior
    stock = doc["stock"]
    stock["record_sha256"] = digest(json.dumps({k: v for k, v in stock.items() if k != "record_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    raw = gzip.compress(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode(), mtime=0)
    prov[side]["capture_gzip"] = raw
    prov[side]["capture_sha256"] = digest(raw)


def replace_source_header(args, header, *, side="current"):
    """Rebind a minimal original-print header; fiscal proof stays unchanged."""
    doc = args[0] if side == "current" else args[1]
    binding = args[3][side]
    original = gzip.decompress(binding["text_gzip"]).decode()
    old_header = doc["fields"]["operating_revenue"]["actual_table_header"]
    printed = original.replace(old_header, header)
    raw_text = gzip.compress(printed.encode(), mtime=0)
    doc["stock"]["provenance"]["text_sha256"] = digest(raw_text)
    binding["text_gzip"] = raw_text
    for evidence in doc["fields"].values():
        evidence["actual_table_header"] = header
        evidence["source_row_offset"] = printed.index(evidence["source_label"])
    if side == "current":
        args[2] = printed
    rebound(args, side=side)


def use_legacy_page_json(args, *, side="current", content_hash=True):
    """Minimal list-of-page-strings container, matching the retained schema."""
    doc = args[0] if side == "current" else args[1]
    binding = args[3][side]
    original = gzip.decompress(binding.pop("text_gzip")).decode()
    intro, later = original.split("===SOURCE_PAGE:56===\n")
    income_page, policy_page = later.split("===SOURCE_PAGE:114===\n")
    pages = [f"报告正文第{index + 1}页" for index in range(114)]
    pages[0], pages[55], pages[113] = intro.rstrip("\n"), income_page.rstrip("\n"), policy_page.rstrip("\n")
    packet = {"source_sha256": doc["stock"]["pdf_sha256"],
              "pages": [page.replace("\n", "\r\n") for page in pages]}
    decoded = "\n".join(f"===SOURCE_PAGE:{index + 1}===\n{page}" for index, page in enumerate(pages))
    raw = json.dumps(packet, ensure_ascii=False).encode()
    binding["text_json"] = raw
    prov = doc["stock"]["provenance"]
    prov.update(text_sha256=digest(raw), text_path="retained-page-text.json", text_storage_format="page_text_json",
                text_storage_binding={"format": "page_text_json", "path": "retained-page-text.json",
                                      "sha256": digest(raw), "projection": PAGE_TEXT_JSON_PROJECTION})
    if content_hash:
        prov["text_content_sha256"] = digest(decoded.encode())
    for evidence in doc["fields"].values():
        evidence["source_row_offset"] = decoded.index(evidence["source_label"])
    if side == "current":
        args[2] = decoded
    rebound(args, side=side)
    return decoded


def rebind_page_packet(args, packet, *, side="current"):
    doc = args[0] if side == "current" else args[1]
    raw = json.dumps(packet, ensure_ascii=False).encode()
    args[3][side]["text_json"] = raw
    prov = doc["stock"]["provenance"]
    prov["text_sha256"] = digest(raw)
    prov["text_storage_binding"]["sha256"] = digest(raw)
    rebound(args, side=side)


NO_POLICY = ("重要会计政策变更\n适用 □不适用\n单位：元\n"
             "会计政策变更的内容和原因 受重要影响的报表项目名称 影响金额\n"
             "报告期内本公司无重要会计政策变更。")
NO_EFFECT = ("重要会计政策变更\n适用 □不适用\n"
             "1.公司执行解释第17号流动负债与非流动负债的划分，该项会计政策变更对公司财务报表无影响。\n"
             "2.公司执行解释第17号关于供应商融资安排的披露规定。\n"
             "3.公司执行解释第17号售后租回的会计处理，该项会计政策变更对公司财务报表无影响。\n"
             "4.公司执行解释第18号保证类质量保证的会计处理，该项会计政策变更对公司财务报表无影响。")


def warranty(cost="33,620,826.08", expense="-33,620,826.08", *, comparable=True):
    period = "并对可比期间信息进行追溯调整" if comparable else "本期处理"
    return ("重要会计政策变更\n适用 □不适用\n单位：元\n"
            f"公司执行企业会计准则解释第18号保证类质量保证的会计处理，{period}。\n"
            f"营业成本 {cost}\n销售费用 {expense}\n"
            "1.公司执行解释第17号流动负债划分，该项会计政策变更对公司财务报表无影响。\n"
            "2.公司执行解释第17号关于供应商融资安排的披露规定。\n"
            f"3.公司执行解释第18号保证类质量保证的会计处理，{period}。")


def warranty_args(**kwargs):
    return pair(warranty(**kwargs), current_year=2024,
                current_revenue="2,735,154,206.29 2,146,446,119.70",
                current_cost="2,454,869,636.03 2,001,611,578.38",
                old_revenue="2,146,446,119.70 1,487,992,571.91",
                old_cost="1,967,990,752.30 1,403,836,774.68")


def test_generic_helper_does_not_match_body_no_policy_or_later_estimate_checkbox():
    text = NO_POLICY + "\n（2）重要会计估计变更\n适用 □不适用\n"
    events = _actual_change_evidence(text, "")
    assert len(events) == 1  # The genuine policy heading's checkbox stays cautious.
    assert events[0]["kind"] == "accounting_policy"
    assert "会计估计" not in events[0]["source_evidence"]
    assert _actual_change_evidence("报告期内本公司无重要会计政策变更。\n（2）重要会计估计变更\n适用 □不适用", "") == []


@pytest.mark.parametrize("choice", ["□适用 不适用", "", "见附注"])
def test_generic_helper_never_reads_next_section_choice(choice):
    text = "（1）重要会计政策变更\n" + choice + "\n（2）重要会计估计变更\n适用 □不适用"
    assert _actual_change_evidence(text, "") == []


def test_generic_true_restatement_scope_and_adjusted_header_are_retained():
    text = ("公司是否需追溯调整或重述以前年度会计数据\n√是 □否\n"
            "七、与上年度财务报告相比，合并报表范围发生变化的情况说明\n适用 □不适用")
    assert {x["kind"] for x in _actual_change_evidence(text, "项目2024年度2023年度调整后")} == {
        "restatement", "consolidation_scope", "adjusted_comparative_columns"}


@pytest.mark.parametrize("text,kind", [
    ("公司是否需追溯调整或重述以前年度会计数据：√是 □否", "restatement"),
    ("公司是否需追溯调整或重述\n以前年度会计数据：\n√是 □否", "restatement"),
    ("七、与上年度财务报告相比，合并报表范围发生变化的情况说明：适用 □不适用", "consolidation_scope"),
    ("七、与上年度财务报告相比，\n合并报表范围发生变化的\n情况说明\n适用 □不适用", "consolidation_scope"),
    ("与上年度财务报告相比，合并\n财务报表\n范围发生变化的情况说明：\n☑适用 □不适用", "consolidation_scope"),
    ("（1）重要会计政策变更：适用 □不适用\n执行新政策。\n（2）重要会计估计变更\n□适用 不适用", "accounting_policy"),
])
def test_affirmative_same_line_or_wrapped_non_policy_questions_stay_blocked(text, kind):
    assert [e["kind"] for e in _actual_change_evidence(text, "")] == [kind]


def test_scope_resolution_api_retains_same_line_event_after_income_bridge():
    text = NO_EFFECT + "\n七、与上年度财务报告相比，合并报表范围发生变化的情况说明：适用 □不适用"
    args = pair(text)
    # This fixture puts a later scope question within the policy section.
    # A complete policy bridge cannot swallow or remove that source event.
    result = resolve(args)
    assert [e["kind"] for e in result["remaining_non_income_barriers"]] == ["consolidation_scope"]
    assert not result["full_pit_certified"]


def test_explicit_no_policy_unchanged_prior_prints_resolve_only_income():
    barriers = [{"kind": "consolidation_scope", "source_evidence": "本期收购子公司"},
                {"kind": "actual_balance_comparative_adjusted_header"}]
    args = pair(NO_POLICY, barriers=barriers)
    before = deepcopy(args[0])
    out = resolve(args)
    assert out["status"] == "income_fields_resolved"
    assert set(out["resolved_fields"]) == {"operating_revenue", "operating_cost"}
    assert out["remaining_non_income_barriers"] == barriers
    assert args[0] == before and not out["full_pit_certified"] and not out["pdf_original_freshly_checked"]


def test_itemized_no_effect_same_printed_annual_comparison_resolves():
    out = resolve(pair(NO_EFFECT))
    assert out["status"] == "income_fields_resolved"
    assert out["evidence"][0]["comparative_deltas_yuan"] == {"operating_revenue": "0.00", "operating_cost": "0.00"}


def test_numbered_policy_heading_and_unchecked_management_duplicate_keep_scope_event():
    policy = ("会计政策变更：\n①公司执行解释第18号保证类质量保证，对公司可比期间财务报表数据无影响。\n"
              "②公司执行解释第17号，对公司财务报表无影响。\n会计估计变更：\n估计附注。\n"
              "与上年度财务报告相比，合并报表范围发生变化的情况说明\n适用 □不适用\n"
              "===SOURCE_PAGE:114===\n（1）重要会计政策变更\n适用 □不适用\n单位：元\n"
              "①公司执行解释第18号保证类质量保证，对公司可比期间财务报表数据无影响。\n"
              "②公司执行解释第17号，对公司财务报表无影响。")
    out = resolve(pair(policy))
    assert out["status"] == "income_fields_resolved"
    assert [x["kind"] for x in out["remaining_non_income_barriers"]] == ["consolidation_scope"]


def test_real_000419_style_running_caption_does_not_create_third_fiscal_column():
    args = list(pair(NO_EFFECT))
    prior = args[1]
    original = gzip.decompress(args[3]["prior"]["text_gzip"]).decode()
    header = prior["fields"]["operating_revenue"]["actual_table_header"]
    caption = "===SOURCE_PAGE:56===\n长沙通程控股股份有限公司 2024 年年度报告全文\n56\n"
    new_header = header + caption
    updated = original.replace(header, new_header)
    raw_text = gzip.compress(updated.encode(), mtime=0)
    prior["stock"]["provenance"]["text_sha256"] = digest(raw_text)
    args[3]["prior"]["text_gzip"] = raw_text
    for e in prior["fields"].values():
        e["actual_table_header"] = new_header
        e["source_row_offset"] = updated.index(e["source_label"])
    rebound(args, side="prior")
    assert resolve(args)["status"] == "income_fields_resolved"


@pytest.mark.parametrize("header", [
    "\n2025 年 1—12 月\n单位：元币种：人民币\n项目 附注 2025 年度 2024 年度\n",
    "\n2025 年 1—12 月\n单位：元 币种：人民币\n项目 附注 2025 年度 2024 年度\n",
])
def test_600010_600018_matching_period_caption_is_not_a_third_fiscal_column(header):
    # These two small header forms come from retained600010/600018FY2025
    # sources; their income rows remain bound to the original full header.
    args = list(pair(NO_EFFECT))
    replace_source_header(args, header)
    prior_header = "\n2024 年 1—12 月\n单位：元 币种：人民币\n项目 附注 2024 年度 2023 年度\n"
    replace_source_header(args, prior_header, side="prior")
    assert resolve(args)["status"] == "income_fields_resolved"
    assert _income_fields(args[0], args[2], "2025-12-31")["operating_revenue"]["comparative"].is_finite()


@pytest.mark.parametrize("header", [
    "\n2024 年 1—12 月\n单位：元\n项目 2025 年度 2024 年度\n",
    "\n2025 年 1—6 月\n单位：元\n项目 2025 年度 2024 年度\n",
    "\n2025 年 2—12 月\n单位：元\n项目 2025 年度 2024 年度\n",
    "\n2025 年 1—12 月\n单位：元\n项目 本期金额 上期金额\n",
    "\n2025 年 1—12 月\n单位：元\n项目 2025 年度 2024 年度 2023 年度\n",
    "\n2025 年 1—12 月\n单位：元\n项目 2025 年度 2024 年度\n项目 2024 年度 2023 年度\n",
    "\n2025 年 1—12 月\n2025 年 1—12 月\n单位：元\n项目 2025 年度 2024 年度\n",
])
def test_period_caption_cannot_supply_or_override_ambiguous_fiscal_columns(header):
    args = list(pair(NO_EFFECT))
    replace_source_header(args, header)
    assert resolve(args)["status"] == "pending"


@pytest.mark.parametrize("bad", ["capture", "header", "offset", "material"])
def test_matching_caption_keeps_capture_header_offset_and_no_material_effect_gates(bad):
    args = list(pair(NO_EFFECT))
    header = "\n2025 年 1—12 月\n单位：元\n项目 2025 年度 2024 年度\n"
    replace_source_header(args, header)
    if bad == "capture":
        args[3]["current"]["capture_sha256"] = "0" * 64
    elif bad == "header":
        args[0]["fields"]["operating_cost"]["actual_table_header"] = header.replace("单位：元", "单位：元 币种：人民币")
        rebound(args)
    elif bad == "offset":
        args[0]["fields"]["operating_cost"]["source_row_offset"] = 0
        rebound(args)
    else:
        text = args[2].replace("无影响", "无重大影响")
        raw_text = gzip.compress(text.encode(), mtime=0)
        args[0]["stock"]["provenance"]["text_sha256"] = digest(raw_text)
        args[3]["current"]["text_gzip"] = raw_text
        args[2] = text
        rebound(args)
    assert resolve(args)["status"] == "pending"


@pytest.mark.parametrize("side,content_hash", [("current", True), ("prior", True), ("prior", False)])
def test_source_bound_declared_legacy_page_json_has_exact_contiguous_projection(side, content_hash):
    args = list(pair(NO_EFFECT, barriers=[{"kind": "consolidation_scope"}]))
    decoded = use_legacy_page_json(args, side=side, content_hash=content_hash)
    doc = args[0] if side == "current" else args[1]
    assert _bound_capture(doc, args[3][side])[1] == decoded
    assert decoded.count("===SOURCE_PAGE:") == 114
    assert "===SOURCE_PAGE:56===\n3、合并利润表" in decoded
    result = resolve(args)
    assert result["status"] == "income_fields_resolved"
    assert result["remaining_non_income_barriers"] == [{"kind": "consolidation_scope"}]
    assert not result["full_pit_certified"] and not result["pdf_original_freshly_checked"]


@pytest.mark.parametrize("bad", ["pdf", "missing_key", "extra_key", "pages_object", "empty_pages",
                                 "empty_page", "duplicate_page", "embedded_marker", "non_string"])
def test_legacy_page_json_source_identity_and_page_schema_failures_stay_pending(bad):
    args = list(pair(NO_EFFECT))
    use_legacy_page_json(args, content_hash=False)
    packet = json.loads(args[3]["current"]["text_json"])
    if bad == "pdf":
        packet["source_sha256"] = "b" * 64
    elif bad == "missing_key":
        del packet["source_sha256"]
    elif bad == "extra_key":
        packet["unknown_projection"] = True
    elif bad == "pages_object":
        packet["pages"] = {"1": "正文"}
    elif bad == "empty_pages":
        packet["pages"] = []
    elif bad == "empty_page":
        packet["pages"][1] = " \r\n"
    elif bad == "duplicate_page":
        packet["pages"][2] = packet["pages"][1]
    elif bad == "embedded_marker":
        packet["pages"][1] += "\n===SOURCE_PAGE:200==="
    else:
        packet["pages"][1] = {"page": 2, "text": "正文"}
    rebind_page_packet(args, packet)
    assert resolve(args)["status"] == "pending"


@pytest.mark.parametrize("bad", ["raw_sha", "format", "projection", "storage_sha", "path", "content_sha",
                                 "two_bindings", "missing_binding", "decoded_argument", "header", "offset"])
def test_legacy_page_json_binding_declaration_and_original_field_gates_stay_pending(bad):
    args = list(pair(NO_EFFECT))
    use_legacy_page_json(args)
    prov = args[0]["stock"]["provenance"]
    if bad == "raw_sha":
        prov["text_sha256"] = "0" * 64
    elif bad == "format":
        prov["text_storage_format"] = "unknown"
    elif bad == "projection":
        prov["text_storage_binding"]["projection"] = "guess page order"
    elif bad == "storage_sha":
        prov["text_storage_binding"]["sha256"] = "0" * 64
    elif bad == "path":
        prov["text_storage_binding"]["path"] = "a different file"
    elif bad == "content_sha":
        prov["text_content_sha256"] = "0" * 64
    elif bad == "two_bindings":
        args[3]["current"]["text_gzip"] = None
    elif bad == "missing_binding":
        del args[3]["current"]["text_json"]
    elif bad == "decoded_argument":
        args[2] += "changed"
    elif bad == "header":
        args[0]["fields"]["operating_cost"]["actual_table_header"] += "absent header"
    else:
        args[0]["fields"]["operating_cost"]["source_row_offset"] = 0
    rebound(args)
    assert resolve(args)["status"] == "pending"


def test_json_bytes_cannot_masquerade_as_gzip_or_hide_duplicate_keys():
    args = list(pair(NO_EFFECT))
    use_legacy_page_json(args)
    binding = args[3]["current"]
    binding["text_gzip"] = binding.pop("text_json")
    assert resolve(args)["pending_reason"] == "retained_text_binding_format_mismatch"
    args = list(pair(NO_EFFECT))
    use_legacy_page_json(args)
    raw = args[3]["current"]["text_json"]
    raw = raw.replace(b'{"source_sha256":', b'{"pages": [], "source_sha256":', 1)
    args[3]["current"]["text_json"] = raw
    prov = args[0]["stock"]["provenance"]
    prov["text_sha256"] = prov["text_storage_binding"]["sha256"] = digest(raw)
    rebound(args)
    assert resolve(args)["pending_reason"] == "page_text_json_duplicate_key"


def test_legacy_page_json_does_not_turn_no_material_impact_into_no_impact():
    args = list(pair(NO_EFFECT.replace("无影响", "无重大影响")))
    use_legacy_page_json(args)
    use_legacy_page_json(args, side="prior")
    assert resolve(args)["pending_reason"] == "unbridged_policy_item"


def test_quantified_warranty_source_rows_reconcile_old_and_new_comparison():
    out = resolve(warranty_args())
    assert out["status"] == "income_fields_resolved"
    assert out["evidence"][0]["comparison_period"] == "2023-12-31"
    assert out["evidence"][0]["comparative_deltas_yuan"]["operating_cost"] == "33620826.08"


def test_negative_source_cost_bridge_is_not_forced_positive():
    args = pair(warranty("-33.00", "33.00"), current_cost="100.00 67.00", old_cost="100.00 60.00")
    assert resolve(args)["status"] == "income_fields_resolved"


@pytest.mark.parametrize("kwargs", [{"expense": "33,620,826.08"}, {"cost": "33,620,826.09"}, {"comparable": False}])
def test_unknown_period_wrong_direction_or_amount_cannot_bridge(kwargs):
    assert resolve(warranty_args(**kwargs))["status"] == "pending"


def test_000068_unassigned_34000_or_tax_preamble_are_pending():
    policy = ("重要会计政策变更\n适用 □不适用\n单位：元\n"
              "财政部解释第18号保证类质量保证的会计处理。\n营业成本、销售费用 34,000.00")
    assert resolve(pair(policy))["status"] == "pending"
    policy = ("重要会计政策变更\n适用 □不适用\n执行解释第16号单项交易产生的资产和负债相关递延所得税。\n"
              "1.执行解释第17号对本公司报表项目和金额无影响。\n"
              "2.执行解释第18号对本公司报表项目和金额无影响。")
    assert resolve(pair(policy))["status"] == "pending"


def test_no_material_impact_or_extra_unbridged_item_not_treated_zero():
    assert resolve(pair(NO_EFFECT.replace("无影响", "无重大影响")))["status"] == "pending"
    assert resolve(pair(NO_EFFECT + "\n5.本期采用净额法确认营业收入。"))["status"] == "pending"
    assert resolve(pair(NO_EFFECT + "另按净额法调整营业收入。"))["status"] == "pending"


@pytest.mark.parametrize("field", ["operating_revenue", "operating_cost"])
def test_no_policy_does_not_resolve_changed_comparative(field):
    args = pair(NO_POLICY)
    args[0]["fields"][field]["comparative_yuan"] = "1.00"
    rebound(args)
    assert resolve(args)["status"] == "pending"


@pytest.mark.parametrize("bad", ["capture_hash", "capture_document", "text_hash", "decoded_text", "stock_record_hash", "period", "amount", "scope", "page"])
def test_source_binding_identity_period_and_field_proofs_are_required(bad):
    args = list(pair(NO_POLICY))
    current, prior, text, prov = args
    if bad == "capture_hash":
        prov["current"]["capture_sha256"] = "0" * 64
    elif bad == "capture_document":
        current["fields"]["operating_cost"]["current_yuan"] = "1.00"
    elif bad == "text_hash":
        prov["prior"]["text_gzip"] = gzip.compress(b"wrong", mtime=0)
    elif bad == "decoded_text":
        args[2] = text + "changed"
    elif bad == "stock_record_hash":
        current["stock"]["code"] = "999999"
        raw = gzip.compress(json.dumps(current, ensure_ascii=False, sort_keys=True).encode(), mtime=0)
        prov["current"]["capture_gzip"] = raw
        prov["current"]["capture_sha256"] = digest(raw)
    else:
        item = current["fields"]["operating_cost"]
        if bad == "period":
            item["period_evidence"]["comparative_end"] = "2023-12-31"
        elif bad == "amount":
            item["comparative_yuan"] = "1.00"
        elif bad == "scope":
            item["statement_scope"] = "parent"
        else:
            item["source_page"] = 999
        rebound(args)
    assert resolve(args)["status"] == "pending"
