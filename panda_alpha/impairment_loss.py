"""Narrow retained FY25 signed asset-impairment facts, not native/alpha proof."""
from decimal import Decimal
import hashlib
import re

from .cash_collection import _annual_columns, _text_pair
from .financial_statements import (
    PREFIX, TABLE, amount_unit, amount_text, money, printed_quantum_yuan, source_page,
)

HORIZONTAL_PREFIX = PREFIX.replace(r"\s*", r"[^\S\n]*")
CAPTION = r"(?P<caption>[（(][^）)]{0,100}[）)])"
NUMERIC = r"[\d,，.\s+\-−－—–/()（）]+"


def _label(label):
    return r"[^\S\n]*".join(map(re.escape, label))


def _minus_caption(caption):
    return bool(re.search(r"损失以[“\"‘'「]?[-−－—][”\"’'」]?号填列", re.sub(r"\s+", "", caption)))


def _clean_exact_page_header(header, issuer):
    if not issuer:
        return header, None
    pattern = (r"(?m)^===SOURCE_PAGE:(?P<page>\d+)===\n" + re.escape(issuer)
               + r"[^\S\n]*2025[^\S\n]*年[^\S\n]*年度报告[^\S\n]*\n"
               + r"(?P=page)[^\S\n]*/[^\S\n]*(?P<total>\d+)[^\S\n]*(?:\n|$)")
    matches = list(re.finditer(pattern, header))
    if len(matches) != 1 or int(matches[0]["total"]) < int(matches[0]["page"]):
        return header, None
    hit = matches[0]
    cleaned = header[:hit.start()] + header[hit.end():]
    return cleaned, {"status": "exact_bound_issuer_sourcepage_report_packet_removed_from_column_preamble",
                     "issuer": issuer, "physical_page": int(hit["page"]), "total_pages": int(hit["total"]),
                     "removed_packet_sha256": hashlib.sha256(hit.group().encode()).hexdigest(),
                     "before_header_sha256": hashlib.sha256(header.encode()).hexdigest(),
                     "after_header_sha256": hashlib.sha256(cleaned.encode()).hexdigest(),
                     "other_fiscal_years_removed": False}


def _closed_previous_credit(source, hit, body, row, current_cells):
    # The sole exception requires two independently closed wrapped caption/row
    # pairs on one physical page. It never reconstructs or emits credit amounts.
    if row["rest"].strip() or "\n" not in (row["caption"] or "") or not _minus_caption(row["caption"] or ""):
        return None
    previous = body[:row.start()].rstrip()
    pattern = (r"(?m)^[^\S\n]*" + HORIZONTAL_PREFIX + _label("信用减值损失")
               + r"[^\S\n]*" + CAPTION + r"[^\S\n]*\n(?P<pair>[^\n]+)\Z")
    m = re.search(pattern, previous)
    current = re.match(r"\n(?P<pair>[^\n]+)", body[row.end():])
    if (not m or not current or "\n" not in m["caption"] or not _minus_caption(m["caption"])
            or "===SOURCE_PAGE:" in m["caption"] or not re.fullmatch(NUMERIC, m["pair"].strip())):
        return None
    old_cells, _, old_proof = _text_pair(m["pair"], [], False)
    own_cells, _, own_proof = _text_pair(current["pair"], [], False)
    if (len(old_cells) != 2 or len(own_cells) != 2 or own_cells != current_cells
            or old_proof["status"] != "complete_text_column_pair" or own_proof["status"] != "complete_text_column_pair"
            or any(money(c) is None for c in old_cells)):
        return None
    current_offset, previous_offset = hit.end() + row.start(), hit.end() + m.start()
    own_pair_end = hit.end() + row.end() + current.end()
    page = source_page(source, current_offset)
    if page is None or source_page(source, previous_offset) != page or source_page(source, own_pair_end - 1) != page:
        return None
    return {"status": "complete_wrapped_credit_and_asset_caption_pairs_are_separate_same_physical_page",
            "source_page": page, "previous_credit_offset": previous_offset, "asset_offset": current_offset,
            "previous_closed_credit_row_sha256": hashlib.sha256(m.group().encode()).hexdigest(),
            "asset_own_pair_sha256": hashlib.sha256(current.group().encode()).hexdigest(), "credit_amounts_emitted": False}


