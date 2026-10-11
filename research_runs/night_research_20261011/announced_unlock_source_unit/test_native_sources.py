"""Synthetic sessions/PDF bytes only; no real HTTP or source documents."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import read_native_sources as reader


def case(code="600548", annid="1225575337"):
    return {"symbol": code, "announcement_id": annid, "published_at": "2026-09-22", "time_precision": "date", "pdf_url": f"https://static.cninfo.com.cn/finalpage/2026-09-22/{annid}.PDF"}


class Response:
    status_code = 200
    headers = {}
    def __init__(self, chunks=None, status=200):
        self.chunks = chunks or [b"%PDF-1.7 synthetic\n%%EOF"]
        self.status_code = status
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, chunk_size): return iter(self.chunks)


class Session:
    def __init__(self, responses): self.responses = list(responses); self.calls = []
    def get(self, url, **kwargs):
        assert kwargs == {"timeout": 15.0, "stream": True, "allow_redirects": False}
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return response


def native(raw, *, deadline_reached): return ["Synthetic source text\n上市流通日期：待核", "Second physical native page"]


def test_exact_cases_metadata_and_official_URL_bound():
    cases = [case(*identity) for identity in reader.IDENTITIES]
    p = {"selected_cases": cases, "selected_metadata_sha256": sha256(reader.canonical(cases)).hexdigest()}
    assert reader.validate_cases(p, {"planned_body_first3": cases}) == cases
    changed = [dict(row) for row in cases]; changed[0]["pdf_url"] = "https://example.test/1225575337.PDF"
    with pytest.raises(ValueError, match="metadata_exact_selected_records_changed"):
        reader.validate_cases(p, {"planned_body_first3": changed})


def test_complete_page_hashes_memory_only_and_fields_unknown():
    session = Session([Response()])
    receipt, document = reader.read_once(case(), session, seconds_remaining=lambda: 60, extractor=native)
    assert len(session.calls) == 1 and receipt["physical_pages"] == 2
    assert receipt["text_sha256"] == sha256(document["full_text"].encode()).hexdigest()
    assert receipt["page_proof"][1]["physical_page"] == 2
    assert not receipt["manual_fulltext_review_performed"] and not receipt["PDF_or_fulltext_file_saved"]
    assert "actual_circulating_shares" in receipt["unknown_fields"]


def test_redirect_no_follow_and_failure_preserves_first_not_third():
    session = Session([Response(), Response(status=307), Response()])
    result, documents = reader.consume([case(*identity) for identity in reader.IDENTITIES], session, seconds_remaining=lambda: 60, extractor=native)
    assert len(session.calls) == 2 and len(result["records"]) == 1 and len(documents) == 1
    assert result["unattempted_sources"] == 1 and "PDF_redirect_not_accepted" in result["failed_source"]["reason"]
    assert not result["automatic_resume"]


def test_size_cap_checked_streaming_without_parser(monkeypatch):
    monkeypatch.setattr(reader, "MAX_BYTES", 8)
    session = Session([Response([b"%PDF-1.7", b"larger"] )])
    with pytest.raises(reader.SourcePending, match="actual_size_above"):
        reader.read_once(case(), session, seconds_remaining=lambda: 60, extractor=lambda *_args, **_kwargs: pytest.fail("oversize parser called"))
    assert len(session.calls) == 1


def test_missing_native_page_never_complete_or_OCR():
    with pytest.raises(reader.SourcePending, match="native_text_missing"):
        reader.read_once(case(), Session([Response()]), seconds_remaining=lambda: 60, extractor=lambda *_args, **_kwargs: ["page1", ""])


def test_transport_failure_no_retry_and_deadline_no_next_GET():
    session = Session([ConnectionError("synthetic"), Response()])
    result, docs = reader.consume([case(*identity) for identity in reader.IDENTITIES], session, seconds_remaining=lambda: 60, extractor=native)
    assert len(session.calls) == 1 and not result["records"] and result["unattempted_sources"] == 2
    empty = Session([])
    result, docs = reader.consume([case(*identity) for identity in reader.IDENTITIES], empty, seconds_remaining=lambda: 0, extractor=native)
    assert result["HTTP_attempts"] == 0 and result["unattempted_sources"] == 3


def test_real_pdfplumber_reads_two_synthetic_physical_pages():
    # Tiny valid two-page PDF generated in memory without new dependencies.
    streams = [b"BT /F1 10 Tf 72 720 Td (Synthetic first physical page) Tj ET", b"BT /F1 10 Tf 72 720 Td (Synthetic second physical page) Tj ET"]
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 6 0 R >> >> /Contents 4 0 R >>",
               b"<< /Length " + str(len(streams[0])).encode() + b" >>\nstream\n" + streams[0] + b"\nendstream",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 6 0 R >> >> /Contents 7 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(streams[1])).encode() + b" >>\nstream\n" + streams[1] + b"\nendstream"]
    raw = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(raw)); raw.extend(str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(raw); raw.extend(b"xref\n0 8\n0000000000 65535 f \n")
    for offset in offsets[1:]: raw.extend(f"{offset:010d} 00000 n \n".encode())
    raw.extend(b"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    session = Session([Response([bytes(raw)])])
    receipt, document = reader.read_once(case(), session, seconds_remaining=lambda: 60)
    assert receipt["physical_pages"] == 2
    assert document["page_texts"] == ["Synthetic first physical page", "Synthetic second physical page"]
