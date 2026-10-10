"""Bound, opt-in revision scope for G; never upgrades a source mask."""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import gzip
import json
import re


G_FIELDS = frozenset({"operating_revenue", "operating_cost", "total_assets"})
FIELD_TOKENS = {
    "operating_revenue": ("营业收入", "营业总收入"),
    "operating_cost": ("营业成本",),
    "total_assets": ("资产总计", "总资产"),
    "environmental_penalty_disclosure": ("环境和社会责任", "重大环保问题"),
    "entrusted_financial_product_disclosure": ("委托理财",),
    "audit_note_reference": ("审计报告",),
    "segment_revenue_disclosure_unit": ("特种装备", "超硬材料"),
}
IDENTITY_KEYS = ("code", "announcement_id", "edition", "report_date", "published_at", "available_date", "pdf_sha256")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _dense(text):
    return re.sub(r"\s+", "", text)


def assess_g_revision_scope(event: dict, contract: dict, evidence: dict, *,
                            original_pdf: bytes, extract_pages,
                            source_identity_capture: bytes | None = None) -> dict:
    """Assess one explicitly reviewed event/object, not all issuer revisions.

    ``extract_pages(pdf_bytes, page_numbers)`` is trusted caller code returning
    ``{'page_count': int, 'pages': {one_based_number: native_text}}``. It must
    derive pages from those bytes; it is never supplied by evidence/data.
    All span offsets and SHA256s are checked against that extraction. Notice
    scope requires its complete pages and explicit unchanged-other-content
    statement. The annual template applies only to the named before/after
    comparative-column pairs, not all policy changes or current values.
    Missing source/roles stay pending. No old six-field default is changed.
    """
    result = {"status": "pending", "affected_G_inputs": [],
              "scope_object": evidence.get("scope_object"),
              "source_mask_auto_unlock": False, "full_pit_certified": False}
    try:
        if any(evidence.get("event_identity", {}).get(k) != event.get(k) for k in IDENTITY_KEYS):
            raise ValueError("event_identity_or_vintage_mismatch")
        if not re.fullmatch(r"[0-9]{6}", str(event.get("code", ""))) or not str(event.get("announcement_id", "")).isdigit():
            raise ValueError("event_identity_pending")
        if contract.get("code") != event["code"]:
            raise ValueError("contract_issuer_mismatch")
        current = date.fromisoformat(contract["report_date"])
        if (current.month, current.day) != (12, 31):
            raise ValueError("G_full_FY_contract_required")
        asof = date.fromisoformat(contract["asof"])
        publication = date.fromisoformat(str(event["published_at"])[:10])
        available = date.fromisoformat(event["available_date"])
        if available < publication + timedelta(days=1) or available > asof:
            raise ValueError("event_availability_pending_or_after_asof")
        source = evidence["source"]
        pdf_sha = source["pdf_sha256"]
        if (not isinstance(original_pdf, bytes) or not re.fullmatch(r"[0-9a-f]{64}", str(pdf_sha))
                or pdf_sha != event.get("pdf_sha256") or _sha(original_pdf) != pdf_sha):
            raise ValueError("original_PDF_bytes_or_SHA_mismatch")
        scope = evidence["scope_object"]
        pages = source["read_pages"]
        if (not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages)
                or len(set(pages)) != len(pages)):
            raise ValueError("source_page_selection_pending")
        native = extract_pages(original_pdf, pages)
        if native["page_count"] != source["physical_page_count"] or set(native["pages"]) != set(pages):
            raise ValueError("source_physical_pages_mismatch")
        texts = {p: t.replace("\r\n", "\n") for p, t in native["pages"].items() if isinstance(t, str)}
        if set(texts) != set(pages) or any(not t.strip() for t in texts.values()):
            raise ValueError("native_source_page_text_pending")
        spans = {}
        for span in evidence["spans"]:
            name, page, start, end = (span[k] for k in ["name", "page", "start", "end"])
            if (name in spans or page not in texts or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(texts[page])):
                raise ValueError("source_span_coordinates_pending")
            text = texts[page][start:end]
            if _sha(text.encode("utf-8")) != span["sha256"]:
                raise ValueError("source_span_SHA_mismatch")
            spans[name] = _dense(text)
        affected = set(evidence["affected_fields"])
        if not affected or not affected.issubset(FIELD_TOKENS):
            raise ValueError("affected_fields_unknown")
        for field in affected:
            proof = spans[evidence["field_proofs"][field]]
            if not any(token in proof for token in FIELD_TOKENS[field]):
                raise ValueError("affected_field_body_evidence_pending")
        periods = set(evidence["affected_report_periods"])
        if not periods or any(not re.fullmatch(r"20\d{2}-12-31", p) for p in periods):
            raise ValueError("affected_period_roles_pending")
        if scope == "complete_correction_notice":
            if event["edition"] != "CORRECTION_NOTICE" or set(pages) != set(range(1, native["page_count"] + 1)):
                raise ValueError("complete_notice_pages_required")
            if "其他内容不变" not in spans[evidence["unchanged_other_content_proof"]]:
                raise ValueError("explicit_complete_notice_scope_pending")
            body = _dense("\n".join(texts[p] for p in sorted(texts)))
            issuer_codes = set(re.findall(r"(?m)^\s*(?:证券代码|股票代码)\s*[:：]?\s*(\d{6})(?!\d)",
                                          "\n".join(texts[p] for p in sorted(texts))))
            if issuer_codes != {event["code"]}:
                raise ValueError("original_notice_issuer_identity_pending")
            if not re.search("关于" + event["report_date"][:4] + r"年年度报告(?:全文)?(?:的)?更正公告", body):
                raise ValueError("original_notice_fiscal_title_pending")
            if re.search(r"追溯|重述|20\d{2}年?(?:半年度|第?[一二三四1234]季度)", body):
                raise ValueError("notice_cross_period_roles_need_separate_explicit_proof")
            for field in G_FIELDS - affected:
                if any(token in body for token in FIELD_TOKENS[field]):
                    audit_segment = (field == "operating_revenue"
                                     and "segment_revenue_disclosure_unit" in affected
                                     and "audit_note_reference" in affected
                                     and "审计报告" in body and "合并利润表" not in body
                                     and "合并资产负债表" not in body)
                    if not audit_segment:
                        raise ValueError("notice_contains_unmapped_G_field")
            source_years = set(re.findall(r"(20\d{2})年?(?:年度|年末)", body))
            if periods != {year + "-12-31" for year in source_years}:
                raise ValueError("notice_body_periods_incomplete_or_conflicting")
            for period in periods:
                proof = spans[evidence["period_proofs"][period]]
                if not re.search(period[:4] + r"年?(?:年度|年末)", proof):
                    raise ValueError("affected_period_body_evidence_pending")
        elif scope == "named_comparative_before_after_pairs":
            if event["edition"] not in {"ORIGINAL_FULL_REPORT", "REVISED_FULL_REPORT"}:
                raise ValueError("full_report_comparative_object_required")
            year = int(event["report_date"][:4])
            if (not isinstance(source_identity_capture, bytes)
                    or _sha(source_identity_capture) != event.get("source_capture_sha256")):
                raise ValueError("upstream_captured_source_identity_binding_required")
            stock = json.loads(gzip.decompress(source_identity_capture))["stock"]
            canonical = json.dumps({k: v for k, v in stock.items() if k != "record_sha256"},
                                   ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            if (_sha(canonical) != stock.get("record_sha256")
                    or any(stock.get(k) != event.get(k) for k in IDENTITY_KEYS)):
                raise ValueError("upstream_captured_source_identity_mismatch")
            if not re.search(f"{year}年年度报告", spans[evidence["report_title_proof"]]):
                raise ValueError("original_annual_report_title_pending")
            header = spans[evidence["comparison_header_proof"]]
            # One published-current column plus two before/after comparative
            # pairs in the exact supplied annual-summary template. Current
            # may already be labelled adjusted-after; it is not a before/after
            # pair being used as G's prior historical-basis operand.
            if (not re.search(f"{year}年.*?{year-1}年.*?{year-2}年", header)
                    or header.count("调整前") != 2 or header.count("调整后") != 3
                    or periods != {f"{year-1}-12-31", f"{year-2}-12-31"}
                    or evidence.get("column_role_assertion") != "published_current_plus_two_comparative_before_after_pairs"):
                raise ValueError("comparative_column_roles_pending")
        else:
            raise ValueError("scope_object_unknown")
        required_periods = {contract["report_date"], f"{current.year-1}-12-31"}
        intersections = sorted(affected & G_FIELDS)
        result["affected_G_inputs"] = [{"field": f, "report_period": p}
                                       for f in intersections for p in sorted(periods & required_periods)]
        result.update(status="affected" if result["affected_G_inputs"] else "unrelated",
                      source_pdf_sha256=pdf_sha, affected_fields=sorted(affected),
                      affected_report_periods=sorted(periods), G_required_report_periods=sorted(required_periods),
                      event_available_date=event["available_date"], source_spans_bound=True,
                      scope_limit="Human-reviewed field annotations, not automatic understanding of all PDF semantics. Only this event/object and this G contract; no whole-report certification. Other revision, policy, stock and source gates remain separate.")
    except (KeyError, ValueError, TypeError, OSError) as exc:
        result["pending_reason"] = str(exc)
    return result
