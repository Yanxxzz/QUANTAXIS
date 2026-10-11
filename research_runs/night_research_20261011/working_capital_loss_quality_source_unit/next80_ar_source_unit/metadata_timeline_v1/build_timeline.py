"""Inert metadata-only publication/version view; no AR qualification or values."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

OUT = Path(__file__).resolve().parent
KEYS = ("code", "announcement_id", "report_date", "source_record_sha256")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def bound(item):
    path = Path(item["path"])
    if sha(path) != item["sha256"]:
        raise ValueError("frozen_metadata_input_changed:" + str(path))
    return path


def day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def calendar_contract(original, recent):
    old = sorted(set(original["dates"]))
    if not old:
        raise ValueError("empty_frozen_original_calendar")
    ranges = [{"start": old[0], "end": old[-1]}, *recent["verified_reference_windows"]]
    ranges = sorted({(r["start"], r["end"]) for r in ranges})
    merged = []
    for start, end in ranges:
        if start > end:
            raise ValueError("invalid_reference_coverage_interval")
        if merged and start <= (date.fromisoformat(merged[-1][1]) + timedelta(days=1)).isoformat():
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    verified = sorted(set(old) | {d for d in recent["dates"] if any(a <= d <= b for a, b in merged)})
    planned = sorted(set(recent.get("planned_sessions", [])) - set(verified))
    return {"sessions": verified, "coverage": merged, "planned": planned,
            "reference_not_perstock_trade_or_emergency_census": True}


def availability(published, calendar, old_available=None):
    result = {"published_at_literal": published, "old_available_date": old_available,
              "same_day_preopen_release_certified": False, "available_reference_session": None,
              "planned_next_session": None, "status": "publication_or_calendar_pending"}
    d = day(published)
    if not d:
        return result
    sessions = calendar["sessions"]
    i = bisect_right(sessions, d)
    nxt = sessions[i] if i < len(sessions) else None
    if nxt and any(a <= d < nxt <= b for a, b in calendar["coverage"]):
        result.update(status="next_verified_reference_session", available_reference_session=nxt)
    else:
        result["planned_next_session"] = next((s for s in calendar["planned"] if s > d), None)
        result["status"] = "planned_next_session_actual_pending" if result["planned_next_session"] else "calendar_coverage_pending"
    return result


def observation(code, ann, published, periods, kind, ref, precision="date", old_available=None):
    periods = sorted({d for v in periods if (d := day(v))})
    return {"code": str(code), "announcement_id": str(ann), "published_at": published,
            "periods": periods, "kind": kind, "source_ref": ref,
            "time_precision": precision or "unknown", "old_available_date": old_available}


def collapse(observations):
    """Conflicting ID metadata is pending; unknown hints never become absence."""
    grouped = defaultdict(list)
    for r in observations:
        grouped[r["announcement_id"]].append(r)
    result = []
    for ann, rows in sorted(grouped.items()):
        codes = {r["code"] for r in rows}
        pubdays = {day(r["published_at"]) for r in rows}
        known_periods = {tuple(r["periods"]) for r in rows if r["periods"]}
        kinds = {r["kind"] for r in rows if r["kind"] in {"original_full", "revised_full", "correction_notice"}}
        precise = set()
        for r in rows:
            if r["time_precision"] == "second":
                try:
                    stamp = datetime.fromisoformat(str(r["published_at"]).replace("Z", "+00:00"))
                    precise.add(stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else str(r["published_at"]))
                except ValueError:
                    precise.add(str(r["published_at"]))
        conflict = len(codes) != 1 or None in pubdays or len(pubdays) != 1 or len(known_periods) > 1 or len(kinds) > 1 or len(precise) > 1
        # Date-only and a precise timestamp on the same day are not different
        # effective days. Preserve both literals; never upgrade to preopen.
        result.append({"announcement_id": ann, "codes": sorted(codes),
                       "published_dates": sorted(d for d in pubdays if d),
                       "published_literals": sorted({str(r["published_at"]) for r in rows}),
                       "periods": sorted({p for r in rows for p in r["periods"]}),
                       "kinds": sorted(kinds), "time_precision": sorted({r["time_precision"] for r in rows}),
                       "old_available_dates": sorted({str(r["old_available_date"]) for r in rows if r["old_available_date"]}),
                       "source_refs": sorted({r["source_ref"] for r in rows}),
                       "status": "same_ID_conflicting_metadata_pending" if conflict else "identity_time_metadata_bound_only",
                       "AR_field_effect": "unknown_pending"})
    return result


def case_map(case, events, calendar):
    relevant = [e for e in events if case["code"] in e["codes"]]
    selected = next((e for e in relevant if e["announcement_id"] == case["announcement_id"]), None)
    clock = availability(case["published_at"], calendar, case["original_recorded_available_date_metadata"])
    if selected is None or selected["status"] != "identity_time_metadata_bound_only":
        clock.update(status="selected_ID_metadata_pending", available_reference_session=None)
    linked = []; unknown = []; other_period = []
    for e in relevant:
        if e["announcement_id"] == case["announcement_id"]:
            continue
        item = {**e, "availability": availability(e["published_dates"][0], calendar) if len(e["published_dates"]) == 1 else None,
                "permanent_code_wide_block": False}
        if e["status"] != "identity_time_metadata_bound_only" or not e["periods"]:
            unknown.append(item)
        elif case["report_date"] in e["periods"]:
            linked.append(item)
        else:
            other_period.append(item)
    successors = [e for e in linked if set(e["kinds"]) & {"original_full", "revised_full"}
                  and e["published_dates"][0] >= day(case["published_at"])]
    boundary = min((e["availability"]["available_reference_session"] for e in successors
                    if e["availability"]["available_reference_session"]), default=None)
    base = {"announcement_id": case["announcement_id"], "from_reference_session": clock["available_reference_session"],
            "until_reference_session_exclusive": boundary, "metadata_source_interval_only": True,
            "field_source_eligibility": False,
            "can_claim_original_is_latest_after_known_full_successor": False if successors else None}
    return {"source_identity": {k: case[k] for k in KEYS}, "base_availability": clock,
            "selected_metadata": selected, "base_interval": base,
            "explicit_period_linked_candidates": linked, "unknown_period_or_conflict_candidates": unknown,
            "other_explicit_period_metadata": other_period,
            "same_day_successor_choice": "competing_or_single_new_version_AR_effect_pending" if successors else "no_successor_in_supplied_metadata_not_absence_certificate",
            "all_channel_latest_certified": False, "AR_source_ready_certified": False,
            "cash_unknown_used_as_gate": False, "unread_revision_None_means_clean": False}


def load_observations(plan, bindings):
    codes = {c["code"] for c in plan["selected_cases"]}
    rows = [observation(c["code"], c["announcement_id"], c["published_at"], [c["report_date"]],
                        "revised_full" if c["edition"] == "REVISED_FULL_REPORT" else "original_full", "plan:" + c["announcement_id"])
            for c in plan["selected_cases"]]
    for r in read(bindings["publication"]["path"])["records"]:
        if r["code"] in codes:
            rows.append(observation(r["code"], r["announcement_id"], r["published_at"], [r.get("report_date")],
                                    "period_publication", "publication:" + str(r["announcement_id"]), old_available=r.get("available_date")))
    for key in ("source_index_0", "source_index_1", "source_index_2", "source_index_3"):
        for r in read(bindings[key]["path"])["documents"]:
            code = str(r.get("code", r.get("symbol")))
            if code not in codes:
                continue
            kind = {"REVISED_FULL_REPORT": "revised_full", "ORIGINAL_FULL_REPORT": "original_full", "CORRECTION_NOTICE": "correction_notice"}.get(r.get("edition"), "unknown")
            rows.append(observation(code, r["announcement_id"], r["published_at"], [r.get("report_date")], kind,
                                    key + ":" + str(r["announcement_id"]), r.get("time_precision")))
    for r in read(bindings["corrections"]["path"])["events"]:
        code = str(r.get("code", r.get("symbol")))
        if code in codes:
            rows.append(observation(code, r["announcement_id"], r["published_at"], r.get("report_dates") or [r.get("report_date")],
                                    "correction_metadata", "corrections:" + str(r["announcement_id"]), r.get("time_precision")))
    with sqlite3.connect(Path(bindings["recent_metadata"]["path"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        sql = "SELECT id,COALESCE(json_extract(metadata_json,'$.code'),json_extract(metadata_json,'$.symbol')),json_extract(metadata_json,'$.announcement_id'),json_extract(metadata_json,'$.published_at'),json_extract(metadata_json,'$.report_date'),json_extract(metadata_json,'$.category'),json_extract(metadata_json,'$.time_precision') FROM announcements WHERE COALESCE(json_extract(metadata_json,'$.code'),json_extract(metadata_json,'$.symbol')) IN (" + ",".join("?" for _ in codes) + ")"
        recent = db.execute(sql, sorted(codes)).fetchall()
        for aid, code, ann, published, period, category, precision in recent:
            rows.append(observation(code, ann or aid, published, [period], "recent_unclassified_metadata", "recent_metadata:" + aid, precision))
        statuses = dict(Counter(r[0] for r in db.execute("SELECT status FROM windows")))
        selected_ids = {r[0] for r in recent}
        availability_status = dict(Counter(status for aid, session, status in db.execute("SELECT id,available_session,status FROM availability") if aid in selected_ids))
        membership = Counter()
        for aid, cat in db.execute("SELECT m.id,json_extract(w.query_json,'$.category') FROM membership m JOIN windows w ON w.id=m.window_id"):
            if aid in selected_ids:
                membership[cat if cat else "unclassified_query"] += 1
    return collapse(rows), {"recent_identity_rows": len(recent), "recent_window_statuses": statuses,
                            "recent_identity_availability_statuses": availability_status,
                            "recent_category_membership_counts": dict(membership),
                            "full_channel_coverage": False, "PENDING_UNRESOLVED_not_discarded": True,
                            "no_title_period_or_AR_effect_inference": True}


def main(argv=None):
    cli = argparse.ArgumentParser(); cli.add_argument("--execute", action="store_true")
    cli.add_argument("--protocol", type=Path, default=OUT / "protocol.json"); cli.add_argument("--protocol-sha")
    args = cli.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_METADATA_MAP_NO_INPUT_OR_BODY_READ"})); return
    if not args.protocol_sha or sha(args.protocol) != args.protocol_sha:
        raise ValueError("root_exact_metadata_map_protocol_required")
    p = read(args.protocol)
    if p["status"] != "FROZEN_SOURCE80_METADATA_TIMELINE_AWAIT_ROOT_EXECUTION" or datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("source_map_freeze_or_deadline_pending")
    output = Path(p["output_path"])
    if output.exists():
        raise ValueError("owned_metadata_map_exists_no_implicit_replay")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        event = db.execute("SELECT payload FROM research_events WHERE kind='execution_repair' AND event_id=? AND subject=?", (p["event_prefix"] + args.protocol_sha, p["registration_subject"])).fetchone()
        if not event or json.loads(event[0]).get("new_contexts") != 0:
            raise ValueError("root_zero_context_map_registration_required")
    if sha(__file__) != p["runtime_sha256"]:
        raise ValueError("frozen_map_runtime_changed")
    for ref in p["bindings"].values():
        bound(ref)
    plan = read(p["bindings"]["source_plan"]["path"]); cases = plan["selected_cases"]
    keys = [{k: c[k] for k in KEYS} for c in cases]
    if len(cases) != 80 or len({c["code"] for c in cases}) != 80 or hashlib.sha256(canonical(keys)).hexdigest() != p["selected_identity_sha256"]:
        raise ValueError("fixed80_identity_or_order_changed")
    calendar = calendar_contract(read(p["bindings"]["original_calendar"]["path"]), read(p["bindings"]["recent_calendar"]["path"]))
    events, coverage = load_observations(plan, p["bindings"])
    records = []; stop = None
    for index, c in enumerate(cases):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
            stop = {"first_uncompleted_index": index, "reason": "human_source_deadline_reached"}; break
        try:
            records.append(case_map(c, events, calendar))
        except Exception as exc:
            stop = {"first_uncompleted_index": index, "source_identity": {k: c[k] for k in KEYS}, "error": type(exc).__name__ + ":" + str(exc)[:512]}; break
    result = {"status": "COMPLETED_SOURCE80_METADATA_VERSION_AVAILABILITY_VIEW" if stop is None else "METADATA_SOURCE_PENDING_NO_AUTO_RESUME",
              "protocol_sha256": args.protocol_sha, "records": records, "execution_stop": stop,
              "source_coverage": coverage, "runtime_sha256": p["runtime_sha256"],
              "new_contexts": 0, "Q_or_values_or_price_labels_read": False, "native_body_or_capture_gzip_read": False,
              "AR_qualification_or_old_masks_changed": False, "all_channel_latest_or_full_PIT_certified": False,
              "planned_sessions_not_actual_verified": calendar["planned"], "automatic_resume": False, "network_or_official_calls": 0}
    temp = output.with_suffix(".json.tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"); temp.replace(output)
    print(json.dumps({"status": result["status"], "records": len(records), "output_sha256": sha(output)}))


if __name__ == "__main__":
    main()
