"""Narrow annual sales-cash pair from retained consolidated statement text.

This parser never opens an archived PDF or certifies a complete PIT history.
The loader must bind both the gzip file and its decoded UTF-8 content hashes.
Only explicit two-column annual cash-flow tables are currently supported.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import re

from panda_alpha.financial_statements import (
    CELL, PREFIX, TABLE, amount_text, amount_unit, column_pair, flow_spans,
    money, public_availability, source_page, strip_note_reference,
    wrapped_money_requires_geometry,
)

LABEL = "销售商品、提供劳务收到的现金"
_LABEL = r"\s*".join(map(re.escape, LABEL)).replace("、", "[、，,]")
_ROW = re.compile(r"(?m)^[^\S\n]*" + PREFIX + _LABEL + r"(?P<rest>[^\n]*)$")


def _annual_columns(header: str, period: str) -> dict:
    """A preceding caption is not a fiscal column (e.g. 2025,2025,2024)."""
    anchor = re.search(r"项\s*目", header)
    if not anchor:
        return {"status": "explicit_project_column_header_missing", "source": header}
    # Everything before 项目 may be the report caption or amount unit. Only the
    # actual column header can bind current/prior, including wrapped headers.
    columns = header[anchor.start():]
    columns = re.split(r"(?m)^\s*" + PREFIX + r"经营活动", columns, maxsplit=1)[0]
    spans = flow_spans(columns, period)
    ready = (spans["current_status"] == "current_period_verified"
             and spans["comparative_status"] == "explicit_prior_same_span")
    return {"status": "explicit_annual_column_pair" if ready else "annual_columns_pending",
            "source": columns, "spans": spans,
            "note_column_explicit": bool(re.search(r"附\s*注", columns))}


def _text_pair(rest: str, following: list[str], note_column: bool) -> tuple[list[str], str, dict]:
    cleaned = strip_note_reference(rest)
    note = rest.strip()[:len(rest.strip()) - len(cleaned)] if cleaned != rest.strip() else None
    # An Arabic subnote marker is syntactically distinct from an amount. A
    # plain integer is only a note when a three-cell row and 附注 header agree.
    if note_column:
        hit = re.match(r"^\s*(?:附注\s*)?\d{1,3}(?:[（(]\d{1,3}[）)])+(?=\s|$)", cleaned)
        if hit:
            note, cleaned = hit.group().strip(), cleaned[hit.end():].strip()
    cells, snippet = column_pair(cleaned, following)
    if note_column and len(cells) == 3 and re.fullmatch(r"\d{1,3}", cells[0]):
        note, cells = cells[0], cells[1:]
    # If the first two extracted cells could be note/current, an immediately
    # following monetary line makes the original row three cells, not a pair.
    # This handles an integer note on its own line without guessing decimals.
    if note_column and len(cells) == 2 and re.fullmatch(r"\d{1,3}", cells[0]):
        used_lines = len(snippet.splitlines()) - len(cleaned.splitlines())
        remaining = following[max(used_lines, 0):]
        next_line = next((line for line in remaining if line.strip()), "")
        if re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", next_line or " "):
            more, merged = column_pair(snippet + "\n" + next_line, [])
            if len(more) == 3 and not wrapped_money_requires_geometry(merged):
                note, cells, snippet = more[0], more[1:], merged
    return cells, snippet, {"status": "complete_text_column_pair" if len(cells) == 2
                           else "text_columns_pending", "note_reference": note,
                           "method": "strict_retained_text_no_pdf_layout_recovery"}


def parse_annual_sales_cash(text: str, metadata: dict, provenance: dict) -> dict:
    """Return a research-only same-edition consolidated annual cash pair.

    ``metadata`` accepts the captured stock record's code/symbol, report_date,
    announcement_id and publication fields. ``provenance.text_sha256`` binds
    the gzip file; ``text_content_sha256`` binds decoded text. The loader's
    text_hash_reverified flag is required because plain text cannot verify the
    original gzip bytes. Negative printed amounts are preserved for callers.
    """
    original = str(text)
    content_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    prov = deepcopy(provenance)
    if "historical_pdf_hash_reverified" not in prov:
        prov["historical_pdf_hash_reverified"] = bool(prov.get("pdf_hash_reverified"))
    prov["pdf_hash_reverified"] = False
    expected_content_sha = prov.get("text_content_sha256")
    content_matches = bool(expected_content_sha and expected_content_sha == content_sha)
    text_bound = bool(content_matches and prov.get("text_hash_reverified")
                      and re.fullmatch(r"[0-9a-f]{64}", str(prov.get("text_sha256", ""))))
    prov["text_content_sha256"] = content_sha
    prov["text_content_hash_reverified"] = content_matches
    prov["pdf_verification_status"] = "pdf_archive_only"
    base = {"schema_version": 1, "source": "retained_original_consolidated_sales_cash_text",
            "code": str(metadata.get("code", metadata.get("symbol", ""))),
            "announcement_id": str(metadata.get("announcement_id", "")),
            "report_date": metadata.get("report_date"),
            "published_at": metadata.get("published_at") or metadata.get("pub_date"),
            "values": {"current": None, "prior": None},
            "status": "pending", "pdf_verification_status": "pdf_archive_only",
            "text_sha256": prov.get("text_sha256"), "text_content_sha256": content_sha,
            "provenance": prov, "full_pit_certified": False,
            "source_coverage": "within_supplied_retained_original_editions_only"}
    try:
        period = date.fromisoformat(str(metadata.get("report_date"))).isoformat()
    except (TypeError, ValueError):
        return {**base, "pending_reason": "report_period_missing_or_invalid"}
    if not period.endswith("12-31"):
        return {**base, "pending_reason": "annual_report_required"}
    if base["published_at"]:
        try:
            base["available_date"] = public_availability(base["published_at"], metadata.get("available_date"))
        except (TypeError, ValueError):
            base["available_date"] = None
    source = original.replace("\r\n", "\n")
    headings = list(TABLE.finditer(source))
    sections = []
    for index, hit in enumerate(headings):
        if hit["scope"] != "合并" or hit["name"] != "现金流量表" or hit["continued"]:
            continue
        end = next((h.start() for h in headings[index + 1:]
                    if not (h["scope"] == "合并" and h["name"] == "现金流量表" and h["continued"])), len(source))
        body = source[hit.end():end]
        rows = list(_ROW.finditer(body))
        header = body[:rows[0].start()] if rows else body[:600]
        # Cash-flow categories delimit the preamble; other rows cannot supply
        # unit/year evidence. The first sales-cash row normally follows them.
        preamble = re.split(r"(?m)^\s*" + PREFIX + r"经营活动", header, maxsplit=1)[0]
        sections.append({"body": body, "body_start": hit.end(), "header": preamble,
                         "source_page": source_page(source, hit.start()),
                         "unit": amount_unit(preamble), "columns": _annual_columns(preamble, period),
                         "rows": rows})
    if len(sections) != 1:
        return {**base, "pending_reason": "consolidated_cashflow_table_missing_or_ambiguous",
                "consolidated_table_count": len(sections)}
    section = sections[0]
    table_evidence = {k: v for k, v in section.items() if k not in {"body", "body_start", "rows"}}
    base["cashflow_statement_evidence"] = table_evidence
    if len(section["rows"]) != 1:
        return {**base, "pending_reason": "exact_sales_cash_row_missing_or_ambiguous",
                "row_count": len(section["rows"])}
    row = section["rows"][0]
    cells, snippet, column_proof = _text_pair(row["rest"], section["body"][row.end():].splitlines(),
                                               section["columns"].get("note_column_explicit", False))
    preceding = section["body"][:row.start()].rstrip().splitlines()
    previous_line = preceding[-1].strip() if preceding else ""
    if previous_line and re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", previous_line):
        # A numeric fragment immediately above the label can be the first
        # half of a lost-layout cell. No text-only repair can certify it.
        cells = []
        column_proof.update(status="text_columns_pending", pending_reason="adjacent_numeric_fragment_requires_geometry")
    evidence = {"source_label": row.group().strip(), "canonical_label": LABEL,
                "source_page": source_page(source, section["body_start"] + row.start()),
                "statement_scope": "consolidated", "source_cells": cells,
                "column_source": snippet, "column_parse_evidence": column_proof,
                "period_evidence": section["columns"], "unit_evidence": section["unit"],
                "current_yuan": None, "comparative_yuan": None, "status": "pending"}
    base["field_evidence"] = {"sales_cash_received": evidence}
    unit, columns = section["unit"], section["columns"]
    if len(cells) != 2 or columns["status"] != "explicit_annual_column_pair" or unit["status"] != "unit_verified":
        return {**base, "pending_reason": "annual_cash_columns_unit_or_period_pending"}
    current, prior = map(money, cells)
    if current is None or prior is None:
        return {**base, "pending_reason": "annual_cash_amount_missing_or_invalid"}
    multiplier = Decimal(unit["multiplier"])
    values = {"current": amount_text(current * multiplier), "prior": amount_text(prior * multiplier)}
    spans = columns["spans"]
    evidence.update(status="explicit_annual_text_pair", current_yuan=values["current"],
                    comparative_yuan=values["prior"], currency="CNY", printed_unit=unit["printed_unit"],
                    source_unit_multiplier=unit["multiplier"], flow_start=spans["current_start"],
                    flow_end=spans["current_end"], comparative_flow_start=spans["comparative_start"],
                    comparative_flow_end=spans["comparative_end"], comparative_span_verified=True)
    base["values"] = values
    if text_bound:
        base["status"] = "text_source_pair_ready"
    else:
        base["pending_reason"] = "retained_text_file_or_content_hash_unbound"
    return base
