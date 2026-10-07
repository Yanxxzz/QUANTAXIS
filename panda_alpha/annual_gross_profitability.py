"""GP02 annual publication frequency, retaining known later revision barriers."""
from __future__ import annotations
import re
from decimal import Decimal
from panda_alpha.financial_statements import (
    public_availability, UNIT_MULTIPLIERS, money as _money,
    printed_quantum_yuan, display_precision_comparison,
)
from panda_alpha.gross_profitability import GP_FIELDS, select_gross_profit_asof


def _published(row):
    pub=row.get("published_at") or row.get("pub_date")
    return public_availability(pub,row.get("available_date")) if pub else row.get("available_date","9999")


def select_annual_gross_profit_asof(records,*,code,decision_date,corrections=(),asset_comparisons=()):
    """Use latest public FY only. H1/quarter evidence is never hidden.

    Later same-year flow corrections are uncertain for the annual total unless
    explicitly proved absorbed or unchanged. No fractional-year interpolation,
    annual replacement by H1, or automatic missing-to-zero is performed.
    """
    day=str(decision_date)[:10]
    supplied=[r for r in records if r.get("code",r.get("symbol"))==code]
    annuals=[r for r in supplied if str(r.get("report_date","")).endswith("12-31")]
    base=select_gross_profit_asof(annuals,code=code,decision_date=day,corrections=())
    if not base["pit_usable"]:return base
    period=base["report_date"];year=period[:4];source_id=base["announcement_id"]
    selected=next(r for r in annuals if r["announcement_id"]==source_id and r["record_sha256"]==base["record_sha256"])
    failure={"pit_usable":False,"value":None,"full_pit_certified":False,"report_date":period,"selected_annual_id":source_id}
    for event in corrections:
        if event.get("code",event.get("symbol"))!=code or _published(event)>day:continue
        linked=set(event.get("report_dates",[]))
        if event.get("report_date"):linked.add(event["report_date"])
        linked.update(p["report_date"] for p in event.get("period_links",[]) if p.get("report_date"))
        verified=bool(event.get("scope_verified")) and bool(event.get("scope_evidence"))
        affected=set(event.get("affected_fields",[]))
        if verified and event.get("impact_status")=="financial_unrelated":continue
        if verified and event.get("impact_status")=="unrelated_fields" and affected and affected.isdisjoint(GP_FIELDS):continue
        if linked and period not in linked:
            # Quarter/H1 flows contribute to the selected FY. A later correction
            # of that same-year flow is not automatically absent from FY effects.
            same_year_flow=any(p.startswith(year+"-") and not p.endswith("12-31") for p in linked)
            if not same_year_flow:continue
            flow_effect=not verified or not affected or bool(affected.intersection({"operating_revenue","operating_cost"}))
            if not flow_effect:continue
            if (verified and event.get("annual_effect_status") in {"annual_current_unchanged","absorbed_by_selected_annual"}
                    and source_id in event.get("annual_source_ids",[]) and event.get("annual_effect_evidence")):
                continue
            if _published(event)<=selected["available_date"] and verified and event.get("impact_status")=="revised_report":
                # Explicit period/field revision predates this annual original;
                # its already-public current full-year edition is authoritative.
                continue
            return {**failure,"status":"annual_same_year_flow_correction_unbridged","correction_announcement_id":event.get("announcement_id")}
        pub=event.get("published_at") or event.get("pub_date")
        if not linked and pub and str(pub)[:10]<period:continue
        if (verified and period in linked and event.get("impact_status")=="revised_report"
                and source_id in event.get("resolved_by_report_ids",[])):
            continue
        return {**failure,"status":"annual_selected_fy_correction_unresolved","correction_announcement_id":event.get("announcement_id")}
    diagnostics=[]
    for report in supplied:
        if report["report_date"].endswith("12-31") or _published(report)>day:continue
        if _published(report)<=selected["available_date"]:continue
        flags=report.get("comparison_change_barriers",[])
        if flags:diagnostics.append({"ID":report["announcement_id"],"report_date":report["report_date"],"flags":flags})
        affects_old_fy=(report["report_date"][:4]==str(int(year)+1))
        for flag in flags:
            kind=flag.get("kind")
            quote=flag.get("source_evidence","")
            if kind=="consolidation_scope":
                ordinary_quote=quote.replace("非同一控制下","").replace("非同控","")
                known_retro=bool(re.search(r"同一控制下|同控|追溯|重述|比较.{0,25}(?:调整|更正)",ordinary_quote))
                if known_retro and (affects_old_fy or year+"年" in quote or period in quote):
                    return {**failure,"status":"later_scope_selected_fy_restatement_unbridged","later_report_id":report["announcement_id"],"source_evidence":flag}
                continue
            # Positive retrospective/adjusted comparisons in next-year H1 can
            # affect the selected prior FY. Unknown accounting effect remains
            # pending; ordinary current-only group changes are diagnostics.
            if affects_old_fy and kind in {"restatement","adjusted_comparative_columns","accounting_policy"}:
                return {**failure,"status":"later_report_selected_fy_basis_uncertain","later_report_id":report["announcement_id"],"source_evidence":flag}
            if period in quote or (year+"年" in quote and re.search(r"追溯|重述|差错更正|调整后",quote)):
                return {**failure,"status":"later_report_selected_fy_restatement_unbridged","later_report_id":report["announcement_id"],"source_evidence":flag}
        # If explicitly source-linked previous-year-end asset values disagree,
        # the current annual leaf cannot be silently carried forward.
        asset=report.get("field_evidence",{}).get("total_assets",{})
        contract=asset.get("field_contract",{})
        comparative=contract.get("comparative",{})
        comparison=asset.get("comparative_amount_yuan",comparative.get("amount_yuan"))
        comparison_period=asset.get("comparative_stock_asof",comparative.get("stock_asof"))
        if (contract and comparison is not None and comparison_period is None and affects_old_fy):
            return {**failure,"status":"later_report_asset_comparison_period_pending",
                    "later_report_id":report["announcement_id"],"source_asset_field_contract":contract}
        if comparison_period==period and comparison is not None:
            selected_asset=selected["field_evidence"]["total_assets"]
            selected_contract=selected_asset.get("field_contract",{}).get("current",{})
            old_cells=selected_asset.get("cells",[])
            old_quantum=(selected_contract.get("printed_quantum_yuan") or
                         printed_quantum_yuan(old_cells[0] if len(old_cells)==2 else None,
                                              selected_asset.get("printed_unit")))
            comparison_check=display_precision_comparison(
                selected["values"]["total_assets"],comparison,old_quantum,
                comparative.get("printed_quantum_yuan") or asset.get("comparative_printed_quantum_yuan"))
            if contract and comparison_check["status"]=="printed_precision_pending":
                return {**failure,"status":"later_report_asset_comparison_precision_pending",
                        "later_report_id":report["announcement_id"],"source_asset_field_contract":contract}
            # Older snapshots remain accepted as before when they lack v2
            # contracts. The original-byte-bound sidecar below can supply their
            # comparison dates and precision without rewriting snapshot hashes.
            mismatch=(comparison_check["status"]=="different_beyond_printed_precision" or
                      not contract and Decimal(comparison)!=Decimal(selected["values"]["total_assets"]))
            if mismatch:
                return {**failure,"status":"later_report_selected_fy_asset_comparison_mismatch",
                        "later_report_id":report["announcement_id"],
                        "comparison_difference_yuan":comparison_check["difference_yuan"],
                        "printed_precision_tolerance_yuan":comparison_check["tolerance_yuan"]}
    for proof in asset_comparisons:
        if proof.get("code")!=code or proof.get("comparative_stock_asof")!=period or _published(proof)>day:
            continue
        report=next((r for r in supplied if r["announcement_id"]==proof.get("current_report_id")),None)
        if not report or report["pdf_sha256"]!=proof.get("current_pdf_sha256"):
            continue
        if report.get("provenance",{}).get("text_sha256")!=proof.get("current_text_sha256"):
            continue
        dates=proof.get("balance_header_dates",[])
        expected_dates=[list(map(int,report["report_date"].split("-"))),list(map(int,period.split("-")))]
        cells=proof.get("source_cells",[])
        if (dates!=expected_dates or len(cells)!=2 or proof.get("currency")!="CNY"
                or not proof.get("consolidated_scope_verified") or proof.get("printed_unit") not in UNIT_MULTIPLIERS):
            continue
        current=_money(cells[0]);comparison=_money(cells[1])
        if current is None or comparison is None:continue
        comparison*=UNIT_MULTIPLIERS[proof["printed_unit"]]
        if comparison!=Decimal(proof["comparative_amount_yuan"]):continue
        selected_asset=selected["field_evidence"]["total_assets"]
        selected_cells=selected_asset.get("cells",[])
        selected_unit=selected_asset.get("printed_unit")
        if not selected_cells or selected_unit not in UNIT_MULTIPLIERS:continue
        # Each printed number represents an interval of half its unit quantum.
        # Differing display precision is not evidence of an actual restatement.
        old_printed=_money(selected_cells[0])
        if old_printed is None:continue
        old_quantum=printed_quantum_yuan(selected_cells[0],selected_unit)
        comparison_check=display_precision_comparison(selected["values"]["total_assets"],str(comparison),
                                                      old_quantum,proof["comparative_printed_quantum_yuan"])
        if comparison_check["status"]=="different_beyond_printed_precision":
            return {**failure,"status":"later_report_selected_fy_asset_comparison_mismatch",
                    "later_report_id":proof["current_report_id"],"source_asset_comparison_proof":proof,
                    "comparison_difference_yuan":comparison_check["difference_yuan"],
                    "printed_precision_tolerance_yuan":comparison_check["tolerance_yuan"]}
    return {**base,"method":"latest_published_fy_current_gp_over_same_fy_current_assets",
            "annual_only_frequency":True,"later_report_change_diagnostics":diagnostics,
            "later_reports_checked":sum(not r["report_date"].endswith("12-31") and _published(r)<=day for r in supplied)}
