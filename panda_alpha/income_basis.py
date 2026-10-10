"""Narrow, byte-bound bridges for retained FY revenue/cost comparisons.

This module does not select a vintage or certify PIT. A resolution concerns
only the two named income fields; balance-sheet and consolidation events stay
visible. Callers supply archived capture/text bytes and their bound hashes.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
import re

from panda_alpha.financial_statements import (
    UNIT_MULTIPLIERS, flow_spans, money, source_page,
)


FIELDS = ("operating_revenue", "operating_cost")
PAGE_TEXT_JSON_PROJECTION = "join normalized page strings with ===SOURCE_PAGE:N=== markers; no stored duplicate"
_SPACE = r"[^\S\n]*"
_PREFIX = r"(?:[（(]?[一二三四五六七八九十\d]+[）)]?[、.．]?[^\S\n]*)?"
POLICY_HEADING = re.compile(
    r"(?m)^" + _SPACE + _PREFIX + r"(?:重要)?会计政策变更" + _SPACE + r"[:：]?"
    + r"(?=[^\S\n]*(?:[□☑√✓■](?:适用|不适用)|$))")
_NEXT_SECTION = re.compile(
    r"(?m)^" + _SPACE + _PREFIX
    + r"(?:(?:重要)?会计估计变更|(?:20\d{2}\s*年起)?首次执行|(?:20\d{2}\s*年起)?首次采用|其他\s*$)")
_CHECKED = r"[☑√✓■]"
_HEX = re.compile(r"[0-9a-f]{64}")


def checked_section_choice(text: str, end: int, yes: str, no: str) -> bool:
    """Read the next checkbox line, never a subsequent section's choice."""
    tail = text[end:]
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        dense = re.sub(r"\s+", "", line).strip("：:")
        if not dense:
            continue
        # The check must be on this first meaningful line, not in later prose.
        if re.search(_CHECKED + re.escape(no), dense):
            return False
        return bool(re.search(_CHECKED + re.escape(yes), dense))
    return False


def policy_change_sections(text: str) -> list[dict]:
    """Retain complete policy sections, excluding prose mentions of a heading."""
    headings = list(POLICY_HEADING.finditer(text))
    sections = []
    for index, hit in enumerate(headings):
        limit = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        following = _NEXT_SECTION.search(text, hit.end(), limit)
        end = following.start() if following else limit
        sections.append({"start": hit.start(), "end": end,
                         "source_page": source_page(text, hit.start()),
                         "source_evidence": text[hit.start():end],
                         "checked_applicable": checked_section_choice(text, hit.end(), "适用", "不适用")})
    return sections


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: dict) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode())


def _unique_page_container_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("page_text_json_duplicate_key")
        result[key] = value
    return result


def _bound_text(prov: dict, binding: dict) -> str:
    text_keys = [key for key in ("text_gzip", "text_json") if key in binding]
    if len(text_keys) != 1:
        raise ValueError("exactly_one_retained_text_binding_required")
    key = text_keys[0]
    raw = binding[key]
    if not isinstance(raw, bytes) or _sha(raw) != prov.get("text_sha256"):
        raise ValueError("retained_text_hash_mismatch")
    if key == "text_gzip":
        if prov.get("text_storage_format") not in {None, "native_text_gzip"}:
            raise ValueError("retained_text_binding_format_mismatch")
        decoded = gzip.decompress(raw).decode("utf-8")
    else:
        storage = prov.get("text_storage_binding", {})
        if (prov.get("text_storage_format") != "page_text_json"
                or not isinstance(storage, dict)
                or storage.get("format") != "page_text_json"
                or storage.get("projection") != PAGE_TEXT_JSON_PROJECTION):
            raise ValueError("page_text_json_format_or_projection_pending")
        if (storage.get("sha256") != prov.get("text_sha256")
                or not isinstance(prov.get("text_path"), str)
                or not prov["text_path"] or storage.get("path") != prov["text_path"]):
            raise ValueError("page_text_json_storage_binding_mismatch")
        packet = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_page_container_keys)
        if not isinstance(packet, dict) or set(packet) != {"source_sha256", "pages"}:
            raise ValueError("page_text_json_schema_pending")
        if packet["source_sha256"] != prov["pdf_sha256"]:
            raise ValueError("page_text_json_pdf_identity_mismatch")
        pages = packet["pages"]
        if not isinstance(pages, list) or not pages or any(not isinstance(page, str) for page in pages):
            raise ValueError("page_text_json_page_schema_pending")
        pages = [page.replace("\r\n", "\n") for page in pages]
        if (any(not page.strip() or re.search(r"===SOURCE_PAGE:\d+===", page) for page in pages)
                or len(set(pages)) != len(pages)):
            raise ValueError("page_text_json_empty_duplicate_or_embedded_page_marker")
        # This legacy schema identifies physical pages by array position. Raw
        # bytes bind their order; the declared projection emits contiguous1..N.
        decoded = "\n".join(f"===SOURCE_PAGE:{index + 1}===\n{page}" for index, page in enumerate(pages))
    if "text_content_sha256" in prov and _sha(decoded.encode("utf-8")) != prov["text_content_sha256"]:
        raise ValueError("retained_text_content_hash_mismatch")
    return decoded


