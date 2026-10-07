"""Original consolidated revenue/cost flows and public-time GP01 dependencies."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
import hashlib
import json
import re

from panda_alpha.financial_statements import (
    TABLE, TABLE_NAMES, PREFIX, column_pair as _column_pair,
    amount_unit as _units, header_period as _header_period,
    money as _money, amount_text as _number_text, source_page as _page,
    public_availability, flow_spans as _flow_spans, normalize_statement_field,
    display_precision_comparison,
)


GP_FIELDS = {"operating_revenue", "operating_cost", "total_assets"}


def _hash_record(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _actual_change_evidence(text: str, income_header: str) -> list[dict]:
    """Only affirmative period-specific disclosures; boilerplate is not a change."""
    results = []
    checks = [("restatement", r"是否需追溯调整或重述以前年度会计数据", "是"),
              ("accounting_policy", r"(?:重要)?会计政策变更", "适用"),
              ("consolidation_scope", r"与上年度财务报告相比.{0,35}合并(?:报表|财务报表)范围.{0,30}情况说明", "适用")]
    for kind, pattern, word in checks:
        for hit in re.finditer(pattern, text):
            after = re.sub(r"\s+", "", text[hit.end():hit.end()+100])
            if re.match(r"(?:[：:]|[（(]\d+[）)])*(?:[☑√✓■])" + word, after) or re.search(r"^[^\n]{0,15}[☑√✓■]" + word, after):
                # Checked no/not-applicable cannot be mistaken for affirmative.
                if re.search(r"^[^\n]{0,15}[☑√✓■](?:否|不适用)", after):
                    continue
                results.append({"kind": kind, "source_page": _page(text, hit.start()),
                                "source_evidence": text[hit.start():hit.end()+160], "status": "explicit_change_bridge_not_supplied"})
    if "调整后" in income_header or "重述后" in income_header:
        results.append({"kind": "adjusted_comparative_columns", "source_evidence": income_header,
                        "status": "explicit_change_bridge_not_supplied"})
    return results


def parse_gross_profit_report(text: str, metadata: dict, provenance: dict, *, asset_record: dict) -> dict:
    code, period = str(metadata["symbol"]), str(metadata["report_date"])
    published = metadata.get("published_at") or metadata["pub_date"]
    original = text.replace("\r\n", "\n")
    headers = list(TABLE.finditer(original))
    merged = set()
    for i in range(1, len(headers)):
        a, b = headers[i-1], headers[i]
        between = original[a.end():b.start()]
        if a["scope"] == b["scope"] and a["name"] == b["name"] and len(between) < 400 and not re.search(r"营业(?:总)?收入|营业(?:总)?成本", between):
            merged.add(i)
    sections = []
    source_intro = re.sub(r"\s+", "", original[:2500])
    year = int(period[:4])
    kind = "(?:年年度报告|年度报告)" if period.endswith("12-31") else "(?:年半年度报告|半年度报告)"
    proof = re.search(str(year)+kind, source_intro)
    for i, hit in enumerate(headers):
        if hit["scope"] != "合并" or hit["name"] != "利润表" or hit["continued"] or i in merged:
            continue
        end = next((h for k,h in enumerate(headers[i+1:],i+1) if k not in merged and not (h["scope"]=="合并" and h["name"]=="利润表" and h["continued"])), None)
        if end is None:
            continue
        body = original[hit.end():end.start()]
        first = re.search(r"(?m)^\s*"+PREFIX+r"(?:其中[:：])?营业(?:总)?(?:收入|成本)", body)
        preamble = body[:first.start()] if first else body[:400]
        unit = _units(preamble)
        labels = ["营业总收入", "其中：营业收入", "营业收入", "营业总成本", "其中：营业成本", "营业成本", "利息收入", "利息支出", "税金及附加", "销售费用", "管理费用", "研发费用", "财务费用", "手续费及佣金收入", "手续费及佣金支出", "退保金", "已赚保费", "其他收益"]
        body = re.sub(r"(?<=[\s\d,.])(?<!\n)(?=(?:"+"|".join(map(re.escape,sorted(labels,key=len,reverse=True)))+r")(?:\s|[:：\d—-]))", "\n", body)
        sections.append({"body": body, "header": preamble, "source_page": _page(original,hit.start()),
                         "unit": unit, "spans": _flow_spans(preamble,period,proof.group() if proof else "")})
    evidence, values = {}, {}
    for field,label in [("operating_revenue","营业收入"),("operating_cost","营业成本")]:
        rows=[]
        if len(sections)==1:
            section=sections[0]
            pattern=re.compile(r"(?m)^\s*"+PREFIX+r"(?:其中\s*[:：]\s*)?"+r"\s*".join(map(re.escape,label))+r"(?P<rest>[^\n]*)$")
            for hit in pattern.finditer(section["body"]):
                cells,snippet=_column_pair(hit["rest"],section["body"][hit.end():].splitlines())
                if (len(cells)==3 and "附注" in section["header"] and re.fullmatch(r"\d{1,3}",cells[0])
                        and section["spans"]["comparative_status"]=="explicit_prior_same_span"
                        and not re.search(r"调整前|调整后|重述",section["header"])):
                    cells=cells[1:]
                pages=re.findall(r"===SOURCE_PAGE:(\d+)===",section["body"][:hit.start()])
                row={"source_page":int(pages[-1]) if pages else section["source_page"],"source_label":hit.group().strip(),"source_cells":cells,"column_source":snippet,"statement_scope":"consolidated","status":"column_unit_period_pending","current_yuan":None,"comparative_yuan":None}
                if len(cells)==2 and section["unit"]["status"]=="unit_verified" and section["spans"]["current_status"]=="current_period_verified":
                    a,b=_money(cells[0]),_money(cells[1]);mult=Decimal(section["unit"]["multiplier"])
                    if a is not None:
                        row.update(status="printed_current_flow",current_yuan=_number_text(a*mult),comparative_yuan=_number_text(b*mult) if b is not None else None,currency="CNY",printed_unit=section["unit"]["printed_unit"],flow_start=section["spans"]["current_start"],flow_end=period,comparative_flow_start=section["spans"]["comparative_start"],comparative_flow_end=section["spans"]["comparative_end"],comparative_span_verified=section["spans"]["comparative_status"]=="explicit_prior_same_span")
                rows.append(row)
        accepted=rows[0] if len(rows)==1 else {"status":"statement_or_exact_field_ambiguous","current_yuan":None,"comparative_yuan":None,"candidates":rows}
        evidence[field]=accepted
        values[field]=accepted["current_yuan"]
        values[field+"_comparative"]=accepted["comparative_yuan"]
    asset=asset_record.get("field_evidence",{}).get("total_assets",{})
    asset_valid=(asset_record.get("code")==code and asset_record.get("report_date")==period and asset_record.get("announcement_id")==str(metadata["announcement_id"]) and asset_record.get("pdf_sha256")==provenance.get("pdf_sha256") and asset.get("currency")=="CNY" and asset.get("statement_scope")=="consolidated" and asset.get("stock_asof")==period and asset.get("amount_yuan") is not None and Decimal(asset["amount_yuan"])>0)
    values["total_assets"]=asset["amount_yuan"] if asset_valid else None
    evidence["total_assets"]={**asset,"source_asset_record_sha256":asset_record.get("record_sha256"),"source_same_original_document_verified":asset_valid}
    for field, item in evidence.items():
        item["field_contract"] = normalize_statement_field(
            item, measure="stock" if field == "total_assets" else "flow",
            publication=metadata, provenance=provenance)
    income_header=sections[0]["header"] if len(sections)==1 else ""
    barriers=_actual_change_evidence(original,income_header)
    source_verified=provenance.get("pdf_hash_reverified") and provenance.get("text_hash_reverified")
    current_valid=source_verified and all(values[f] is not None for f in GP_FIELDS)
    row={"schema_version":2,"field_contract_version":1,"schema_note":"v2 adds explicit comparative spans and field contracts; existing v1 receipt hashes remain unchanged","source":"cninfo_original_consolidated_gross_profit_flows","code":code,"symbol":code,"announcement_id":str(metadata["announcement_id"]),"report_date":period,"published_at":str(published),"pub_date":str(published)[:10],"available_date":public_availability(published,metadata.get("available_date")),"edition":metadata.get("edition"),"title":metadata.get("title"),"pdf_sha256":provenance.get("pdf_sha256"),"provenance":provenance,"values":values,"field_evidence":evidence,"income_statement_evidence":[{k:v for k,v in s.items() if k!="body"} for s in sections],"comparison_change_barriers":barriers,"status":"source_current_values_verified" if current_valid else "source_values_pending","full_pit_certified":False,"comparative_vintage":"only known on this original report publication, never backdated"}
    row["record_sha256"]=_hash_record(row)
    return row


def _select_period(records,period,day):
    rows=[r for r in records if r["report_date"]==period and r["available_date"]<=day]
    if not rows:return "missing_disclosed_report",None
    last=max(r["available_date"] for r in rows);latest=[r for r in rows if r["available_date"]==last]
    identities={(r["announcement_id"],r["pdf_sha256"],r["record_sha256"]) for r in latest}
    if len(identities)!=1:return "ambiguous_same_day_versions",None
    return ("source_current_values_verified" if latest[0]["status"]=="source_current_values_verified" else "blocked_by_latest_unparsed_report"),latest[0]


def select_gross_profit_asof(records,*,code:str,decision_date:str,corrections=()):
    day=str(decision_date)[:10]
    rows=[r for r in records if r["code"]==code and r["report_date"]<=day and r["available_date"]<=day]
    base={"pit_usable":False,"value":None,"full_pit_certified":False}
    if not rows:return {**base,"status":"missing_disclosed_report"}
    period=max(r["report_date"] for r in rows)
    status,current=_select_period(rows,period,day)
    if status!="source_current_values_verified":return {**base,"status":status,"report_date":period}
    y=int(period[:4]);dependencies=[current];dependent_periods={period};annual=None
    if period.endswith("06-30"):
        if not all(current["field_evidence"][f].get("comparative_span_verified") and current["values"][f+"_comparative"] is not None for f in ["operating_revenue","operating_cost"]):return {**base,"status":"comparative_h1_span_or_amount_pending","report_date":period}
        fy=f"{y-1}-12-31";prior_h1=f"{y-1}-06-30"
        a_status,annual=_select_period(rows,fy,day)
        if a_status!="source_current_values_verified":return {**base,"status":"prior_fy_"+a_status,"report_date":period}
        dependencies.append(annual);dependent_periods.update([fy,prior_h1])
        # Earlier H1 source is diagnostic when supplied; its absence is visible.
        old_status,old_h1=_select_period(rows,prior_h1,day)
        if old_status in {"blocked_by_latest_unparsed_report","ambiguous_same_day_versions"}:
            return {**base,"status":"prior_h1_"+old_status,"report_date":period}
        if old_status=="source_current_values_verified":
            for f in ["operating_revenue","operating_cost"]:
                old_contract=old_h1["field_evidence"][f].get("field_contract",{})
                new_contract=current["field_evidence"][f].get("field_contract",{})
                check=display_precision_comparison(
                    old_h1["values"][f],current["values"][f+"_comparative"],
                    old_contract.get("current",{}).get("printed_quantum_yuan"),
                    new_contract.get("comparative",{}).get("printed_quantum_yuan"))
                if old_contract and new_contract and check["status"]=="printed_precision_pending":
                    return {**base,"status":"prior_h1_comparison_precision_pending","report_date":period,"mismatch_field":f}
                mismatch=(check["status"]=="different_beyond_printed_precision" or
                          not (old_contract and new_contract) and Decimal(old_h1["values"][f])!=Decimal(current["values"][f+"_comparative"]))
                if mismatch:return {**base,"status":"unbridged_comparative_restatement_mismatch","report_date":period,"mismatch_field":f,"earlier_h1_source_id":old_h1["announcement_id"],"printed_precision_comparison":check}
    if annual and any(r.get("comparison_change_barriers") for r in dependencies):return {**base,"status":"explicit_accounting_or_scope_change_unbridged","report_date":period,"dependency_ids":[r["announcement_id"] for r in dependencies]}
    for notice in corrections:
        if notice.get("code",notice.get("symbol"))!=code:continue
        pub=notice.get("published_at") or notice.get("pub_date")
        availability=public_availability(pub,notice.get("available_date")) if pub else notice.get("available_date","9999")
        if availability>day:continue
        linked=set(notice.get("report_dates",[]))
        if notice.get("report_date"):linked.add(notice["report_date"])
        linked.update(p["report_date"] for p in notice.get("period_links",[]) if p.get("report_date"))
        if linked and not linked.intersection(dependent_periods):continue
        if not linked and pub and str(pub)[:10]<min(dependent_periods):continue
        verified=bool(notice.get("scope_verified")) and bool(notice.get("scope_evidence"))
        affected=set(notice.get("affected_fields",[]))
        if verified and notice.get("impact_status")=="financial_unrelated":continue
        if verified and notice.get("impact_status")=="unrelated_fields" and affected and affected.isdisjoint(GP_FIELDS):continue
        if verified and notice.get("impact_status")=="revised_report" and linked and all(r["announcement_id"] in notice.get("resolved_by_report_ids",[]) for r in dependencies if r["report_date"] in linked) and not (period.endswith("06-30") and f"{y-1}-06-30" in linked):continue
        return {**base,"status":"blocked_by_unresolved_dependency_correction","report_date":period,"correction_announcement_id":notice.get("announcement_id")}
    v=current["values"];gp=Decimal(v["operating_revenue"])-Decimal(v["operating_cost"])
    method="current_fy"
    if annual:
        av=annual["values"]
        gp+=Decimal(av["operating_revenue"])-Decimal(av["operating_cost"])
        gp-=Decimal(v["operating_revenue_comparative"])-Decimal(v["operating_cost_comparative"])
        method="current_h1_plus_already_public_previous_fy_minus_current_report_prior_h1"
    return {**base,"status":"usable_within_supplied_vintages","pit_usable":True,"value":float(gp/Decimal(v["total_assets"])),"gross_profit_ttm_yuan":_number_text(gp),"total_assets_yuan":v["total_assets"],"report_date":period,"announcement_id":current["announcement_id"],"available_date":current["available_date"],"published_at":current["published_at"],"pdf_sha256":current["pdf_sha256"],"record_sha256":current["record_sha256"],"method":method,"dependent_report_periods":sorted(dependent_periods),"source_dependencies":[{"ID":r["announcement_id"],"report_date":r["report_date"],"available_date":r["available_date"],"pdf_sha256":r["pdf_sha256"],"record_sha256":r["record_sha256"]} for r in dependencies],"source_change_diagnostics":[{"ID":r["announcement_id"],"report_date":r["report_date"],"flags":r.get("comparison_change_barriers",[])} for r in dependencies if r.get("comparison_change_barriers")],"FY_same_current_document_does_not_require_comparison_bridge":not bool(annual),"unseen_earlier_h1_revision_risk":bool(annual and not any(r["report_date"]==f"{y-1}-06-30" for r in rows))}
