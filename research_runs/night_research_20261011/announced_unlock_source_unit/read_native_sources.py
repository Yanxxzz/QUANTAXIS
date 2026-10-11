"""Deferred exact-three public PDFs; native text stays in memory, fields pending."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

OUT = Path(__file__).resolve().parent
PDF_PACKAGES = OUT.parents[2] / ".runtime" / "pdf-research" / "Lib" / "site-packages"
MAX_BYTES = 16 * 1024 * 1024
IDENTITIES = (("600548", "1225575337"), ("603459", "1225575321"), ("688531", "1225574632"))
TEXT_RECIPE = "exact pdfplumber extract_text(layout=False) per physical page; join @@SOURCE_PAGE:n@@ markers, no trimming"
LOCATOR = re.compile(r"上市流通日期|上市流通时间|上市流通数量|上市流通股份|解除限售|限售条件|承诺|延期|变更|总股本")


class SourcePending(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bound(ref):
    path = Path(ref["path"])
    if sha(path) != ref["sha256"]:
        raise ValueError("source_binding_changed:" + path.name)
    return path


def validate_cases(protocol, metadata):
    cases = protocol["selected_cases"]
    if tuple((r["symbol"], r["announcement_id"]) for r in cases) != IDENTITIES:
        raise ValueError("exact_frozen_three_announcement_identities_required")
    if canonical(cases) != canonical(metadata["planned_body_first3"]):
        raise ValueError("metadata_exact_selected_records_changed")
    if hashlib.sha256(canonical(cases)).hexdigest() != protocol["selected_metadata_sha256"]:
        raise ValueError("selected_metadata_digest_changed")
    for row in cases:
        url = urlsplit(row["pdf_url"])
        expected = f'/finalpage/{row["published_at"][:10]}/{row["announcement_id"]}.PDF'
        if url.scheme != "https" or url.netloc != "static.cninfo.com.cn" or url.path != expected or url.query or url.fragment:
            raise ValueError("exact_official_CNInfo_PDF_URL_required")
    return cases


def extract_native(pdf_bytes, *, deadline_reached=lambda: False):
    # Reuse the project's existing PDF runtime; no installation or new cache.
    sys.path.insert(0, str(PDF_PACKAGES))
    import pdfplumber
    if Path(pdfplumber.__file__).resolve() != (PDF_PACKAGES / "pdfplumber" / "__init__.py").resolve():
        raise SourcePending("PDF_native_extractor_import_changed")
    texts = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            if deadline_reached():
                raise SourcePending("deadline_during_native_extraction")
            texts.append(page.extract_text(layout=False) or "")
    return texts


def read_once(case, session, *, seconds_remaining, extractor=extract_native):
    """No retry/redirect. Return receipt and a memory-only native document."""
    if seconds_remaining() <= 0:
        raise SourcePending("deadline_before_PDF_request")
    raw = bytearray()
    with session.get(case["pdf_url"], timeout=min(15.0, seconds_remaining()), stream=True, allow_redirects=False) as response:
        if 300 <= response.status_code < 400:
            raise SourcePending("PDF_redirect_not_accepted")
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > MAX_BYTES:
            raise SourcePending("PDF_declared_size_above_16MiB")
        for chunk in response.iter_content(chunk_size=65536):
            if seconds_remaining() <= 0:
                raise SourcePending("deadline_during_PDF_response")
            if len(raw) + len(chunk) > MAX_BYTES:
                raise SourcePending("PDF_actual_size_above_16MiB")
            raw.extend(chunk)
        http_status = response.status_code
    pdf_bytes = bytes(raw)
    if not pdf_bytes.startswith(b"%PDF-") or b"%%EOF" not in pdf_bytes[-4096:]:
        raise SourcePending("PDF_header_or_EOF_missing")
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    texts = extractor(pdf_bytes, deadline_reached=lambda: seconds_remaining() <= 0)
    if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
        raise SourcePending("native_text_missing_on_physical_page_no_OCR_or_partial_pass")
    full_text = "".join(f"@@SOURCE_PAGE:{i}@@\n{t}\n" for i, t in enumerate(texts, 1))
    page_proof = []
    locators = []
    for i, text in enumerate(texts, 1):
        page_proof.append({"physical_page": i, "native_characters": len(text), "native_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                           "short_first_native_line": next((line for line in text.splitlines() if line.strip()), "")[:160]})
        offset = 0
        for line in text.splitlines(keepends=True):
            if LOCATOR.search(line) and len(locators) < 12:
                locators.append({"physical_page": i, "page_text_offset": offset, "snippet": line.rstrip("\r\n")[:180], "field_role_verified": False})
            offset += len(line)
    receipt = {"symbol": case["symbol"], "announcement_id": case["announcement_id"],
               "metadata_record_sha256": hashlib.sha256(canonical(case)).hexdigest(),
               "pdf_url": case["pdf_url"], "published_at": case["published_at"], "time_precision": case["time_precision"],
               "status": "FULL_NATIVE_SOURCE_READ_FIELD_REVIEW_PENDING", "HTTP_attempts": 1, "http_status": http_status,
               "PDF_bytes": len(pdf_bytes), "PDF_sha256": pdf_sha, "text_sha256": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
               "text_recipe": TEXT_RECIPE, "physical_pages": len(texts), "page_proof": page_proof, "short_locator_hints": locators,
               "fresh_original_PDF_received": True, "complete_native_text_read": True, "manual_fulltext_review_performed": False,
               "unknown_fields": ["actual_listing_date", "released_shares", "actual_circulating_shares", "share_unit", "holder_restrictions", "latest_revision_chain", "market_denominator"],
               "qualification_or_tradability_claimed": False, "PDF_or_fulltext_file_saved": False}
    document = {"metadata": case, "page_texts": texts, "full_text": full_text, "PDF_sha256": pdf_sha, "text_sha256": receipt["text_sha256"]}
    return receipt, document


def consume(cases, session, *, seconds_remaining, extractor=extract_native):
    """Stop at first problem; completed source receipts survive, no auto resume."""
    records = []
    documents = []
    failed = None
    attempted = 0
    for index, case in enumerate(cases):
        if seconds_remaining() <= 0:
            failed = {"index": index, "symbol": case["symbol"], "announcement_id": case["announcement_id"], "reason": "deadline_before_next_source", "HTTP_attempts": 0}
            break
        attempted += 1
        try:
            receipt, document = read_once(case, session, seconds_remaining=seconds_remaining, extractor=extractor)
        except Exception as error:
            failed = {"index": index, "symbol": case["symbol"], "announcement_id": case["announcement_id"], "reason": f"{type(error).__name__}:{error}"[:512], "HTTP_attempts": 1}
            break
        records.append(receipt)
        documents.append(document)
    result = {"status": "THREE_NATIVE_SOURCES_COMPLETE_FIELDS_PENDING" if len(records) == 3 else "SOURCE_PENDING_WITH_COMPLETED_SOURCE_RECORDS",
              "records": records, "failed_source": failed, "HTTP_attempts": attempted,
              "unattempted_sources": len(cases) - attempted, "new_contexts": 0,
              "official_calls": 0, "price_factor_signal_or_label_reads": False,
              "source_fields_qualified": 0, "automatic_resume": False,
              "PDF_or_fulltext_file_saved": False, "manual_review_status": "NOT_YET_PERFORMED"}
    return result, documents


def execute(protocol_path, protocol_sha, *, session=None):
    """Trusted caller can inspect returned documents in memory before disposal."""
    if sha(protocol_path) != protocol_sha:
        raise ValueError("exact_root_frozen_body_protocol_required")
    protocol = json.loads(Path(protocol_path).read_text(encoding="utf-8"))
    deadline = datetime.fromisoformat(protocol["deadline_utc"])
    seconds_remaining = lambda: (deadline - datetime.now(timezone.utc)).total_seconds()
    if seconds_remaining() <= 0:
        raise ValueError("human_deadline_reached")
    output = Path(protocol["output_path"])
    if output.exists():
        raise ValueError("body_source_receipt_exists_no_implicit_retry")
    with sqlite3.connect(Path(protocol["registry_path"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM research_events WHERE kind IN ('source_repair','execution_repair') AND event_id=? AND subject=?", (protocol["event_prefix"] + protocol_sha, protocol["registration_subject"])).fetchone()
        if not row or json.loads(row[0]).get("new_contexts") != 0:
            raise ValueError("root_zero_context_source_freeze_required")
    for ref in protocol["bindings"].values():
        bound(ref)
    if bound(protocol["bindings"]["runtime"]).resolve() != Path(__file__).resolve():
        raise ValueError("runtime_path_changed")
    metadata = json.loads(bound(protocol["bindings"]["metadata"]).read_text(encoding="utf-8"))
    cases = validate_cases(protocol, metadata)
    if session is None:
        import requests
        with requests.Session() as owned_session:
            result, documents = consume(cases, owned_session, seconds_remaining=seconds_remaining)
    else:
        result, documents = consume(cases, session, seconds_remaining=seconds_remaining)
    result.update(protocol_sha256=protocol_sha, metadata_sha256=protocol["bindings"]["metadata"]["sha256"],
                  calendar_reference_sha256=protocol["bindings"]["calendar"]["sha256"],
                  availability_status="publication_precision_preserved_next_verified_session_requires_explicit_review",
                  original_files_registry_masks_state_unchanged=True, completed_at_utc=datetime.now(timezone.utc).isoformat())
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result, documents


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--protocol", type=Path, default=OUT / "body_protocol.json")
    cli.add_argument("--protocol-sha")
    cli.add_argument("--execute", action="store_true")
    args = cli.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_NO_HTTP_PDF_TEXT_PRICE_FACTOR_OR_LABEL_READ"}))
        return
    if not args.protocol_sha:
        raise ValueError("exact_protocol_SHA_required")
    result, documents = execute(args.protocol, args.protocol_sha)
    print(json.dumps({"status": result["status"], "records": len(result["records"]), "HTTP_attempts": result["HTTP_attempts"], "output_sha256": sha(json.loads(args.protocol.read_text(encoding="utf-8"))["output_path"])}))
    # No PDF or complete source text is written or printed by this CLI.
    del documents


if __name__ == "__main__":
    main()