def parse_current_asset_impairment(text, metadata, provenance, *, issuer_page_header=None):
    digest = hashlib.sha256(text.encode()).hexdigest()
    if (metadata.get("report_date") != "2025-12-31" or not provenance.get("text_hash_reverified")
            or provenance.get("text_content_sha256") != digest):
        raise ValueError("Exact FY25 retained text period/hash binding required")
    source = text.replace("\r\n", "\n")
    heads = list(TABLE.finditer(source)); tables = []
    for i, h in enumerate(heads):
        if h["scope"] != "合并" or h["name"] != "利润表" or h["continued"]:
            continue
        end = next((n.start() for n in heads[i+1:] if not (n["scope"] == "合并" and n["name"] == "利润表" and n["continued"])), len(source))
        tables.append((h, end, source[h.end():end]))
    if len(tables) != 1:
        return {"status": "pending", "reason": "unique_consolidated_income_table_required", "field": {"status": "pending", "current_amount_yuan": None}}
    hit, end, body = tables[0]
    first = re.search(r"(?m)^\s*" + PREFIX + r"(?:营业总收入|营业收入)", body)
    if first is None:
        return {"status": "pending", "reason": "income_column_preamble_boundary_missing", "field": {"status": "pending", "current_amount_yuan": None}}
    original_header = body[:first.start()]
    header, header_proof = _clean_exact_page_header(original_header, issuer_page_header)
    columns, unit = _annual_columns(header, "2025-12-31"), amount_unit(header)
    pattern = (r"(?m)^[^\S\n]*" + HORIZONTAL_PREFIX + _label("资产减值损失")
               + r"(?:[^\S\n]*" + CAPTION + r")?(?P<rest>[^\n]*)$")
    rows = list(re.finditer(pattern, body))
    f = {"status": "pending", "canonical_label": "资产减值损失", "row_count": len(rows), "current_amount_yuan": None}
    result = {"status": "pending", "field": f, "statement": {"scope": "consolidated", "name": "利润表", "heading_start": hit.start(), "body_start": hit.end(), "body_end": end,
              "header": original_header, "column_header_projection": header, "columns": columns, "unit": unit}, "header_projection_evidence": header_proof,
              "text_content_sha256": digest, "credit_amounts_emitted": False, "original_PDF_freshly_verified": False,
              "native_sign_period_PIT_financial_gate_released": False, "ratios_sign_transformed_values_or_returns": False}
    if len(rows) != 1:
        f["reason"] = "unique_asset_impairment_row_required"
        return result
    row = rows[0]; caption = row["caption"] or ""
    cells, snippet, proof = _text_pair(row["rest"], body[row.end():].splitlines(), columns.get("note_column_explicit", False))
    offset = hit.end() + row.start(); minus = _minus_caption(caption)
    f.update(source_label=row.group().strip(), source_row_offset=offset, source_page=source_page(source, offset),
             row_sha256=hashlib.sha256((row.group() + "\n" + snippet).encode()).hexdigest(), current_printed=cells[0] if len(cells) == 2 else None,
             printed_column_count=len(cells), column_parse_evidence=proof, literal_sign_caption=caption, losses_with_minus_caption_bound=minus,
             raw_role="signed_income_contribution_negative_is_loss_positive_reversal_or_recovery" if minus else "sign_role_pending",
             loss_burden_sign_mapping="minus_signed_line_if_normalized_loss_burden_is_later_defined" if minus else "unknown",
             native_field_sign_mapping_certified=False, comparative_amount_claimed=False, sign_transformed_amount_computed=False)
    previous = body[:row.start()].rstrip().splitlines()
    if previous and re.fullmatch(NUMERIC, previous[-1].strip()):
        boundary = _closed_previous_credit(source, hit, body, row, cells)
        if boundary is None:
            f["reason"] = "adjacent_numeric_fragment_requires_geometry"
            return result
        f["previous_credit_boundary_evidence"] = boundary
    if len(cells) != 2 or columns["status"] != "explicit_annual_column_pair" or unit["status"] != "unit_verified":
        f["reason"] = "explicit_current_columns_unit_or_pair_pending"
        return result
    amount = money(cells[0])
    if amount is None:
        f["reason"] = "current_blank_dash_not_zero"
        return result
    f.update(status="current_annual_amount_bound", current_amount_yuan=amount_text(amount * Decimal(unit["multiplier"])), currency=unit["currency"], printed_unit=unit["printed_unit"],
             current_printed_quantum_yuan=printed_quantum_yuan(cells[0], unit["printed_unit"]), current_start=columns["spans"]["current_start"], current_end=columns["spans"]["current_end"],
             statement_scope="consolidated", explicit_numeric_zero=amount == 0, negative_printed_amount=amount < 0, positive_printed_amount=amount > 0,
             no_absolute_value_or_clipping=True, new_fact=True)
    result["status"] = "current_annual_asset_impairment_amount_bound"
    return result
