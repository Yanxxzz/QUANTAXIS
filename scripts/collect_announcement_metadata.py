"""Finite CNInfo metadata only, reusing the retained market pagination code.

The empty-category adapter is in memory; external indices/code are read-only.
One SQLite store retains each metadata identity once plus query/page provenance.
No PDF, financial extraction, quote, label, mask or account operation occurs.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def object_sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bound(binding):
    path = Path(binding["path"])
    if sha(path) != binding["sha256"]:
        raise ValueError("Bound file changed: " + str(path))
    return path


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(canonical(value) + "\n", encoding="utf-8")
    temporary.replace(path)


class SourceBudgetPending(ValueError):
    pass


def exception_diagnostics(error):
    """Bounded exception chain only; no environment, request or header dump."""
    chain, seen, queue = [], set(), [error]
    while queue and len(chain) < 8:
        item = queue.pop(0)
        if id(item) in seen or not isinstance(item, BaseException):
            continue
        seen.add(id(item))
        message = re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", str(item))[:2000]
        chain.append({"type": type(item).__name__, "message": message,
                      "errno": getattr(item, "errno", None), "winerror": getattr(item, "winerror", None)})
        queue.extend(x for x in (item.__cause__, item.__context__, getattr(item, "reason", None)) if isinstance(x, BaseException))
        queue.extend(x for x in item.args if isinstance(x, BaseException))
    winerrors = sorted({int(m) for item in chain for m in re.findall(r"WinError\s+(\d+)", item["message"])}
                       | {item["winerror"] for item in chain if isinstance(item["winerror"], int)})
    return {"chain": chain, "winerror_codes": winerrors,
            "HTTP_status": getattr(getattr(error, "response", None), "status_code", None)}


def window_failure_action(error, first, last):
    """Transport cannot become date-density evidence or new child queries."""
    details = exception_diagnostics(error)
    transport_types = {"ConnectionError", "ProxyError", "ConnectTimeout", "ReadTimeout", "Timeout", "SSLError", "ChunkedEncodingError"}
    if (str(error).startswith("CNInfo page transport failed after three attempts:")
            or any(item["type"] in transport_types for item in details["chain"])):
        return "stop_source_pending"
    return "split" if first < last else "unresolved"


def pending_window(db, namespace=None):
    where, params = "status='PENDING'", ()
    if namespace:
        where += " AND json_extract(query_json,'$.repair_namespace')=?"
        params = (namespace,)
    return db.execute("SELECT id,query_json FROM windows WHERE " + where +
                      " ORDER BY CASE WHEN query_json LIKE '%\"category\":\"\"%' THEN 1 ELSE 0 END,query_json LIMIT 1", params).fetchone()


def calendar_availability(published, calendar_data):
    dates = calendar_data.get("dates", [])
    coverage = calendar_data.get("coverage_window", {})
    at = bisect_right(dates, published)
    if not (coverage.get("start", "9999") <= published <= coverage.get("end", "") and at < len(dates)):
        return None, "pending_independent_calendar_scope_or_tail"
    next_day = dates[at]
    verified = calendar_data.get("verified_reference_windows", [])
    if calendar_data.get("actual_verified") is True:
        verified = [coverage]
    elif calendar_data.get("historical_vendor_reference_verified") is True and isinstance(calendar_data.get("actual_verified_window"), dict):
        # This explicit accepted union bridges the old/new vendor range edge.
        # Its end still excludes future scheduled sessions such as Oct12.
        verified = [*verified, calendar_data["actual_verified_window"]]
    if any(w.get("start", "9999") <= published < next_day <= w.get("end", "") for w in verified):
        return next_day, "next_verified_reference_session"
    # Keep the useful planned date in the label, but NULL actual availability.
    return None, "next_planned_session:" + next_day + ":actual_pending"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--expected-protocol-sha", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--calendar", type=Path)
    ap.add_argument("--calendar-sha")
    ap.add_argument("--repair", type=Path)
    ap.add_argument("--expected-repair-sha")
    ap.add_argument("--attempt-plan", type=Path)
    ap.add_argument("--expected-attempt-plan-sha")
    args = ap.parse_args()
    path = args.protocol.resolve()
    if sha(path) != args.expected_protocol_sha:
        raise ValueError("Root-frozen metadata protocol changed")
    p = json.loads(path.read_text(encoding="utf-8"))
    attempt_plan, namespace = None, None
    if bool(args.attempt_plan) != bool(args.expected_attempt_plan_sha):
        raise ValueError("Source frontier needs its explicit root-reviewed SHA")
    if args.attempt_plan:
        if sha(args.attempt_plan) != args.expected_attempt_plan_sha:
            raise ValueError("Source attempt frontier changed")
        attempt_plan = json.loads(args.attempt_plan.read_text(encoding="utf-8"))
        if (attempt_plan["original_protocol_sha256"] != args.expected_protocol_sha
                or attempt_plan["initial_queries"] != p["initial_queries"]
                or attempt_plan["old_HTTP_cap"] != p["limits"]["maximum_HTTP_attempts"]
                or attempt_plan["new_HTTP_cap"] != 2000):
            raise ValueError("Source frontier/query/explicit budget contract mismatch")
        namespace = attempt_plan["namespace"]
        if not re.fullmatch(r"[A-Z0-9_]{1,64}", namespace):
            raise ValueError("Invalid source repair namespace")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(attempt_plan["no_new_start_after"]):
            raise ValueError("No new source start after the frozen night deadline")
    if bool(args.repair) != bool(args.expected_repair_sha):
        raise ValueError("Runtime repair needs its explicit root-reviewed SHA")
    if args.repair:
        if sha(args.repair) != args.expected_repair_sha:
            raise ValueError("Execution repair changed")
        repair = json.loads(args.repair.read_text(encoding="utf-8"))
        if repair["original_protocol_sha256"] != args.expected_protocol_sha:
            raise ValueError("Execution repair belongs to another source unit")
        previous = bound(repair["previous_collector"])
        if sha(previous) != p["collector"]["sha256"] or bound(repair["new_collector"]).resolve() != Path(__file__).resolve():
            raise ValueError("Execution repair does not preserve the exact original runtime")
    else:
        bound(p["collector"])
    for binding in p["external_code"].values():
        bound(binding)
    for binding in p["original_G_bindings"].values():
        bound(binding)
    sys.path.insert(0, p["external_workspace"])
    source = importlib.import_module("factor_workflow.filings_market_index")
    if (str(Path(source.__file__).resolve()) != str(Path(p["external_code"]["market_index"]["path"]).resolve())
            or source.QUERY_URL != p["endpoint"] or source.MAX_SAFE_PAGES != 100 or source.PAGE_SIZE != 30):
        raise ValueError("Retained pagination/API implementation drifted")
    # The verified blank-stock, blank-plate payload/pagination stays intact.
    # Only this additional category key maps to the public API's blank filter.
    source.CATEGORIES[""] = ""
    source.SLUGS[""] = "all"
    calendar, calendar_binding, calendar_data = [], None, {}
    if bool(args.calendar) != bool(args.calendar_sha):
        raise ValueError("Calendar needs both path and SHA")
    if args.calendar:
        calendar_binding = {"path": str(args.calendar.resolve()), "sha256": args.calendar_sha}
        value = json.loads(bound(calendar_binding).read_text(encoding="utf-8"))
        calendar = value.get("dates", [])
        calendar_data = value
        if (not calendar or calendar != sorted(set(calendar))
                or any(date.fromisoformat(d).isoformat() != d for d in calendar)):
            raise ValueError("Explicit independent trading-session list required")
    if attempt_plan and attempt_plan.get("calendar_binding"):
        if calendar_binding != attempt_plan["calendar_binding"]:
            raise ValueError("Source frontier needs the exact accepted calendar binding")
        bound(attempt_plan["calendar_receipt"])
    out = path.parent
    dbpath = out / "metadata.sqlite3"
    if dbpath.exists() and not args.resume:
        raise ValueError("Source unit exists; inspect its process before explicit --resume")
    if attempt_plan:
        # Only the first start binds the exact post-diagnostic state. Later
        # explicit resumes verify the immutable plan recorded in the journal.
        check = sqlite3.connect(dbpath)
        prior_frontier = check.execute("SELECT value FROM meta WHERE key=?", ("attempt_plan:" + namespace,)).fetchone()
        check.close()
        if prior_frontier and prior_frontier[0] != args.expected_attempt_plan_sha:
            raise ValueError("The recorded source frontier changed")
        if not prior_frontier:
            bound(attempt_plan["metadata_before"])
    db = sqlite3.connect(dbpath)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS announcements(id TEXT PRIMARY KEY,metadata_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS windows(id TEXT PRIMARY KEY,query_json TEXT NOT NULL,status TEXT NOT NULL,proof_json TEXT);
      CREATE TABLE IF NOT EXISTS pages(seq INTEGER PRIMARY KEY,window_id TEXT NOT NULL,query_json TEXT NOT NULL,response_sha256 TEXT,error TEXT);
      CREATE TABLE IF NOT EXISTS membership(window_id TEXT NOT NULL,id TEXT NOT NULL,PRIMARY KEY(window_id,id));
      CREATE TABLE IF NOT EXISTS rejected(window_id TEXT NOT NULL,row_sha TEXT NOT NULL,row_json TEXT NOT NULL,PRIMARY KEY(window_id,row_sha));
    """)
    prior = db.execute("SELECT value FROM meta WHERE key='protocol_sha256'").fetchone()
    if prior and prior[0] != args.expected_protocol_sha:
        raise ValueError("Checkpoint belongs to another protocol")
    db.execute("INSERT OR IGNORE INTO meta VALUES('protocol_sha256',?)", (args.expected_protocol_sha,))
    db.commit()
    started = time.monotonic()
    budget = dict(p["limits"])
    if attempt_plan:
        budget["maximum_HTTP_attempts"] = attempt_plan["new_HTTP_cap"]
        db.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", ("attempt_plan:" + namespace, args.expected_attempt_plan_sha))
        db.commit()

    def accept(row, window_id):
        ident = str(row["announcement_id"])
        # category is query membership, not part of the announcement identity.
        metadata = {key: row.get(key) for key in ("symbol", "announcement_id", "title", "published_at", "detail_url", "pdf_url", "time_precision")}
        serialized = canonical(metadata)
        old = db.execute("SELECT metadata_json FROM announcements WHERE id=?", (ident,)).fetchone()
        if old and old[0] != serialized:
            raise ValueError("Conflicting metadata for announcement " + ident)
        db.execute("INSERT OR IGNORE INTO announcements VALUES(?,?)", (ident, serialized))
        db.execute("INSERT OR IGNORE INTO membership VALUES(?,?)", (window_id, ident))

    def enqueue(query):
        key = object_sha(query)
        db.execute("INSERT OR IGNORE INTO windows VALUES(?,?,'PENDING',NULL)", (key, canonical(query)))
        return key

    # Legacy reuse is bounded to the requested dates and stores no source text
    # or whole index copy. Metadata duplicates merge by exact announcement ID.
    with db:
        for binding in p["legacy_indices"]:
            legacy_path = bound(binding)
            packet = json.loads(legacy_path.read_text(encoding="utf-8"))
            if (packet.get("query_complete") is not True or packet["total_announcements"] != len(packet["filings"]) + len(packet["rejected"])):
                raise ValueError("Incomplete retained metadata window")
            key = "legacy:" + binding["sha256"]
            db.execute("INSERT OR IGNORE INTO windows VALUES(?,?,'REUSED',?)", (key, canonical(packet["query"]), canonical(binding)))
            for row in packet["filings"]:
                if p["reuse_window"]["start"] <= row["published_at"][:10] <= p["reuse_window"]["end"]:
                    accept(row, key)
        for query in p["initial_queries"]:
            enqueue({**query, "repair_namespace": namespace} if namespace else query)

    import requests
    native_session = requests.Session()
    active_id, active_query = None, None

    class Session:
        def post(self, url, *, data, timeout):
            count = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            if count >= budget["maximum_HTTP_attempts"] or time.monotonic() - started > budget["maximum_run_seconds"]:
                raise SourceBudgetPending("Frozen HTTP/time budget reached; remaining windows are pending")
            if (url != p["endpoint"] or data.get("stock") != "" or data.get("plate") != ""
                    or data.get("searchkey") != "" or data.get("column") != "szse"):
                raise ValueError("Outside frozen whole-market metadata query")
            seq = count + 1
            db.execute("INSERT INTO pages VALUES(?,?,?,NULL,NULL)", (seq, active_id, canonical(data)))
            db.commit()
            if seq == 1 or seq % 10 == 0:
                progress = {"pid": os.getpid(), "active_query": active_query, "HTTP_attempts": seq,
                            "windows": dict(db.execute("SELECT status,COUNT(*) FROM windows GROUP BY status"))}
                save(out / "progress.json", progress)
                print(canonical(progress), flush=True)
            try:
                response = native_session.post(url, data=data, timeout=timeout)
                digest = hashlib.sha256(response.content).hexdigest()
                db.execute("UPDATE pages SET response_sha256=? WHERE seq=?", (digest, seq))
                db.commit()
                return response
            except Exception as exc:
                db.execute("UPDATE pages SET error=? WHERE seq=?", (canonical(exception_diagnostics(exc)), seq))
                db.commit()
                raise

    pending_budget = None
    try:
        while True:
            row = pending_window(db, namespace)
            if not row:
                break
            active_id, raw_query = row
            active_query = json.loads(raw_query)
            fetched = db.execute("SELECT COUNT(*) FROM windows WHERE status IN ('COMPLETE','SPLIT','UNRESOLVED')").fetchone()[0]
            if fetched >= budget["maximum_query_windows"]:
                pending_budget = "Frozen window budget reached"
                break
            try:
                packet = source.discover_window(active_query["start_date"], active_query["end_date"], active_query["category"],
                    session=Session(), timeout=budget["timeout_seconds"], page_delay=budget["page_delay_seconds"],
                    max_announcements=budget["maximum_announcements_per_window"])
                with db:
                    for item in packet["filings"]:
                        accept(item, active_id)
                    for item in packet["rejected"]:
                        db.execute("INSERT OR IGNORE INTO rejected VALUES(?,?,?)", (active_id, object_sha(item), canonical(item)))
                    proof = {"total_announcements": packet["total_announcements"], "pages": packet["pages"],
                             "accepted_rows": len(packet["filings"]), "rejected_rows": len(packet["rejected"]),
                             "complete_packet_sha256": object_sha(packet), "query_complete": packet["query_complete"]}
                    db.execute("UPDATE windows SET status='COMPLETE',proof_json=? WHERE id=?", (canonical(proof), active_id))
            except (source.WindowTooDense, source.PaginationUnstable) as exc:
                left = datetime.strptime(active_query["start_date"], "%Y%m%d").date()
                right = datetime.strptime(active_query["end_date"], "%Y%m%d").date()
                with db:
                    proof = {"error": type(exc).__name__ + ": " + str(exc), "diagnostics": exception_diagnostics(exc)}
                    action = window_failure_action(exc, left, right)
                    if action == "split":
                        middle = left + timedelta(days=(right - left).days // 2)
                        for first, last in ((left, middle), (middle + timedelta(days=1), right)):
                            enqueue({**active_query, "start_date": first.strftime("%Y%m%d"), "end_date": last.strftime("%Y%m%d")})
                        status = "SPLIT"
                    else:
                        status = "UNRESOLVED"
                    db.execute("UPDATE windows SET status=?,proof_json=? WHERE id=?", (status, canonical(proof), active_id))
                if action == "stop_source_pending":
                    pending_budget = proof["error"]
                    break
            except SourceBudgetPending as exc:
                pending_budget = str(exc)
                break
            except Exception as exc:
                pending_budget = type(exc).__name__ + ": " + str(exc)
                with db:
                    db.execute("UPDATE windows SET status='UNRESOLVED',proof_json=? WHERE id=?",
                               (canonical({"error": pending_budget}), active_id))
                break
            count = db.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
            if count > budget["maximum_unique_metadata_rows"] or dbpath.stat().st_size > budget["maximum_metadata_bytes"]:
                pending_budget = "Frozen metadata row/storage budget reached"
                break
            progress = {"pid": os.getpid(), "last_query": active_query,
                        "windows": dict(db.execute("SELECT status,COUNT(*) FROM windows GROUP BY status")),
                        "unique_metadata_rows": count, "HTTP_attempts": db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]}
            save(out / "progress.json", progress)
            print(canonical(progress), flush=True)
    finally:
        native_session.close()
    # Availability is a separate projection on an explicitly bound calendar.
    # Never infer exchange sessions from observed stock bars.
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS availability(id TEXT PRIMARY KEY,available_session TEXT,status TEXT NOT NULL)")
        for ident, serialized in db.execute("SELECT id,metadata_json FROM announcements").fetchall():
            published = json.loads(serialized)["published_at"][:10]
            available, status = calendar_availability(published, calendar_data)
            db.execute("INSERT OR REPLACE INTO availability VALUES(?,?,?)", (ident, available, status))
    counts = dict(db.execute("SELECT status,COUNT(*) FROM windows GROUP BY status"))
    active_counts = dict(db.execute("SELECT status,COUNT(*) FROM windows WHERE json_extract(query_json,'$.repair_namespace')=? GROUP BY status", (namespace,))) if namespace else counts
    result = {"status": "METADATA_QUERY_RANGE_COMPLETE" if not pending_budget and not active_counts.get("PENDING") and not active_counts.get("UNRESOLVED") else "METADATA_SOURCE_PENDING",
              "protocol_sha256": args.expected_protocol_sha, "windows": counts, "pending_budget": pending_budget,
              "active_namespace": namespace, "active_windows": active_counts,
              "attempt_plan_sha256": args.expected_attempt_plan_sha,
              "shared_HTTP_cap": budget["maximum_HTTP_attempts"],
              "unique_metadata_rows": db.execute("SELECT COUNT(*) FROM announcements").fetchone()[0],
              "rejected_rows": db.execute("SELECT COUNT(*) FROM rejected").fetchone()[0],
              "HTTP_attempts": db.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
              "availability": dict(db.execute("SELECT status,COUNT(*) FROM availability GROUP BY status")),
              "calendar_binding": calendar_binding, "source_mask_applied": False, "full_channel_certification": False,
              "full_PIT_certified": False, "new_definitions": 0, "official_spent": 0, "PDF_or_fulltext_downloads": 0}
    db.close()
    result["metadata"] = {"path": str(dbpath), "sha256": sha(dbpath)}
    result_path = out / ("resume_summary.json" if namespace else "summary.json")
    if result_path.exists():
        raise ValueError("Completed source summary exists; do not overwrite it")
    save(result_path, result)
    print(canonical(result), flush=True)


if __name__ == "__main__":
    main()
