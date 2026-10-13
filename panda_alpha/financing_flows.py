"""Current FY2025 borrowing/repayment cash rows from bound retained text.

These are named financing cash-flow facts, not proof of loan rollover, distress,
matching debt components, complete notice versions or native-platform periods.
The consumer can request only fields that are still pending; other amounts are
not parsed or copied. Original PDFs are not verified by this text parser.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import re

from .cash_collection import _annual_columns, _text_pair
from .financial_statements import (
    PREFIX, TABLE, amount_text, amount_unit, money, printed_quantum_yuan, source_page,
)


LABELS = {
    "cash_received_from_financial_institution_borrows":
        ("取得借款收到的现金", "取得借款所收到的现金"),
    "cash_paid_for_debt": ("偿还债务支付的现金", "偿还债务所支付的现金"),
}
_NUMERIC_FRAGMENT = r"[\d,，.\s+\-−－—–/()（）]+"
_MINORITY_LABEL = "子公司吸收少数股东投资收到的现金"


def _label_pattern(label):
    return r"\s*".join(map(re.escape, label))


def _requested_fields(target_fields):
    if target_fields is None:
        return list(LABELS)
    if isinstance(target_fields, str):
        raise ValueError("target_fields must be a field sequence")
    requested = list(target_fields)
    if not requested or len(set(requested)) != len(requested) or any(key not in LABELS for key in requested):
        raise ValueError("Unknown, duplicate or empty financing target_fields")
    return requested


def _closed_previous_minority_pair(source, hit, body, row, note_column):
    """Prove the one observed prior wrapped subitem is independent of this row.

    Only an inline complete current row qualifies. The preceding numeric line
    must directly terminate the fully named wrapped minority-investment subitem
    on the same physical page; arbitrary numeric fragments remain unresolved.
    Prior numbers are checked structurally and never emitted as amount facts.
    """
    inline_cells, _, inline_proof = _text_pair(row["rest"], [], note_column)
    if len(inline_cells) != 2 or inline_proof["status"] != "complete_text_column_pair":
        return None
    previous = body[:row.start()].rstrip()
    pattern = (r"(?m)^[^\S\n]*(?P<label>其\s*中\s*[:：]\s*" +
               _label_pattern(_MINORITY_LABEL) + r")[^\S\n]*\n"
               r"(?P<pair>[^\n]+)\Z")
    match = re.search(pattern, previous)
    if not match or "\n" not in match["label"]:
        return None
    fragment = match["pair"].strip()
    if not re.fullmatch(_NUMERIC_FRAGMENT, fragment):
        return None
    prior_cells, _, prior_proof = _text_pair(fragment, [], False)
    if (len(prior_cells) != 2 or prior_proof["status"] != "complete_text_column_pair"
            or any(money(cell) is None for cell in prior_cells)):
        return None
    previous_offset, current_offset = hit.end() + match.start(), hit.end() + row.start()
    page = source_page(source, current_offset)
    if (page is None or source_page(source, previous_offset) != page
            or source_page(source, hit.end() + match.end() - 1) != page):
        return None
    return {"status": "closed_prior_wrapped_minority_pair_separate_from_inline_target",
            "source_page": page, "previous_label_offset": previous_offset,
            "current_row_offset": current_offset,
            "previous_label_sha256": hashlib.sha256(match["label"].encode()).hexdigest(),
            "boundary_sha256": hashlib.sha256((match.group() + "\n" + row.group()).encode()).hexdigest(),
            "current_inline_pair_complete": True, "previous_amounts_emitted": False}


def _role_clues(source, hit, body, field_rows):
    sections = list(re.finditer(r"(?m)^\s*" + PREFIX +
        r"(?P<activity>经营|投资|筹资)\s*活动\s*(?:产生的)?\s*现金流量[^\n]*", body))
    roles = {}
    for name, row in field_rows.items():
        prior = [section for section in sections if section.start() < row.start()]
        activity = prior[-1]["activity"] if prior else None
        roles[name] = {
            "status": "named_financing_cashflow_row" if activity == "筹资" else "activity_group_pending",
            "preceding_activity": activity,
            "preceding_heading_page": source_page(source, hit.end() + prior[-1].start()) if prior else None,
            "heading_sha256": hashlib.sha256(prior[-1].group().encode()).hexdigest() if prior else None,
            "row_page": source_page(source, hit.end() + row.start()),
            "row_offset": hit.end() + row.start(), "literal_label": re.sub(r"\s+", "", row["label"]),
        }
    patterns = {
        "bond_or_security_issuance": r"发\s*行\s*(?:债\s*券|证\s*券)\s*收\s*到\s*(?:的\s*)?现\s*金",
        "equity_investment": r"吸\s*收\s*投\s*资\s*收\s*到\s*(?:的\s*)?现\s*金",
        "dividend_profit_or_interest": r"分\s*配\s*股\s*利\s*[、，,]?\s*利\s*润\s*或\s*偿\s*付\s*利\s*息\s*支\s*付\s*(?:的\s*)?现\s*金",
    }
    clues = {}
    for name, pattern in patterns.items():
        matches = list(re.finditer(r"(?m)^(?P<prefix>[^\n]*?)(?P<label>" + pattern + r")(?P<rest>[^\n]*)$", body))
        clues[name] = {
            "status": "label_observed_only" if matches else "label_not_bound_unknown_never_zero",
            "count": len(matches), "amount_extracted": False,
            "locators": [{"literal_label": re.sub(r"\s+", "", match["label"]),
                          "presentation": "explicit_subitem_marker" if "其中" in match["prefix"] else "separate_named_row_without_subitem_proof",
                          "page": source_page(source, hit.end() + match.start()),
                          "offset": hit.end() + match.start(),
                          "label_and_prefix_sha256": hashlib.sha256((match["prefix"] + match["label"]).encode()).hexdigest()}
                         for match in matches],
        }
    return roles, clues


def parse_current_flows(text, metadata, provenance, *, target_fields=None):
    """Bind only requested current annual cash fields in the original edition.

    ``target_fields=None`` requests both known fields. A partial request returns
    only those fields/roles and cannot claim a newly parsed two-field pair.
    The original FY2025 boundary, strict period/unit/text evidence and unresolved
    debt-component scope are retained. Missing values are never filled with zero.
    """
    requested = _requested_fields(target_fields)
    period = str(metadata.get("report_date", ""))
    content_sha = hashlib.sha256(text.encode()).hexdigest()
    base = {"requested_fields": requested, "unrequested_fields": [key for key in LABELS if key not in requested],
            "text_content_sha256": content_sha, "paired_current_amounts": False,
            "financial_input_eligible": False, "original_PDF_freshly_verified": False}
    if (period != "2025-12-31" or not provenance.get("text_hash_reverified")
            or content_sha != provenance.get("text_content_sha256")):
        return {**base, "status": "pending", "reason": "frozen_annual_period_or_text_binding_pending", "fields": {}}
    source = text.replace("\r\n", "\n")
    headings = list(TABLE.finditer(source))
    sections = []
    for index, hit in enumerate(headings):
        if hit["scope"] != "合并" or hit["name"] != "现金流量表" or hit["continued"]:
            continue
        end = next((heading.start() for heading in headings[index + 1:] if not
                    (heading["scope"] == "合并" and heading["name"] == "现金流量表" and heading["continued"])), len(source))
        sections.append((hit, end, source[hit.end():end]))
    if len(sections) != 1:
        return {**base, "status": "pending", "reason": "unique_consolidated_cashflow_table_required", "fields": {}, "table_count": len(sections)}
    hit, end, body = sections[0]
    header = re.split(r"(?m)^\s*" + PREFIX + r"经营活动", body, maxsplit=1)[0]
    columns, unit = _annual_columns(header, period), amount_unit(header)
    fields, field_rows = {}, {}
    for name in requested:
        aliases = LABELS[name]
        pattern = r"(?m)^[^\S\n]*" + PREFIX + r"(?P<label>" + "|".join(_label_pattern(label) for label in aliases) + r")(?P<rest>[^\n]*)$"
        matches = list(re.finditer(pattern, body))
        field = {"status": "pending", "canonical_label": aliases[0], "literal_label": None,
                 "row_count": len(matches), "current_amount_yuan": None}
        fields[name] = field
        if len(matches) != 1:
            field["reason"] = "unique_named_cashflow_row_required"
            continue
        row = matches[0]
        field_rows[name] = row
        cells, snippet, proof = _text_pair(row["rest"], body[row.end():].splitlines(), columns.get("note_column_explicit", False))
        previous = body[:row.start()].rstrip().splitlines()
        field.update(literal_label=re.sub(r"\s+", "", row["label"]),
                     source_row_offset=hit.end() + row.start(), source_page=source_page(source, hit.end() + row.start()),
                     source_row_sha256=hashlib.sha256((row.group() + "\n" + snippet).encode()).hexdigest(),
                     source_label=row.group().strip(), column_parse_evidence=proof,
                     current_printed=cells[0] if len(cells) == 2 else None,
                     printed_column_count=len(cells), comparative_amount_claimed=False,
                     statement_scope="consolidated")
        if previous and re.fullmatch(_NUMERIC_FRAGMENT, previous[-1].strip()):
            boundary = _closed_previous_minority_pair(source, hit, body, row, columns.get("note_column_explicit", False))
            if boundary is None:
                field["reason"] = "adjacent_numeric_fragment_requires_geometry"
                continue
            field["previous_fragment_boundary_evidence"] = boundary
        if len(cells) != 2 or columns["status"] != "explicit_annual_column_pair" or unit["status"] != "unit_verified":
            field["reason"] = "explicit_columns_unit_or_pair_pending"
            continue
        current = money(cells[0])
        if current is None:
            field["reason"] = "current_amount_missing_never_zero"
            continue
        field.update(status="current_annual_amount_bound", current_amount_yuan=amount_text(current * Decimal(unit["multiplier"])),
                     currency=unit["currency"], printed_unit=unit["printed_unit"], source_unit_multiplier=unit["multiplier"],
                     current_printed_quantum_yuan=printed_quantum_yuan(cells[0], unit["printed_unit"]),
                     current_start=columns["spans"]["current_start"], current_end=columns["spans"]["current_end"],
                     explicit_numeric_zero=current == 0, negative_printed_amount=current < 0,
                     negative_requires_domain_review=current < 0)
    roles, clues = _role_clues(source, hit, body, field_rows)
    ready = all(field["status"] == "current_annual_amount_bound" for field in fields.values())
    pair = ready and set(requested) == set(LABELS)
    status = ("two_current_annual_flow_amounts_bound" if pair else
              "requested_current_annual_flow_amounts_bound" if ready else "pending")
    return {**base, "status": status, "paired_current_amounts": pair, "fields": fields,
            "statement": {"scope": "consolidated", "name": "现金流量表", "body_start": hit.end(), "body_end": end,
                          "page": source_page(source, hit.start()), "columns": columns, "unit": unit},
            "field_roles": roles, "other_role_clues_without_amounts": clues,
            "borrowing_vs_total_debt_repayment_scope_matched": False,
            "scope_status": "borrowing_inflow_vs_unallocated_debt_repayment_components_unknown",
            "actual_contract_rollover_or_repayment_difficulty_proved": False,
            "all_interest_or_lease_debt_service_proved": False, "native_default_period_certified": False}
