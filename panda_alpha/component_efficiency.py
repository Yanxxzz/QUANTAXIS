"""WC03: separately sales-scaled receivables and cost-scaled inventory/payables.

Cost is reported consolidated operating cost, a proxy for activity rather than
observed purchases. All three balance items keep their originally reported sign.
"""
from decimal import Decimal
import hashlib
import json
import re

from panda_alpha.financial_statements import (
    PREFIX, amount_unit, flow_spans, money, amount_text,
    normalize_statement_field, public_availability,
)
from panda_alpha.trade_accrual import TRADE_FIELDS
from panda_alpha.trade_efficiency import select_trade_efficiency_asof, _note_free_rest

COMPONENT_FIELDS = set(TRADE_FIELDS) | {"operating_revenue", "operating_cost"}


def _hash(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def attach_component_cost(stock, revenue, source_row):
    """Normalize a frozen original-byte-bound source audit's exact cost row.

    Amounts and periods are re-derived from printed cells and the actual income
    header, never from earlier parser placeholders or independent older reports.
    """
    period = stock["report_date"]
    same = (source_row.get("code") == stock["code"] and source_row.get("report_date") == period
            and source_row.get("announcement_id") == stock["announcement_id"]
            and source_row.get("stock_record_sha256") == stock["record_sha256"]
            and source_row.get("pdf_sha256") == stock["pdf_sha256"]
            and source_row.get("text_sha256") == stock["provenance"]["text_sha256"]
            and revenue.get("parent_stock_record_sha256") == stock["record_sha256"]
            and revenue.get("pdf_sha256") == stock["pdf_sha256"]
            and revenue.get("text_sha256") == stock["provenance"]["text_sha256"]
            and revenue.get("announcement_id") == stock["announcement_id"]
            and revenue.get("report_date") == period and revenue.get("code") == stock["code"])
    header = source_row.get("actual_income_header", "")
    unit, spans = amount_unit(header), flow_spans(header, period)
    candidates = source_row.get("cost_candidates", [])
    evidence = {"status": "cost_source_amount_period_or_binding_pending", "current_yuan": None,
                "comparative_yuan": None, "actual_income_header": header, "spans": spans}
    if len(candidates) == 1:
        candidate = candidates[0]
        proof = source_row.get("bounded_consolidated_income_proof", {})
        scope_verified = (proof.get("status")=="source_row_inside_bounded_consolidated_income"
                          and proof.get("statement_scope")=="consolidated" and proof.get("table_type")=="income"
                          and proof.get("pdf_sha256")==stock["pdf_sha256"]
                          and proof.get("text_sha256")==stock["provenance"]["text_sha256"]
                          and proof.get("announcement_id")==stock["announcement_id"]
                          and proof.get("source_label")==candidate.get("source_label")
                          and proof.get("source_cells")==candidate.get("source_cells")
                          and proof.get("column_source")==candidate.get("column_source")
                          and proof.get("actual_income_header")==header
                          and all(isinstance(proof.get(k),int) for k in ["statement_start_offset","statement_end_offset","source_row_offset"])
                          and proof["statement_start_offset"]<=proof["source_row_offset"]<proof["statement_end_offset"])
        cells = candidate.get("source_cells", [])
        label_match = re.fullmatch(r"\s*"+PREFIX+r"(?:其中\s*[:：]\s*)?营\s*业\s*成\s*本"
                                  r"(?=[\t :：+\-−－—–/()（）\d]|$)(?P<rest>[^\n]*)", candidate.get("source_label", ""))
        exact_label = bool(label_match and re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]*", _note_free_rest(label_match["rest"])))
        evidence.update({k:candidate[k] for k in ["source_label","source_cells","column_source","source_page"] if k in candidate})
        evidence["bounded_consolidated_income_proof"] = proof
        if (same and scope_verified and exact_label and len(cells) == 2 and all(money(c) is not None and money(c)>0 for c in cells)
                and unit["status"] == "unit_verified" and spans["current_status"] == "current_period_verified"
                and spans["comparative_status"] == "explicit_prior_same_span" and period.endswith("12-31")):
            multiplier = Decimal(unit["multiplier"])
            evidence.update(status="strict_same_original_annual_cost_pair", statement_scope="consolidated",
                            current_yuan=amount_text(money(cells[0])*multiplier),
                            comparative_yuan=amount_text(money(cells[1])*multiplier), currency="CNY",
                            printed_unit=unit["printed_unit"], flow_start=spans["current_start"], flow_end=spans["current_end"],
                            comparative_flow_start=spans["comparative_start"], comparative_flow_end=spans["comparative_end"],
                            comparative_span_verified=True)
    evidence["field_contract"] = normalize_statement_field(evidence, measure="flow",
                                                           publication=stock, provenance=stock["provenance"])
    out = {"schema_version": 1, "code": stock["code"], "announcement_id": stock["announcement_id"],
           "report_date": period, "published_at": stock["published_at"], "available_date": stock["available_date"],
           "parent_stock_record_sha256": stock["record_sha256"], "parent_revenue_attachment_sha256": revenue["record_sha256"],
           "pdf_sha256": stock["pdf_sha256"], "text_sha256": stock["provenance"]["text_sha256"],
           "same_original_binding_verified": bool(same), "source": "same_original_consolidated_cost_attachment",
           "status": "strict_annual_cost_verified" if evidence["status"] == "strict_same_original_annual_cost_pair" else "annual_cost_source_pending",
           "values": {"current": evidence["current_yuan"], "prior": evidence["comparative_yuan"]},
           "field_evidence": {"operating_cost": evidence}, "full_pit_certified": False}
    out["record_sha256"] = _hash(out)
    return out


def component_efficiency_score(stock_values, revenue_values, cost_values):
    stocks = {k: {role: Decimal(str(v)) for role, v in pair.items()} for k, pair in stock_values.items()}
    flows = [{k: Decimal(str(v)) for k, v in values.items()} for values in [revenue_values, cost_values]]
    if set(stocks) != set(TRADE_FIELDS) or any(set(p)!={"current", "opening"} for p in stocks.values()):
        raise ValueError("Exactly four current/opening stock pairs are required")
    if any(not v.is_finite() or v<0 for p in stocks.values() for v in p.values()) or any(v<=0 for v in stocks["total_assets"].values()):
        raise ValueError("Nonnegative stocks and positive assets are required")
    if any(set(f)!={"current", "prior"} or any(not v.is_finite() or v<=0 for v in f.values()) for f in flows):
        raise ValueError("Both annual sales and operating cost pairs must be strictly positive")
    sales, cost = flows
    sales_ratio, cost_ratio = sales["current"]/sales["prior"], cost["current"]/cost["prior"]
    actual = stocks["accounts_receivable"]["current"]+stocks["inventory"]["current"]-stocks["accounts_payable"]["current"]
    expected = stocks["accounts_receivable"]["opening"]*sales_ratio+(stocks["inventory"]["opening"]-stocks["accounts_payable"]["opening"])*cost_ratio
    return float(-(actual-expected)/((stocks["total_assets"]["current"]+stocks["total_assets"]["opening"])/2))


def _available(row):
    pub = row.get("published_at") or row.get("pub_date")
    return public_availability(pub, row.get("available_date")) if pub else row.get("available_date", "9999")


def select_component_efficiency_asof(stocks, revenues, costs, *, code, decision_date, corrections=()):
    day = str(decision_date)[:10]
    base = select_trade_efficiency_asof(stocks, revenues, code=code, decision_date=day, corrections=())
    if not base["pit_usable"]:
        return base
    failure = {**base, "value": None, "pit_usable": False}
    selected = next(s for s in stocks if s["code"]==code and s["record_sha256"]==base["record_sha256"])
    revenue = next(r for r in revenues if r["record_sha256"]==base["revenue_attachment_sha256"])
    matches = [c for c in costs if c["code"]==code and c["announcement_id"]==base["announcement_id"]
               and c["parent_stock_record_sha256"]==selected["record_sha256"]
               and c["parent_revenue_attachment_sha256"]==revenue["record_sha256"]
               and c["pdf_sha256"]==selected["pdf_sha256"] and c["text_sha256"]==selected["provenance"]["text_sha256"]]
    if len(matches)!=1 or matches[0]["status"]!="strict_annual_cost_verified":
        return {**failure, "status": "latest_annual_operating_cost_source_pending"}
    cost = matches[0]
    period, year = selected["report_date"], int(selected["report_date"][:4])
    for event in corrections:
        if event.get("code", event.get("symbol"))!=code or _available(event)>day:
            continue
        verified = bool(event.get("scope_verified") and event.get("scope_evidence"))
        affected = set(event.get("affected_fields", []))
        if verified and event.get("impact_status")=="financial_unrelated":
            continue
        if verified and event.get("impact_status")=="unrelated_fields" and affected and affected.isdisjoint(COMPONENT_FIELDS):
            continue
        links = set(event.get("report_dates", []))
        if event.get("report_date"):
            links.add(event["report_date"])
        links.update(p["report_date"] for p in event.get("period_links", []) if p.get("report_date"))
        # Unknown, title-derived periods are diagnostics, never proof of a
        # disjoint affected annual stock/flow basis.
        if verified and links and not any(p[:4] in {str(year), str(year-1)} for p in links):
            continue
        if not links and str(event.get("published_at", event.get("pub_date", "9999")))[:10]<f"{year-1}-01-01":
            continue
        if (verified and event.get("impact_status")=="revised_report" and period in links
                and selected["announcement_id"] in event.get("resolved_by_report_ids", [])):
            continue
        return {**failure, "status": "selected_annual_component_cost_or_stock_correction_unresolved",
                "correction_announcement_id": event.get("announcement_id")}
    return {**base, "value": component_efficiency_score(selected["values"], revenue["values"], cost["values"]),
            "status": "component_scaled_trade_efficiency_source_usable", "cost_attachment_sha256": cost["record_sha256"],
            "method": "receivables_sales_scaled_inventory_payables_cost_scaled_same_report_over_mean_assets"}
