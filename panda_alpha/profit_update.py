"""Same-edition FY/H1 gross-margin updates from retained statement text.

This narrow parser never opens a PDF, reconstructs lost column geometry, or
certifies a full publication/revision history. The caller binds the retained
text file and its decoded UTF-8 content to the capture record.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import re

from panda_alpha.cash_collection import _annual_columns, _text_pair
from panda_alpha.financial_statements import (
    PREFIX, TABLE, amount_text, amount_unit, money, public_availability,
    source_page,
)
from panda_alpha.gross_profitability import _actual_change_evidence


_FIELDS = {"operating_revenue": "营业收入", "operating_cost": "营业成本"}
_ROW_PREFIX = PREFIX.replace(r"\s", r"[^\S\n]")
_FIRST_INCOME_ROW = re.compile(
    r"(?m)^[^\S\n]*" + _ROW_PREFIX
    + r"(?:(?:其中|减)\s*[:：]\s*)?营\s*业\s*(?:总\s*)?(?:收\s*入|成\s*本)"
)
# A full report's running page caption is not a fiscal column. Require the
# complete report-caption suffix; a bare '2026半年度' column is never removed.
_PAGE_CAPTION = re.compile(
    r"^[^\n]{0,100}?20\d{2}\s*年?\s*(?:年\s*度|半\s*年\s*度)"
    r"\s*报\s*告(?:\s*全\s*文)?\s*$"
)


def _same_span_columns(header: str, period: str) -> dict:
    """Reuse strict two-flow columns, excluding only explicit page captions."""
    anchor = re.search(r"项\s*目", header)
    if not anchor:
        return {"status": "explicit_project_column_header_missing", "source": header,
                "excluded_page_captions": []}
    columns = header[anchor.start():]
    kept, excluded = [], []
    for line in columns.splitlines():
        if (_PAGE_CAPTION.fullmatch(line.strip()) and not re.search(r"项\s*目", line)
                and len(re.findall(r"20\d{2}", line)) == 1):
            excluded.append(line)
        else:
            kept.append(line)
    result = _annual_columns("\n".join(kept), period)
    if result["status"] == "explicit_annual_column_pair":
        result["status"] = "explicit_same_span_column_pair"
    elif result["status"] == "annual_columns_pending":
        result["status"] = "same_span_columns_pending"
    result.update(excluded_page_captions=excluded, original_source=columns,
                  supported_span="FY" if period.endswith("12-31") else "H1")
    return result


def parse_profit_update(text: str, metadata: dict, provenance: dict) -> dict:
    """Return research-only current/comparative revenue, cost and delta GM.

    ``values`` contains Yuan strings for ``operating_revenue``,
    ``operating_revenue_comparative``, ``operating_cost`` and
    ``operating_cost_comparative``. ``gross_margin_change`` is populated only
    when the explicit same-span pair, positive revenues, nonnegative costs,
    text hashes and absence of an unbridged disclosed change all agree.
    """
    original = str(text)
    content_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    prov = deepcopy(provenance)
    if "historical_pdf_hash_reverified" not in prov:
        prov["historical_pdf_hash_reverified"] = bool(prov.get("pdf_hash_reverified"))
    prov["pdf_hash_reverified"] = False
    expected = prov.get("text_content_sha256")
    content_matches = bool(expected and expected == content_sha)
    text_bound = bool(content_matches and prov.get("text_hash_reverified")
                      and re.fullmatch(r"[0-9a-f]{64}", str(prov.get("text_sha256", ""))))
    prov.update(text_content_sha256=content_sha,
                text_content_hash_reverified=content_matches,
                pdf_verification_status="pdf_archive_only")
    base = {
        "schema_version": 1, "source": "retained_original_consolidated_profit_update_text",
        "code": str(metadata.get("code", metadata.get("symbol", ""))),
        "announcement_id": str(metadata.get("announcement_id", "")),
        "report_date": metadata.get("report_date"),
        "published_at": metadata.get("published_at") or metadata.get("pub_date"),
        "edition": metadata.get("edition"), "title": metadata.get("title"),
        "values": {key: None for field in _FIELDS for key in (field, field + "_comparative")},
        "gross_margin_change": None, "status": "pending",
        "pdf_verification_status": "pdf_archive_only",
        "text_sha256": prov.get("text_sha256"), "text_content_sha256": content_sha,
        "provenance": prov, "full_pit_certified": False,
        "source_coverage": "within_supplied_retained_original_editions_only",
        "comparative_vintage": "same original report publication; never backdated",
    }
    try:
        period = date.fromisoformat(str(metadata.get("report_date"))).isoformat()
    except (TypeError, ValueError):
        return {**base, "pending_reason": "report_period_missing_or_invalid"}
    if not period.endswith(("12-31", "06-30")):
        return {**base, "pending_reason": "fy_or_h1_report_required"}
    base["supported_span"] = "FY" if period.endswith("12-31") else "H1"
    # A summary or a title/edition declaration cannot supply column evidence.
    summary_title = "摘要" in str(metadata.get("title", "")) + str(metadata.get("edition", ""))
    summary_text = bool(re.search(r"20\d{2}\s*年?\s*(?:年\s*度|半\s*年\s*度)\s*报\s*告\s*摘\s*要", original[:2500]))
    if summary_title or summary_text:
        return {**base, "pending_reason": "full_original_report_required"}
    if base["published_at"]:
        try:
            base["available_date"] = public_availability(base["published_at"], metadata.get("available_date"))
        except (TypeError, ValueError):
            base["available_date"] = None
    else:
        base["available_date"] = None
    source = original.replace("\r\n", "\n")
    headings = list(TABLE.finditer(source))
    sections = []
    for index, hit in enumerate(headings):
        if hit["scope"] != "合并" or hit["name"] != "利润表" or hit["continued"]:
            continue
        end = next((h.start() for h in headings[index + 1:]
                    if not (h["scope"] == "合并" and h["name"] == "利润表" and h["continued"])), len(source))
        body = source[hit.end():end]
        first = _FIRST_INCOME_ROW.search(body)
        header = body[:first.start()] if first else body[:600]
        sections.append({"body": body, "body_start": hit.end(), "header": header,
                         "source_page": source_page(source, hit.start()),
                         "unit": amount_unit(header), "columns": _same_span_columns(header, period)})
    if len(sections) != 1:
        return {**base, "pending_reason": "consolidated_income_table_missing_or_ambiguous",
                "consolidated_table_count": len(sections)}
    section = sections[0]
    base["income_statement_evidence"] = {key: value for key, value in section.items()
                                         if key not in {"body", "body_start"}}
    base["comparison_change_barriers"] = _actual_change_evidence(source, section["header"])
    base["field_evidence"] = {}
    values_complete = True
    for field, label in _FIELDS.items():
        row_pattern = re.compile(
            r"(?m)^[^\S\n]*" + _ROW_PREFIX + r"(?:(?:其中|减)\s*[:：]\s*)?"
            + r"\s*".join(map(re.escape, label)) + r"(?P<rest>[^\n]*)$")
        rows = list(row_pattern.finditer(section["body"]))
        if len(rows) != 1:
            base["field_evidence"][field] = {
                "status": "exact_income_row_missing_or_ambiguous", "canonical_label": label,
                "row_count": len(rows), "current_yuan": None, "comparative_yuan": None,
            }
            values_complete = False
            continue
        row = rows[0]
        cells, snippet, column_proof = _text_pair(
            row["rest"], section["body"][row.end():].splitlines(),
            section["columns"].get("note_column_explicit", False))
        preceding = section["body"][:row.start()].rstrip().splitlines()
        previous_line = preceding[-1].strip() if preceding else ""
        if previous_line and re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", previous_line):
            cells = []
            column_proof.update(status="text_columns_pending",
                                pending_reason="adjacent_numeric_fragment_requires_geometry")
        evidence = {
            "source_label": row.group().strip(), "canonical_label": label,
            "source_page": source_page(source, section["body_start"] + row.start()),
            "statement_scope": "consolidated", "source_cells": cells,
            "column_source": snippet, "column_parse_evidence": column_proof,
            "period_evidence": section["columns"], "unit_evidence": section["unit"],
            "current_yuan": None, "comparative_yuan": None, "status": "pending",
        }
        base["field_evidence"][field] = evidence
        unit, columns = section["unit"], section["columns"]
        if (len(cells) != 2 or columns["status"] != "explicit_same_span_column_pair"
                or unit["status"] != "unit_verified" or unit.get("printed_unit") not in {"元", "千元", "万元"}):
            values_complete = False
            continue
        current, prior = map(money, cells)
        if current is None or prior is None:
            values_complete = False
            continue
        multiplier = Decimal(unit["multiplier"])
        current_yuan, prior_yuan = map(amount_text, (current * multiplier, prior * multiplier))
        base["values"].update({field: current_yuan, field + "_comparative": prior_yuan})
        spans = columns["spans"]
        evidence.update(status="explicit_same_span_text_pair", current_yuan=current_yuan,
                        comparative_yuan=prior_yuan, currency="CNY", printed_unit=unit["printed_unit"],
                        source_unit_multiplier=unit["multiplier"], flow_start=spans["current_start"],
                        flow_end=spans["current_end"], comparative_flow_start=spans["comparative_start"],
                        comparative_flow_end=spans["comparative_end"], comparative_span_verified=True)
    if not values_complete:
        return {**base, "pending_reason": "income_columns_unit_row_or_period_pending"}
    values = base["values"]
    revenue, prior_revenue = (Decimal(values[key]) for key in ("operating_revenue", "operating_revenue_comparative"))
    cost, prior_cost = (Decimal(values[key]) for key in ("operating_cost", "operating_cost_comparative"))
    if revenue <= 0 or prior_revenue <= 0 or cost < 0 or prior_cost < 0:
        return {**base, "pending_reason": "positive_revenue_or_nonnegative_cost_required"}
    if base["comparison_change_barriers"]:
        return {**base, "pending_reason": "explicit_accounting_or_scope_change_unbridged"}
    if not text_bound:
        return {**base, "pending_reason": "retained_text_file_or_content_hash_unbound"}
    if not base["available_date"]:
        return {**base, "pending_reason": "report_publication_missing_or_invalid"}
    base.update(status="text_source_pair_ready",
                gross_margin_change=float((revenue - cost) / revenue - (prior_revenue - prior_cost) / prior_revenue))
    return base
