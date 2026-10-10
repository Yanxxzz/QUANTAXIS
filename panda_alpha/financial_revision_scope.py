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
    def validate_source(event, source):
        pdf_sha = source["pdf_sha256"]
        if (not isinstance(original_pdf, bytes) or not re.fullmatch(r"[0-9a-f]{64}", str(pdf_sha))
                or pdf_sha != event.get("pdf_sha256") or _sha(original_pdf) != pdf_sha):
            raise ValueError("original_PDF_bytes_or_SHA_mismatch")
    def load_source(event, source, pages):
        return extract_pages(original_pdf, pages)

    return _assess_g_revision_scope(event, contract, evidence, load_source=load_source,
                                    validate_source=validate_source,
                                    source_identity_capture=source_identity_capture)


def assess_g_revision_scope_from_retained_text(event: dict, contract: dict, evidence: dict, *,
                                               source_index_bytes: bytes,
                                               source_index_sha256: str,
                                               text_binding: dict,
                                               source_identity_capture: bytes | None = None) -> dict:
    """Use one completely retained collector source, never claim a fresh PDF.

    The archived source-index bytes bind the unique event, original PDF hash,
    completed transfer and complete native page extraction. ``text_binding``
    contains only ``text_gzip`` bytes in the collector's streamed page format.
    Existing capture bytes, when supplied, must also bind stock/provenance and
    text through the shared capture validator. Other archive projections remain
    pending. Caller annotations and their page offsets/hashes are never repaired.
    The source evidence level changes; economic scope rules stay identical.
    """
    binding_receipt = {}

    def load_source(event, source, pages):
        from panda_alpha.income_basis import _bound_capture, _bound_text, _unique_page_container_keys

        if (not isinstance(source_index_bytes, bytes)
                or not re.fullmatch(r"[0-9a-f]{64}", str(source_index_sha256))
                or _sha(source_index_bytes) != source_index_sha256):
            raise ValueError("retained_source_index_bytes_or_SHA_mismatch")
        index = json.loads(source_index_bytes.decode("utf-8"), object_pairs_hook=_unique_page_container_keys)
        documents = index.get("documents") if isinstance(index, dict) else None
        if not isinstance(documents, list) or any(not isinstance(row, dict) for row in documents):
            raise ValueError("retained_source_index_format_unsupported_pending")
        matches = [row for row in documents if str(row.get("announcement_id")) == event["announcement_id"]]
        if len(matches) != 1:
            raise ValueError("retained_source_index_unique_vintage_pending")
        row = matches[0]
        if any(row.get(k) != event.get(k) for k in IDENTITY_KEYS if k != "available_date"):
            raise ValueError("retained_source_index_event_identity_mismatch")
        if row.get("symbol", event["code"]) != event["code"]:
            raise ValueError("retained_source_index_issuer_alias_mismatch")
        # Legacy collectors retain exact publication but no available-date.
        # Their only supported availability projection is publication + 1 day;
        # this binds the legacy calendar-day rule, not verified trading-day
        # availability or independent calendar certification.
        available = row.get("available_date", (date.fromisoformat(str(row["published_at"])[:10]) + timedelta(days=1)).isoformat())
        if available != event["available_date"]:
            raise ValueError("retained_source_index_availability_mismatch")
        if (row.get("status") != "SOURCE_BYTES_AND_NATIVE_TEXT_READY_NOT_FIELDS_OR_PIT_CERTIFIED"
                or row.get("http_status") != 200 or row.get("EOF_tail_verified") is not True
                or not str(row.get("content_type", "")).lower().startswith("application/pdf")
                or type(row.get("bytes")) is not int or row["bytes"] <= 0
                or row.get("transport_actual_bytes") != row["bytes"]):
            raise ValueError("retained_original_collection_completeness_pending")
        if (not re.fullmatch(r"[0-9a-f]{64}", str(source.get("pdf_sha256")))
                or source["pdf_sha256"] != row["pdf_sha256"]):
            raise ValueError("retained_original_PDF_identity_mismatch")
        if not isinstance(text_binding, dict) or set(text_binding) != {"text_gzip"}:
            raise ValueError("retained_text_projection_unsupported_pending")
        prov = {"pdf_sha256": row["pdf_sha256"], "text_sha256": row.get("text_sha256"),
                "text_storage_format": "native_text_gzip"}
        if source_identity_capture is not None:
            capture_sha = event.get("source_capture_sha256")
            document = json.loads(gzip.decompress(source_identity_capture))
            stock, decoded = _bound_capture(document, {**text_binding, "capture_gzip": source_identity_capture,
                                                       "capture_sha256": capture_sha})
            if (any(stock.get(k) != event.get(k) for k in IDENTITY_KEYS)
                    or stock["provenance"].get("text_sha256") != row.get("text_sha256")
                    or stock["provenance"].get("text_path") != row.get("text_path")):
                raise ValueError("retained_capture_index_identity_mismatch")
        else:
            decoded = _bound_text(prov, text_binding)
        # Collector recipe: '\n===SOURCE_PAGE:N===\n' + native page bytes.
        # Removing only the exact delimiters preserves every page-body offset.
        parts = re.split(r"\n===SOURCE_PAGE:([1-9][0-9]*)===\n", decoded)
        count = row.get("pages")
        if (type(count) is not int or count < 1 or not parts or parts[0] != ""
                or len(parts) != 1 + 2 * count
                or [int(parts[i]) for i in range(1, len(parts), 2)] != list(range(1, count + 1))):
            raise ValueError("retained_physical_page_projection_pending")
        full_pages = {i + 1: parts[2 + 2 * i] for i in range(count)}
        if (row.get("pages_without_native_text") != 0
                or any(not page.strip() or "===SOURCE_PAGE:" in page for page in full_pages.values())
                or type(row.get("native_text_characters")) is not int
                or sum(map(len, full_pages.values())) != row["native_text_characters"]):
            raise ValueError("retained_native_page_completeness_pending")
        if count != source["physical_page_count"] or not set(pages).issubset(full_pages):
            raise ValueError("source_physical_pages_mismatch")
        binding_receipt.update(retained_source_bound=True, source_index_sha256=source_index_sha256,
                               source_text_sha256=row["text_sha256"], archived_physical_page_count=count,
                               availability_binding="index_available_date" if "available_date" in row else "published_date_plus_one_day")
        if source_identity_capture is not None:
            binding_receipt["source_capture_sha256"] = event["source_capture_sha256"]
        return {"page_count": count, "pages": {p: full_pages[p] for p in pages}}

    result = _assess_g_revision_scope(event, contract, evidence, load_source=load_source,
                                      source_identity_capture=source_identity_capture)
    result.update(verification_mode="retained_verified_text", fresh_original_PDF=False,
                  scoped_only=True, retained_source_bound=False,
                  verification_level="retained_verified_text" if binding_receipt else "unverified_retained_text_pending")
    result.update(binding_receipt)
    return result


def _assess_g_revision_scope(event, contract, evidence, *, load_source, source_identity_capture, validate_source=None):
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
        if validate_source is not None:
            validate_source(event, source)
        scope = evidence["scope_object"]
        pages = source["read_pages"]
        if (not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages)
                or len(set(pages)) != len(pages)):
            raise ValueError("source_page_selection_pending")
        native = load_source(event, source, pages)
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
                      scope_limit="Explicitly reviewed field annotations, not automatic understanding of all PDF semantics. Only this event/object and this G contract; no whole-report certification. Other revision, policy, stock and source gates remain separate.")
    except (KeyError, ValueError, TypeError, OSError, UnicodeError, EOFError) as exc:
        result["pending_reason"] = str(exc)
    return result
