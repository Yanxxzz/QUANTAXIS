"""One new preselected public metadata week; no economic labels or PDF reads."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3
import sys

KEYWORD = "限售股上市流通"
WINDOW = ("20260914", "20260920")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def title_kind(record):
    title = re.sub(r"<[^>]*>", "", record["title"])
    if re.search(r"核查意见|法律意见|见证意见|专项意见|律师|保荐机构", title):
        return "supporting_opinion"
    if re.search(r"股权激励|限制性股票|员工持股|激励对象|股票期权", title):
        return "employee_incentive_separate"
    if re.search(r"更正|修订|取消|延期|变更", title):
        return "revision_reschedule_pending"
    return "primary_title_only_body_pending" if KEYWORD in title and "公告" in title else "unknown_pending"


def collect(source, session, deadline):
    rows, rejected, attempts, seen = [], [], [], set()
    result = {"query_complete": False, "total_announcements": None,
              "status": "SOURCE_PENDING", "metadata": rows, "rejected": rejected,
              "attempts": attempts, "new_contexts": 0, "PDF_calls": 0,
              "official_calls": 0, "economic_failure": False}
    try:
        total = None
        for page in range(1, 4):
            if datetime.now(timezone.utc) >= deadline:
                raise ValueError("night research deadline reached")
            request = {"pageNum": page, "pageSize": 30, "column": "szse",
                       "tabName": "fulltext", "plate": "", "stock": "",
                       "searchkey": KEYWORD, "category": "",
                       "seDate": "2026-09-14~2026-09-20", "sortName": "",
                       "sortType": "", "isHLtitle": "true"}
            attempt = {"page": page, "request_sha256": digest(request)}
            attempts.append(attempt)
            response = session.post(source.QUERY_URL, data=request, timeout=15,
                                    allow_redirects=False)
            attempt["http_status"] = response.status_code
            if 300 <= response.status_code < 400:
                raise ValueError("redirect not followed")
            response.raise_for_status()
            attempt["response_sha256"] = hashlib.sha256(response.content).hexdigest()
            packet = response.json()
            page_total = packet.get("totalAnnouncement")
            if type(page_total) is not int or page_total < 0:
                raise ValueError("invalid total announcement count")
            if total is not None and total != page_total:
                raise ValueError("pagination total changed")
            total = page_total
            result["total_announcements"] = total
            batch = packet.get("announcements") or []
            if not isinstance(batch, list) or len(batch) > 30:
                raise ValueError("invalid metadata page")
            for raw in batch:
                announcement = str(raw.get("announcementId") or "")
                if announcement in seen:
                    raise ValueError("duplicate announcement across pages")
                seen.add(announcement)
                try:
                    rows.append(asdict(source._record(raw, source._day(WINDOW[0]),
                                                     source._day(WINDOW[1]), "")))
                except (ValueError, OverflowError) as error:
                    rejected.append({"announcement_id": announcement, "reason": str(error)[:256]})
            if len(seen) == total:
                result.update(query_complete=True, status="COMPLETE_FIXED_NEW_WEEK_METADATA")
                break
            if not batch or len(seen) > total:
                raise ValueError("incomplete or inconsistent page")
        if not result["query_complete"]:
            result["status"] = "PARTIAL_FIXED_NEW_WEEK_METADATA_RETAINED"
    except Exception as error:
        result.update(status="SOURCE_PENDING_PARTIAL_METADATA_RETAINED",
                      error_type=type(error).__name__, error=str(error)[:384])
    ordered = sorted(rows, key=lambda r: (r["published_at"], r["symbol"], r["announcement_id"]))
    result["metadata"] = ordered
    result["classifications"] = [{"symbol": r["symbol"], "announcement_id": r["announcement_id"],
                                  "title_kind": title_kind(r), "body_verified": False} for r in ordered]
    main = [r for r in ordered if title_kind(r) == "primary_title_only_body_pending"]
    result["next_body_first6"] = main[:6]
    result["selection_scope"] = "first6 primary-title candidates in returned validated metadata; no label/quantity selection"
    result["full_channel_or_tradable_universe_certified"] = False
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--protocol-sha", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("DEFERRED_NO_HTTP_OR_FACTOR_CALLS")
        return
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    if sha(args.protocol) != args.protocol_sha:
        raise ValueError("frozen protocol changed")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    deadline = datetime.fromisoformat(p["deadline_utc"])
    if datetime.now(timezone.utc) >= deadline:
        raise ValueError("human deadline reached")
    output = Path(p["output"])
    if output.exists():
        raise ValueError("receipt exists; no automatic replay")
    for b in p["bindings"].values():
        if sha(b["path"]) != b["sha256"]:
            raise ValueError("source/runtime binding changed")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri()+"?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM research_events WHERE event_id=? AND subject=?",
                         ("new-week-unlock-metadata-freeze:"+args.protocol_sha, p["id"])).fetchone()
        if not row or json.loads(row[0]).get("new_contexts") != 0:
            raise ValueError("zero-context source registration required")
    sys.path.insert(0, p["external_workspace"])
    source = importlib.import_module("factor_workflow.filings_market_index")
    if Path(source.__file__).resolve() != Path(p["bindings"]["index"]["path"]).resolve():
        raise ValueError("index import changed")
    import requests
    with requests.Session() as session:
        result = collect(source, session, deadline)
    result.update(protocol_sha256=args.protocol_sha,
                  completed_at_utc=datetime.now(timezone.utc).isoformat(),
                  prices_factor_values_or_labels_read=False, existing_data_or_masks_changed=False)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "HTTP_calls": len(result["attempts"]),
                      "metadata_rows": len(result["metadata"]), "next_body_rows": len(result["next_body_first6"]),
                      "receipt_sha256": sha(output)}))


if __name__ == "__main__":
    main()
