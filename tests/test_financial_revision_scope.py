from copy import deepcopy
import hashlib
import gzip
import json

import pytest

from panda_alpha.financial_revision_scope import (
    assess_g_revision_scope, assess_g_revision_scope_from_retained_text,
)


def fixture(*, period=2021, field="operating_revenue", asof="2026-08-26", G_year=2025):
    body = f"证券代码：000519\n关于{period}年年度报告的更正公告\n营业收入\n除上述更正内容外，其他内容不变。"
    raw = b"bound-original-PDF-fixture"
    event = {"code": "000519", "announcement_id": "1212978279", "edition": "CORRECTION_NOTICE",
             "report_date": f"{period}-12-31", "published_at": "2022-04-20", "available_date": "2022-04-21",
             "pdf_sha256": hashlib.sha256(raw).hexdigest()}
    evidence = {"event_identity": deepcopy(event), "scope_object": "complete_correction_notice",
                "source": {"pdf_sha256": hashlib.sha256(raw).hexdigest(), "read_pages": [1], "physical_page_count": 1},
                "spans": [{"name": "body", "page": 1, "start": 0, "end": len(body), "sha256": hashlib.sha256(body.encode()).hexdigest()}],
                "affected_fields": [field], "field_proofs": {field: "body"},
                "affected_report_periods": [f"{period}-12-31"], "period_proofs": {f"{period}-12-31": "body"},
                "unchanged_other_content_proof": "body"}
    contract = {"code": "000519", "report_date": f"{G_year}-12-31", "asof": asof}
    def extract(pdf, pages):
        assert pdf == raw and pages == [1]
        return {"page_count": 1, "pages": {1: body}}
    return event, contract, evidence, raw, extract


def assess(args):
    return assess_g_revision_scope(*args[:3], original_pdf=args[3], extract_pages=args[4],
                                   source_identity_capture=args[5] if len(args) > 5 else None)


def test_explicit_bound_body_period_outside_G_inputs_is_unrelated_without_source_unlock():
    result = assess(fixture())
    assert result["status"] == "unrelated" and result["affected_G_inputs"] == []
    assert not result["source_mask_auto_unlock"] and not result["full_pit_certified"]


def test_bound_relevant_revenue_revision_is_affected():
    result = assess(fixture(period=2024))
    assert result["status"] == "affected"
    assert result["affected_G_inputs"] == [{"field": "operating_revenue", "report_period": "2024-12-31"}]


@pytest.mark.parametrize("bad", ["PDF", "event", "span", "fields", "period", "future", "availability", "pages", "metadata_only"])
def test_unknown_or_unbound_scope_cannot_clear_G_gate(bad):
    args = list(fixture())
    if bad == "PDF":
        args[3] += b"changed"
    elif bad == "event":
        args[0]["announcement_id"] = "another"
    elif bad == "span":
        args[2]["spans"][0]["sha256"] = "0" * 64
    elif bad == "fields":
        args[2]["affected_fields"] = ["unknown"]
    elif bad == "period":
        args[2]["affected_report_periods"] = ["2020-12-31"]
    elif bad == "future":
        args[1]["asof"] = "2022-04-20"
    elif bad == "availability":
        args[0]["available_date"] = args[2]["event_identity"]["available_date"] = "2022-04-20"
    elif bad == "pages":
        args[2]["source"]["physical_page_count"] = 2
    else:
        args[2]["spans"] = []
    assert assess(args)["status"] == "pending"


