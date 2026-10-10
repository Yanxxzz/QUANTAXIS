from copy import deepcopy
import hashlib
import gzip
import json

import pytest

from panda_alpha.financial_revision_scope import assess_g_revision_scope


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
