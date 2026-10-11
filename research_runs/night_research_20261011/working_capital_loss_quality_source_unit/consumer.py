"""Deferred AR/INV note facts only; no factor, financial mask or cash claim."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from panda_alpha.financial_statements import UNIT_MULTIPLIERS, money, source_page


def digest(raw): return hashlib.sha256(raw).hexdigest()
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
def read(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def bound(item, retain=False):
    h=hashlib.sha256()
    with Path(item["path"]).open("rb") as handle:
        for chunk in iter(lambda:handle.read(4*1024*1024),b""):h.update(chunk)
    if h.hexdigest()!=item["sha256"]:raise ValueError("source/runtime_binding_changed:"+Path(item["path"]).name)
    return Path(item["path"]).read_bytes() if retain else None


def locator(text, start, end):
    return {"start": start, "end": end, "source_page": source_page(text, start),
            "span_sha256": digest(text[start:end].encode("utf-8"))}


ACCOUNTS = "应收账款|应收票据|应收款项融资|预付款项|其他应收款|存货|合同资产|持有待售资产|一年内到期的非流动资产|其他流动资产|长期股权投资|长期应收款|其他权益工具投资|投资性房地产|固定资产|在建工程|使用权资产|无形资产|开发支出|商誉|长期待摊费用|递延所得税资产|其他非流动资产"
NOTE = re.compile(r"(?m)^[ \t]*(?:[（(]?\d+[）)]?[、.．][ \t]*)(?P<name>" + ACCOUNTS + r")[ \t]*[：:]?[ \t]*$")
ANY_NUMBERED_HEADING = re.compile(r"(?m)^[ \t]*(?:\d+[、.．]|[（(]\d+[）)])[ \t]*[^\d\s][^\n]*$")
SCOPE = re.compile(r"(?m)^[ \t]*([一二三四五六七八九十]+)[、.．][ \t]*合并财务报表项目注释[ \t]*$")
NEXT_CHAPTER = re.compile(r"(?m)^[ \t]*[一二三四五六七八九十]+[、.．][ \t]*[^\n]+$")
NUMBER = re.compile(r"[-−－]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?|[（(](?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?[）)]")
STOCK_HEADERS = {
    "AR": "项目期末账面余额期末坏账准备期末账面价值期初账面余额期初坏账准备期初账面价值",
    "INV": "项目期末账面余额期末跌价准备期末账面价值期初账面余额期初跌价准备期初账面价值"}
STOCK_ROLES = ["closing_gross", "closing_provision", "closing_net", "fiscal_opening_gross", "fiscal_opening_provision", "fiscal_opening_net"]
ROLL_HEADERS = {
    "AR": "项目期初余额本期计提本期转回本期核销其他增加其他减少期末余额",
    "INV": "项目期初余额本期计提本期转回本期转销其他增加其他减少期末余额"}
ROLL_ROLES = ["fiscal_opening_provision", "charge", "reversal", "writeoff", "other_increase", "other_decrease", "closing_provision"]


def sections(text):
    parents = list(SCOPE.finditer(text))
    if len(parents) != 1: return {}, ["unique_consolidated_note_scope_pending"]
    parent = parents[0]
    next_parent = NEXT_CHAPTER.search(text, parent.end())
    end = next_parent.start() if next_parent else len(text)
    notes = list(NOTE.finditer(text, parent.end(), end))
    result, pending = {}, []
    for kind, title in [("AR", "应收账款"), ("INV", "存货")]:
        hits = [(i, note) for i, note in enumerate(notes) if note["name"] == title]
        if len(hits) != 1:
            pending.append(kind + "_unique_note_caption_pending"); continue
        i, note = hits[0]
        following = ANY_NUMBERED_HEADING.search(text,note.end(),end)
        # Unknown or ambiguous-level numbering can only shorten the range;
        # never borrow its following table into this asset note.
        limit = following.start() if following else end
        result[kind] = {"start": note.start(), "end": limit, "scope_header": locator(text, parent.start(), parent.end()),
                        "note": locator(text, note.start(), limit), "title": title}
    return result, pending


def decimal_fact(raw, multiplier):
    value = money(raw)
    if value is None or value < 0: raise ValueError("nonfinite_or_negative_stock_movement_pending")
    quantum = Decimal(1).scaleb(value.as_tuple().exponent) * multiplier
    return {"printed": raw, "amount_yuan": str(value * multiplier), "quantum_yuan": str(quantum)}


def reconcile(facts, coefficients):
    residual = sum((Decimal(facts[role]["amount_yuan"]) * sign for role, sign in coefficients.items()), Decimal(0))
    tolerance = sum((Decimal(facts[role]["quantum_yuan"]) * abs(sign) / 2 for role, sign in coefficients.items()), Decimal(0))
    return {"status": "source_display_intervals_overlap" if abs(residual) <= tolerance else "source_arithmetic_conflict_pending",
            "residual_yuan": str(residual), "maximum_display_rounding_tolerance_yuan": str(tolerance)}


def table_fact(text, note, kind, table_kind, year):
    """A deliberately narrow flat native header; unfamiliar geometry is pending.

    Percent/aging/classification columns, dashes, omitted movement classes and
    multilevel headers cannot be guessed into amount roles. No blank is zero.
    """
    lines = list(re.compile(r"(?m)^.*$").finditer(text, note["start"], note["end"]))
    header = (STOCK_HEADERS if table_kind == "stock" else ROLL_HEADERS)[kind]
    hits = [(i, row) for i, row in enumerate(lines) if re.sub(r"\s+", "", row.group()) == header]
    if len(hits) != 1:
        words = (["账面余额","账面价值","坏账准备" if kind=="AR" else "跌价准备","期初余额","期末余额"] if table_kind=="stock"
                 else ["期初余额","期末余额","计提","转回","核销","转销","其他","本期变动"])
        clues = [row for row in lines if sum(word in row.group() for word in words)>=2][:4]
        return {"status": "pending", "reason": "unique_explicit_" + table_kind + "_column_roles_pending",
                "uninterpreted_native_header_clues": [{**locator(text,row.start(),row.end()),"brief_text":row.group().strip()[:200]} for row in clues]}
    i, h = hits[0]
    preamble = text[max(note["start"], lines[max(0,i-4)].start()):h.start()]
    dense_preamble = re.sub(r"\s+","",preamble)
    if (re.search(r"20\d{2}(?:年\d{1,2}月\d{1,2}日?|[-/]\d{1,2}[-/]\d{1,2})",dense_preamble)
            or re.search(r"半年度|半年|季度",dense_preamble)):
        return {"status":"pending","reason":"table_explicit_date_or_nonannual_period_mapping_pending","header":locator(text,h.start(),h.end())}
    printed_years = re.findall(r"(20\d{2})(?:年|[-/]\d{1,2}[-/]\d{1,2})",dense_preamble)
    printed_years += re.findall(r"(?m)^\s*(20\d{2})\s*$",preamble)
    if any(int(value) != year for value in printed_years):
        return {"status": "pending", "reason": "table_printed_year_conflict_pending", "header": locator(text,h.start(),h.end())}
    all_preceding = [row for row in lines[:i] if row.group().strip()]
    partial = re.search(r"按\s*(?:组\s*合|单\s*项)",text[note["start"]:h.start()])
    if partial:
        start=note["start"]+partial.start();end=note["start"]+partial.end()
        return {"status":"pending","reason":"known_partial_population_ancestor_scope_pending",
                "header":locator(text,h.start(),h.end()),"partial_ancestor":locator(text,start,end)}
    scope_caption = next((row for row in reversed(all_preceding) if not re.search(r"单位|币种",row.group())),None)
    scope_name = re.sub(r"\s+","",scope_caption.group()) if scope_caption else ""
    allowed = ({"AR":{"应收账款分类披露","应收账款按坏账计提方法分类披露"},"INV":{"存货分类"}} if table_kind=="stock"
               else {"AR":{"坏账准备的情况"},"INV":{"存货跌价准备"}})[kind]
    direct_note = table_kind=="stock" and scope_caption and scope_caption.start()==note["start"]
    if not direct_note and scope_name not in allowed:
        return {"status":"pending","reason":"whole_asset_table_population_scope_pending","header":locator(text,h.start(),h.end()),
                "scope_caption":({**locator(text,scope_caption.start(),scope_caption.end()),"brief_text":scope_caption.group().strip()[:200]} if scope_caption else None)}
    preceding = all_preceding[-2:]
    unit_start = preceding[0].start() if preceding else h.start()
    local = text[unit_start:h.start()]
    declarations=[row for row in preceding if re.search(r"单位|币种",row.group())]
    declared=re.sub(r"\s+","","".join(row.group() for row in declarations)).replace("：",":").replace("，",",")
    unit_match=re.fullmatch(r"单位:(人民币)?(百万元|万元|千元|元),?(?:币种:(人民币))?",declared)
    if not unit_match or not (unit_match[1] or unit_match[3]):
        return {"status": "pending", "reason": "table_local_CNY_unit_pending", "header": locator(text, h.start(), h.end())}
    unit = unit_match[2]; multiplier = UNIT_MULTIPLIERS[unit]
    following = lines[i+1:]
    # Stop before any next explicit table header or numbered subsection.
    stop = next((j for j, row in enumerate(following) if re.match(r"\s*(?:项目|类别|[（(]?\d+[）)]?[、.．])", row.group())), len(following))
    totals = [row for row in following[:stop] if re.match(r"^\s*合\s*计\s+", row.group())]
    if len(totals) != 1: return {"status": "pending", "reason": "unique_same_table_total_row_pending", "header": locator(text, h.start(), h.end())}
    row = totals[0]; payload = re.sub(r"^\s*合\s*计\s+", "", row.group())
    tokens = NUMBER.findall(payload)
    roles = STOCK_ROLES if table_kind == "stock" else ROLL_ROLES
    if kind == "INV" and table_kind == "roll": roles = ["inventory_disposal_or_consumption" if role == "writeoff" else role for role in roles]
    if len(tokens) != len(roles) or re.sub(NUMBER, "", payload).strip():
        return {"status": "pending", "reason": "exact_complete_numeric_columns_pending", "row": locator(text, row.start(), row.end())}
    try: facts = {role: decimal_fact(raw, multiplier) for role, raw in zip(roles, tokens)}
    except ValueError as exc: return {"status": "pending", "reason": str(exc), "row": locator(text, row.start(), row.end())}
    if table_kind == "stock":
        checks = {period: reconcile(facts, {period+"_gross":1,period+"_provision":-1,period+"_net":-1}) for period in ["closing", "fiscal_opening"]}
    else:
        reduction = "writeoff" if kind == "AR" else "inventory_disposal_or_consumption"
        checks = {"provision_rollforward": reconcile(facts, {"fiscal_opening_provision":1,"charge":1,"reversal":-1,reduction:-1,"other_increase":1,"other_decrease":-1,"closing_provision":-1})}
    return {"status": "printed_amount_facts_bound", "scope": "same_consolidated_note_table", "asset": kind, "table_kind": table_kind,
            "unit": unit, "currency": "CNY", "report_fiscal_year": year,
            "closing_stock_asof": str(year)+"-12-31", "opening_stock_asof": str(year)+"-01-01",
            "opening_role": "sameFY_fiscal_opening", "opening_literal_date_printed": False,
            "period_binding_method": "bound_current_fullannual_report_and_explicit_closing_opening_role_header",
            "unit_source": locator(text,declarations[0].start(),declarations[-1].end()),
            "whole_asset_population_scope_caption":locator(text,scope_caption.start(),scope_caption.end()),
            "header": locator(text,h.start(),h.end()), "row": locator(text,row.start(),row.end()),
            "facts": facts, "source_reconciliations": checks,
            "cash_collection_or_true_inventory_release_certified": False,
            "inventory_transfer_out_classified_as_bad": False}


def consume_text(text, case):
    year = int(case["report_date"][:4])
    if case["report_date"] != "2025-12-31": raise ValueError("fixed_FY2025_fullannual_source_only")
    first_page = text.split("===SOURCE_PAGE:2===",1)[0]
    captions = re.findall(r"(20\d{2})年?年度报告",re.sub(r"\s+","",first_page))
    if not captions or set(captions)!={str(year)}:raise ValueError("native_first_page_current_annual_year_pending")
    notes, pending = sections(text)
    facts = {}
    for kind, note in notes.items():
        tables = {role: table_fact(text,note,kind,role,year) for role in ["stock", "roll"]}
        facts[kind] = {"note": note["note"], "scope_header": note["scope_header"], "tables": tables}
        pending.extend(kind+"_"+value["reason"] for value in tables.values() if value["status"] == "pending")
        pending.extend(kind+"_"+check["status"] for value in tables.values() for check in value.get("source_reconciliations",{}).values() if check["status"] != "source_display_intervals_overlap")
    # Source facts never clear accounting/version barriers or prove cash release.
    pending.extend(["policy_scope_version_comparability_unreviewed_pending", "net_decline_cause_and_actual_cash_release_unverified"])
    return {"status": "SOURCE_FACTS_ONLY_NO_ELIGIBILITY", "source_identity": {k:case[k] for k in ["code","announcement_id","report_date","source_record_sha256","pdf_sha256","retained_text_sha256","edition","retained_text_path","published_at","pdf_url"] if k in case},
            "source_fact_tables": facts, "pending_reasons": sorted(set(pending)),
            "original_metadata_flags": case.get("original_metadata_flags"),
            "original_metadata_flags_present": "original_metadata_flags" in case, "empty_flags_mean_clean": False,
            "known_revision_or_later_report_metadata": case.get("known_revision_or_later_report_metadata"),
            "latest_annual_version_metadata": case.get("latest_annual_version_metadata"),
            "scope_policy_restatement_version_status": case.get("scope_policy_restatement_version_status"),
            "known_version_events_or_later_reports_cleared": False,
            "no_factor_or_financial_mask_defined": True}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--protocol",type=Path,default=OUT/"consumer_protocol_draft.json")
    parser.add_argument("--protocol-sha"); parser.add_argument("--execute",action="store_true"); args=parser.parse_args()
    raw=args.protocol.read_bytes();p=json.loads(raw)
    if not args.execute:
        print(json.dumps({"status":"DEFERRED_SOURCE_ONLY_NO_BODY_OR_AMOUNT_READ","protocol_sha256":digest(raw)}));return
    if (not args.protocol_sha or digest(raw)!=args.protocol_sha or digest(Path(__file__).read_bytes())!=p["consumer_sha256"]
            or p["status"]!="FROZEN_SOURCE_ONLY20_CONSUMER_AWAIT_ROOT_EXECUTION"):
        raise ValueError("Root-frozen final source consumer protocol/runtime required")
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p["deadline_utc"]):raise ValueError("human_deadline_reached")
    output=Path(p["output_path"])
    if output.exists():raise ValueError("source_receipt_exists_no_implicit_replay")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri()+"?mode=ro",uri=True) as registry:
        event=registry.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?",(p["registration_event_prefix"]+args.protocol_sha,p["registration_subject"])).fetchone()
        if not event or json.loads(event[0]).get("new_contexts")!=0:raise ValueError("root_source_only_freeze_required_before_body")
    for item in p["bindings"].values():bound(item)
    plan=json.loads(bound(p["bindings"]["source_plan"],retain=True));cases=plan["selected_cases"]
    if len(cases)!=20:raise ValueError("fixed20_source_count_changed")
    identity=[{k:case[k] for k in ["code","announcement_id","report_date","source_record_sha256"]} for case in cases]
    if digest(canonical(identity))!=p["selected_identity_sha256"]:raise ValueError("fixed_ordered20_identity_changed")
    api=p["bindings"]["source_reader"];spec=importlib.util.spec_from_file_location("bound_complete_source_reader",api["path"])
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    indices=plan["bindings"]["source_indices"]
    indexes={item["path"]:json.loads(bound(item,retain=True)) for item in indices.values()}
    records=[]
    with sqlite3.connect(Path(p["bindings"]["capture_db"]["path"]).resolve().as_uri()+"?mode=ro",uri=True) as db:
        for original in cases:
            case=dict(original);case["source_index_ref"]=indices[case["source_index_binding_key"]]
            try:
                document,binding,text=reader.read_case(case,db,indexes)
                records.append(consume_text(text,case));del document,binding,text
            except ValueError as error:
                records.append({"status":"source_binding_or_format_pending","source_identity":{k:case[k] for k in identity[0]},"pending_reasons":[str(error)]})
    result={"status":"COMPLETED_SOURCE_ONLY20_NOTE_FACTS","protocol_sha256":args.protocol_sha,"consumer_sha256":p["consumer_sha256"],
            "plan_binding":p["bindings"]["source_plan"],"records":records,"new_contexts":0,"official_calls":0,"source_masks_or_original_records_changed":False,
            "full_text_copies":0,"new_daily_stock_data":0,"new_factor_or_financial_eligibility_defined":False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"records":len(records),"output_sha256":digest(output.read_bytes())}))


if __name__=="__main__":main()
