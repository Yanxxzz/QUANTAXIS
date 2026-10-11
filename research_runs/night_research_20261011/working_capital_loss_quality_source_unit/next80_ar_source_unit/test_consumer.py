"""Metadata and execution controls only; original source bodies never loaded."""
from pathlib import Path
import sys
import hashlib
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import consume_sources as c


def metadata_cases():
    return [{"code": str(900000+i), "announcement_id": str(1000+i), "report_date": "2025-12-31", "source_record_sha256": str(i),
             "known_revision_or_later_report_metadata": {"status": "unknown_pending"}} for i in range(80)]


def inputs(cases=None):
    cases = metadata_cases() if cases is None else cases
    parent_ref = {"path": "synthetic", "sha256": c.PARENT_SHA}
    parent = {"selected_cases": [{"code": "000157"}], "bindings": {"source_indices": {}}}
    ids = [{k: row[k] for k in c.KEY} for row in cases]; digest = hashlib.sha256(c.canonical(ids)).hexdigest()
    plan = {"selected_cases": cases, "selected_identity_sha256": digest, "parent_source_plan_sha256": c.PARENT_SHA,
            "bindings": {"parent_source_plan": parent_ref, "source_indices": {}}}
    p = {"selected_identity_rows": ids, "selected_identity_sha256": digest, "bindings": {"parent_source_plan": parent_ref}}
    return plan, parent, p


def test_exact_80_identity_sorted_excludes_old_and_posthoc_not_policy_flags():
    plan, parent, p = inputs()
    plan["selected_cases"][0]["scope_policy_status"] = "true_change_pending"
    cases, _ = c.validated_cases(plan, parent, p)
    assert len(cases) == 80 and cases[0]["scope_policy_status"] == "true_change_pending"
    altered = metadata_cases(); altered[0]["code"] = "000157"
    with pytest.raises(ValueError, match="exclusion"):
        c.validated_cases(*inputs(altered))
    altered[0]["code"] = "300571"
    with pytest.raises(ValueError, match="exclusion"):
        c.validated_cases(*inputs(altered))


def test_unknown_format_retained_and_runtime_stops_without_later_body(monkeypatch):
    cases = metadata_cases()[:3]; called = []; checkpoints = []
    def reader(case):
        called.append(case["code"])
        if len(called) == 2: raise RuntimeError("synthetic byte reader error")
        return {"stock": {"comparison_change_barriers": ["policy_change_unknown"]}}, {}, "synthetic"
    def parser(*args, **kwargs): raise ValueError("unsupported_AR_header_pending")
    monkeypatch.setattr(c, "consume_pending_ar", parser)
    records, stop = c.consume_cases(cases, reader, lambda: False, lambda r: checkpoints.append(list(r)))
    assert len(records) == 1 and called == [cases[0]["code"], cases[1]["code"]]
    assert len(checkpoints) == 1
    assert records[0]["original_metadata_flags"] == ["policy_change_unknown"]
    assert records[0]["source_qualified"] is False
    assert stop["error_type"] == "RuntimeError" and stop["source_identity"]["code"] == cases[1]["code"]


def test_midnight_stop_does_not_read_next_case(monkeypatch):
    cases = metadata_cases()[:2]; called = []
    def reader(case): called.append(case["code"]); return {"stock": {}}, {}, "synthetic"
    monkeypatch.setattr(c, "consume_pending_ar", lambda *a, **k: {"AR": {"stock": []}})
    records, stop = c.consume_cases(cases, reader, lambda: bool(called), lambda _: None)
    assert len(records) == 1 and called == [cases[0]["code"]] and stop["error_type"] == "DeadlineReached"


def test_source_only_parser_called_with_no_old_report_or_INV_and_independent_flags(monkeypatch):
    cases = metadata_cases()[:1]; called = []
    def parser(text, year, report): called.append((text, year, report)); return {"AR": {"stock": []}, "financial_qualification": False}
    monkeypatch.setattr(c, "consume_pending_ar", parser)
    records, stop = c.consume_cases(cases, lambda _: ({"stock": {"comparison_change_barriers": []}}, {}, "synthetic"), lambda: False, lambda _: None)
    assert called == [("synthetic", 2025, None)] and stop is None
    assert records[0]["empty_metadata_flags_mean_clean"] is False
    assert records[0]["policy_estimate_consolidation_restatement_version_and_AR_qualification"] == "unreviewed_pending"