def test_restated_old_comparative_pairs_do_not_reject_already_published_current_column():
    args = list(fixture())
    text = "2023年年度报告\n2023年 2022年 2021年 调整前 调整后 调整后 调整前 调整后 营业收入"
    args[0].update(edition="ORIGINAL_FULL_REPORT", report_date="2023-12-31")
    args[1]["report_date"] = "2024-12-31"
    args[2].update(event_identity=deepcopy(args[0]), scope_object="named_comparative_before_after_pairs",
                   affected_report_periods=["2022-12-31", "2021-12-31"],
                   comparison_header_proof="body", report_title_proof="body",
                   column_role_assertion="published_current_plus_two_comparative_before_after_pairs")
    stock = deepcopy(args[0])
    stock["record_sha256"] = hashlib.sha256(json.dumps(stock,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    capture = gzip.compress(json.dumps({"stock":stock},ensure_ascii=False).encode())
    args[0]["source_capture_sha256"] = hashlib.sha256(capture).hexdigest()
    args[2]["event_identity"] = deepcopy(args[0])
    args.append(capture)
    args[2]["spans"][0].update(end=len(text), sha256=hashlib.sha256(text.encode()).hexdigest())
    args[4] = lambda pdf, pages: {"page_count": 1, "pages": {1: text}}
    assert assess(args)["status"] == "unrelated"
    args[2]["affected_report_periods"] = ["2023-12-31"]
    assert assess(args)["status"] == "pending"


def test_restatement_checkbox_alone_never_establishes_comparative_field_roles():
    args = list(fixture())
    text = "是否需追溯调整以前年度 □否 √是 营业收入"
    args[2].update(scope_object="named_comparative_before_after_pairs", comparison_header_proof="body")
    args[2]["spans"][0].update(end=len(text), sha256=hashlib.sha256(text.encode()).hexdigest())
    args[4] = lambda pdf, pages: {"page_count": 1, "pages": {1: text}}
    assert assess(args)["status"] == "pending"


@pytest.mark.parametrize("extra", ["另调整总资产", "追溯调整以前年度", "调整2024年第三季度数据"])
def test_notice_cannot_hide_an_extra_field_or_cross_period_scope(extra):
    args = list(fixture())
    text = f"证券代码：000519\n关于2021年年度报告的更正公告\n营业收入\n{extra}\n其他内容不变"
    args[2]["spans"][0].update(end=len(text), sha256=hashlib.sha256(text.encode()).hexdigest())
    args[4] = lambda pdf, pages: {"page_count": 1, "pages": {1: text}}
    assert assess(args)["status"] == "pending"


def test_metadata_period_without_exhaustive_source_statement_stays_pending():
    args = list(fixture())
    text = "关于2021年年度报告的更正公告\n营业收入\n还有其它未说明更正"
    args[2]["spans"][0].update(end=len(text), sha256=hashlib.sha256(text.encode()).hexdigest())
    args[4] = lambda pdf, pages: {"page_count": 1, "pages": {1: text}}
    assert assess(args)["status"] == "pending"


@pytest.mark.parametrize("label", ["证券代码：000525", "投资股票代码：000519"])
def test_metadata_cannot_override_wrong_or_non_issuer_stock_code_in_original_notice(label):
    args = list(fixture())
    text = f"{label}\n关于2021年年度报告的更正公告\n营业收入\n其他内容不变"
    args[2]["spans"][0].update(end=len(text), sha256=hashlib.sha256(text.encode()).hexdigest())
    args[4] = lambda pdf,pages: {"page_count":1,"pages":{1:text}}
    assert assess(args)["pending_reason"] == "original_notice_issuer_identity_pending"



def retained_fixture(*, with_capture=False, native_body=None, count=1):
    args = list(fixture())
    body = "证券代码：000519\n关于2021年年度报告的更正公告\n营业收入\n除上述更正内容外，其他内容不变。" if native_body is None else native_body
    args[2]["spans"][0].update(end=len(body), sha256=hashlib.sha256(body.encode()).hexdigest())
    pages = [body] * count
    text = "".join(f"\n===SOURCE_PAGE:{i+1}===\n{page}" for i, page in enumerate(pages))
    text_raw = gzip.compress(text.encode(), mtime=0)
    row = deepcopy(args[0])
    row.update(text_sha256=hashlib.sha256(text_raw).hexdigest(), text_path="archive.txt.gz",
               pages=count, native_text_characters=sum(map(len,pages)), pages_without_native_text=sum(not p.strip() for p in pages),
               status="SOURCE_BYTES_AND_NATIVE_TEXT_READY_NOT_FIELDS_OR_PIT_CERTIFIED",
               http_status=200, EOF_tail_verified=True, bytes=len(args[3]), transport_actual_bytes=len(args[3]),
               content_type="application/pdf")
    capture = None
    if with_capture:
        stock = deepcopy(args[0])
        stock["provenance"]={"pdf_sha256":stock["pdf_sha256"],"text_sha256":row["text_sha256"],"text_path":"archive.txt.gz","text_storage_format":"native_text_gzip"}
        stock["record_sha256"]=hashlib.sha256(json.dumps(stock,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        capture=gzip.compress(json.dumps({"stock":stock},ensure_ascii=False).encode(),mtime=0)
        args[0]["source_capture_sha256"]=hashlib.sha256(capture).hexdigest()
    index_raw=json.dumps({"documents":[row]},ensure_ascii=False).encode()
    return args, {"source_index_bytes":index_raw,"source_index_sha256":hashlib.sha256(index_raw).hexdigest(),"text_binding":{"text_gzip":text_raw},"source_identity_capture":capture}


def retained_assess(args, binding):
    return assess_g_revision_scope_from_retained_text(*args[:3], **binding)


@pytest.mark.parametrize("with_capture", [False, True])
def test_retained_full_notice_preserves_scope_without_claiming_fresh_PDF(with_capture):
    args,binding=retained_fixture(with_capture=with_capture)
    result=retained_assess(args,binding)
    assert result["status"]=="unrelated"
    assert result["verification_level"]=="retained_verified_text"
    assert result["fresh_original_PDF"] is False and result["scoped_only"] is True
    assert result["retained_source_bound"] is True
    assert not result["source_mask_auto_unlock"] and not result["full_pit_certified"]
    assert assess(args)["status"]=="unrelated"
    args[3]+=b"changed"
    assert assess(args)["pending_reason"]=="original_PDF_bytes_or_SHA_mismatch"


@pytest.mark.parametrize("bad", ["text", "index", "issuer", "publication", "version", "duplicate_index", "pages", "empty_page", "native_chars", "incomplete", "pdf_hash", "unsupported_json", "capture", "span_projection"])
def test_retained_archive_binding_failures_remain_pending(bad):
    args,binding=retained_fixture(with_capture=(bad=="capture"))
    packet=json.loads(binding["source_index_bytes"])
    row=packet["documents"][0]
    if bad=="text": binding["text_binding"]["text_gzip"]+=b"changed"
    elif bad=="index": binding["source_index_bytes"]+=b" "
    elif bad=="issuer": row["code"]="000525"
    elif bad=="publication": row["published_at"]="2022-04-19"
    elif bad=="version": row["edition"]="REVISED_FULL_REPORT"
    elif bad=="duplicate_index": packet["documents"].append(deepcopy(row))
    elif bad=="pages": row["pages"]=2
    elif bad=="empty_page": row["pages_without_native_text"]=1
    elif bad=="native_chars": row["native_text_characters"]+=1
    elif bad=="incomplete": row["EOF_tail_verified"]=False
    elif bad=="pdf_hash": row["pdf_sha256"]="0"*64
    elif bad=="unsupported_json": binding["text_binding"]={"text_json":b"{}"}
    elif bad=="capture": binding["source_identity_capture"]+=b"changed"
    elif bad=="span_projection":
        raw=gzip.decompress(binding["text_binding"]["text_gzip"]).decode().replace("===SOURCE_PAGE:1===\n", "===SOURCE_PAGE:1===\n\n")
        binding["text_binding"]["text_gzip"]=gzip.compress(raw.encode(),mtime=0)
        row["text_sha256"]=hashlib.sha256(binding["text_binding"]["text_gzip"]).hexdigest()
        row["native_text_characters"]+=1
    if bad not in {"index","text","unsupported_json","capture"}:
        binding["source_index_bytes"]=json.dumps(packet,ensure_ascii=False).encode()
        binding["source_index_sha256"]=hashlib.sha256(binding["source_index_bytes"]).hexdigest()
    result=retained_assess(args,binding)
    assert result["status"]=="pending"
    if bad=="span_projection":
        assert result["pending_reason"]=="source_span_SHA_mismatch" and result["retained_source_bound"]
    elif bad=="index":
        assert result["verification_level"]=="unverified_retained_text_pending"


def test_retained_missing_or_out_of_order_native_physical_page_cannot_self_certify():
    args,binding=retained_fixture(count=2)
    raw=gzip.decompress(binding["text_binding"]["text_gzip"]).decode().replace("SOURCE_PAGE:2", "SOURCE_PAGE:3")
    binding["text_binding"]["text_gzip"]=gzip.compress(raw.encode(),mtime=0)
    packet=json.loads(binding["source_index_bytes"])
    packet["documents"][0]["text_sha256"]=hashlib.sha256(binding["text_binding"]["text_gzip"]).hexdigest()
    binding["source_index_bytes"]=json.dumps(packet,ensure_ascii=False).encode()
    binding["source_index_sha256"]=hashlib.sha256(binding["source_index_bytes"]).hexdigest()
    args[2]["source"]["physical_page_count"]=2
    assert retained_assess(args,binding)["pending_reason"]=="retained_physical_page_projection_pending"


def test_retained_native_blank_page_rejected_even_if_metadata_claims_nonempty():
    args,binding=retained_fixture(native_body="   ")
    packet=json.loads(binding["source_index_bytes"])
    packet["documents"][0]["pages_without_native_text"]=0
    binding["source_index_bytes"]=json.dumps(packet,ensure_ascii=False).encode()
    binding["source_index_sha256"]=hashlib.sha256(binding["source_index_bytes"]).hexdigest()
    assert retained_assess(args,binding)["pending_reason"]=="retained_native_page_completeness_pending"


@pytest.mark.parametrize("extra", ["调整2024年第三季度数据", "另调整总资产"])
def test_retained_entry_does_not_expand_existing_economic_scope(extra):
    args,binding=retained_fixture(native_body=f"证券代码：000519\n关于2021年年度报告的更正公告\n营业收入\n{extra}\n其他内容不变")
    assert retained_assess(args,binding)["pending_reason"]==("notice_cross_period_roles_need_separate_explicit_proof" if "第三季度" in extra else "notice_contains_unmapped_G_field")



def test_retained_named_comparatives_still_require_bound_capture_and_original_roles():
    text="2023年年度报告\n2023年 2022年 2021年 调整前 调整后 调整后 调整前 调整后 营业收入"
    args,binding=retained_fixture(with_capture=True,native_body=text)
    args[0].update(edition="ORIGINAL_FULL_REPORT",report_date="2023-12-31")
    args[1]["report_date"]="2024-12-31"
    args[2].update(event_identity=deepcopy(args[0]),scope_object="named_comparative_before_after_pairs",
                   affected_report_periods=["2022-12-31","2021-12-31"],comparison_header_proof="body",report_title_proof="body",
                   column_role_assertion="published_current_plus_two_comparative_before_after_pairs")
    stock=json.loads(gzip.decompress(binding["source_identity_capture"]))["stock"]
    stock.update(edition=args[0]["edition"],report_date=args[0]["report_date"])
    stock["record_sha256"]=hashlib.sha256(json.dumps({k:v for k,v in stock.items() if k!="record_sha256"},ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    binding["source_identity_capture"]=gzip.compress(json.dumps({"stock":stock},ensure_ascii=False).encode(),mtime=0)
    args[0]["source_capture_sha256"]=hashlib.sha256(binding["source_identity_capture"]).hexdigest()
    packet=json.loads(binding["source_index_bytes"])
    packet["documents"][0].update(edition=args[0]["edition"],report_date=args[0]["report_date"])
    binding["source_index_bytes"]=json.dumps(packet,ensure_ascii=False).encode()
    binding["source_index_sha256"]=hashlib.sha256(binding["source_index_bytes"]).hexdigest()
    assert retained_assess(args,binding)["status"]=="unrelated"
    binding["source_identity_capture"]=None
    assert retained_assess(args,binding)["pending_reason"]=="upstream_captured_source_identity_binding_required"



def test_retained_truncated_gzip_with_matching_archive_hash_returns_pending():
    args,binding=retained_fixture()
    raw=binding["text_binding"]["text_gzip"][:-5]
    binding["text_binding"]["text_gzip"]=raw
    packet=json.loads(binding["source_index_bytes"])
    packet["documents"][0]["text_sha256"]=hashlib.sha256(raw).hexdigest()
    binding["source_index_bytes"]=json.dumps(packet,ensure_ascii=False).encode()
    binding["source_index_sha256"]=hashlib.sha256(binding["source_index_bytes"]).hexdigest()
    result=retained_assess(args,binding)
    assert result["status"]=="pending" and not result["retained_source_bound"]
    assert result["verification_level"]=="unverified_retained_text_pending"
    assert "end-of-stream" in result["pending_reason"]