def _bound_capture(document: dict, binding: dict) -> tuple[dict, str]:
    """Bind the fields outside stock.record_sha256 to the actual capture bytes."""
    raw, digest = binding.get("capture_gzip"), binding.get("capture_sha256", "")
    if not isinstance(raw, bytes) or not _HEX.fullmatch(str(digest)) or _sha(raw) != digest:
        raise ValueError("capture_bytes_or_hash_mismatch")
    if json.loads(gzip.decompress(raw)) != document:
        raise ValueError("capture_document_mismatch")
    stock = document["stock"]
    digest = stock.get("record_sha256", "")
    if not _HEX.fullmatch(str(digest)) or _canonical({k: v for k, v in stock.items() if k != "record_sha256"}) != digest:
        raise ValueError("stock_record_hash_mismatch")
    prov = stock["provenance"]
    if (stock.get("pdf_sha256") != prov.get("pdf_sha256")
            or not _HEX.fullmatch(str(prov.get("pdf_sha256", "")))):
        raise ValueError("archived_pdf_identity_mismatch")
    return stock, _bound_text(prov, binding)


def _income_column_header(header: str, period: str) -> str:
    """Keep fiscal columns distinct from one matching FY period caption."""
    lines = [line for line in header.splitlines()
             if not (re.fullmatch(r"[^\n]{0,100}20\d{2}\s*年?\s*年度报告(?:全文)?", line.strip())
                     and len(re.findall(r"20\d{2}", line)) == 1
                     and "项目" not in line)]
    captions = []
    for index, line in enumerate(lines):
        hit = re.fullmatch(r"(20\d{2})\s*年?\s*(\d{1,2})\s*[-－—~至]\s*(\d{1,2})\s*月", line.strip())
        if hit:
            if (hit[1], int(hit[2]), int(hit[3])) != (period[:4], 1, 12):
                raise ValueError("income_period_caption_conflicts_with_columns")
            captions.append(index)
    column_lines = [(index, line) for index, line in enumerate(lines) if "项目" in line]
    # A reporting-period caption is removable only before one complete pair of
    # explicit FY columns. Split/extra/contradictory columns stay pending.
    if (len(captions) == 1 and len(column_lines) == 1
            and captions[0] < column_lines[0][0]
            and flow_spans(column_lines[0][1], period)["comparative_status"] == "explicit_prior_same_span"):
        lines.pop(captions[0])
    return "\n".join(lines)


def _income_fields(document: dict, text: str, period: str) -> dict:
    result = {}
    for field in FIELDS:
        e = document.get("fields", {}).get(field, {})
        if e.get("statement_scope") != "consolidated" or e.get("currency") != "CNY":
            raise ValueError("income_scope_or_currency_pending")
        unit = e.get("printed_unit")
        header = e.get("actual_table_header", "")
        cells = e.get("source_cells", [])
        column_header = _income_column_header(header, period)
        spans = flow_spans(column_header, period)
        if (unit not in UNIT_MULTIPLIERS or len(cells) != 2
                or spans["comparative_status"] != "explicit_prior_same_span"):
            raise ValueError("income_period_unit_or_columns_pending")
        if header not in text:
            raise ValueError("income_header_not_in_retained_text")
        offset = e.get("source_row_offset")
        if not isinstance(offset, int) or not 0 <= offset < len(text):
            raise ValueError("income_row_offset_pending")
        label = e.get("source_label", "")
        snippet = text[offset:offset + max(200, len(label) + 20)]
        if not label or label not in snippet or not all(str(c) in snippet for c in cells):
            raise ValueError("income_row_not_in_retained_text")
        if source_page(text, offset) != e.get("source_page"):
            raise ValueError("income_row_page_mismatch")
        a, b = map(money, cells)
        if a is None or b is None:
            raise ValueError("income_amount_pending")
        a, b = a * UNIT_MULTIPLIERS[unit], b * UNIT_MULTIPLIERS[unit]
        if money(str(e.get("current_yuan", ""))) != a or money(str(e.get("comparative_yuan", ""))) != b:
            raise ValueError("income_printed_amount_mismatch")
        supplied = e.get("period_evidence", {})
        if any(supplied.get(k) != spans[k] for k in ["current_end", "comparative_end"]):
            raise ValueError("income_supplied_period_mismatch")
        result[field] = {"current": a, "comparative": b, "evidence": deepcopy(e)}
    return result


