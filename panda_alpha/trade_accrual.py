"""Original-report annual trade working-capital investment (WC01).

Four consolidated stock fields share one report's closing/opening columns.
Blank monetary cells stay unknown. Half-year statements are comparison
evidence only; they never replace the annual signal.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re

from panda_alpha.financial_statements import (
    TABLE, TABLE_NAMES, PREFIX, amount_unit, amount_text, column_pair,
    money, source_page, public_availability, normalize_statement_field,
    printed_quantum_yuan, display_precision_comparison,
)

TRADE_FIELDS = {"accounts_receivable": "应收账款", "inventory": "存货",
                "accounts_payable": "应付账款", "total_assets": "资产总计"}


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def trade_stock_periods(header, period, original_title):
    """Bind original two-column roles, retaining printed Jan 1 when present."""
    target = date.fromisoformat(period)
    dense = re.sub(r"\s+", "", header)
    try:
        dates = [date(int(y), int(m), int(d)).isoformat() for y, m, d in re.findall(
            r"(20\d{2})(?:年|-|/)(\d{1,2})(?:月|-|/)(\d{1,2})日?", dense)]
    except ValueError:
        return {"status": "invalid_printed_stock_date", "header_evidence": header}
    role_labels = bool(re.search(r"(?:期末余额|年末余额|本年年末余额|期末数).*(?:期初余额|年初余额|上年年末余额|期初数)", dense))
    annual = (target.month, target.day) == (12, 31)
    half = (target.month, target.day) == (6, 30)
    title_ok = bool(re.search(str(target.year) + (r"年?(?:年度报告)" if annual else r"年?(?:半年度报告|中期报告)"),
                              re.sub(r"\s+", "", original_title)))
    fail = {"status": "opening_or_current_period_unbound", "printed_header_dates": dates,
            "column_role_labels": role_labels}
    if not (annual or half) or not title_ok:
        return fail
    expected_open = {f"{target.year-1}-12-31", f"{target.year}-01-01"}
    # Collapse only a repeated current caption, never another arbitrary date.
    collapsed = dates[:]
    while len(collapsed) > 2 and collapsed[:2] == [period, period]:
        collapsed.pop(0)
    if len(collapsed) == 2 and collapsed[0] == period and collapsed[1] in expected_open:
        return {"status": "same_report_stock_columns_bound", "current_stock_asof": period,
                "opening_stock_asof": collapsed[1], "opening_fiscal_year": target.year,
                "method": "explicit_stock_column_dates", "printed_header_dates": dates,
                "column_role_labels": role_labels, "repeated_current_caption_collapsed": dates != collapsed}
    # A verified yearly/H1 report and a dated current table plus printed opening
    # roles bind fiscal-year opening. This is not a previous-report substitution.
    if role_labels and dates and all(d == period for d in dates):
        return {"status": "same_report_stock_columns_bound", "current_stock_asof": period,
                "opening_stock_asof": f"{target.year}-01-01", "opening_fiscal_year": target.year,
                "method": "verified_report_current_date_and_printed_fiscal_opening_role",
                "printed_header_dates": dates, "column_role_labels": True,
                "repeated_current_caption_collapsed": len(dates) > 1}
    return fail


def _balance_sections(text, period, source_title):
    headers = list(TABLE.finditer(text))
    duplicates = set()
    for i in range(1, len(headers)):
        previous, current = headers[i-1], headers[i]
        between = text[previous.end():current.start()]
        if (previous["scope"] == current["scope"] and previous["name"] == current["name"]
                and len(between) < 400 and not re.search(r"资产总计|流动资产|应收账款", between)):
            duplicates.add(i)
    sections = []
    for i, h in enumerate(headers):
        if h["scope"] != "合并" or TABLE_NAMES[h["name"]] != "balance" or h["continued"] or i in duplicates:
            continue
        next_header = next((k for j, k in enumerate(headers[i+1:], i+1) if j not in duplicates and not
                            (k["scope"] == "合并" and k["name"] == h["name"] and k["continued"])), None)
        if not next_header:
            sections.append({"status": "statement_end_boundary_missing"})
            continue
        body = text[h.end():next_header.start()]
        first = re.search(r"(?m)^\s*(?:流动资产|非流动资产|货币资金|现金及存放中央银行)", body)
        header = body[:first.start()] if first else body[:500]
        unit = amount_unit(header)
        if unit["status"] != "unit_verified":
            before = text[max(0, h.start()-160):h.start()]
            preceding = re.search(r"单位[:：]\s*(?:人民币)?(?:百万元|千元|万元|亿元|元)\s*$", before)
            if preceding:
                unit = amount_unit(preceding.group() + "\n" + header)
        sections.append({"status": "bounded_consolidated_statement", "body": body,
                         "body_start": h.end(), "header": header, "unit": unit,
                         "period": trade_stock_periods(header, period, source_title),
                         "source_page": source_page(text, h.start())})
    return sections


def _field(text, section, label, metadata, provenance):
    body = section["body"]
    pattern = re.compile(r"(?m)^\s*" + PREFIX + r"\s*".join(map(re.escape, label)) +
                         r"(?=[\t :：+\-−－—–/()（）\d]|$)(?P<rest>[^\n]*)$")
    rows = []
    for hit in pattern.finditer(body):
        rest = re.sub(r"^\s*[:：]\s*", "", hit["rest"])
        monetary_rest = re.sub(r"^[（(]?[一二三四五六七八九十]+[）)]?[、.．]?\s*\d+(?:[（(]\d+[）)])?", "", rest.strip())
        valid_rest = re.fullmatch(r"[\d,，.\s+\-−－—–/()（）]*", monetary_rest) is not None
        cells, snippet = column_pair(rest, body[hit.end():].splitlines()) if valid_rest else ([], rest)
        # A small integer and a note heading do not prove the first numeric
        # cell is a note. Three bare cells need separate column evidence.
        row = {"status": "missing_or_ambiguous_stock_columns", "source_label": hit.group().strip(),
               "source_page": source_page(text, section["body_start"] + hit.start()),
               "cells": cells, "column_source": snippet, "statement_scope": "consolidated",
               "amount_yuan": None, "comparative_amount_yuan": None}
        if (len(cells) == 2 and all(money(c) is not None for c in cells)
                and section["unit"]["status"] == "unit_verified"
                and section["period"]["status"] == "same_report_stock_columns_bound"):
            multiplier = Decimal(section["unit"]["multiplier"])
            current, opening = (money(c) for c in cells)
            row.update(status="same_report_current_and_opening_amounts", amount_yuan=amount_text(current*multiplier),
                       comparative_amount_yuan=amount_text(opening*multiplier), currency="CNY",
                       printed_unit=section["unit"]["printed_unit"], stock_asof=metadata["report_date"],
                       comparative_stock_asof=section["period"]["opening_stock_asof"],
                       zero_method="explicit_numeric_zero" if current == 0 or opening == 0 else None,
                       period_evidence=section["period"])
        rows.append(row)
    result = rows[0] if len(rows) == 1 else {"status": "multiple_field_rows_ambiguous" if rows else "field_not_found",
                                            "amount_yuan": None, "comparative_amount_yuan": None, "candidates": rows}
    result["field_contract"] = normalize_statement_field(result, measure="stock", publication=metadata, provenance=provenance)
    return result


def parse_trade_accrual_report(text, metadata, provenance=None):
    provenance = provenance or {}
    code = str(metadata.get("code", metadata.get("symbol", "")))
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("A six-digit source issuer is required")
    period = date.fromisoformat(str(metadata["report_date"])).isoformat()
    pub = metadata.get("published_at") or metadata["pub_date"]
    available = public_availability(pub, metadata.get("available_date"))
    text = text.replace("\r\n", "\n")
    dense_intro = re.sub(r"\s+", "", text[:2500])
    title = metadata.get("title", "")
    # Metadata titles alone are insufficient to certify the text's fiscal year.
    year = period[:4]
    actual_title = re.search(year + r"年?(?:年度报告|半年度报告|中期报告)", dense_intro)
    excluded_kind = ("annual_summary" if re.search(year+r"年?年度报告摘要", dense_intro[:1000]) else
                     "governance_annual_report_work_rules" if "年度报告工作制度" in dense_intro[:1000] else None)
    sections = _balance_sections(text, period, actual_title.group() if actual_title else "")
    fields = {}
    for key, label in TRADE_FIELDS.items():
        if len(sections) == 1 and sections[0]["status"] == "bounded_consolidated_statement":
            fields[key] = _field(text, sections[0], label, {**metadata, "report_date": period}, provenance)
        else:
            fields[key] = {"status": "statement_scope_missing_or_ambiguous", "amount_yuan": None,
                           "comparative_amount_yuan": None}
    values = {key: {"current": f["amount_yuan"], "opening": f["comparative_amount_yuan"]} for key, f in fields.items()}
    numeric = all(v is not None for pair in values.values() for v in pair.values())
    if numeric:
        numeric = all(Decimal(v) >= 0 for pair in values.values() for v in pair.values()) and all(
            Decimal(v) > 0 for v in values["total_assets"].values())
    source_verified = bool(provenance.get("pdf_hash_reverified") and provenance.get("text_hash_reverified"))
    barriers = []
    for hit in re.finditer(r"(?:是否需|需要)追溯调整或重述以前年度会计数据", text):
        after = re.sub(r"\s+", "", text[hit.end():hit.end()+100])
        affirmative = bool(re.match(r"[:：]*(?:[☑√✓■])是", after))
        if affirmative:
            barriers.append({"kind": "affirmative_prior_year_restatement_scope_pending",
                             "source_page": source_page(text, hit.start()),
                             "source_evidence": text[hit.start():hit.end()+160]})
    for section in sections:
        header = section.get("header", "")
        if re.search(r"调整后|重述后|追溯调整后", header):
            barriers.append({"source_page": section["source_page"], "source_evidence": header,
                             "kind": "actual_balance_comparative_adjusted_header"})
    # A conservative diagnostic does not invalidate same-edition current and
    # adjusted opening amounts; later selected-year contradictions use it.
    out = {"schema_version": 1, "source": "cninfo_same_report_trade_working_capital",
           "code": code, "symbol": code, "announcement_id": str(metadata["announcement_id"]),
           "report_date": period, "published_at": pub, "available_date": available,
           "title": title, "edition": metadata.get("edition"),
           "status": "source_values_verified" if numeric and source_verified else
                     "source_hash_verification_pending" if numeric else "source_values_pending",
           "annual_signal_eligible": period.endswith("12-31") and excluded_kind is None,
           "excluded_source_kind": excluded_kind,
           "source_kind_evidence": text[:850] if excluded_kind else None,
           "values": values, "field_evidence": fields,
           "statement_evidence": [{k: v for k, v in s.items() if k not in {"body", "body_start"}} for s in sections],
           "comparison_change_barriers": barriers, "provenance": provenance,
           "pdf_sha256": provenance.get("pdf_sha256"), "full_pit_certified": False,
           "source_scope": "within_supplied_original_report_editions_only"}
    out["record_sha256"] = _canonical_hash(out)
    return out


def load_trade_accrual_report(document):
    pdf_path, text_path = Path(document.get("path") or document["source_path"]), Path(document["text_path"])
    pdf_sha, text_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest(), hashlib.sha256(text_path.read_bytes()).hexdigest()
    if pdf_sha != document.get("pdf_sha256") or text_sha != document.get("text_sha256"):
        raise ValueError("Original PDF or extracted text hash mismatch")
    provenance = {"pdf_path": str(pdf_path), "text_path": str(text_path), "pdf_sha256": pdf_sha,
                  "text_sha256": text_sha, "pdf_hash_reverified": True, "text_hash_reverified": True}
    with gzip.open(text_path, "rt", encoding="utf-8") as handle:
        return parse_trade_accrual_report(handle.read(), document, provenance)


def trade_accrual_score(values):
    numbers = {key: {role: Decimal(str(value)) for role, value in pair.items()} for key, pair in values.items()}
    if set(numbers) != set(TRADE_FIELDS) or any(set(pair) != {"current", "opening"} for pair in numbers.values()):
        raise ValueError("Exactly four current/opening source stock pairs are required")
    if any(not v.is_finite() or v < 0 for pair in numbers.values() for v in pair.values()) or any(
            v <= 0 for v in numbers["total_assets"].values()):
        raise ValueError("Source stocks must be nonnegative and total assets positive")
    wc = lambda role: numbers["accounts_receivable"][role] + numbers["inventory"][role] - numbers["accounts_payable"][role]
    return float(-(wc("current") - wc("opening")) / ((numbers["total_assets"]["current"] + numbers["total_assets"]["opening"]) / 2))


def _available(row):
    pub = row.get("published_at") or row.get("pub_date")
    return public_availability(pub, row.get("available_date")) if pub else row.get("available_date", "9999")


def select_trade_accrual_asof(records, *, code, decision_date, corrections=()):
    day = str(decision_date)[:10]
    supplied = [r for r in records if r.get("code", r.get("symbol")) == code]
    eligible = [r for r in supplied if r["report_date"].endswith("12-31") and
                r.get("annual_signal_eligible", True) and _available(r) <= day]
    failure = {"pit_usable": False, "value": None, "full_pit_certified": False}
    if not eligible:
        return {**failure, "status": "no_public_annual_report"}
    period = max(r["report_date"] for r in eligible)
    latest_day = max(_available(r) for r in eligible if r["report_date"] == period)
    latest = [r for r in eligible if r["report_date"] == period and _available(r) == latest_day]
    ids = {r["announcement_id"] for r in latest}
    if len(ids) != 1 or len({r["record_sha256"] for r in latest}) != 1:
        return {**failure, "status": "ambiguous_same_day_annual_editions", "report_date": period,
                "announcement_ids": sorted(ids)}
    selected = latest[0]
    detail = {"report_date": period, "announcement_id": selected["announcement_id"],
              "record_sha256": selected["record_sha256"], "available_date": latest_day}
    if selected["status"] != "source_values_verified":
        return {**failure, **detail, "status": "latest_annual_source_values_pending"}
    year = int(period[:4])
    for event in corrections:
        if event.get("code", event.get("symbol")) != code or _available(event) > day:
            continue
        verified = bool(event.get("scope_verified") and event.get("scope_evidence"))
        affected = set(event.get("affected_fields", []))
        if verified and event.get("impact_status") == "financial_unrelated":
            continue
        if verified and event.get("impact_status") == "unrelated_fields" and affected and affected.isdisjoint(TRADE_FIELDS):
            continue
        links = set(event.get("report_dates", []))
        if event.get("report_date"):
            links.add(event["report_date"])
        links.update(p["report_date"] for p in event.get("period_links", []) if p.get("report_date"))
        # Current and opening stocks can be affected by both fiscal years.
        if links and not any(p[:4] in {str(year), str(year-1)} for p in links):
            continue
        unaffected = set(event.get("wc_verified_unaffected_stock_dates", []))
        # A body-reviewed correction can explicitly prove an affected fiscal
        # year's closing stock unchanged. A bare date flag cannot bypass it.
        proof_hash = event.get("scope_source_sha256") or event.get("source_pdf_sha256")
        proofs = event.get("wc_unaffected_stock_evidence", [])
        proof_bound = (isinstance(proofs, list) and isinstance(proof_hash, str)
                       and re.fullmatch(r"[0-9a-f]{64}", proof_hash) and any(
                           isinstance(p, dict) and p.get("source_pdf_sha256") == proof_hash
                           and p.get("source_text_sha256") and p.get("quote") for p in proofs))
        if (verified and proof_bound and links and
                all((p if p.endswith("12-31") else p[:4]+"-12-31") in unaffected
                    for p in links if p[:4] in {str(year), str(year-1)})):
            continue
        if not links and str(event.get("published_at", event.get("pub_date", "9999")))[:10] < f"{year-1}-01-01":
            continue
        if (verified and event.get("impact_status") == "revised_report" and
                selected["announcement_id"] in event.get("resolved_by_report_ids", [])):
            continue
        return {**failure, **detail, "status": "selected_annual_trade_correction_unresolved",
                "correction_announcement_id": event.get("announcement_id")}
    later_periods = {r["report_date"] for r in supplied if not r["report_date"].endswith("12-31")
                     and latest_day < _available(r) <= day and int(r["report_date"][:4]) == year+1}
    for later_period in sorted(later_periods):
        published_versions = [r for r in supplied if r["report_date"] == later_period and _available(r) <= day]
        last_later_date = max(_available(r) for r in published_versions)
        current_versions = [r for r in published_versions if _available(r) == last_later_date]
        if len({(r["announcement_id"], r["record_sha256"]) for r in current_versions}) != 1:
            return {**failure, **detail, "status": "later_trade_comparison_editions_ambiguous",
                    "later_report_ids": sorted({r["announcement_id"] for r in current_versions})}
        later = current_versions[0]
        # Next H1 opening stocks reference the selected FY closing balance.
        for key in TRADE_FIELDS:
            evidence = later.get("field_evidence", {}).get(key, {})
            contract = evidence.get("field_contract", {}).get("comparative", {})
            opening_date = evidence.get("comparative_stock_asof")
            if opening_date not in {period, f"{year+1}-01-01"}:
                continue
            opening_amount = evidence.get("comparative_amount_yuan")
            if opening_amount is None:
                continue
            old = selected["field_evidence"][key]["field_contract"]["current"]
            comparison = display_precision_comparison(old["amount_yuan"], opening_amount,
                                                     old["printed_quantum_yuan"], contract.get("printed_quantum_yuan"))
            if comparison["status"] != "overlapping_source_display_intervals":
                return {**failure, **detail, "status": "later_report_selected_fy_trade_comparison_mismatch",
                        "later_report_id": later["announcement_id"], "affected_field": key,
                        "comparison_evidence": comparison}
        if later.get("comparison_change_barriers"):
            return {**failure, **detail, "status": "later_report_selected_fy_trade_basis_pending",
                    "later_report_id": later["announcement_id"], "basis_evidence": later["comparison_change_barriers"]}
    return {**detail, "pit_usable": True, "value": trade_accrual_score(selected["values"]),
            "status": "source_values_usable_supplied_editions_only", "full_pit_certified": False,
            "method": "negative_same_annual_report_trade_wc_change_over_mean_assets"}
