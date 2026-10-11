"""Current consolidated annual expensed R&D and revenue, source facts only."""
from datetime import date
from decimal import Decimal
import hashlib
import re
from .cash_collection import _annual_columns, _text_pair
from .financial_statements import PREFIX, TABLE, amount_unit, money, amount_text, source_page


def parse_current_annual_research_spending(text, metadata, provenance):
    period = str(metadata.get("report_date", ""))
    try:
        if not date.fromisoformat(period).isoformat().endswith("12-31"):
            raise ValueError()
    except ValueError:
        return {"status": "pending", "reason": "explicit_annual_period_required", "fields": {}}
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not provenance.get("text_hash_reverified") or content_sha != provenance.get("text_content_sha256"):
        return {"status": "pending", "reason": "retained_text_hash_not_bound", "fields": {}}
    source = text.replace("\r\n", "\n")
    headings = list(TABLE.finditer(source))
    tables = []
    for i, hit in enumerate(headings):
        if hit["scope"] != "合并" or hit["name"] != "利润表" or hit["continued"]:
            continue
        end = next((h.start() for h in headings[i+1:] if not
                    (h["scope"] == "合并" and h["name"] == "利润表" and h["continued"])), len(source))
        tables.append((hit, source[hit.end():end]))
    if len(tables) != 1:
        return {"status": "pending", "reason": "unique_consolidated_income_table_required", "fields": {}}
    hit, body = tables[0]
    first = re.search(r"(?m)^\s*"+PREFIX+r"(?:营业总收入|营业收入)", body)
    if not first:
        return {"status": "pending", "reason": "explicit_income_header_boundary_required", "fields": {}}
    header = body[:first.start()]
    columns, unit = _annual_columns(header, period), amount_unit(header)
    fields = {}
    for key, label in {"research_expense": "研发费用", "operating_revenue": "营业收入"}.items():
        pattern = r"\s*".join(map(re.escape, label))
        qualifier = r"(?:其中\s*[:：]\s*)?" if key == "operating_revenue" else ""
        rows = list(re.finditer(r"(?m)^[^\S\n]*"+PREFIX+qualifier+pattern+r"(?P<rest>[^\n]*)$", body))
        field = {"status": "pending", "label": label, "row_count": len(rows), "current_amount_yuan": None}
        fields[key] = field
        if len(rows) != 1:
            field["reason"] = "unique_consolidated_income_row_required"
            continue
        row = rows[0]
        cells, snippet, proof = _text_pair(row["rest"], body[row.end():].splitlines(), columns.get("note_column_explicit", False))
        previous = body[:row.start()].rstrip().splitlines()
        if previous and re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", previous[-1].strip()):
            field["reason"] = "adjacent_numeric_fragment_requires_geometry"
            continue
        field.update(source_page=source_page(source, hit.end()+row.start()), source_cells=cells,
                     source_label=row.group().strip(), column_parse_evidence=proof)
        if len(cells) != 2 or columns["status"] != "explicit_annual_column_pair" or unit["status"] != "unit_verified":
            field["reason"] = "explicit_columns_unit_or_pair_pending"
            continue
        current = money(cells[0])
        if current is None:
            field["reason"] = "current_amount_missing_never_zero"
            continue
        field.update(status="current_annual_amount_bound", current_amount_yuan=amount_text(current*Decimal(unit["multiplier"])),
                     current_start=columns["spans"]["current_start"], current_end=columns["spans"]["current_end"],
                     printed_unit=unit["printed_unit"], multiplier=unit["multiplier"], statement_scope="consolidated")
    return {"status": "research_expense_and_revenue_current_annual_bound" if all(
                f["status"] == "current_annual_amount_bound" for f in fields.values()) else "pending",
            "fields": fields, "table_columns": columns, "table_unit": unit, "text_content_sha256": content_sha,
            "expense_is_not_total_R_and_D_or_capitalized_development": True,
            "pdf_hash_freshly_reverified": False, "full_PIT_or_production_qualified": False}
