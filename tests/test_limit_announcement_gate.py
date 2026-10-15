"""Synthetic metadata/calendars only; no real source, prices or labels."""
from datetime import date, timedelta

from panda_alpha.limit_announcement_gate import evaluate_announcement_gate


def row(ident="1", day="2025-01-03", title="关于公司收到中国证监会立案告知书的公告", role="ISSUER_EXPLICIT_RECEIPT_TITLE_ONLY", **extra):
    return dict(announcement_id=ident, code="300086", published_date=day,
                title=title, subject_role=role, **extra)


def refs():
    first, last = date(2024, 10, 1), date(2025, 1, 6)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)
            if (first + timedelta(days=i)).weekday() < 5
            and (first + timedelta(days=i)).isoformat() != "2025-01-01"]


def evaluate(rows, **changes):
    args = dict(code="300086", event_date="2025-01-06", reference_dates=refs(),
                source_complete=True, positive_route=True)
    args.update(changes)
    return evaluate_announcement_gate(rows, **args)


def test_exact_90_day_boundary_and_same_day_are_not_lookahead():
    assert evaluate([row(day="2024-10-08")])["flag"] == 1
    assert evaluate([row(day="2024-10-07")])["flag"] == 0
    assert evaluate([row(day="2025-01-06")])["flag"] == 0


def test_weekend_and_holiday_use_next_independent_reference():
    assert evaluate([row(day="2025-01-05")])["flag"] == 1
    holiday = evaluate([row(day="2025-01-01")], event_date="2025-01-02")
    assert holiday["flag"] == 1
    assert evaluate([row(day="2025-01-03")], event_date="2025-01-05")["flag"] is None


def test_ambiguous_role_is_unknown_never_clean_zero():
    result = evaluate([row(title="关于收到立案告知书的公告", role="SUBJECT_OR_EVENT_AMBIGUOUS")])
    assert result["flag"] is None and result["score"] is None
    assert not result["gate"] and not result["common_support"]
    assert result["ambiguous_ids"] == ["1"]


def test_related_regulatory_person_is_exposure_but_police_is_not():
    person = row(title="关于实际控制人被中国证监会立案调查的公告", role="RELATED_PARTY_EXPLICIT")
    assert evaluate([person])["flag"] == 1
    police = row(title="关于公司收到公安机关立案通知书的公告", role="OTHER_AUTHORITY_EXPLICIT")
    assert evaluate([police])["flag"] == 0
    no_authority = row(title="关于高管收到立案告知书的公告", role="RELATED_PARTY_EXPLICIT")
    assert evaluate([no_authority])["flag"] is None


def test_positive_presence_overrides_other_role_ambiguity():
    result = evaluate([row(), row("2", title="关于收到立案告知书的公告", role="SUBJECT_OR_EVENT_AMBIGUOUS")])
    assert result["flag"] == 1 and result["score"] == 0 and not result["gate"]
    assert result["positive_overrides_role_ambiguity"]
    assert result["common_support"] and result["information_control_supported"]


def test_incomplete_source_or_route_keeps_even_positive_unknown():
    assert evaluate([row()], source_complete=False)["flag"] is None
    assert evaluate([row()], positive_route=False)["flag"] is None
    assert evaluate([], source_complete=False)["flag"] is None


def test_no_match_zero_is_narrow_and_progress_does_not_reset_case():
    empty = evaluate([])
    assert empty["flag"] == 0 and empty["score"] == 1 and empty["gate"]
    assert not empty["case_status_certified"]
    progress = row("2", title="关于公司立案调查进展暨风险提示公告", role="PROGRESS_OR_CASE_END")
    assert evaluate([progress])["flag"] == 0
    assert evaluate([row(), progress])["flag"] == 1
    assert evaluate([row(), progress])["progress_ids"] == ["2"]


def test_publication_clock_conflict_and_missing_reference_remain_unknown():
    conflict = row(published_at="2025-01-04T18:00:00+08:00")
    assert evaluate([conflict])["flag"] is None
    short_refs = ["2025-01-06"]
    assert evaluate([row()], reference_dates=short_refs)["flag"] is None
