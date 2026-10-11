"""Synthetic metadata clocks/version conflicts, no real inputs or body reads."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_timeline as t


def calendar():
    return {"sessions": ["2026-04-24", "2026-04-27", "2026-05-15", "2026-05-18", "2026-10-09"],
            "coverage": [["2026-04-01", "2026-10-09"]], "planned": ["2026-10-12"]}


def case():
    return {"code": "001395", "announcement_id": "base", "report_date": "2025-12-31",
            "source_record_sha256": "s" * 64, "published_at": "2026-04-24", "original_recorded_available_date_metadata": "2026-04-25"}


def event(ann="base", pub="2026-04-24", periods=("2025-12-31",), kind="original_full"):
    return t.observation("001395", ann, pub, periods, kind, "synthetic:" + ann)


def test_weekend_clock_preserves_old_date_and_never_claims_same_day():
    r = t.availability("2026-04-25", calendar(), "2026-04-26")
    assert r["old_available_date"] == "2026-04-26" and r["available_reference_session"] == "2026-04-27"
    assert r["same_day_preopen_release_certified"] is False


def test_planned_october12_is_not_a_verified_effective_session():
    r = t.availability("2026-10-09", calendar())
    assert r["available_reference_session"] is None and r["planned_next_session"] == "2026-10-12"
    assert r["status"] == "planned_next_session_actual_pending"


def test_unknown_calendar_gap_does_not_jump_to_later_listed_session():
    c = calendar(); c["coverage"] = [["2026-04-01", "2026-04-24"], ["2026-04-27", "2026-10-09"]]
    assert t.availability("2026-04-24", c)["available_reference_session"] is None


def test_known_revised_identity_stops_original_latest_claim_at_next_reference_day():
    rows = t.collapse([event(), event("revised", "2026-05-16", kind="revised_full")])
    r = t.case_map(case(), rows, calendar())
    assert r["base_interval"]["until_reference_session_exclusive"] == "2026-05-18"
    assert r["base_interval"]["can_claim_original_is_latest_after_known_full_successor"] is False
    assert r["explicit_period_linked_candidates"][0]["AR_field_effect"] == "unknown_pending"


def test_unknown_period_event_never_becomes_a_code_wide_block_or_version_supersession():
    r = t.case_map(case(), t.collapse([event(), event("unknown", "2026-05-16", periods=(), kind="unknown")]), calendar())
    assert r["base_interval"]["until_reference_session_exclusive"] is None
    assert r["unknown_period_or_conflict_candidates"][0]["permanent_code_wide_block"] is False
    assert r["unread_revision_None_means_clean"] is False


def test_same_ID_conflicting_dates_are_pending_not_silently_chosen():
    r = t.case_map(case(), t.collapse([event(), event(pub="2026-04-27")]), calendar())
    assert r["selected_metadata"]["status"] == "same_ID_conflicting_metadata_pending"
    assert r["base_availability"]["available_reference_session"] is None


def test_same_ID_conflicting_periods_and_issuer_identity_are_pending():
    a = event(); b = event(periods=("2024-12-31",)); b["code"] = "002001"
    assert t.collapse([a, b])[0]["status"] == "same_ID_conflicting_metadata_pending"


def test_same_day_competing_revised_IDs_both_remain_candidates():
    r = t.case_map(case(), t.collapse([event(), event("new1", "2026-05-16", kind="revised_full"), event("new2", "2026-05-16", kind="revised_full")]), calendar())
    assert len(r["explicit_period_linked_candidates"]) == 2
    assert r["AR_source_ready_certified"] is False


def test_known_other_period_kept_separate_without_permanent_issuer_block():
    r = t.case_map(case(), t.collapse([event(), event("other", "2026-05-16", periods=("2023-12-31",), kind="correction_notice")]), calendar())
    assert r["other_explicit_period_metadata"][0]["permanent_code_wide_block"] is False
    assert r["base_interval"]["until_reference_session_exclusive"] is None


def test_precise_timestamp_refinement_does_not_claim_preopen_or_replace_literals():
    a = event(); b = event(pub="2026-04-24T08:27:03+08:00"); b["time_precision"] = "second"
    e = t.collapse([a, b])[0]
    assert e["status"] == "identity_time_metadata_bound_only" and len(e["published_literals"]) == 2
    assert t.case_map(case(), [e], calendar())["base_availability"]["same_day_preopen_release_certified"] is False


def test_two_conflicting_precise_timestamps_are_pending_even_on_same_date():
    a = event(pub="2026-04-24T08:00:00+08:00"); a["time_precision"] = "second"
    b = event(pub="2026-04-24T11:00:00+08:00"); b["time_precision"] = "second"
    assert t.collapse([a, b])[0]["status"] == "same_ID_conflicting_metadata_pending"


def test_default_inert_never_reads_even_nonexistent_protocol(monkeypatch, capsys):
    monkeypatch.setattr(t, "bound", lambda ref: (_ for _ in ()).throw(AssertionError("Input read")))
    t.main(["--protocol", "does-not-exist.json"])
    assert "DEFERRED_METADATA_MAP_NO_INPUT_OR_BODY_READ" in capsys.readouterr().out
