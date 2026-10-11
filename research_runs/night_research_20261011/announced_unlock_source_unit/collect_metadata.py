"""One frozen public keyword window. No PDF, price, factor or label execution."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
KEYWORD = "限售股上市流通"
WINDOW = ("20260921", "20260927")


class SourceTransportPending(ValueError): pass
class SourceBudgetPending(ValueError): pass


def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def bound(item):
    path = Path(item["path"])
    if sha(path) != item["sha256"]: raise ValueError("frozen_unlock_source_binding_changed:"+path.name)
    return path


def title_classification(record):
    """Provisional title type; preserve original metadata and infer no shares."""
    title = re.sub(r"<[^>]*>", "", str(record.get("title", "")))
    if re.search(r"核查意见|法律意见|见证意见|专项意见|律师|保荐机构", title):
        kind = "supporting_intermediary_opinion_title"
    elif re.search(r"股权激励|限制性股票|员工持股|激励对象|股票期权", title):
        kind = "employee_or_incentive_unlock_title_separate_scope"
    elif re.search(r"更正|修订|取消|延期|变更", title):
        kind = "revision_cancellation_or_reschedule_title_pending"
    elif KEYWORD in title and "公告" in title:
        kind = "company_primary_notice_title_candidate_body_pending"
    else:
        kind = "unknown_title_scope_pending"
    return {"kind": kind, "body_type_verified": False,
            "actual_circulating_supply_or_tradability_inferred": False}


def source_order(rows):
    ordered=sorted(rows,key=lambda r:(r["published_at"][:10],r["symbol"],r["announcement_id"]))
    classified=[{"announcement_id":r["announcement_id"],"symbol":r["symbol"],**title_classification(r)} for r in ordered]
    primary=[r for r in ordered if title_classification(r)["kind"]=="company_primary_notice_title_candidate_body_pending"]
    return ordered,classified,primary[:3]


def keyword_metadata(source, session, *, deadline_reached=lambda: False):
    """Reuse original record/pagination checks; isolate keyword and hard calls."""
    attempts = []
    first_packet = {}
    class KeywordSession:
        def post(self, url, *, data, timeout):
            if deadline_reached() or len(attempts) >= 3:
                raise SourceBudgetPending("fixed_three_POST_or_deadline_budget_reached")
            if (url != source.QUERY_URL or data.get("stock") != "" or data.get("plate") != ""
                    or data.get("category") != "" or data.get("column") != "szse"
                    or data.get("seDate") != "2026-09-21~2026-09-27"
                    or data.get("pageSize") != 30 or data.get("pageNum") not in (1, 2, 3)):
                raise ValueError("outside_exact_keyword_metadata_window")
            request = dict(data); request["searchkey"] = KEYWORD
            item = {"page": request["pageNum"], "request_sha256": hashlib.sha256(canonical(request)).hexdigest()}
            attempts.append(item)
            try:
                response = session.post(url, data=request, timeout=10, allow_redirects=False)
                if 300 <= response.status_code < 400:
                    raise ValueError("metadata_redirect_response_not_accepted")
                response.raise_for_status()  # prevent inherited HTTP retry/sleep
                item.update(http_status=response.status_code, response_sha256=hashlib.sha256(response.content).hexdigest())
                if request["pageNum"]==1:
                    payload=response.json()
                    if isinstance(payload,dict):first_packet.update(payload)
                return response
            except Exception as error:
                item.update(error_type=type(error).__name__, error=str(error)[:512])
                # The old _page retries known request exception type names.
                # Preserve cause but stop this source unit after this one call.
                raise SourceTransportPending(type(error).__name__+":"+str(error)[:512]) from error
    previous = source.CATEGORIES.get("", None); had_blank = "" in source.CATEGORIES
    source.CATEGORIES[""] = ""
    try:
        packet = source.discover_window(*WINDOW, "", max_days=7, max_announcements=90,
                                        timeout=10, page_delay=0, session=KeywordSession(), page_size=30)
        rows = packet["filings"]
        ordered,classifications,selected=source_order(rows)
        return {"status": "KEYWORD_METADATA_WINDOW_COMPLETE_SOURCE_LIMITED", "query_complete": packet["query_complete"],
                "total_announcements": packet["total_announcements"], "pages": packet["pages"],
                "metadata": ordered, "title_classification_inventory": classifications,
                "rejected": packet["rejected"], "attempts": attempts,
                "planned_body_first3": selected, "body_consumed": False,
                "body_sample_scope":"complete_keyword_window_after_provisional_title_filter",
                "full_channel_or_tradable_universe_certified": False}
    except (source.WindowTooDense, source.PaginationUnstable, SourceTransportPending, SourceBudgetPending, ValueError) as error:
        if isinstance(error,source.WindowTooDense):
            accepted=[]; rejected=[]; counts={}
            rows=first_packet.get("announcements") or []
            for raw in rows:
                ident=str(raw.get("announcementId") or "") if isinstance(raw,dict) else ""
                counts[ident]=counts.get(ident,0)+1
            for raw in rows:
                try:
                    if not isinstance(raw,dict):raise ValueError("nonobject_firstpage_row")
                    if counts[str(raw.get("announcementId") or "")]>1:raise ValueError("duplicate_firstpage_announcement_identity_pending")
                    accepted.append(asdict(source._record(raw,source._day(WINDOW[0]),source._day(WINDOW[1]),"")))
                except (ValueError,OverflowError) as exc:
                    rejected.append({"announcement_id":str(raw.get("announcementId") or "") if isinstance(raw,dict) else "", "reason":str(exc)})
            ordered,classified,selected=source_order(accepted)
            return {"status":"SOURCE_PENDING_COVERAGE_PARTIAL_FIRST_PAGE_METADATA_RETAINED",
                    "query_complete":False,"coverage_partial":True,"total_announcements":error.total,
                    "returned_first_page_rows":len(rows),"metadata":ordered,"rejected":rejected,
                    "title_classification_inventory":classified,"planned_body_first3":selected,
                    "body_sample_scope":"validated_returned_first_page_only_not_whole_window_first3",
                    "attempts":attempts,"error_type":type(error).__name__,"error":str(error),
                    "date_window_split_or_keyword_variants_attempted":False,"body_consumed":False,
                    "economic_failure_claimed":False,"full_channel_or_tradable_universe_certified":False}
        return {"status": "SOURCE_PENDING_KEYWORD_METADATA_ONLY", "query_complete": False,
                "total_announcements": getattr(error, "total", None), "metadata": [], "planned_body_first3": [],
                "attempts": attempts, "error_type": type(error).__name__, "error": str(error)[:512],
                "date_window_split_or_keyword_variants_attempted": False, "body_consumed": False,
                "economic_failure_claimed": False, "full_channel_or_tradable_universe_certified": False}
    finally:
        if had_blank: source.CATEGORIES[""] = previous
        else: source.CATEGORIES.pop("", None)


def main():
    cli=argparse.ArgumentParser(); cli.add_argument("--protocol", type=Path, default=OUT/"protocol.json")
    cli.add_argument("--protocol-sha"); cli.add_argument("--execute", action="store_true"); args=cli.parse_args()
    if not args.execute:
        print(json.dumps({"status":"DEFERRED_UNLOCK_KEYWORD_NO_HTTP_PDF_FACTOR_OR_LABEL_CALLS"})); return
    if not args.protocol_sha or sha(args.protocol)!=args.protocol_sha:
        raise ValueError("exact_root_frozen_unlock_source_protocol_required")
    p=json.loads(args.protocol.read_text(encoding="utf-8"))
    if datetime.now(timezone.utc)>=datetime.fromisoformat(p["deadline_utc"]): raise ValueError("human_deadline_reached")
    output=Path(p["output_path"])
    if output.exists(): raise ValueError("unlock_source_receipt_exists_no_implicit_retry")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri()+"?mode=ro",uri=True) as db:
        event=db.execute("SELECT payload FROM research_events WHERE kind IN ('source_repair','execution_repair') AND event_id=? AND subject=?",(p["event_prefix"]+args.protocol_sha,p["registration_subject"])).fetchone()
        if not event or json.loads(event[0]).get("new_contexts")!=0: raise ValueError("root_zero_context_source_freeze_required")
    for ref in [*p["runtime_bindings"].values(),*p["external_code"].values()]:bound(ref)
    if p["query"]!={"start":"20260921","end":"20260927","category":"","stock":"","plate":"","searchkey":KEYWORD,"page_size":30,"max_pages":3,"max_total":90}:
        raise ValueError("fixed_unlock_query_contract_changed")
    sys.path.insert(0,p["external_workspace"])
    source=importlib.import_module("factor_workflow.filings_market_index")
    if Path(source.__file__).resolve()!=bound(p["external_code"]["market_index"]).resolve(): raise ValueError("market_index_import_drift")
    import requests
    with requests.Session() as session:
        result=keyword_metadata(source,session,deadline_reached=lambda:datetime.now(timezone.utc)>=datetime.fromisoformat(p["deadline_utc"]))
    result.update(protocol_sha256=args.protocol_sha,completed_at_utc=datetime.now(timezone.utc).isoformat(),
                  new_contexts=0,official_calls=0,PDF_calls=0,new_candidate_or_values_or_labels=False,
                  old_data_or_masks_or_registry_or_state_changed=False,
                  body_requires_exact_selected_metadata_binding_and_root_start=True)
    tmp=output.with_suffix(".json.tmp");tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8");tmp.replace(output)
    print(json.dumps({"status":result["status"],"HTTP_attempts":len(result["attempts"]),"metadata_rows":len(result["metadata"]),"output_sha256":sha(output)}))


if __name__=="__main__":main()
