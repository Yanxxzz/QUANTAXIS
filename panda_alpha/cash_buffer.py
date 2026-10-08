"""Original-edition cash/near-debt source values, with public-time selection.

This is a narrow financial source contract. It neither imports vendor latest
snapshots nor treats missing debt as zero. Amounts refer to consolidated current
columns; source acceptance is not certification of complete revision history.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

from panda_alpha.financial_statements import (
    TABLE, TABLE_NAMES, PREFIX, CELL, HEX, UNIT_MULTIPLIERS,
    iso_day as _day, compact_text as _compact, money as _money,
    amount_text as _number_text, source_page as _page,
    header_period as _header_period, amount_unit as _units,
    column_pair as _column_pair, public_availability,
    flow_spans, normalize_statement_field, printed_quantum_yuan, resolve_statement_columns,
)

FIELDS = {
    "closing_cash_equivalents": ("cashflow", "期末现金及现金等价物余额"),
    "short_term_borrowings": ("balance", "短期借款"),
    "current_noncurrent_liabilities": ("balance", "一年内到期的非流动负债"),
    "total_assets": ("balance", "资产总计"),
}
CURRENT_LIABILITY_LABELS = ["短期借款", "向中央银行借款", "拆入资金", "交易性金融负债", "衍生金融负债",
                          "应付票据", "应付账款", "预收款项", "合同负债", "卖出回购金融资产款", "吸收存款及同业存放",
                          "代理买卖证券款", "代理承销证券款", "应付职工薪酬", "应交税费", "其他应付款",
                          "应付手续费及佣金", "应付分保账款", "持有待售负债", "一年内到期的非流动负债", "一年内到期非流动负债",
                          "其他流动负债", "短期应付债券"]
ROW_LABELS = CURRENT_LIABILITY_LABELS + ["流动负债合计", "非流动负债", "负债合计", "流动负债", "资产总计", "资产合计",
              "流动资产合计", "非流动资产合计", "六、期末现金及现金等价物余额", "现金及现金等价物期末余额",
              "现金及现金等价物的期末余额", "现金和现金等价物期末余额", "加：期初现金及现金等价物余额",
              "其中：应付利息", "其中：应付股利", "应付利息", "应付股利", "法定代表人"]
def _current_liability_zero_proof(section: dict, coordinate_rows: dict | None = None) -> dict:
    """Prove explicitly blank current cells at the original's printed precision.

    No incomplete sum, unknown top-level item, negative component, unclear
    single column or nested child can be used to manufacture debt-free issuers.
    """
    body = section["body"]
    start = re.search(r"(?m)^\s*流动负债[:：]?\s*$", body)
    end = re.search(r"(?m)^\s*流动负债合计(?P<rest>[^\n]*)$", body[start.end():]) if start else None
    base = {"status": "identity_not_proven", "blank_current_labels": []}
    if not start or not end or section["unit"]["status"] != "unit_verified" or section["period"]["status"] != "current_period_verified":
        return {**base, "reason": "source_scope_unit_period_or_total_missing"}
    end_offset = start.end() + end.start()
    segment = body[start.end():end_offset]
    total_cells, total_snippet = _column_pair(end["rest"], [])
    if len(total_cells) == 3 and "附注" in section["header_evidence"] and re.fullmatch(r"\d{1,3}", total_cells[0]):
        total_cells = total_cells[1:]
    total = _money(total_cells[0]) if len(total_cells) == 2 else None
    if total is None or total < 0:
        return {**base, "reason": "current_liability_total_unbound"}
    lines = segment.splitlines()
    components, blanks, seen, last_parent = [], [], set(), None
    labels = sorted(CURRENT_LIABILITY_LABELS, key=len, reverse=True)
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line or re.fullmatch(r"===SOURCE_PAGE:\d+===|\d+|第\d+页共\d+页", line):
            continue
        if re.search(r"(?:年年度报告|年半年度报告|年度报告全文|半年度报告全文)", line):
            continue
        if re.match(r"(?:单位[:：]|人民币元|项目|附注|法定代表人)", line):
            continue
        child = re.match(r"(?:其中[:：])?(应付利息|应付股利)(?:\s|$)", line)
        if child and last_parent == "其他应付款":
            continue
        label = next((label for label in labels if re.match(re.escape(label) + r"(?:\s|$)", line)), None)
        if not label:
            # A wrapped numeric line belongs to the immediately preceding row,
            # but arbitrary prose/unknown liability labels invalidate closure.
            if re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]+", line) and components:
                continue
            return {**base, "reason": "unknown_or_nested_unbound_item", "line": line}
        canonical = "一年内到期的非流动负债" if label == "一年内到期非流动负债" else label
        if canonical in seen:
            return {**base, "reason": "duplicate_primary_liability_row", "line": line}
        seen.add(canonical)
        cells, snippet = _column_pair(line[len(label):], lines[index + 1:])
        if len(cells) == 3 and "附注" in section["header_evidence"] and re.fullmatch(r"\d{1,3}", cells[0]):
            cells = cells[1:]
        coordinate = (coordinate_rows or {}).get(canonical)
        if coordinate and len(cells) == 1:
            original = _money(cells[0])
            present = coordinate.get("current_printed_amount") or coordinate.get("comparative_printed_amount")
            if present is not None and _money(present) == original:
                cells = [coordinate.get("current_printed_amount") or "—",
                         coordinate.get("comparative_printed_amount") or "—"]
        remainder = CELL.sub("", re.sub(r"^[（(]?[一二三四五六七八九十]+[）)]?[、.．]?\s*\d+(?:[（(]\d+[）)])?", "", snippet.strip()))
        if re.search(r"[^\s,，.（）()、]", remainder):
            return {**base, "reason": "unparsed_text_inside_primary_row", "line": line}
        if len(cells) not in {0, 2}:
            return {**base, "reason": "current_column_ambiguous", "line": line, "cells": cells}
        amount = _money(cells[0]) if cells else None
        if amount is not None and amount < 0:
            return {**base, "reason": "negative_primary_liability", "line": line}
        if amount is None:
            blanks.append(canonical)
        pages = re.findall(r"===SOURCE_PAGE:(\d+)===", body[:start.end()] + "\n".join(lines[:index]))
        components.append({"label": canonical, "printed_current_amount": _number_text(amount) if amount is not None else None,
                           "source_cells": cells, "source_line": line,
                           "source_coordinate_proof": coordinate,
                           "source_page": int(pages[-1]) if pages else section["source_page"]})
        last_parent = canonical
    known_sum = sum((Decimal(c["printed_current_amount"]) for c in components if c["printed_current_amount"] is not None), Decimal(0))
    if known_sum != total:
        return {**base, "reason": "printed_primary_sum_does_not_equal_total", "known_sum": _number_text(known_sum),
                "printed_total": _number_text(total), "components": components}
    return {"status": "closed_at_printed_precision", "blank_current_labels": blanks,
            "known_sum": _number_text(known_sum), "printed_total": _number_text(total),
            "components": components, "total_source_line": total_snippet,
            "printed_unit": section["unit"]["printed_unit"], "multiplier": section["unit"]["multiplier"],
            "method": "all_reported_primary_current_liabilities_nonnegative_and_exact_printed_sum_closed",
            "qualification": "zero_at_source_display_precision_not_unrounded_economic_amount"}


def _validated_column_proof(proof: dict, original: dict, field: str, pdf_hash: str, period: str) -> bool:
    """Accept only a source-bound unique amount located relative to both headers."""
    if (proof.get("method") != "original_pdf_header_and_row_coordinates" or proof.get("statement_scope") != "consolidated"
            or proof.get("pdf_sha256") != pdf_hash or proof.get("report_date") != period
            or proof.get("source_page") != original.get("source_page") or proof.get("label") != FIELDS[field][1]
            or original.get("status") != "missing_or_ambiguous_current_column" or len(original.get("cells", [])) != 1):
        return False
    headers = proof.get("column_headers", {})
    current, comparison = headers.get("current", {}), headers.get("comparative", {})
    cells = proof.get("numeric_cells", [])
    try:
        current_x, comparison_x = float(current["x"]), float(comparison["x"])
        if not 50 < comparison_x - current_x < 400 or abs(float(current["y"]) - float(comparison["y"])) >= 4 or len(cells) != 1:
            return False
        cell = cells[0]
        x = (cell["box"][0] + cell["box"][2]) / 2
        y = (cell["box"][1] + cell["box"][3]) / 2
        label = proof["label_box"]
        gap = comparison_x - current_x
        if not (label[1] - 2 <= y <= label[3] + 2
                and max(label[2] + 5, current_x - .6 * gap) < x < comparison_x + .6 * gap):
            return False
        expected_column = "current" if x < (current_x + comparison_x) / 2 else "comparative"
        present = proof.get(expected_column + "_printed_amount")
        absent = proof.get(("comparative" if expected_column == "current" else "current") + "_printed_amount")
        return (cell["column"] == expected_column and absent is None and present is not None
                and _money(present) == _money(cell["printed"]) == _money(original["cells"][0]))
    except (KeyError, TypeError, ValueError):
        return False


def _source_field(text: str, section: dict, field: str, label: str, period: str,
                  zero_evidence: dict | None, pdf_hash: str, provenance: dict | None = None) -> dict:
    body = section["body"]
    aliases = [label]
    if field == "current_noncurrent_liabilities":
        aliases.append("一年内到期非流动负债")
    if field == "closing_cash_equivalents":
        aliases.extend(["现金及现金等价物期末余额", "现金和现金等价物期末余额", "现金及现金等价物的期末余额",
                        "年末现金及现金等价物余额", "期末现金和现金等价物余额"])
    labels = [r"\s*".join(map(re.escape, alias)) for alias in aliases]
    pattern = re.compile(r"(?m)^\s*" + PREFIX + "(?:" + "|".join(labels) + r")(?P<rest>[^\n]*)$")
    matches = list(pattern.finditer(body))
    rows = []
    for hit in matches:
        pages = re.findall(r"===SOURCE_PAGE:(\d+)===", body[:hit.start()])
        row_page = int(pages[-1]) if pages else section["source_page"]
        matched_label = next(alias for alias in aliases if re.sub(r"\s+", "", alias) in re.sub(r"\s+", "", hit.group()))
        cells, snippet, column_proof = resolve_statement_columns(
            hit["rest"], body[hit.end():].splitlines(), text=text, provenance=provenance or {},
            label=matched_label, table_kind=FIELDS[field][0], period=period, source_page_hint=row_page)
        item = {"source_page": int(pages[-1]) if pages else section["source_page"],
                "source_label": hit.group().strip(), "column_source": snippet,
                "cells": cells, "statement_scope": "consolidated"}
        item["column_parse_evidence"] = column_proof
        if len(cells) != 2:
            item.update(status="missing_or_ambiguous_current_column", amount_yuan=None)
        else:
            amount = _money(cells[0])
            comparison = _money(cells[1])
            if amount is None:
                item.update(status="missing_current_amount", amount_yuan=None)
            elif section["unit"]["status"] != "unit_verified" or section["period"]["status"] != "current_period_verified":
                item.update(status="unit_or_period_unverified", amount_yuan=None)
            else:
                multiplier = Decimal(section["unit"]["multiplier"])
                item.update(status="printed_current_amount", amount_yuan=_number_text(amount * multiplier),
                            comparative_amount_yuan=_number_text(comparison * multiplier) if comparison is not None else None,
                            printed_unit=section["unit"]["printed_unit"], currency="CNY",
                            stock_asof=period, zero_method="explicit_numeric_zero" if amount == 0 else None,
                            current_printed_quantum_yuan=printed_quantum_yuan(cells[0], section["unit"]["printed_unit"]),
                            comparative_printed_quantum_yuan=printed_quantum_yuan(cells[1], section["unit"]["printed_unit"]))
                if field == "closing_cash_equivalents":
                    spans = flow_spans(section["header_evidence"], period)
                    comparative_date = spans["comparative_end"]
                    comparative_status = spans["comparative_status"]
                else:
                    comparative_date = section["period"].get("comparative_stock_asof")
                    comparative_status = section["period"].get("comparative_status", "comparative_period_unbound")
                item.update(comparative_stock_asof=comparative_date,
                            comparative_period_status=comparative_status)
        rows.append(item)
    accepted = [r for r in rows if r["status"] == "printed_current_amount"]
    if len(rows) == 1 and len(accepted) == 1:
        return accepted[0]
    if len(rows) > 1:
        return {"status": "multiple_field_rows_ambiguous", "amount_yuan": None, "candidates": rows}
    # Only explicit, source-bound semantic absence is accepted. A dash/blank,
    # caller's zero flag or unrelated debt prose is insufficient.
    proof = zero_evidence or {}
    evidence = re.sub(r"\s+", "", str(proof.get("evidence", "")))
    dense_text = re.sub(r"\s+", "", text)
    valid_zero = (proof.get("method") == "explicit_semantic_absence"
                  and proof.get("statement_scope") == "consolidated"
                  and proof.get("report_date") == period
                  and proof.get("pdf_sha256") == pdf_hash
                  and isinstance(proof.get("source_page"), int)
                  and evidence and evidence in dense_text
                  and any(alias in evidence for alias in aliases)
                  and bool(re.search(r"(?:不存在|余额为零|期末无|无余额|不适用)", evidence)))
    if valid_zero:
        page_marker = f"===SOURCE_PAGE:{proof['source_page']}==="
        start = text.find(page_marker)
        end = text.find("===SOURCE_PAGE:", start + len(page_marker)) if start >= 0 else -1
        valid_zero = start >= 0 and evidence in re.sub(r"\s+", "", text[start:end if end >= 0 else len(text)])
    if valid_zero:
        return {"status": "explicit_semantic_zero", "amount_yuan": "0", "currency": "CNY",
                "statement_scope": "consolidated", "stock_asof": period, "source_page": proof["source_page"],
                "zero_method": "explicit_semantic_absence", "zero_evidence": proof, "blank_candidates": rows}
    return rows[0] if rows else {"status": "field_not_found", "amount_yuan": None}


def parse_cash_buffer_report(text: str, metadata: dict, provenance: dict | None = None,
                             *, zero_evidence: dict | None = None, column_evidence: dict | None = None) -> dict:
    """Parse four current-column stock amounts; keep failures as explicit rows.

    ``text`` can reuse native extraction with ===SOURCE_PAGE:N=== markers.
    Provenance is populated by ``load_cash_buffer_report`` when raw bytes have
    actually been checked. Direct parsing alone is not original-byte acceptance.
    """
    provenance = provenance or {}
    code = str(metadata.get("symbol", metadata.get("code", "")))
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("A six-digit source issuer is required")
    period = _day(metadata["report_date"])
    published = metadata.get("published_at") or metadata["pub_date"]
    available = public_availability(published, metadata.get("available_date"))
    pdf_hash = provenance.get("pdf_sha256") or metadata.get("pdf_sha256") or metadata.get("source_sha256")
    # Keep monetary-column whitespace. Removing it would concatenate current
    # and comparative amounts into a false number.
    normalized = text.replace("\r\n", "\n")
    headers = list(TABLE.finditer(normalized))
    # Some originals print the table title twice around the date, before any
    # actual data. This is one table; fully repeated data tables remain ambiguous.
    continuations = set()
    for i in range(1, len(headers)):
        previous, current = headers[i - 1], headers[i]
        between = normalized[previous.end():current.start()]
        if (previous["scope"] == current["scope"] and previous["name"] == current["name"]
                and len(between) < 400 and not re.search(r"资产总计|短期借款|流动资产|经营活动产生", between)):
            continuations.add(i)
    source_intro = re.sub(r"\s+", "", normalized[:2500])
    target = date.fromisoformat(period)
    source_kind = "(?:年年度报告|年度报告)" if target.month == 12 else "(?:年半年度报告|半年度报告|年中期报告)"
    proof_match = re.search(str(target.year) + source_kind, source_intro)
    source_period_proof = proof_match.group() if proof_match else ""
    sections = {kind: [] for kind in ["balance", "cashflow"]}
    for i, hit in enumerate(headers):
        kind = TABLE_NAMES[hit["name"]]
        if hit["scope"] != "合并" or kind not in sections or hit["continued"] or i in continuations:
            continue
        # A repeated continuation heading does not terminate the same table.
        next_header = next((h for k, h in enumerate(headers[i + 1:], start=i + 1) if k not in continuations and not
                            (h["scope"] == "合并" and TABLE_NAMES[h["name"]] == kind and h["continued"])), None)
        if next_header is None:
            sections[kind].append({"status": "statement_end_boundary_missing", "source_page": _page(normalized, hit.start())})
            continue
        body = normalized[hit.end():next_header.start()]
        # Header ends before the first substantive balance/flow data row.
        first_row = re.search(r"(?m)^\s*(?:流动资产|非流动资产|一[、.]经营活动|货币资金|现金及现金等价物)", body)
        preamble = body[:first_row.start()] if first_row else body[:500]
        unit = _units(preamble)
        if unit["status"] != "unit_verified":
            immediate_before = normalized[max(0, hit.start() - 160):hit.start()]
            # A unit directly preceding a formal table title is table metadata.
            trailing = re.search(r"(?:单位[:：]\s*(?:人民币)?(?:百万元|千元|万元|亿元|元))\s*$", immediate_before)
            if trailing:
                unit = _units(trailing.group() + "\n" + preamble)
                unit["source_context"] = trailing.group()
        labels = "|".join(map(re.escape, sorted(ROW_LABELS, key=len, reverse=True)))
        body = re.sub(r"(?<=[\s\d,，.])(?<!\n)(?=(?:" + labels + r")(?:\s|[:：\d—-]))", "\n", body)
        sections[kind].append({"status": "bounded_consolidated_statement", "source_page": _page(normalized, hit.start()),
                               "body": body, "body_start": hit.end(), "unit": unit,
                               "period": _header_period(preamble, kind, period, source_period_proof), "header_evidence": preamble[-600:]})
    evidence = {}
    for field, (kind, label) in FIELDS.items():
        options = sections[kind]
        if len(options) != 1 or options[0]["status"] != "bounded_consolidated_statement":
            evidence[field] = {"status": "statement_scope_missing_or_ambiguous", "amount_yuan": None}
        else:
            evidence[field] = _source_field(normalized, options[0], field, label, period,
                                            (zero_evidence or {}).get(field), str(pdf_hash), provenance)
    balance_sections = sections["balance"]
    coordinate_rows = {}
    if len(balance_sections) == 1 and balance_sections[0]["status"] == "bounded_consolidated_statement":
        section = balance_sections[0]
        if section["unit"]["status"] == "unit_verified" and section["period"]["status"] == "current_period_verified":
            for field in ["short_term_borrowings", "current_noncurrent_liabilities"]:
                proof = (column_evidence or {}).get(field, {})
                original = evidence[field]
                if not _validated_column_proof(proof, original, field, str(pdf_hash), period):
                    continue
                coordinate_rows[FIELDS[field][1]] = proof
                amount = _money(proof["current_printed_amount"]) if proof["current_printed_amount"] is not None else None
                evidence[field] = {**original, "status": "printed_current_amount_coordinate" if amount is not None else "coordinate_current_column_missing",
                                   "amount_yuan": _number_text(amount * Decimal(section["unit"]["multiplier"])) if amount is not None else None,
                                   "source_coordinate_proof": proof, "printed_unit": section["unit"]["printed_unit"], "currency": "CNY",
                                   "stock_asof": period, "zero_method": "explicit_numeric_zero" if amount == 0 else None}
    zero_proof = _current_liability_zero_proof(balance_sections[0], coordinate_rows) if len(balance_sections) == 1 and balance_sections[0]["status"] == "bounded_consolidated_statement" else {"status": "identity_not_proven", "reason": "consolidated_statement_ambiguous"}
    if zero_proof["status"] == "closed_at_printed_precision":
        for field in ["short_term_borrowings", "current_noncurrent_liabilities"]:
            label = FIELDS[field][1]
            if evidence[field]["amount_yuan"] is None and label in zero_proof["blank_current_labels"]:
                source_row = next(c for c in zero_proof["components"] if c["label"] == label)
                evidence[field] = {"status": "balance_identity_printed_zero", "amount_yuan": "0", "currency": "CNY",
                                   "stock_asof": period, "statement_scope": "consolidated", "source_page": source_row["source_page"],
                                   "source_label": source_row["source_line"], "zero_method": "printed_current_liability_sum_identity",
                                   "blank_source_evidence": evidence[field], "identity_proof": zero_proof,
                                   "pdf_sha256": pdf_hash}
    for row in evidence.values():
        row.setdefault("comparative_stock_asof", None)
        row.setdefault("comparative_period_status", "comparative_period_unbound")
        row["field_contract"] = normalize_statement_field(row, measure="stock", publication=metadata,
                                                          provenance={**provenance, "pdf_sha256": pdf_hash})
    source_verified = (bool(provenance.get("pdf_hash_reverified")) and bool(provenance.get("text_hash_reverified"))
                       and isinstance(pdf_hash, str) and HEX.fullmatch(pdf_hash) is not None)
    values = {field: row["amount_yuan"] for field, row in evidence.items()}
    numeric_ok = all(v is not None for v in values.values())
    if numeric_ok:
        numeric_ok = Decimal(values["total_assets"]) > 0 and all(Decimal(values[f]) >= 0 for f in FIELDS if f != "total_assets")
    status = "source_values_verified" if numeric_ok and source_verified else (
        "source_hash_verification_pending" if numeric_ok else "source_values_pending")
    out = {"schema_version": 2, "field_contract_version": 1,
           "schema_note": "v2 adds source-bound comparative periods and field contracts; v1 receipt hashes are not regenerated in place",
           "source": "cninfo_original_report_cash_near_debt",
           "code": code, "symbol": code, "announcement_id": str(metadata["announcement_id"]),
           "report_date": period, "published_at": str(published), "pub_date": _day(published),
           "available_date": available, "time_precision": metadata.get("time_precision", "date"),
           "title": metadata.get("title", ""), "edition": metadata.get("edition", metadata.get("version_label")),
           "status": status, "values": values, "field_evidence": evidence,
           "statement_evidence": {k: [{a: v for a, v in s.items() if a not in {"body", "body_start"}} for s in ss] for k, ss in sections.items()},
           "provenance": provenance, "pdf_sha256": pdf_hash,
           "current_liability_identity": zero_proof,
           "correction_census_verified": bool(metadata.get("correction_census_verified", False)),
           "corrections": metadata.get("corrections", []), "full_pit_certified": False,
           "source_scope": "four_source_fields_only_within_supplied_original_editions"}
    out["record_sha256"] = hashlib.sha256(json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return out


def load_cash_buffer_report(document: dict, *, zero_evidence: dict | None = None, column_evidence: dict | None = None) -> dict:
    """Reuse original PDF and native text only when both expected hashes match."""
    pdf_path = Path(document.get("path") or document["source_path"])
    text_path = Path(document["text_path"])
    pdf_hash = document.get("pdf_sha256") or document.get("source_sha256") or document.get("sha256")
    text_hash = document.get("text_sha256")
    if not all(isinstance(h, str) and HEX.fullmatch(h) for h in [pdf_hash, text_hash]):
        raise ValueError("Original PDF/text expected SHA256 required")
    raw_pdf, raw_text = pdf_path.read_bytes(), text_path.read_bytes()
    if hashlib.sha256(raw_pdf).hexdigest() != pdf_hash or hashlib.sha256(raw_text).hexdigest() != text_hash:
        raise ValueError("Original PDF or text SHA256 mismatch")
    text = (gzip.decompress(raw_text) if text_path.suffix == ".gz" else raw_text).decode("utf-8")
    provenance = {"pdf_path": str(pdf_path.resolve()), "pdf_sha256": pdf_hash, "pdf_hash_reverified": True,
                  "text_path": str(text_path.resolve()), "text_sha256": text_hash, "text_hash_reverified": True}
    return parse_cash_buffer_report(text, document, provenance, zero_evidence=zero_evidence, column_evidence=column_evidence)


def cash_buffer_score(values: dict) -> float:
    """Frozen CB01 formula, separate from source extraction and admission."""
    numbers = {field: Decimal(str(values[field])) for field in FIELDS}
    if not all(v.is_finite() for v in numbers.values()) or numbers["total_assets"] <= 0:
        raise ValueError("Finite values and positive total assets are required")
    return float((numbers["closing_cash_equivalents"] - numbers["short_term_borrowings"]
                  - numbers["current_noncurrent_liabilities"]) / numbers["total_assets"])


def select_cash_buffer_asof(records: Iterable[dict], *, code: str, decision_date: str,
                            corrections: Iterable[dict] = ()) -> dict:
    """Latest published FY/H1 edition; new missing values block old carry-forward.

    Only supplied revision events are reviewed. The result deliberately does
    not certify a complete announcement census, full market universe or unseen
    economic performance.
    """
    decision = _day(decision_date)
    rows = [r for r in records if r.get("code", r.get("symbol")) == code
            and r.get("available_date", "9999") <= decision and r.get("report_date", "9999") <= decision]
    base = {"pit_usable": False, "values": None, "full_pit_certified": False}
    if not rows:
        return {**base, "status": "missing_disclosed_report"}
    period = max(r["report_date"] for r in rows)
    rows = [r for r in rows if r["report_date"] == period]
    vintage = max(r["available_date"] for r in rows)
    latest = [r for r in rows if r["available_date"] == vintage]
    identities = {(r["announcement_id"], r.get("pdf_sha256"), r.get("record_sha256")) for r in latest}
    if len(identities) > 1:
        # Equal dates and equal amounts cannot repair ambiguous document identity.
        return {**base, "status": "ambiguous_same_day_versions", "report_date": period}
    row = latest[0]
    if row.get("status") != "source_values_verified":
        return {**base, "status": "blocked_by_latest_unparsed_report", "report_date": period,
                "announcement_id": row["announcement_id"]}
    events = list(corrections) + [c for r in rows for c in r.get("corrections", [])]
    for event in events:
        if event.get("code", event.get("symbol")) != code:
            continue
        publication = event.get("published_at") or event.get("pub_date")
        available = public_availability(publication, event.get("available_date")) if publication else event.get("available_date", "9999")
        if available > decision:
            continue
        linked = set(event.get("report_dates", []))
        if event.get("report_date"):
            linked.add(event["report_date"])
        linked.update(p["report_date"] for p in event.get("period_links", []) if p.get("report_date"))
        if linked and period not in linked:
            continue
        if not linked and publication and _day(publication) < period:
            continue
        reviewed = bool(event.get("scope_verified")) and bool(event.get("scope_evidence"))
        if reviewed and event.get("impact_status") == "financial_unrelated":
            continue
        affected = set(event.get("affected_fields", []))
        if reviewed and event.get("impact_status") == "unrelated_fields" and affected and affected.isdisjoint(FIELDS):
            continue
        if (reviewed and period in linked and event.get("impact_status") == "revised_report"
                and row["announcement_id"] in event.get("resolved_by_report_ids", [])):
            continue
        return {**base, "status": "blocked_by_unresolved_correction", "report_date": period,
                "announcement_id": row["announcement_id"], "correction_announcement_id": event.get("announcement_id")}
    return {**base, "status": "usable_within_supplied_vintages", "pit_usable": True,
            "values": row["values"], "report_date": period, "published_at": row["published_at"],
            "available_date": row["available_date"], "announcement_id": row["announcement_id"],
            "pdf_sha256": row["pdf_sha256"], "record_sha256": row["record_sha256"],
            "correction_census_verified": row.get("correction_census_verified", False)}
