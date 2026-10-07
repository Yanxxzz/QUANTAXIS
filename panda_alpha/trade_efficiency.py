"""WC02: same-report sales-scaled trade capital efficiency.

Revenue has its own strict current/prior full-year flow contract. Unknown
comparative periods stay pending; no independent earlier-year substitution.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
import re

from panda_alpha.financial_statements import (
    TABLE, TABLE_NAMES, PREFIX, amount_unit, column_pair, money, amount_text,
    flow_spans, source_page, normalize_statement_field, public_availability,
)
from panda_alpha.trade_accrual import TRADE_FIELDS, select_trade_accrual_asof

EFFICIENCY_FIELDS = set(TRADE_FIELDS) | {"operating_revenue"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _note_free_rest(rest):
    rest = re.sub(r"^\s*[:：]\s*", "", rest).strip()
    # A leading Chinese note identifier is distinct from currency columns.
    rest = re.sub(r"^[（(]?[一二三四五六七八九十]+[）)]?[、.．]?\s*(?:\d+|[（(][一二三四五六七八九十\d]+[）)])(?:[（(]\d+[）)])?", "", rest)
    return rest


def attach_annual_revenue(stock_record, text, metadata, provenance):
    """Attach a source-bound annual revenue pair to one immutable stock record."""
    period = date.fromisoformat(metadata["report_date"]).isoformat()
    code = str(metadata.get("code", metadata.get("symbol", "")))
    source_matches = (code == stock_record["code"] and period == stock_record["report_date"]
                      and str(metadata["announcement_id"]) == stock_record["announcement_id"]
                      and provenance.get("pdf_sha256") == stock_record.get("pdf_sha256")
                      and provenance.get("text_sha256") == stock_record.get("provenance", {}).get("text_sha256")
                      and provenance.get("pdf_hash_reverified") and provenance.get("text_hash_reverified"))
    text = text.replace("\r\n", "\n")
    headers = list(TABLE.finditer(text))
    duplicate_captions = set()
    for i in range(1, len(headers)):
        before, after = headers[i-1], headers[i]
        middle = text[before.end():after.start()]
        if (before["scope"] == after["scope"] and before["name"] == after["name"] and len(middle) < 400
                and not re.search(r"营业(?:总)?收入|营业(?:总)?成本", middle)):
            duplicate_captions.add(i)
    sections = []
    original_title = re.search(period[:4]+r"年?(?:年度报告)", re.sub(r"\s+", "", text[:2500]))
    for i, heading in enumerate(headers):
        if (heading["scope"] != "合并" or TABLE_NAMES[heading["name"]] != "income"
                or heading["continued"] or i in duplicate_captions):
            continue
        end = next((h for j, h in enumerate(headers[i+1:], i+1) if j not in duplicate_captions
                    and not (h["scope"] == "合并" and h["name"] == heading["name"] and h["continued"])), None)
        if not end:
            sections.append({"status": "income_statement_end_missing"})
            continue
        body = text[heading.end():end.start()]
        first = re.search(r"(?m)^\s*"+PREFIX+r"(?:其中\s*[:：]\s*)?营业(?:总)?(?:收入|成本)", body)
        header = body[:first.start()] if first else body[:400]
        sections.append({"status": "bounded_consolidated_income", "body": body, "body_start": heading.end(),
                         "header": header, "source_page": source_page(text, heading.start()),
                         "unit": amount_unit(header),
                         "spans": flow_spans(header, period, original_title.group() if original_title else "")})
    evidence = {"status": "income_statement_missing_or_ambiguous", "current_yuan": None,
                "comparative_yuan": None}
    if len(sections) == 1 and sections[0]["status"] == "bounded_consolidated_income":
        section, rows = sections[0], []
        body = section["body"]
        pattern = re.compile(r"(?m)^\s*"+PREFIX+r"(?:其中\s*[:：]\s*)?营\s*业\s*收\s*入"
                             r"(?=[\t :：+\-−－—–/()（）\d]|$)(?P<rest>[^\n]*)$")
        for hit in pattern.finditer(body):
            rest = _note_free_rest(hit["rest"])
            valid_rest = re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]*", rest) is not None
            cells, snippet = column_pair(rest, body[hit.end():].splitlines()) if valid_rest else ([], rest)
            spans = section["spans"]
            # Keep three bare numeric cells ambiguous; source-column evidence
            # must distinguish an integer note from a third monetary amount.
            row = {"status": "annual_revenue_columns_or_spans_pending", "source_cells": cells,
                   "column_source": snippet, "source_label": hit.group().strip(),
                   "source_page": source_page(text, section["body_start"]+hit.start()),
                   "statement_scope": "consolidated", "current_yuan": None, "comparative_yuan": None,
                   "spans": spans}
            if (len(cells) == 2 and all(money(c) is not None and money(c) > 0 for c in cells)
                    and section["unit"]["status"] == "unit_verified"
                    and spans["current_status"] == "current_period_verified"
                    and spans["comparative_status"] == "explicit_prior_same_span" and period.endswith("12-31")):
                mult = Decimal(section["unit"]["multiplier"])
                row.update(status="strict_same_report_annual_revenue_pair", current_yuan=amount_text(money(cells[0])*mult),
                           comparative_yuan=amount_text(money(cells[1])*mult),
                           currency="CNY", printed_unit=section["unit"]["printed_unit"],
                           flow_start=spans["current_start"], flow_end=spans["current_end"],
                           comparative_flow_start=spans["comparative_start"], comparative_flow_end=spans["comparative_end"],
                           comparative_span_verified=True)
            rows.append(row)
        evidence = rows[0] if len(rows) == 1 else {"status": "annual_revenue_exact_field_missing_or_ambiguous",
                                                  "current_yuan": None, "comparative_yuan": None, "candidates": rows}
    evidence["field_contract"] = normalize_statement_field(evidence, measure="flow", publication=metadata, provenance=provenance)
    eligible = source_matches and period.endswith("12-31") and stock_record.get("annual_signal_eligible", True)
    out = {"schema_version": 1, "source": "same_original_annual_report_revenue_attachment",
           "code": code, "announcement_id": str(metadata["announcement_id"]), "report_date": period,
           "published_at": metadata.get("published_at") or metadata.get("pub_date"),
           "available_date": stock_record["available_date"],
           "parent_stock_record_sha256": stock_record["record_sha256"], "pdf_sha256": provenance.get("pdf_sha256"),
           "text_sha256": provenance.get("text_sha256"), "same_document_binding_verified": bool(source_matches),
           "status": "strict_annual_revenue_verified" if eligible and evidence["status"] == "strict_same_report_annual_revenue_pair"
                     else "annual_revenue_source_pending",
           "field_evidence": {"operating_revenue": evidence},
           "values": {"current": evidence["current_yuan"], "prior": evidence["comparative_yuan"]},
           "income_statement_evidence": [{k: v for k, v in s.items() if k not in {"body", "body_start"}} for s in sections],
           "provenance": provenance, "full_pit_certified": False}
    out["record_sha256"] = _hash(out)
    return out


def trade_efficiency_score(stock_values, revenue_values):
    stocks = {k: {role: Decimal(str(value)) for role, value in pair.items()} for k, pair in stock_values.items()}
    revenue = {k: Decimal(str(value)) for k, value in revenue_values.items()}
    if set(stocks) != set(TRADE_FIELDS) or any(set(p) != {"current", "opening"} for p in stocks.values()):
        raise ValueError("Exactly four stock current/opening pairs are required")
    if set(revenue) != {"current", "prior"} or any(not v.is_finite() or v <= 0 for v in revenue.values()):
        raise ValueError("Both same-report annual revenue amounts must be positive")
    if any(not v.is_finite() or v < 0 for p in stocks.values() for v in p.values()) or any(v <= 0 for v in stocks["total_assets"].values()):
        raise ValueError("Nonnegative reported stocks and positive assets are required")
    wc = lambda role: stocks["accounts_receivable"][role]+stocks["inventory"][role]-stocks["accounts_payable"][role]
    expected = wc("opening")*revenue["current"]/revenue["prior"]
    return float(-(wc("current")-expected)/((stocks["total_assets"]["current"]+stocks["total_assets"]["opening"])/2))


def _available(row):
    pub = row.get("published_at") or row.get("pub_date")
    return public_availability(pub, row.get("available_date")) if pub else row.get("available_date", "9999")


def select_trade_efficiency_asof(stock_records, revenue_attachments, *, code, decision_date, corrections=()):
    day = str(decision_date)[:10]
    base = select_trade_accrual_asof(stock_records, code=code, decision_date=day, corrections=())
    if not base["pit_usable"]:
        return base
    failure = {**base, "pit_usable": False, "value": None, "full_pit_certified": False}
    selected = next(r for r in stock_records if r["code"] == code and r["announcement_id"] == base["announcement_id"]
                    and r["record_sha256"] == base["record_sha256"])
    matches = [r for r in revenue_attachments if r["code"] == code and r["announcement_id"] == selected["announcement_id"]
               and r["parent_stock_record_sha256"] == selected["record_sha256"]
               and r["pdf_sha256"] == selected["pdf_sha256"]
               and r["text_sha256"] == selected["provenance"]["text_sha256"]]
    if len(matches) != 1 or matches[0]["status"] != "strict_annual_revenue_verified":
        return {**failure, "status": "latest_annual_revenue_attachment_pending"}
    attachment = matches[0]
    period, year = selected["report_date"], int(selected["report_date"][:4])
    for event in corrections:
        if event.get("code", event.get("symbol")) != code or _available(event) > day:
            continue
        verified = bool(event.get("scope_verified") and event.get("scope_evidence"))
        affected = set(event.get("affected_fields", []))
        if verified and event.get("impact_status") == "financial_unrelated":
            continue
        if verified and event.get("impact_status") == "unrelated_fields" and affected and affected.isdisjoint(EFFICIENCY_FIELDS):
            continue
        links = set(event.get("report_dates", []))
        if event.get("report_date"):
            links.add(event["report_date"])
        links.update(p["report_date"] for p in event.get("period_links", []) if p.get("report_date"))
        if links and not any(p[:4] in {str(year), str(year-1)} for p in links):
            continue
        if not links and str(event.get("published_at", event.get("pub_date", "9999")))[:10] < f"{year-1}-01-01":
            continue
        if (verified and event.get("impact_status") == "revised_report"
                and selected["announcement_id"] in event.get("resolved_by_report_ids", [])):
            continue
        # A previous WC01 stock-only unchanged proof does not certify annual
        # revenue. This five-field review must stand on its own source scope.
        return {**failure, "status": "selected_annual_efficiency_correction_unresolved",
                "correction_announcement_id": event.get("announcement_id")}
    return {**base, "value": trade_efficiency_score(selected["values"], attachment["values"]),
            "status": "strict_sales_scaled_trade_efficiency_source_usable",
            "revenue_attachment_sha256": attachment["record_sha256"],
            "method": "negative_actual_minus_opening_trade_wc_times_same_report_sales_ratio_over_mean_assets"}
