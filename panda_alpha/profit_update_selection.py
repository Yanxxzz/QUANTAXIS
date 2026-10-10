"""Select supplied FY/H1 profit updates without asserting a complete PIT census.

The caller binds original capture and retained text bytes. This selector keeps
latest missing editions and known revenue/cost revision barriers visible; it
never downloads, scores returns, or supplies zero for missing financial values.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re

from panda_alpha.financial_statements import (
    display_precision_comparison, printed_quantum_yuan, public_availability,
)


READY = {"text_source_pair_ready", "bound_annual_pair_ready"}
FIELDS = ("operating_revenue", "operating_cost")
HEX = re.compile(r"[0-9a-f]{64}")


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _available(row):
    published = row.get("published_at") or row.get("pub_date")
    if not _day(published):
        return None
    try:
        return public_availability(published, row.get("available_date"))
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _spans(evidence):
    period = evidence.get("period_evidence", {})
    spans = period.get("spans", period)
    contract = evidence.get("field_contract", {})
    current, comparative = contract.get("current", {}), contract.get("comparative", {})
    return (
        evidence.get("flow_start") or spans.get("current_start") or current.get("flow_start"),
        evidence.get("flow_end") or spans.get("current_end") or current.get("flow_end"),
        evidence.get("comparative_flow_start") or spans.get("comparative_start") or comparative.get("flow_start"),
        evidence.get("comparative_flow_end") or spans.get("comparative_end") or comparative.get("flow_end"),
    )


def _quantum(evidence, column):
    cells = evidence.get("source_cells", evidence.get("cells", []))
    unit = evidence.get("printed_unit") or evidence.get("unit_evidence", {}).get("printed_unit")
    if len(cells) == 2:
        result = printed_quantum_yuan(cells[column], unit)
        if result:
            return result
    role = "current" if column == 0 else "comparative"
    result = (evidence.get("field_contract", {}).get(role, {}).get("printed_quantum_yuan")
              or evidence.get(role + "_printed_quantum_yuan"))
    value = _number(result)
    return str(value) if value is not None and value > 0 else None


def _ready_reason(row):
    if row.get("status") not in READY:
        return "latest_fy_h1_source_pending"
    if row.get("comparison_change_barriers"):
        return "explicit_accounting_or_scope_change_unbridged"
    period = row["report_date"]
    year = int(period[:4])
    expected = (f"{year}-01-01", period, f"{year - 1}-01-01", f"{year - 1}" + period[4:])
    values = row.get("values", {})
    for field in FIELDS:
        evidence = row.get("field_evidence", {}).get(field, {})
        if _spans(evidence) != expected:
            return "current_comparative_same_span_proof_pending"
        current, prior = _number(values.get(field)), _number(values.get(field + "_comparative"))
        if current is None or prior is None:
            return "income_amount_pending"
        if (field == "operating_revenue" and min(current, prior) <= 0
                or field == "operating_cost" and min(current, prior) < 0):
            return "positive_revenue_or_nonnegative_cost_required"
    if not str(row.get("announcement_id", "")) or any(
            not HEX.fullmatch(str(row.get(key, ""))) for key in ("pdf_sha256", "text_sha256")):
        return "original_source_identity_pending"
    revenue, prior_revenue = (_number(values[key]) for key in
                             ("operating_revenue", "operating_revenue_comparative"))
    cost, prior_cost = (_number(values[key]) for key in
                       ("operating_cost", "operating_cost_comparative"))
    computed = (revenue - cost) / revenue - (prior_revenue - prior_cost) / prior_revenue
    stated = _number(row.get("gross_margin_change"))
    if stated is None or abs(computed - stated) > Decimal("1e-12"):
        return "gross_margin_change_amount_binding_pending"
    return None


def _identity(row):
    # Duplicate transport references do not create a new edition. Conflicting
    # status, period/publication, source hashes, or financial evidence do.
    keys = ("code", "symbol", "report_date", "announcement_id", "published_at",
            "pub_date", "available_date", "status", "pdf_sha256", "text_sha256",
            "gross_margin_change", "values", "field_evidence", "comparison_change_barriers")
    return json.dumps({key: row.get(key) for key in keys}, sort_keys=True, ensure_ascii=False, default=str)


def _latest(rows, period):
    same = [row for row in rows if row["report_date"] == period]
    last = max(_available(row) for row in same)
    latest = [row for row in same if _available(row) == last]
    if len({str(row.get("announcement_id", "")) for row in latest}) != 1:
        return None, "ambiguous_same_day_fy_h1_editions", latest
    if len({_identity(row) for row in latest}) != 1:
        return None, "conflicting_latest_fy_h1_identity", latest
    return latest[0], None, latest


def _correction(selected, day, notices):
    year = int(selected["report_date"][:4])
    relevant_years = {str(year), str(year - 1)}
    for notice in notices:
        verified = bool(notice.get("scope_verified") and notice.get("scope_evidence"))
        impact = notice.get("impact_status")
        affected = set(notice.get("affected_fields", []))
        links = set(notice.get("report_dates", []))
        if notice.get("report_date"):
            links.add(notice["report_date"])
        links.update(link["report_date"] for link in notice.get("period_links", []) if link.get("report_date"))
        if verified and (impact == "financial_unrelated" or
                         impact == "unrelated_fields" and affected and affected.isdisjoint(FIELDS)):
            continue
        if verified and links and not any(str(period)[:4] in relevant_years for period in links):
            continue
        available = _available(notice)
        if available is None:
            return "known_correction_publication_time_pending", notice
        if available > day:
            continue
        if not links and _day(notice.get("published_at") or notice.get("pub_date")) < f"{year - 1}-01-01":
            continue
        if (verified and impact == "revised_report" and selected["report_date"] in links
                and str(selected["announcement_id"]) in {str(v) for v in notice.get("resolved_by_report_ids", [])}):
            for binding in notice.get("resolved_report_source_bindings", []):
                if (str(binding.get("code", binding.get("symbol", ""))) == str(selected.get("code", selected.get("symbol", "")))
                        and str(binding.get("announcement_id", "")) == str(selected["announcement_id"])
                        and binding.get("pdf_sha256") == selected["pdf_sha256"]
                        and binding.get("text_sha256") == selected["text_sha256"]):
                    break
            else:
                return "known_income_revision_scope_pending", notice
            continue
        return "known_income_revision_scope_pending", notice
    return None, None


def select_profit_update_asof(records, *, code, decision_date, corrections=()):
    """Return latest public FY/H1 delta margin, limited to supplied editions.

    A missing older same-span source is a stated comparison limitation. An
    available usable older source is compared at source display precision;
    a later missing edition of the selected period cannot be bypassed.
    """
    day = _day(decision_date)
    if day is None:
        raise ValueError("decision_date must be an ISO date")
    code = str(code)
    base = {"pit_usable": False, "value": None, "full_pit_certified": False,
            "source_coverage": "within_supplied_FY_H1_editions_only",
            "economic_rejection": False, "selected": None, "limitations": []}
    supplied = [row for row in records if str(row.get("code", row.get("symbol", ""))) == code
                and _day(row.get("report_date")) and str(row["report_date"]).endswith(("12-31", "06-30"))
                and row["report_date"] <= day]
    eligible = [row for row in supplied if _available(row) and _available(row) <= day]
    period = max((row["report_date"] for row in eligible), default=None)
    unknown = [row for row in supplied if _available(row) is None
               and (period is None or row["report_date"] >= period)]
    if unknown:
        return {**base, "status": "named_fy_h1_publication_time_pending",
                "report_date": max(row["report_date"] for row in unknown), "selected": unknown[0]}
    if period is None:
        return {**base, "status": "missing_disclosed_fy_h1_report"}
    selected, conflict, latest = _latest(eligible, period)
    if conflict:
        return {**base, "status": conflict, "report_date": period,
                "latest_announcement_ids": sorted({str(row.get("announcement_id", "")) for row in latest})}
    detail = {"selected": selected, "report_date": period,
              "announcement_id": str(selected["announcement_id"]), "available_date": _available(selected),
              "published_at": selected.get("published_at") or selected.get("pub_date"),
              "pdf_sha256": selected.get("pdf_sha256"), "text_sha256": selected.get("text_sha256")}
    reason = _ready_reason(selected)
    if reason:
        return {**base, **detail, "status": reason, "source_status": selected.get("status")}
    notices = [notice for notice in corrections if str(notice.get("code", notice.get("symbol", ""))) == code]
    reason, notice = _correction(selected, day, notices)
    if reason:
        return {**base, **detail, "status": reason,
                "correction_announcement_id": notice.get("announcement_id")}
    prior_period = str(int(period[:4]) - 1) + period[4:]
    prior_rows = [row for row in eligible if row["report_date"] == prior_period]
    limitations = []
    compared = None
    if prior_rows:
        prior, conflict, _ = _latest(prior_rows, prior_period)
        if conflict or _ready_reason(prior):
            limitations.append("earlier_same_span_source_not_usable_for_revision_comparison")
        else:
            for field in FIELDS:
                comparison = display_precision_comparison(
                    prior["values"][field], selected["values"][field + "_comparative"],
                    _quantum(prior["field_evidence"][field], 0),
                    _quantum(selected["field_evidence"][field], 1))
                if comparison["status"] != "overlapping_source_display_intervals":
                    return {**base, **detail,
                            "status": "earlier_same_span_printed_precision_pending" if comparison["status"] == "printed_precision_pending"
                                      else "unbridged_comparative_restatement_mismatch",
                            "mismatch_field": field, "earlier_announcement_id": str(prior["announcement_id"]),
                            "printed_precision_comparison": comparison}
            compared = str(prior["announcement_id"])
    else:
        limitations.append("earlier_same_span_original_source_missing")
    return {**base, **detail, "status": "usable_within_supplied_FY_H1_editions",
            "pit_usable": True, "value": float(selected["gross_margin_change"]),
            "limitations": limitations, "earlier_same_span_compared_id": compared,
            "method": "latest_public_same_edition_gross_margin_change"}