def _dense(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"===SOURCE_PAGE:\d+===", "", text))


def _item_kind(text: str) -> str:
    dense = _dense(text)
    if "无重大影响" in dense:
        return "unknown"
    if re.search(r"对(?:公司|本公司|本集团)?(?:可比期间)?财务报表(?:数据)?无影响[。.]?$", dense):
        return "explicit_no_effect"
    if re.search(r"对本公司报表项目和金额无影响[。.]?$", dense):
        return "explicit_no_effect"
    if ("供应商融资安排的披露" in dense
            and not re.search(r"追溯|调整|重述|营业收入|营业成本", dense)):
        return "disclosure_only"
    if ("保证类质量保证" in dense and "18号" in dense
            and "可比期间" in dense and "追溯调整" in dense):
        return "warranty_reclassification"
    return "unknown"


def _policy_items(section: str) -> tuple[str, list[str]]:
    # Explicit numbered/bulleted items are required for a multi-policy section.
    hits = list(re.finditer(r"(?m)^\s*(?:\d+[.．、]|[①②③④⑤⑥⑦⑧⑨⑩])", section))
    if not hits:
        return section, []
    return section[:hits[0].start()], [section[h.start():hits[i + 1].start() if i + 1 < len(hits) else len(section)]
                                     for i, h in enumerate(hits)]


def _boilerplate_only(text: str) -> bool:
    dense = _dense(text)
    for value in ["会计政策变更的内容和原因", "受重要影响的报表项目名称", "影响金额",
                  "重要会计政策变更", "会计政策变更", "适用□不适用", "☑适用□不适用",
                  "√适用□不适用", "✓适用□不适用", "单位：元", "单位:元", "币种：人民币"]:
        dense = dense.replace(value, "")
    dense = re.sub(r"^[（(]?\d+[）)]?[、.．]?", "", dense)
    dense = dense.strip("：:")
    return not dense


