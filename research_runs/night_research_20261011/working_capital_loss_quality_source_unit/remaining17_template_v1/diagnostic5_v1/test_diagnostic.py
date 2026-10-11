"""Structural synthetic observations only; never load actual report inputs."""
from pathlib import Path
import importlib.util
import json
import sys
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location("diagnostic5_under_test", HERE / "diagnose_structure.py")
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)


def doc():
    return ("===SOURCE_PAGE:1===\n合成公司2025年度报告\n===SOURCE_PAGE:2===\n"
            "五、合并财务报表项目注释\n4、应收账款\n（1）按坏账计提方法分类披露\n"
            "单位：元\n类别\n期末余额 期初余额\n账面余额 坏账准备 账面价值\n"
            "合计 12.34 1.00 11.34\n按组合计提坏账准备\n类别\n合计 2.00\n"
            "6、合同资产\n8、存货\n合计 999.99\n六、研发支出\n")


def prior():
    return {"status": "source_only_stock_template_facts", "assets": {"AR": {
        "pending_reasons": ["unique_next_bare_parent_ordinal_pending"], "note_geometry": {"status": "pending"}}}}


def test_pending_ordinal_observations_do_not_accept_or_parse_source():
    got = d.observe(doc(), prior())
    assert got["expected_successor_ordinal"] == "六"
    assert got["first_bare_successor_after_AR_candidate"][0]["short_caption"] == "6、合同资产"
    assert got["AR_regions"][0]["authority"] == "unqualified_AR_diagnostic_window"
    assert got["AR_regions"][0]["total_row_observations"]["count"] == 2
    assert got["stock_parser_replayed"] is False


def test_existing_AR_span_is_verified_and_not_extended_to_successful_inventory():
    text = doc(); a = text.index("4、应收账款"); b = text.index("6、合同资产")
    p = prior(); p["assets"]["AR"]["note_geometry"] = {"status": "bounded_consolidated_asset_note",
        "start": a, "end": b, "range": d.templates.locator(text, a, b)}
    got = d.observe(text, p)
    assert got["AR_regions"][0]["range"]["end"] == b
    assert "999.99" not in json.dumps(got)
    p["assets"]["AR"]["note_geometry"]["range"]["span_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="geometry_span_binding"):
        d.observe(text, p)


def test_short_structure_captions_never_disclose_or_map_monetary_cells():
    got = d.observe(doc(), prior()); raw = json.dumps(got, ensure_ascii=False)
    assert "12.34" not in raw and "2.00" not in raw
    assert "<number omitted>" in raw
    def walk(value):
        if isinstance(value, dict):
            if "short_caption" in value: assert len(value["short_caption"]) <= 180
            for v in value.values(): walk(v)
        elif isinstance(value, list):
            for v in value: walk(v)
    walk(got)


def test_duplicate_exact_chapter_is_observed_not_silently_selected():
    got = d.observe(doc().replace("六、研发支出", "五、合并财务报表项目注释\n六、研发支出"), prior())
    assert got["exact_current_chapter_matches"]["count"] == 2
    assert "expected_successor_ordinal" not in got


def test_second_runtime_failure_saves_first_record_and_never_consumes_third(tmp_path):
    cases = [{"code": code} for code in ("first", "second", "third")]
    identities = [{"code": c["code"], "announcement_id": str(i), "report_date": "2025-12-31",
                   "source_record_sha256": str(i) * 64} for i, c in enumerate(cases, 1)]
    called = []
    def reader(case):
        called.append(case["code"])
        if case["code"] == "second": raise RuntimeError("synthetic reader failure")
        return {}, {}, doc()
    got = d.collect_cases(cases, identities, {c["code"]: prior() for c in cases}, reader, lambda: False)
    output = tmp_path / "diagnostic.json"; d.save_receipt(output, got)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert called == ["first", "second"]
    assert saved["status"] == "SOURCE_EXECUTION_PENDING_FIXED5_STRUCTURE_DIAGNOSTIC"
    assert [r["source_identity"]["code"] for r in saved["records"]] == ["first"]
    assert saved["execution_stop"]["error_type"] == "RuntimeError"
    assert saved["execution_stop"]["failed_index_zero_based"] == 1
    assert saved["execution_stop"]["failed_source_identity"] == identities[1]
    assert saved["resume_position"]["first_uncompleted_source_identity"] == identities[1]
    assert saved["resume_position"]["automatic_resume_allowed"] is False


def test_integer_cells_redacted_while_title_year_and_structural_ordinal_remain():
    text = "2025年度报告\n6、合同资产\n合计 12345 678\n账面余额 45678 90%\n"
    total_a = text.index("合计"); total_b = text.index("账面余额")
    total = d.observation(text, total_a, total_b, "TOTAL_locator_numbers_omitted")
    header = d.observation(text, total_b, len(text), "header_or_category_line_not_role_mapped")
    assert "12345" not in total["short_caption"] and "678" not in total["short_caption"]
    assert "45678" not in header["short_caption"] and "90" not in header["short_caption"]
    assert total["span_sha256"] == d.templates.locator(text, total_a, total_b)["span_sha256"]
    assert d.observation(text, 0, text.index("6、"), "first_page_title")["short_caption"] == "2025年度报告"
    assert d.observation(text, text.index("6、"), total_a, "bare_note_or_nested_candidate")["short_caption"] == "6、合同资产"


def test_mid_loop_deadline_returns_partial_without_reading_next_case():
    cases = [{"code": code} for code in ("first", "second")]
    identities = [{"code": c["code"]} for c in cases]; called = []
    def reader(case):
        called.append(case["code"])
        return {}, {}, doc()
    got = d.collect_cases(cases, identities, {c["code"]: prior() for c in cases}, reader, lambda: bool(called))
    assert called == ["first"]
    assert got["execution_stop"]["error_type"] == "DeadlineReached"
    assert got["execution_stop"]["failed_source_identity"] == identities[1]
    assert len(got["records"]) == 1
    assert got["status"] != "COMPLETED_FIXED5_SOURCE_STRUCTURE_DIAGNOSTIC"
