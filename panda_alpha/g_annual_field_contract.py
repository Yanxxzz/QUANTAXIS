"""Byte-bound annual G operands, independent of AR/INV/AP populations.

Amount facts and current-report comparability are separate. This module never
selects a vintage, substitutes an H1, applies masks, or certifies full PIT.
Existing captured geometry annotations can be reused, not freshly verified.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import re

from panda_alpha.financial_statements import (
    PREFIX, TABLE, UNIT_MULTIPLIERS, amount_unit, column_pair, money, printed_quantum_yuan, source_page,
)
from panda_alpha.gross_profitability import _actual_change_evidence
from panda_alpha.income_basis import POLICY_HEADING, _bound_capture, _income_fields, policy_change_sections
from panda_alpha.trade_accrual import _balance_sections, trade_stock_periods


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _location(text, start, end):
    return {"offset": start, "end_offset": end, "source_page": source_page(text, start),
            "excerpt_sha256": _sha(text[start:end])}


def _choice(text, end, yes, no):
    """Three-state immediate choice; an absent check is not a negative."""
    tail = text[end:]
    for line in tail.splitlines():
        dense = re.sub(r"\s+", "", line).strip("：:")
        if not dense:
            continue
        a = bool(re.search(r"[☑√✓■]" + re.escape(yes), dense))
        b = bool(re.search(r"[☑√✓■]" + re.escape(no), dense))
        return "affirmative" if a and not b else "negative" if b and not a else "unknown"
    return "unknown"


def _H1_scope_none_note(text, stock):
    """One bounded H1 note of the exact one-item or six-item explicit-none form."""
    if not str(stock.get("report_date", "")).endswith("-06-30"):
        return None
    headings = list(re.finditer(r"(?m)^[^\S\n]*九[、.．][^\S\n]*合并范围的变更[^\S\n]*$", text))
    if len(headings) != 1:
        return None
    hit = headings[0]
    end = re.search(r"(?m)^[^\S\n]*十[、.．][^\S\n]*在其他主体中的权益[^\S\n]*$", text[hit.end():])
    if not end:
        return None
    stop = hit.end() + end.start()
    body = re.sub(r"\s+", "", text[hit.end():stop])
    simple_none = bool(re.fullmatch(r"(?:1[、.．]其他)?无[。.]?", body))
    labels = ["非同一控制下企业合并", "同一控制下企业合并", "反向购买", "处置子公司", "其他原因的合并范围变动", "其他"]
    six_none = bool(re.fullmatch("".join(str(index) + r"[、.．]" + label + r"无[。.]?"
                                        for index, label in enumerate(labels, 1)), body))
    if not simple_none and not six_none:
        return None
    return {"kind": "consolidation_scope", "choice": "negative",
            "method": "bounded_H1_note_explicit_all_six_none" if six_none else "bounded_H1_note_explicit_none",
            **_location(text, hit.start(), stop)}


def current_comparability(text, income_header, stock):
    """Retain true or unknown current changes; absence of flags is not clean."""
    pending, locators = [], []
    sections = policy_change_sections(text)
    if not sections:
        pending.append("current_accounting_policy_section_unbound")
    for section in sections:
        body = section["source_evidence"]
        heading = POLICY_HEADING.search(body)
        heading_end = heading.end() if heading else len(body)
        rest = body[heading_end:]
        dense = re.sub(r"\s+", "", rest)
        choice = _choice(body, heading_end, "适用", "不适用")
        # Only a bounded section's explicit issuer no-change or bare '无'.
        explicit_none = bool(re.fullmatch(r"(?:本公司|本集团|公司|集团)?(?:本期|报告期内)?无(?:重要)?会计政策变更[。.]?", dense)
                             or dense == "无")
        status = "negative" if choice == "negative" or explicit_none else choice
        locators.append({"kind": "accounting_policy", "choice": status,
                         **_location(text, section["start"], section["end"])})
        if status != "negative":
            pending.append("current_accounting_policy_G_income_and_assets_bridge_pending")
    patterns = [("restatement", r"是否需追溯调整或重述\s*以前年度会计数据", "是", "否"),
                ("consolidation_scope", r"与上年度财务报告相比[\s\S]{0,35}合并\s*(?:财务\s*)?报表\s*范围[\s\S]{0,30}情况说明", "适用", "不适用")]
    for kind, pattern, yes, no in patterns:
        hits = list(re.finditer(pattern, text))
        if not hits:
            note = _H1_scope_none_note(text, stock) if kind == "consolidation_scope" else None
            if note:
                locators.append(note)
            else:
                pending.append("current_" + kind + "_section_unbound")
        for hit in hits:
            status = _choice(text, hit.end(), yes, no)
            locators.append({"kind": kind, "choice": status,
                             **_location(text, hit.start(), min(len(text), hit.end() + 200))})
            if status != "negative":
                pending.append("current_" + kind + "_G_field_scope_pending")
    actual = _actual_change_evidence(text, income_header)
    original = stock.get("comparison_change_barriers", [])
    if actual:
        pending.extend("current_explicit_" + str(e.get("kind")) + "_bridge_pending" for e in actual)
    if original:
        pending.extend("captured_current_" + str(e.get("kind")) + "_gate_preserved" for e in original)
    if stock.get("edition") != "ORIGINAL_FULL_REPORT":
        pending.append("current_revised_edition_G_version_bridge_pending")
    return {"status": "current_G_comparability_ready" if not pending else "current_G_comparability_pending",
            "pending_reasons": sorted(set(pending)), "locators": locators,
            "captured_barriers_preserved": original,
            "actual_change_kinds": sorted({e.get("kind") for e in actual})}


def _statement_bounds(e, text, label):
    start, end, offset = (e.get(k) for k in ("statement_start_offset", "statement_end_offset", "source_row_offset"))
    if not (isinstance(start, int) and isinstance(end, int) and isinstance(offset, int)
            and 0 <= start <= offset < end <= len(text)):
        raise ValueError("G_" + label + "_statement_offsets_pending")
    heading = e.get("statement_heading", "")
    if not heading or heading not in text[max(0, start - len(heading) - 100):start]:
        raise ValueError("G_" + label + "_consolidated_heading_unbound")
    expected = "合并资产负债表" if label == "assets" else "合并利润表"
    boundary = e.get("end_boundary_heading", "")
    if (expected not in re.sub(r"\s+", "", heading) or not boundary
            or boundary not in text[end:end + len(boundary) + 100]):
        raise ValueError("G_" + label + "_statement_scope_boundary_pending")


def _assets(document, stock, text, period):
    e = document.get("fields", {}).get("total_assets", {})
    old = stock.get("field_evidence", {}).get("total_assets", {})
    if (old.get("statement_scope") != "consolidated" or old.get("currency") != "CNY"
            or old.get("status") != "same_report_current_and_opening_amounts"):
        raise ValueError("G_assets_same_report_scope_currency_pending")
    geometry = e.get("status") == "original_consolidated_dated_grid_two_stock_amounts_bound"
    if geometry:
        # Reuse bound grid annotations only inside their one actual native
        # consolidated table. A continuation row need not share header page.
        row_offset = e.get("native_source_row_offset")
        if not isinstance(row_offset, int):
            raise ValueError("G_assets_retained_geometry_row_offset_pending")
        native = _balance_sections(text, period, text[:2500])
        joined = [(c, s) for c in stock.get("statement_evidence", []) for s in native
                  if c.get("status") == s.get("status") == "bounded_consolidated_statement"
                  and c.get("header") == s.get("header") and c.get("period") == s.get("period")
                  and c.get("source_page") == s.get("source_page")
                  and s["body_start"] <= row_offset < s["body_start"] + len(s["body"])]
        sections = [c for c, _ in joined]
        if len(joined) != 1:
            raise ValueError("G_assets_unique_consolidated_table_pending")
        native_section = joined[0][1]
        start = native_section["body_start"]; end = start + len(native_section["body"])
        header_positions = [m.start() + start for m in re.finditer(re.escape(native_section["header"]), text[start:end])]
        if len(header_positions) != 1:
            raise ValueError("G_assets_unique_geometry_native_header_pending")
        header_offset = header_positions[0]
    else:
        # Header and total-assets rows can occupy different physical pages.
        # Bind one exact original header inside this row's original statement
        # interval, rather than equating the two page numbers.
        _statement_bounds(e, text, "assets")
        start, end = e["statement_start_offset"], e["statement_end_offset"]
        original_header = e.get("actual_table_header", "")
        positions = [m.start() + start for m in re.finditer(re.escape(original_header), text[start:end])] if original_header else []
        if len(positions) != 1:
            raise ValueError("G_assets_unique_bounded_header_pending")
        header_offset = positions[0]
        sections = [s for s in stock.get("statement_evidence", [])
                    if s.get("status") == "bounded_consolidated_statement" and s.get("header") == original_header
                    and s.get("period") == e.get("period_evidence")
                    and s.get("source_page") == source_page(text, header_offset)]
    if len(sections) != 1:
        raise ValueError("G_assets_unique_consolidated_table_pending")
    section, unit = sections[0], old.get("printed_unit")
    header = section.get("header", "")
    if not header or header not in text or unit not in UNIT_MULTIPLIERS:
        raise ValueError("G_assets_original_header_or_unit_pending")
    actual_unit = amount_unit(header)
    if actual_unit.get("status") != "unit_verified" or actual_unit.get("printed_unit") != unit:
        raise ValueError("G_assets_printed_unit_mismatch")
    periods = trade_stock_periods(header, period, text[:2500])
    if periods.get("status") != "same_report_stock_columns_bound":
        raise ValueError("G_assets_current_opening_period_pending")
    if periods != section.get("period") or periods != old.get("period_evidence"):
        raise ValueError("G_assets_supplied_period_role_mismatch")
    cells = old.get("cells", [])
    if len(cells) != 2:
        raise ValueError("G_assets_two_printed_columns_pending")
    current, opening = [money(cell) for cell in cells]
    if current is None or opening is None:
        raise ValueError("G_assets_printed_amount_pending")
    current, opening = current * UNIT_MULTIPLIERS[unit], opening * UNIT_MULTIPLIERS[unit]
    if (current <= 0 or opening <= 0 or money(str(old.get("amount_yuan"))) != current
            or money(str(old.get("comparative_amount_yuan"))) != opening
            or old.get("stock_asof") != period or old.get("comparative_stock_asof") != periods["opening_stock_asof"]):
        raise ValueError("G_assets_positive_amount_or_role_mismatch")
    stock_values = stock.get("values", {}).get("total_assets", {})
    if (not isinstance(stock_values, dict) or money(str(stock_values.get("current"))) != current
            or money(str(stock_values.get("opening"))) != opening):
        raise ValueError("G_assets_original_stock_values_mismatch")
    offset = e.get("native_source_row_offset") if geometry else e.get("source_row_offset")
    label = e.get("native_source_label", "") if geometry else e.get("source_label", "")
    if (not isinstance(offset, int) or offset < 0 or not label or not text.startswith(label, offset)
            or source_page(text, offset) != e.get("source_page") or e.get("source_page") != old.get("source_page")
            or not all(str(cell) in label for cell in cells)):
        raise ValueError("G_assets_native_row_offset_page_or_cells_mismatch")
    if geometry:
        dates = e.get("original_header_date_cells", [])
        normal_dates = [re.sub(r"\s+", "", str(value)) for value in dates] if isinstance(dates, list) else []
        dated_page = e.get("dated_header_source_page")
        if (e.get("bounded_native_and_actual_grid_row_text_match") is not True
                or e.get("same_table_grid_x_edges_verified") is not True
                or not isinstance(dated_page, int)
                or not source_page(text, start) <= dated_page <= source_page(text, offset)
                or e.get("currency") != "CNY" or e.get("printed_unit") != unit
                or normal_dates != [period.replace("-", "年", 1).replace("-", "月", 1) + "日",
                                    periods["opening_stock_asof"].replace("-", "年", 1).replace("-", "月", 1) + "日"]
                or e.get("current_stock_asof") != period or e.get("opening_stock_asof") != periods["opening_stock_asof"]
                or money(str(e.get("current_yuan"))) != current or money(str(e.get("opening_yuan"))) != opening):
            raise ValueError("G_assets_retained_geometry_annotation_pending")
    continuation_unit = amount_unit(text[header_offset:offset])
    if (continuation_unit.get("status") != "unit_verified"
            or continuation_unit.get("printed_unit") != unit):
        raise ValueError("G_assets_bounded_continuation_unit_pending")
    if not geometry:
        if (e.get("actual_table_header") != header or e.get("source_cells") != cells
                or e.get("period_evidence") != periods or e.get("printed_unit") != unit
                or e.get("statement_scope") != "consolidated" or e.get("currency") != "CNY"
                or money(str(e.get("current_yuan"))) != current or money(str(e.get("comparative_yuan"))) != opening):
            raise ValueError("G_assets_capture_annotation_mismatch")
    return {"current_yuan": str(current), "opening_yuan": str(opening),
            "current_stock_asof": period, "opening_stock_asof": periods["opening_stock_asof"],
            "opening_fiscal_year": periods["opening_fiscal_year"], "opening_date_method": periods["method"],
            "opening_literal_date_printed": periods["method"] == "explicit_stock_column_dates",
            "printed_unit": unit, "current_quantum_yuan": printed_quantum_yuan(cells[0], unit),
            "opening_quantum_yuan": printed_quantum_yuan(cells[1], unit),
            "source_page": source_page(text, offset), "source_row_offset": offset,
            "source_header_page": section["source_page"], "source_header_offset": header_offset,
            "statement_start_offset": start, "statement_end_offset": end,
            "source_row_sha256": _sha(label), "source_header_sha256": _sha(header),
            "retained_geometry_reused": geometry, "fresh_PDF_geometry_verified": False}


def bound_annual_G_fields(document, binding):
    """Return only source facts and unresolved current gates, never a signal."""
    stock, text = _bound_capture(document, binding)
    period = stock["report_date"]
    if (not period.endswith("-12-31") or not stock.get("annual_signal_eligible")
            or stock.get("excluded_source_kind") or stock.get("edition") not in {"ORIGINAL_FULL_REPORT", "REVISED_FULL_REPORT"}):
        raise ValueError("G_full_annual_source_kind_pending")
    income = _income_fields(document, text, period)
    table_keys = ("actual_table_header", "statement_start_offset", "statement_end_offset",
                  "statement_heading", "end_boundary_heading", "statement_scope", "currency", "printed_unit")
    if len({tuple(row["evidence"].get(key) for key in table_keys) for row in income.values()}) != 1:
        raise ValueError("G_income_fields_not_one_same_consolidated_statement")
    amounts = {}
    for field, row in income.items():
        e = row["evidence"]
        _statement_bounds(e, text, field)
        printed_unit = amount_unit(e["actual_table_header"])
        if (printed_unit.get("status") != "unit_verified" or printed_unit.get("currency") != "CNY"
                or printed_unit.get("printed_unit") != e["printed_unit"]):
            raise ValueError("G_income_original_header_unit_mismatch")
        if row["current"] < 0 or row["comparative"] < 0:
            raise ValueError("G_income_nonnegative_printed_domain_pending")
        amounts[field] = {"current_yuan": str(row["current"]), "comparative_yuan": str(row["comparative"]),
                         "current_span": [period[:4] + "-01-01", period],
                         "comparative_span": [str(int(period[:4]) - 1) + "-01-01", str(int(period[:4]) - 1) + "-12-31"],
                         "printed_unit": e["printed_unit"], "current_quantum_yuan": printed_quantum_yuan(e["source_cells"][0], e["printed_unit"]),
                         "comparative_quantum_yuan": printed_quantum_yuan(e["source_cells"][1], e["printed_unit"]),
                         "source_page": e["source_page"], "source_row_offset": e["source_row_offset"],
                         "source_row_sha256": _sha(e["source_label"]), "source_header_sha256": _sha(e["actual_table_header"])}
    amounts["total_assets"] = _assets(document, stock, text, period)
    comparison = current_comparability(text, income["operating_revenue"]["evidence"]["actual_table_header"], stock)
    return {"amounts_status": "three_G_same_report_amounts_bound", "amounts": amounts,
            "current_comparability": comparison,
            "source_identity": {k: stock.get(k) for k in ["code", "announcement_id", "report_date", "published_at", "edition", "record_sha256", "pdf_sha256"]},
            "cashflow_fields_captured": [k for k in document.get("fields", {}) if "cash" in k],
            "non_G_fields_required": [], "full_PIT_certified": False,
            "income_bridge_used_as_assets_bridge": False, "original_pdf_freshly_verified": False}


def bound_H1_assets_from_native(document, binding):
    """Project existing H1 stock facts through one native bounded table.

    H1 captures need not carry annual supplement fields/offset annotations.
    Reuse the pure native statement parser; do not invoke its PDF fallback or
    update the capture. This binds only H1 closing/opening assets, not annual
    income, policy, consolidation, versions or an admission mask.
    """
    stock, text = _bound_capture(document, binding)
    period = stock["report_date"]
    if (not period.endswith("-06-30") or stock.get("excluded_source_kind")
            or stock.get("edition") not in {"ORIGINAL_FULL_REPORT", "REVISED_FULL_REPORT"}):
        raise ValueError("H1_assets_full_halfyear_source_kind_pending")
    sections = _balance_sections(text, period, text[:2500])
    captured = stock.get("statement_evidence", [])
    matches = [s for s in sections if s.get("status") == "bounded_consolidated_statement"
               and s.get("period", {}).get("status") == "same_report_stock_columns_bound"
               and sum(c.get("status") == s["status"] and c.get("header") == s["header"]
                       and c.get("period") == s["period"] and c.get("source_page") == s["source_page"]
                       for c in captured) == 1]
    if len(matches) != 1:
        raise ValueError("H1_assets_unique_native_and_captured_statement_pending")
    section = matches[0]; start = section["body_start"]; end = start + len(section["body"])
    heads = [h for h in TABLE.finditer(text) if h.end() == start and h["scope"] == "合并" and h["name"] == "资产负债表"]
    boundaries = [h for h in TABLE.finditer(text) if h.start() == end]
    if len(heads) != 1 or len(boundaries) != 1:
        raise ValueError("H1_assets_original_statement_boundaries_pending")
    rows = list(re.finditer(r"(?m)^[^\S\n]*" + PREFIX + r"资\s*产\s*总\s*计(?=[\t :：+\-−－—–/()（）\d]|$)(?P<rest>[^\n]*)$", section["body"]))
    if len(rows) != 1:
        raise ValueError("H1_assets_unique_native_total_assets_row_pending")
    hit = rows[0]; label = hit.group().strip()
    cells, _ = column_pair(hit["rest"], section["body"][hit.end():].splitlines())
    old = stock.get("field_evidence", {}).get("total_assets", {})
    if cells != old.get("cells") or label != old.get("source_label") or len(cells) != 2:
        raise ValueError("H1_assets_original_native_printed_cells_mismatch")
    offset = start + hit.start() + len(hit.group()) - len(hit.group().lstrip())
    unit = section["unit"].get("printed_unit")
    projected = {"status": "original_current_and_comparative_amounts_bound",
                 "actual_table_header": section["header"], "source_label": label, "source_cells": cells,
                 "current_yuan": old.get("amount_yuan"), "comparative_yuan": old.get("comparative_amount_yuan"),
                 "statement_scope": "consolidated", "currency": section["unit"].get("currency"),
                 "printed_unit": unit, "period_evidence": section["period"], "source_page": source_page(text, offset),
                 "source_row_offset": offset, "statement_heading": heads[0].group().strip(),
                 "statement_start_offset": start, "statement_end_offset": end,
                 "end_boundary_heading": boundaries[0].group().strip()}
    # Projection is transient source evidence, not a new captured record.
    view = {"stock": stock, "fields": {"total_assets": projected}}
    assets = _assets(view, stock, text, period)
    assets.update(adapter_method="bound_native_H1_stock_field_without_annual_supplement", original_asset_status=old.get("status"),
                  original_capture_fields_modified=False, original_stock_record_sha256=stock["record_sha256"],
                  statement_start_offset=start, statement_end_offset=end)
    return assets