def resolve_income_basis_change(current: dict, prior: dict, *, text: str,
                                provenance: dict) -> dict:
    """Resolve only Rev/Cost events using two source-bound annual captures.

    ``provenance`` has ``current``/``prior`` mappings, each containing archived
    ``capture_gzip`` bytes, their bound ``capture_sha256``, and exactly one of
    ``text_gzip`` or explicitly declared legacy ``text_json`` bytes. ``text``
    must equal the source-bound decoded current text. No PDF recheck is claimed.
    Unrecognized extra policy items, uncertain amounts/periods, and merely
    'no material impact' statements remain pending.

    Current and prior non-income barriers are returned separately for callers
    to reconcile by field. Prior barriers are unknown (``None``) until its
    capture bytes bind; preserved metadata does not certify complete prior PIT
    or change an otherwise resolved income-field bridge.
    """
    output = {"status": "pending", "resolved_fields": [], "evidence": [],
              "remaining_non_income_barriers": deepcopy(current.get("stock", {}).get("comparison_change_barriers", [])),
              "remaining_prior_non_income_barriers": None,
              "full_pit_certified": False, "pdf_original_freshly_checked": False,
              "scope": "named_revenue_cost_fields_only"}
    try:
        stock, original = _bound_capture(current, provenance.get("current", {}))
        older, old_text = _bound_capture(prior, provenance.get("prior", {}))
        prior_barriers = deepcopy([barrier for barrier in older.get("comparison_change_barriers", [])
                                   if barrier.get("kind") != "accounting_policy"])
        if older.get("edition") == "REVISED_FULL_REPORT" and not any(
                barrier.get("kind") == "selected_prior_revised_report_gate_preserved"
                and barrier.get("announcement_id") == older["announcement_id"] for barrier in prior_barriers):
            prior_barriers.append({"kind": "selected_prior_revised_report_gate_preserved",
                                   "announcement_id": older["announcement_id"]})
        output["remaining_prior_non_income_barriers"] = prior_barriers
        if original != text:
            raise ValueError("decoded_current_text_mismatch")
        period = date.fromisoformat(stock["report_date"])
        if not stock["report_date"].endswith("12-31") or older["report_date"] != f"{period.year - 1}-12-31":
            raise ValueError("annual_comparison_period_mismatch")
        if stock["code"] != older["code"] or stock["announcement_id"] == older["announcement_id"]:
            raise ValueError("source_identity_mismatch")
        if (stock.get("edition") not in {"ORIGINAL_FULL_REPORT", "REVISED_FULL_REPORT"}
                or older.get("edition") not in {"ORIGINAL_FULL_REPORT", "REVISED_FULL_REPORT"}
                or "摘要" in str(stock.get("title", "")) + str(older.get("title", ""))):
            raise ValueError("full_report_required")
        if str(older.get("available_date", "9999")) >= str(stock.get("available_date", "")):
            raise ValueError("prior_vintage_available_time_mismatch")
        new_fields = _income_fields(current, original, stock["report_date"])
        old_fields = _income_fields(prior, old_text, older["report_date"])
        from panda_alpha.gross_profitability import _actual_change_evidence
        original_events = _actual_change_evidence(original, new_fields[FIELDS[0]]["evidence"]["actual_table_header"])
        output["remaining_non_income_barriers"].extend(deepcopy(event) for event in original_events
                                                       if event["kind"] != "accounting_policy")
        deltas = {f: new_fields[f]["comparative"] - old_fields[f]["current"] for f in FIELDS}
        all_sections = policy_change_sections(original)
        sections = [s for s in all_sections if s["checked_applicable"]]
        if len(sections) != 1:
            raise ValueError("policy_section_missing_or_ambiguous")
        # An unchecked management-summary duplicate cannot hide an additional
        # unbridged change. Only a fully itemized no-effect duplicate is benign.
        for duplicate in all_sections:
            if duplicate is sections[0]:
                continue
            prefix, items = _policy_items(duplicate["source_evidence"])
            if (not _boilerplate_only(prefix) or not items
                    or any(_item_kind(item) not in {"explicit_no_effect", "disclosure_only"} for item in items)):
                raise ValueError("unbridged_additional_policy_section")
        section = sections[0]
        # Contradictory template checkboxes alone cannot establish an effect.
        dense = _dense(section["source_evidence"])
        no_change = re.search(r"报告期内本公司无重要会计政策变更[。.]?", dense)
        mode = None
        if no_change and _boilerplate_only(section["source_evidence"].replace(no_change.group(), "")):
            if any(deltas.values()):
                raise ValueError("no_policy_but_comparison_amount_changed")
            mode = "explicit_no_policy_and_unchanged_printed_comparatives"
        else:
            if not section["checked_applicable"]:
                raise ValueError("explicit_policy_section_required")
            preamble, items = _policy_items(section["source_evidence"])
            meaningful = ([] if _boilerplate_only(preamble) else [preamble]) + items
            kinds = [_item_kind(item) for item in meaningful]
            if not meaningful or "unknown" in kinds:
                raise ValueError("unbridged_policy_item")
            if "warranty_reclassification" not in kinds:
                if any(deltas.values()):
                    raise ValueError("no_effect_but_comparison_amount_changed")
                mode = "explicit_itemized_no_effect_and_unchanged_printed_comparatives"
            else:
                # The exact signs come from named source rows, not a guessed
                # absolute impact value or the name of a regulation.
                block = section["source_evidence"]
                number = r"[+\-−－]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)\.\d+"
                cost = re.findall(r"(?m)^\s*营业成本\s+(" + number + r")\s*$", block)
                expense = re.findall(r"(?m)^\s*销售费用\s+(" + number + r")\s*$", block)
                if (len(cost) != 1 or len(expense) != 1
                        or not re.search(r"可比期间.{0,20}追溯调整", _dense(block))):
                    raise ValueError("quantified_comparative_reclassification_pending")
                impact, reverse = money(cost[0]), money(expense[0])
                if (impact is None or reverse is None or impact == 0 or impact != -reverse
                        or deltas["operating_cost"] != impact or deltas["operating_revenue"] != 0
                        or "单位：元" not in dense and "单位:元" not in dense):
                    raise ValueError("quantified_reclassification_direction_amount_or_unit_mismatch")
                mode = "source_quantified_comparative_cost_reclassification"
        output.update(status="income_fields_resolved", resolved_fields=list(FIELDS),
                      evidence=[{"mode": mode, "current_announcement_id": stock["announcement_id"],
                                 "prior_announcement_id": older["announcement_id"],
                                 "current_record_sha256": stock["record_sha256"],
                                 "prior_record_sha256": older["record_sha256"],
                                 "current_capture_sha256": provenance["current"]["capture_sha256"],
                                 "prior_capture_sha256": provenance["prior"]["capture_sha256"],
                                 "current_text_sha256": stock["provenance"]["text_sha256"],
                                 "prior_text_sha256": older["provenance"]["text_sha256"],
                                 "comparison_period": older["report_date"],
                                 "policy_source_page": section["source_page"],
                                 "policy_excerpt": section["source_evidence"],
                                 "comparative_deltas_yuan": {f: format(d, "f") for f, d in deltas.items()},
                                 "field_source_pages": {f: new_fields[f]["evidence"]["source_page"] for f in FIELDS}}])
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        output["pending_reason"] = str(exc)
    return output
