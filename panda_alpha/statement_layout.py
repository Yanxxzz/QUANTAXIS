"""Recover monetary columns from one original consolidated PDF table.

The pure grid reader has no PDF dependency. The file adapter verifies original
bytes and the bounded native statement before opening pdfplumber lazily. A
layout repair supplies amount evidence only; publication/revision admission
remains with the caller.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import gzip
import hashlib
from pathlib import Path
import re

from panda_alpha.financial_statements import (
    PREFIX, TABLE, TABLE_NAMES, UNIT_MULTIPLIERS, amount_text, amount_unit,
    flow_spans, money, source_page,
)

LAYOUT_PARSER_VERSION = 2
_INTEGER = r"(?:\d+|\d{1,3}(?:[,，]\d{3})+)"
_AMOUNT = re.compile(r"(?:[+\-−－]?" + _INTEGER + r"(?:\.\d+)?|[（(]" +
                     _INTEGER + r"(?:\.\d+)?[）)])")


def _printed_cell(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if re.search(r"\d[^\S\n]+\d", raw):
        return None
    if len(lines) > 1 and not any("." in line or line.endswith((",", "，", "(", "（"))
                                  for line in lines):
        # Two complete integers inside one cell are not proven continuations.
        return None
    joined = re.sub(r"\s+", "", raw)
    return joined if _AMOUNT.fullmatch(joined) else None


def _label(raw):
    dense = re.sub(r"\s+", "", raw or "")
    return re.sub(r"^" + PREFIX + r"(?:(?:其中|减)[:：])?", "", dense)


def _box_valid(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(isinstance(v, (int, float)) for v in box)
            and box[0] < box[2] and box[1] < box[3])


def _column_role(raw, kind, period, stock_periods=None):
    dense = re.sub(r"\s+", "", raw or "")
    if re.search(r"调整|重述", dense):
        return None
    target = date.fromisoformat(period)
    if kind == "balance":
        if (stock_periods and stock_periods.get("status") == "same_report_stock_columns_bound"
                and stock_periods.get("current_stock_asof") == period
                and stock_periods.get("opening_stock_asof") in {f"{target.year-1}-12-31", f"{target.year}-01-01"}):
            if re.fullmatch(r"期末余额|年末余额|本年年末余额|期末数", dense):
                return "current"
            if re.fullmatch(r"期初余额|年初余额|上年年末余额|期初数", dense):
                return "comparative"
        dates = re.findall(r"(20\d{2})(?:年|-|/)(\d{1,2})(?:月|-|/)(\d{1,2})日?", dense)
        if len(dates) != 1:
            return None
        try:
            value = date(*map(int, dates[0])).isoformat()
        except ValueError:
            return None
        if value == period:
            return "current"
        if value in {f"{target.year-1}-12-31", f"{target.year}-01-01"}:
            return "comparative"
        return None
    # This independently checks the whole cell, avoiding substrings such as a
    # current half-year next to a prior annual or two adjusted values.
    for role, year in [("current", target.year), ("comparative", target.year - 1)]:
        end = f"{year}-{target.month:02}-{target.day:02}"
        spans = flow_spans(dense, end)
        if (spans["current_status"] == "current_period_verified"
                and len(re.findall(r"20\d{2}", dense)) in {1, 2}
                and all(int(y) == year for y in re.findall(r"20\d{2}", dense))):
            return role
    return None


def monetary_grid_pair(rows, boxes, *, label, table_kind, period,
                       statement_scope, unit_evidence, stock_periods=None):
    """Return two dated amount cells, or a precise pending reason.

    Rows and boxes must describe the same observed table. Note columns remain
    separate cells; reversed fiscal columns are supported by explicit labels.
    Dash/blank is unknown, while zero and small amounts keep their printed value.
    """
    pending = lambda reason: {"status": reason, "layout_parser_version": LAYOUT_PARSER_VERSION}
    if (statement_scope != "consolidated" or table_kind not in {"income", "cashflow", "balance"}
            or unit_evidence.get("status") != "unit_verified"
            or unit_evidence.get("currency") != "CNY"
            or unit_evidence.get("printed_unit") not in UNIT_MULTIPLIERS):
        return pending("grid_scope_or_unit_pending")
    hits = [(i, j) for i, row in enumerate(rows) if row
            for j, cell in enumerate(row) if _label(cell) == label]
    if len(hits) != 1:
        return pending("grid_exact_row_missing_or_ambiguous")
    ri, li = hits[0]
    headers = []
    for hi, row in enumerate(rows[:ri]):
        if not row:
            continue
        roles = [_column_role(cell, table_kind, period, stock_periods) for cell in row]
        if roles.count("current") == roles.count("comparative") == 1:
            # Another year/date in a column is a third financial column, not a
            # note identifier to discard. Its period cannot be silently dropped.
            other_dates = any(re.search(r"20\d{2}", cell or "")
                              for cell, role in zip(row, roles) if role is None)
            if not other_dates:
                headers.append((hi, roles.index("current"), roles.index("comparative")))
    if len(headers) != 1:
        return pending("grid_period_columns_pending")
    hi, ci, pi = headers[0]
    if (len(boxes) != len(rows) or any(len(boxes[i]) != len(rows[i]) for i in [hi, ri])):
        return pending("grid_cell_coordinates_pending")
    if not all(_box_valid(boxes[hi][j]) for j in [ci, pi]):
        return pending("grid_cell_coordinates_pending")
    mapped = []
    for j in [ci, pi]:
        header_box = boxes[hi][j]
        candidates = [k for k, cell_box in enumerate(boxes[ri]) if k != li and _box_valid(cell_box)
                      and cell_box[0]-2 <= header_box[0] < header_box[2] <= cell_box[2]+2]
        if len(candidates) != 1:
            return pending("grid_cell_coordinates_pending")
        mapped.append(candidates[0])
    ai, bi = mapped
    cb, pb = boxes[ri][ai], boxes[ri][bi]
    if (min(cb[2], pb[2]) > max(cb[0], pb[0])
            or min(cb[3], pb[3]) <= max(cb[1], pb[1])):
        return pending("grid_cell_coordinates_pending")
    current, prior = _printed_cell(rows[ri][ai]), _printed_cell(rows[ri][bi])
    if current is None or prior is None:
        return pending("grid_amount_cells_pending")
    unit = unit_evidence["printed_unit"]
    mult = UNIT_MULTIPLIERS[unit]
    if table_kind == "balance":
        dates = re.findall(r"(20\d{2})(?:年|-|/)(\d{1,2})(?:月|-|/)(\d{1,2})日?",
                           re.sub(r"\s+", "", rows[hi][pi]))
        comparison = date(*map(int, dates[0])).isoformat() if dates else stock_periods["opening_stock_asof"]
        periods = {"current_stock_asof": period, "comparative_stock_asof": comparison}
    else:
        periods = flow_spans(rows[hi][ci] + " " + rows[hi][pi], period)
        if periods["comparative_status"] != "explicit_prior_same_span":
            return pending("grid_period_columns_pending")
    return {"status": "physical_grid_columns_bound", "layout_parser_version": LAYOUT_PARSER_VERSION,
            "statement_scope": "consolidated", "table_kind": table_kind,
            "currency": "CNY", "printed_unit": unit,
            "current_printed": current, "comparative_printed": prior,
            "current_yuan": amount_text(money(current) * mult),
            "comparative_yuan": amount_text(money(prior) * mult),
            "raw_current_cell": rows[ri][ai], "raw_comparative_cell": rows[ri][bi],
            "current_bbox": list(cb), "comparative_bbox": list(pb),
            "current_header_bbox": list(boxes[hi][ci]), "comparative_header_bbox": list(boxes[hi][pi]),
            "column_headers": rows[hi], "source_row_cells": rows[ri],
            "row_index": ri, "label_column_index": li, "amount_column_indices": [ai, bi],
            "period_evidence": periods}


def _bounded_section(text, kind, *, period=None):
    headings = list(TABLE.finditer(text))
    duplicates = set()
    for i in range(1, len(headings)):
        a, b = headings[i-1], headings[i]
        middle = text[a.end():b.start()]
        if (a["scope"] == b["scope"] and a["name"] == b["name"] and len(middle) < 400
                and not re.search(r"营业(?:总)?收入|营业(?:总)?成本|流动资产|资产总计|应收账款|经营活动", middle)):
            duplicates.add(i)
    sections = []
    for i, heading in enumerate(headings):
        if i in duplicates or heading["scope"] != "合并" or TABLE_NAMES[heading["name"]] != kind or heading["continued"]:
            continue
        end = next((h for j, h in enumerate(headings[i+1:], i+1) if j not in duplicates
                    and not (h["scope"] == "合并" and h["name"] == heading["name"] and h["continued"])), None)
        if end:
            sections.append((heading, end))
    if len(sections) > 1 and kind == "balance" and period is not None:
        from panda_alpha.trade_accrual import trade_stock_periods
        matching = []
        for heading, end in sections:
            body = text[heading.end():end.start()]
            first = re.search(r"(?m)^\s*(?:流动资产|非流动资产|货币资金|现金及存放中央银行)", body)
            header = body[:first.start()] if first else body[:500]
            if trade_stock_periods(header, period, text[:2500])["status"] == "same_report_stock_columns_bound":
                matching.append((heading, end))
        if len(matching) == 1:
            return matching[0]
    return sections[0] if len(sections) == 1 else None


def _page_captions(page):
    lines = []
    for word in sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(lines[-1][0] - word["top"]) > 3:
            lines.append((word["top"], [word]))
        else:
            lines[-1][1].append(word)
    captions = [(y, TABLE.fullmatch("".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))))
                for y, ws in lines]
    return [(y, h) for y, h in captions if h]


def _continuation_pair(pdf, table, *, start_page, page_number, label, table_kind, period, unit, stock_periods):
    """Carry an observed header only across matching physical column edges."""
    rows = table.extract()
    hits = [i for i, row in enumerate(rows) if row and any(_label(cell) == label for cell in row)]
    if len(hits) != 1 or not start_page or start_page >= page_number:
        return None
    # A present but ambiguous header cannot be replaced by an older one.
    if any(row and any(_label(cell) == "项目" for cell in row) for row in rows[:hits[0]]):
        return None
    ri = hits[0]; target_boxes = table.rows[ri].cells
    for number in range(page_number - 1, start_page - 1, -1):
        candidates = []; page = pdf.pages[number - 1]; captions = _page_captions(page)
        for previous in page.find_tables():
            if abs(previous.bbox[0]-table.bbox[0]) > 2 or abs(previous.bbox[2]-table.bbox[2]) > 2:
                continue
            for hi, header in enumerate(previous.extract()):
                if not header:
                    continue
                roles = [_column_role(cell, table_kind, period, stock_periods) for cell in header]
                if roles.count("current") != 1 or roles.count("comparative") != 1:
                    continue
                header_boxes = previous.rows[hi].cells
                if not all(_box_valid(header_boxes[j]) for j, role in enumerate(roles) if role):
                    continue
                before = [(y, h) for y, h in captions if y < min(b[1] for b in header_boxes if _box_valid(b))]
                if before:
                    caption = max(before, key=lambda x: x[0])[1]
                    if caption["scope"] != "合并" or TABLE_NAMES[caption["name"]] != table_kind:
                        continue
                elif number == start_page:
                    continue
                proof = monetary_grid_pair([header, rows[ri]], [header_boxes, target_boxes], label=label,
                    table_kind=table_kind, period=period, statement_scope="consolidated", unit_evidence=unit,
                    stock_periods=stock_periods)
                if proof["status"] == "physical_grid_columns_bound":
                    proof.update(header_source_page=number, source_row_index=ri,
                                 column_binding_method="same_statement_continuation_matching_outer_edges_and_period_column_spans")
                    candidates.append(proof)
        if candidates:
            return candidates[0] if len(candidates) == 1 else None
    return None


def original_pdf_pair(text, provenance, *, label, table_kind, period, source_page_hint=None):
    """Verify original PDF/native bytes, scope, row and grid before repair.

    Missing optional dependency or inaccessible files leave evidence pending.
    The adapter never borrows a parent table's dates/unit or another report.
    """
    pending = lambda reason: {"status": reason, "layout_parser_version": LAYOUT_PARSER_VERSION}
    if not all(provenance.get(k) for k in ("pdf_path", "text_path", "pdf_sha256", "text_sha256")):
        return pending("original_layout_paths_pending")
    try:
        pdf_path, text_path = Path(provenance["pdf_path"]), Path(provenance["text_path"])
        pdf_bytes, raw = pdf_path.read_bytes(), text_path.read_bytes()
        if (hashlib.sha256(pdf_bytes).hexdigest() != provenance["pdf_sha256"]
                or hashlib.sha256(raw).hexdigest() != provenance["text_sha256"]):
            return pending("original_layout_hash_mismatch")
        native = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8").replace("\r\n", "\n")
        if native != text.replace("\r\n", "\n"):
            return pending("original_layout_native_text_mismatch")
    except (OSError, ValueError, UnicodeError, EOFError):
        return pending("original_layout_source_unreadable")
    section = _bounded_section(native, table_kind, period=period)
    if not section:
        return pending("original_layout_consolidated_boundary_pending")
    heading, end = section
    body = native[heading.end():end.start()]
    pattern = re.compile(r"(?m)^\s*" + PREFIX + r"(?:(?:其中|减)\s*[:：]\s*)?" +
                         r"\s*".join(map(re.escape, label)) + r"(?=[\s:：+\-−－—–/()（）\d]|$)[^\n]*$")
    hits = list(pattern.finditer(body))
    if len(hits) != 1:
        return pending("original_layout_exact_row_pending")
    offset = heading.end() + hits[0].start()
    page_number = source_page(native, offset)
    if not page_number or (source_page_hint is not None and source_page_hint != page_number):
        return pending("original_layout_source_page_pending")
    first = re.search(r"(?m)^\s*" + PREFIX + r"(?:其中\s*[:：]\s*)?营业(?:总)?(?:收入|成本)|"
                      r"(?m:^\s*(?:流动资产|非流动资产|货币资金|经营活动))", body)
    header = body[:first.start()] if first else body[:hits[0].start()]
    unit = amount_unit(header)
    stock_periods = None
    if table_kind == "balance":
        from panda_alpha.trade_accrual import trade_stock_periods
        stock_periods = trade_stock_periods(header, period, native[:2500])
    try:
        import pdfplumber
        from pdfplumber.utils.exceptions import PdfminerException
        from pdfminer.pdfexceptions import PDFException
        from pdfminer.psparser import PSException
    except ImportError:
        return pending("original_layout_dependency_unavailable")
    candidates = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_number > len(pdf.pages):
                return pending("original_layout_source_page_pending")
            page = pdf.pages[page_number-1]
            captions = _page_captions(page)
            for table in page.find_tables():
                rows = table.extract()
                proof = monetary_grid_pair(rows, [row.cells for row in table.rows], label=label,
                                           table_kind=table_kind, period=period,
                                           statement_scope="consolidated", unit_evidence=unit, stock_periods=stock_periods)
                if proof["status"] != "physical_grid_columns_bound":
                    proof = _continuation_pair(pdf, table, start_page=source_page(native, heading.start()),
                        page_number=page_number, label=label, table_kind=table_kind, period=period,
                        unit=unit, stock_periods=stock_periods)
                    if proof is None:
                        continue
                else:
                    proof["header_source_page"] = page_number
                    proof["column_binding_method"] = "same_page_physical_grid"
                y = proof["current_bbox"][1]
                before = [(cy, h) for cy, h in captions if cy < y]
                if before:
                    last = max(before, key=lambda x: x[0])[1]
                    if last["scope"] != "合并" or TABLE_NAMES[last["name"]] != table_kind:
                        continue
                elif source_page(native, heading.start()) == page_number:
                    continue
                # Page membership is bound by the native consolidated section;
                # captions on this same PDF page further exclude parent rows.
                proof.update(source_page=page_number, source_label=hits[0].group().strip(),
                             pdf_sha256=provenance["pdf_sha256"], text_sha256=provenance["text_sha256"],
                             original_byte_hashes_reverified=True, statement_start_offset=heading.end(),
                             statement_end_offset=end.start(), source_row_offset=offset,
                             actual_statement_header=header, unit_evidence=unit)
                candidates.append(proof)
    except (OSError, ValueError, IndexError, PDFException, PSException, PdfminerException):
        return pending("original_layout_pdf_unreadable")
    return candidates[0] if len(candidates) == 1 else pending("original_layout_grid_missing_or_ambiguous")
