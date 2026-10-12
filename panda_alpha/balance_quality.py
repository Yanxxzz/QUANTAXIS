"""Current-column balance facts from retained native text, not factor signals.

Current stocks do not require an annual comparative amount. Text columns must
still identify the current cell; blanks and dashes never become numeric zeros.
No PDF/HTTP/geometry fallback or revision/source-qualification changes occur.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date
import hashlib
import re

from .financial_statements import (
    PREFIX, amount_text, column_pair, header_period, iso_day, money,
    printed_quantum_yuan, public_availability, source_page,
)
from .trade_accrual import _balance_sections


DEFAULT_FIELDS = {"goodwill": ("商誉",),
                  "total_assets": ("资产总计", "资产合计", "资产总额")}
MATURITY_FIELDS = {
    "non_current_liability_due_one_year": ("一年内到期的非流动负债", "一年内到期非流动负债"),
    "non_current_liabilities": ("非流动负债合计",),
}


def _numeric_note_column(header: str, period: str, cells: list) -> dict | None:
    """A printed note/current/comparison header identifies three-cell rows."""
    if len(cells) != 3 or not re.fullmatch(r"[1-9]\d{0,2}", cells[0]):
        return None
    heads = [line.strip() for line in header.splitlines()
             if re.match(r"^(?:项目\s+)?附注[一二三四五六七八九十]*\s+", line.strip())]
    if len(heads) != 1:
        return None
    try:
        dates = [date(int(y), int(m), int(d)).isoformat() for y, m, d in re.findall(
            r"(20\d{2})\s*(?:年|-|/)\s*(\d{1,2})\s*(?:月|-|/)\s*(\d{1,2})\s*日?", heads[0])]
    except ValueError:
        return None
    if len(dates) != 2 or dates[0] != period or dates[1] >= period:
        return None
    return {"method": "explicit_note_then_current_and_other_stock_date_header",
            "header_line": heads[0], "printed_date_roles": dates,
            "note_reference": cells[0]}


def parse_current_balance_fields(text: str, metadata: dict, provenance: dict,
                                 fields: dict | None = None) -> dict:
    """Bind a unique consolidated current column using strict text only.

    ``fields`` maps field names to explicit source-label aliases. This function
    returns source amounts and roles; it does not divide, select stocks, certify
    original PDF bytes or clear accounting/version barriers.
    """
    fields = fields or DEFAULT_FIELDS
    if not isinstance(fields, dict) or not fields:
        raise ValueError("Explicit balance field labels are required")
    if any(not isinstance(k, str) or not isinstance(v, (tuple, list)) or not v
           or any(not isinstance(label, str) or not label for label in v)
           for k, v in fields.items()):
        raise ValueError("Each balance field requires nonempty label aliases")
    period = iso_day(metadata["report_date"])
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (not provenance.get("text_hash_reverified")
            or provenance.get("text_content_sha256") != digest):
        raise ValueError("Current balance retained-text binding changed")
    text = text.replace("\r\n", "\n")
    sections = _balance_sections(text, period, text[:2500])
    title_kinds = {"12-31": r"年度报告", "06-30": r"(?:半年度报告|中期报告)",
                   "03-31": r"(?:第一季度报告|一季度报告)", "09-30": r"(?:第三季度报告|三季度报告)"}
    kind = title_kinds.get(period[5:])
    title_hit = re.search(re.escape(period[:4]) + r"年?" + kind,
                          re.sub(r"\s+", "", text[:2500])) if kind else None
    current_title_proof = title_hit.group() if title_hit else ""
    candidates = []
    for section in sections:
        if section.get("status") != "bounded_consolidated_statement":
            continue
        current_period = header_period(section["header"], "balance", period,
                                       current_title_proof)
        if (current_period.get("status") == "current_period_verified"
                and section["unit"].get("status") == "unit_verified"):
            candidates.append((section, current_period))
    result = {
        "status": "pending", "report_date": period,
        "statement_scope": "consolidated", "measure": "stock",
        "publication": {"announcement_id": str(metadata["announcement_id"]),
                        "published_at": metadata["published_at"],
                        "available_date": public_availability(
                            metadata["published_at"], metadata.get("available_date"))},
        "source": {k: provenance.get(k) for k in
                   ("pdf_sha256", "text_sha256", "text_content_sha256")},
        "pdf_hash_reverified": False, "text_hash_reverified": True,
        "full_pit_certified": False, "native_production_certified": False,
        "fields": {}, "candidate_current_statement_count": len(candidates),
        "accounting_barriers_cleared": False,
    }
    if len(candidates) != 1:
        result["pending_reasons"] = ["unique_current_consolidated_balance_pending"]
        return result
    section, current_period = candidates[0]
    start = section["body_start"]
    result["statement"] = {
        "body_start": start, "body_end": start + len(section["body"]),
        "header": section["header"], "header_source_page": section["source_page"],
        "current_period_evidence": current_period,
        "unit_evidence": section["unit"],
    }
    for name, aliases in fields.items():
        patterns = [r"[^\S\n]*".join(map(re.escape, alias)) for alias in aliases]
        horizontal_prefix = PREFIX.replace(r"\s*", r"[^\S\n]*")
        pattern = re.compile(r"(?m)^[^\S\n]*" + horizontal_prefix + "(?:" + "|".join(patterns)
                             + r")(?=[\t :：+\-−－—–/()（）\d]|$)(?P<rest>[^\n]*)$")
        hits = [(hit, None) for hit in pattern.finditer(section["body"])]
        # Only a documented text extraction join from either known preceding
        # liability row may expose an otherwise complete two-cell maturity row.
        # Arbitrary prose, sideloaded notes and horizontally interleaved assets
        # cannot become a balance source by finding the label inside a line.
        if name == "non_current_liability_due_one_year":
            inline = re.compile(r"(?m)(?<!\S)(?:" + "|".join(patterns)
                                + r")(?=[\t :：+\-−－—–/()（）\d]|$)(?P<rest>[^\n]*)$")
            for hit in inline.finditer(section["body"]):
                if any(old.start() <= hit.start() < old.end() for old, _ in hits):
                    continue
                line_start = section["body"].rfind("\n", 0, hit.start()) + 1
                prefix = section["body"][line_start:hit.start()].strip()
                parent = re.fullmatch(r"(应付股利|持有待售负债)[^\S\n]+(.+)", prefix)
                parent_cells, _ = column_pair(parent[2], []) if parent else ([], "")
                if parent and len(parent_cells) in (1, 2) and all(money(c) is not None for c in parent_cells):
                    hits.append((hit, {"method": "known_preceding_balance_row_text_join",
                                       "preceding_row": prefix, "preceding_label": parent[1]}))
        hits.sort(key=lambda pair: pair[0].start())
        rows = []
        for hit, inline_proof in hits:
            offset = start + hit.start()
            cells, snippet = column_pair(hit["rest"], section["body"][hit.end():].splitlines())
            original_cells = cells[:]
            note_proof = _numeric_note_column(section["header"], period, cells)
            if note_proof:
                cells = cells[1:]
            # Adjacent numeric fragments can split one cell across lines. Plain
            # text cannot reconstruct that missing geometry safely.
            previous = text[:offset].rstrip().splitlines()
            adjacent_fragment = bool(previous and re.fullmatch(
                r"[\d,，.\s+\-−－—–/()（）]+", previous[-1].strip()))
            page = source_page(text, offset)
            if (len(previous) >= 2 and page is not None
                    and previous[-1].strip() == str(page)
                    and (previous[-2].strip() == f"===SOURCE_PAGE:{page}==="
                         or re.search(r"20\d{2}.*(?:年度报告|半年度报告|中期报告)", previous[-2]))):
                adjacent_fragment = False
            row = {"source_page": source_page(text, offset),
                   "source_row_offset": offset, "source_label": hit.group().strip(),
                   "source_cells": cells, "column_source": snippet,
                   "original_source_cells": original_cells,
                   "numeric_note_column_evidence": note_proof,
                   "inline_row_source_evidence": inline_proof,
                   "current_amount_yuan": None,
                   "status": "current_column_ambiguous"}
            if len(cells) == 2 and not adjacent_fragment:
                current = money(cells[0])
                if current is None:
                    row["status"] = "current_printed_blank_or_dash_pending"
                else:
                    unit = section["unit"]
                    row.update(status="current_stock_amount_bound",
                               current_amount_yuan=amount_text(current * Decimal(unit["multiplier"])),
                               current_printed_amount=cells[0], stock_asof=period,
                               currency=unit["currency"], printed_unit=unit["printed_unit"],
                               current_printed_quantum_yuan=printed_quantum_yuan(cells[0], unit["printed_unit"]),
                               zero_method="explicit_numeric_zero" if current == 0 else None)
            rows.append(row)
        if len(rows) == 1:
            result["fields"][name] = rows[0]
        else:
            result["fields"][name] = {"status": "multiple_rows_pending" if rows else "field_missing_pending",
                                       "current_amount_yuan": None, "row_count": len(rows),
                                       "candidates": rows}
    pending = [name + ":" + row["status"] for name, row in result["fields"].items()
               if row["status"] != "current_stock_amount_bound"]
    result["status"] = "current_balance_source_amounts_bound" if not pending else "pending"
    result["pending_reasons"] = pending
    return result
