"""Current annual cash-flow facts for investment funding, without return labels."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
import hashlib
import re

from .cash_collection import _annual_columns, _text_pair
from .financial_statements import PREFIX, TABLE, amount_unit, money, amount_text, source_page

LABELS = {
    "operating_cash_net": "经营活动产生的现金流量净额",
    "operating_cash_outflow": "经营活动现金流出小计",
    "capital_expenditure_cash": "购建固定资产、无形资产和其他长期资产支付的现金",
}


def parse_current_annual_investment_cash(text, metadata, provenance):
    """Bind three current flows to one consolidated table/year/unit; no zero fill."""
    period = str(metadata.get("report_date", ""))
    try:
        if not date.fromisoformat(period).isoformat().endswith("12-31"):
            raise ValueError()
    except ValueError:
        return {"status": "pending", "reason": "explicit_annual_period_required", "fields": {}}
    original = str(text)
    content_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    if not provenance.get("text_hash_reverified") or content_sha != provenance.get("text_content_sha256"):
        return {"status": "pending", "reason": "retained_text_hash_not_bound", "fields": {}}
    source = original.replace("\r\n", "\n")
    headings = list(TABLE.finditer(source))
    sections = []
    for i, hit in enumerate(headings):
        if hit["scope"] != "合并" or hit["name"] != "现金流量表" or hit["continued"]:
            continue
        end = next((h.start() for h in headings[i+1:] if not
                    (h["scope"] == "合并" and h["name"] == "现金流量表" and h["continued"])), len(source))
        sections.append((hit, source[hit.end():end]))
    if len(sections) != 1:
        return {"status": "pending", "reason": "unique_consolidated_cashflow_table_required", "fields": {}}
    hit, body = sections[0]
    header = re.split(r"(?m)^\s*"+PREFIX+r"经营活动", body, maxsplit=1)[0]
    columns, unit = _annual_columns(header, period), amount_unit(header)
    fields = {}
    for key, label in LABELS.items():
        pattern = r"\s*".join(map(re.escape, label)).replace("、", "[、，,]")
        if key == "capital_expenditure_cash":
            pattern = pattern.replace(r"支\s*付", r"(?:所\s*)?支\s*付")
        rows = list(re.finditer(r"(?m)^[^\S\n]*"+PREFIX+pattern+r"(?P<rest>[^\n]*)$", body))
        field = {"status": "pending", "label": label, "row_count": len(rows), "current_amount_yuan": None}
        fields[key] = field
        if len(rows) != 1:
            field["reason"] = "unique_cashflow_row_required"; continue
        row = rows[0]
        cells, snippet, proof = _text_pair(row["rest"], body[row.end():].splitlines(),
                                         columns.get("note_column_explicit", False))
        prior_line = body[:row.start()].rstrip().splitlines()
        if prior_line and re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", prior_line[-1].strip()):
            field["reason"] = "adjacent_numeric_fragment_requires_geometry"; continue
        field.update(source_page=source_page(source, hit.end()+row.start()),
                     source_label=row.group().strip(), source_cells=cells,
                     column_parse_evidence=proof, statement_scope="consolidated")
        if len(cells) != 2 or columns["status"] != "explicit_annual_column_pair" or unit["status"] != "unit_verified":
            field["reason"] = "explicit_columns_unit_or_pair_pending"; continue
        current = money(cells[0])
        if current is None:
            field["reason"] = "current_amount_missing_never_zero"; continue
        field.update(status="current_annual_amount_bound", current_amount_yuan=amount_text(current*Decimal(unit["multiplier"])),
                     printed_unit=unit["printed_unit"], multiplier=unit["multiplier"],
                     current_start=columns["spans"]["current_start"], current_end=columns["spans"]["current_end"],
                     comparative_amount_claimed=False)
    ready = all(f["status"] == "current_annual_amount_bound" for f in fields.values())
    return {"status": "three_current_annual_cash_amounts_bound" if ready else "pending",
            "fields": fields, "table_columns": columns, "table_unit": unit,
            "text_content_sha256": content_sha, "pdf_hash_freshly_reverified": False,
            "full_PIT_or_production_qualified": False, "signals_or_return_labels_calculated": False}
