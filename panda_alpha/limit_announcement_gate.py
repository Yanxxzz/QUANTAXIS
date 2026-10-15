"""Conservative pre-order observation of a fixed regulatory-receipt title class.

This is a keyword-source observation, not confirmed issuer wrongdoing, a first
case, an active-case state or a prediction of returns. No prices or labels enter
this module. The 90-calendar-day window and direction are fixed, not parameters.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from datetime import date, datetime, timedelta
import html
import re
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

LOOKBACK_CALENDAR_DAYS = 90
_REGULATOR = re.compile(r"证监会|证券监督管理委员会")
_RECEIPT = re.compile(r"收到|被")
_OTHER_AUTHORITY = re.compile(r"公安|法院|检察|城管|市场监督管理|行政执法")
_POSITIVE_ROLES = {"ISSUER_EXPLICIT_RECEIPT_TITLE_ONLY", "RELATED_PARTY_EXPLICIT"}
_PROGRESS_ROLES = {"PROGRESS_OR_CASE_END"}
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _day(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("date must be YYYY-MM-DD text")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("date must be exact YYYY-MM-DD")
    return result


def _title(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing title")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", value))).strip()


def _published_day(row: Mapping[str, Any]) -> date:
    result = _day(row.get("published_date"))
    # A supplied timestamp may corroborate the date, never move it earlier.
    if row.get("published_at") is not None:
        at = datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00"))
        if at.tzinfo is None or at.astimezone(_SHANGHAI).date() != result:
            raise ValueError("publication date/timestamp disagree or timezone missing")
    return result


def evaluate_announcement_gate(
    rows: Iterable[Mapping[str, Any]], *, code: str, event_date: str,
    reference_dates: Sequence[str], source_complete: bool, positive_route: bool,
) -> dict[str, Any]:
    """Return a three-state receipt-title observation before event-day preorders.

    Input rows use the collector's code, announcement_id, title, published_date,
    optional published_at and subject_role fields. Reference dates must be the
    independently supplied, ordered unique trading-session calendar. They cannot
    be inferred from a particular stock's available bars.

    flag=1: a title in this narrow class is observed; flag=0: no such title was
    observed in this complete fixed keyword query; flag=None: source/role/clock
    unknown. A positive observation supersedes role ambiguity in other rows,
    but cannot supersede incomplete source/routing/calendar prerequisites.
    """
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        raise ValueError("code must be a six-character identity")
    t = _day(event_date)
    start = t - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    end = t - timedelta(days=1)
    answer: dict[str, Any] = {
        "code": code, "event_date": event_date,
        "publication_window": {"start": start.isoformat(), "end": end.isoformat()},
        "flag": None, "score": None, "direction": 1, "gate": False,
        "common_support": False, "information_control_supported": False,
        "status": "UNKNOWN", "matched_ids": [], "positive_ids": [],
        "progress_ids": [], "non_predicate_ids": [], "ambiguous_ids": [],
        "role_counts": {}, "unknown_reasons": [], "ignored_counts": {},
        "positive_overrides_role_ambiguity": False,
        "zero_meaning": "No class-matching receipt title observed in this complete fixed keyword query; not a clean company or absence of investigation/risk.",
        "case_status_certified": False,
    }
    prerequisite_unknowns = []
    if source_complete is not True:
        prerequisite_unknowns.append("SOURCE_QUERY_INCOMPLETE_OR_UNVERIFIED")
    if positive_route is not True:
        prerequisite_unknowns.append("POSITIVE_KEYWORD_ROUTE_UNVERIFIED")
    try:
        refs = [_day(d) for d in reference_dates]
        if not refs or refs != sorted(set(refs)) or t not in refs:
            raise ValueError("reference calendar not unique/ordered or event not a session")
    except (ValueError, TypeError):
        prerequisite_unknowns.append("REFERENCE_CALENDAR_INVALID_OR_EVENT_UNVERIFIED")
        refs = []
    if prerequisite_unknowns:
        answer["unknown_reasons"] = prerequisite_unknowns
        return answer

    roles: Counter[str] = Counter()
    ignored: Counter[str] = Counter()
    unknowns = set()
    seen: dict[str, tuple[Any, ...]] = {}
    hard_source_unknown = False
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            unknowns.add("MALFORMED_METADATA_ROW")
            hard_source_unknown = True
            continue
        if row.get("code") != code:
            ignored["OTHER_ISSUER"] += 1
            continue
        ident = row.get("announcement_id")
        if not isinstance(ident, str) or not ident:
            ident = "unknown-row-" + str(index)
            unknowns.add("ANNOUNCEMENT_ID_MISSING")
            hard_source_unknown = True
        try:
            title = _title(row.get("title"))
        except ValueError:
            unknowns.add("TITLE_MISSING:" + ident)
            hard_source_unknown = True
            continue
        if "立案" not in title:
            ignored["NO_LITERAL_TITLE_MATCH"] += 1
            continue
        try:
            published = _published_day(row)
        except (ValueError, TypeError, OverflowError):
            unknowns.add("PUBLICATION_CLOCK_UNKNOWN:" + ident)
            answer["ambiguous_ids"].append(ident)
            continue
        if not start <= published <= end:
            ignored["OUTSIDE_FIXED_PUBLICATION_WINDOW"] += 1
            continue
        role = row.get("subject_role")
        role = role if isinstance(role, str) and role else "UNKNOWN_ROLE"
        signature = (title, published.isoformat(), role)
        if ident in seen:
            if seen[ident] != signature:
                hard_source_unknown = True
                unknowns.add("CONFLICTING_ANNOUNCEMENT_METADATA:" + ident)
            else:
                ignored["EXACT_DUPLICATE_ID"] += 1
            continue
        seen[ident] = signature
        next_index = bisect_right(refs, published)
        if published < refs[0] or next_index >= len(refs) or refs[next_index] > t:
            unknowns.add("NEXT_INDEPENDENT_REFERENCE_UNVERIFIED:" + ident)
            answer["ambiguous_ids"].append(ident)
            continue
        answer["matched_ids"].append(ident)
        roles[role] += 1
        if role in _PROGRESS_ROLES:
            # This does not create another receipt or clear an active case.
            answer["progress_ids"].append(ident)
        elif _REGULATOR.search(title) and _RECEIPT.search(title) and role in _POSITIVE_ROLES:
            answer["positive_ids"].append(ident)
        elif _OTHER_AUTHORITY.search(title) and not _REGULATOR.search(title):
            answer["non_predicate_ids"].append(ident)
        else:
            answer["ambiguous_ids"].append(ident)
            unknowns.add("UNKNOWN_ROLE_OR_REGULATORY_RECEIPT_SEMANTICS:" + ident)

    for key in ["matched_ids", "positive_ids", "progress_ids", "non_predicate_ids", "ambiguous_ids"]:
        answer[key] = sorted(set(answer[key]))
    answer["role_counts"] = dict(sorted(roles.items()))
    answer["ignored_counts"] = dict(sorted(ignored.items()))
    answer["unknown_reasons"] = sorted(unknowns)
    if hard_source_unknown:
        return answer
    if answer["positive_ids"]:
        flag = 1
        answer["positive_overrides_role_ambiguity"] = bool(unknowns)
        answer["status"] = "KNOWN_RECEIPT_TITLE_EXPOSURE"
    elif unknowns:
        return answer
    else:
        flag = 0
        answer["status"] = "KNOWN_NO_OBSERVED_CLASS_MATCH_IN_FIXED_QUERY"
    answer.update(flag=flag, score=1 - flag, gate=flag == 0,
                  common_support=True, information_control_supported=True)
    return answer
