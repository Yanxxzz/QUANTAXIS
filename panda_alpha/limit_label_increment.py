"""Necessary scoped label persistence increment, not an old study/account replay.

Import has no source I/O. Only read_increment constructs a local Mongo client.
The hash-bound old runtime supplies pure label clock/validation functions only;
its expired execution/source guard and fixed100 planner are never called.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

PURE_SHA = "d36f4d0dd8f3e61b52405a12deec918b8b97f2538cff105a23702076fe45cbcf"
MAX_CODES, MAX_RAW_KEYS = 1100, 20000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Exact ISO reference date required")
    return value


def _key(item):
    return (item["entry_date"], item["code"])


def plan_identities(selected, calendar, excluded_old_identities=()):
    """Identity-only planner; all selected rows retained, including calendar tail."""
    refs = [_day(d) for d in calendar]
    if not refs or refs != sorted(set(refs)):
        raise ValueError("Complete independent reference calendar must be sorted/unique")
    positions = {d: i for i, d in enumerate(refs)}
    old = sorted([list(_key(r)) if isinstance(r, dict) else list(r) for r in excluded_old_identities])
    if len(old) != len({tuple(k) for k in old}):
        raise ValueError("Duplicate excluded old identities")
    old_set = {tuple(k) for k in old}
    normalized, events, keys = [], [], set()
    for item in selected:
        day, code = _day(item["entry_date"]), item["code"]
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
            raise ValueError("Exact six-character code required")
        if day not in positions or positions[day] < 2 or (day, code) in old_set:
            raise ValueError("Event clock missing or already completed old label identity")
        at = positions[day]
        boards = refs[at - 2:at]
        if "board_dates" in item and item["board_dates"] != boards:
            raise ValueError("Board dates differ from prior two independent references")
        normalized.append({"entry_date": day, "code": code, "board_dates": boards})
        future = refs[at + 1:at + 6]
        events.append({"entry_date": day, "code": code, "board_dates": boards,
                       "future_reference_dates": future, "full_reference_horizon": len(future) == 5})
        keys.update((code, d) for d in [day] + future)
    event_keys = [_key(r) for r in normalized]
    if not normalized or event_keys != sorted(set(event_keys)):
        raise ValueError("Selected identities must be nonempty sorted unique date/code")
    if len({r["code"] for r in normalized}) > MAX_CODES or len(keys) > MAX_RAW_KEYS:
        raise ValueError("Frozen bounded source scope exceeded")
    ordered = [list(k) for k in sorted(keys)]
    return {"selected": normalized, "reference_dates": refs, "excluded_old_identities": old,
            "events": events, "unique_raw_keys": ordered, "unique_factor_asof_keys": ordered,
            "events_sha256": digest(events), "raw_keys_sha256": digest(ordered),
            "event_day_slots": sum(1 + len(r["future_reference_dates"]) for r in events),
            "tail_incomplete_events": sum(not r["full_reference_horizon"] for r in events)}


def load_pure_runtime(binding):
    path = Path(binding["path"])
    if binding["sha256"] != PURE_SHA or file_sha(path) != PURE_SHA:
        raise ValueError("Old pure clock runtime binding changed")
    spec = importlib.util.spec_from_file_location("hash_bound_old_event_clock", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _before_deadline(protocol):
    if protocol.get("deadline_utc") is None:
        return True
    cutoff = datetime.fromisoformat(protocol["deadline_utc"].replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("New authority deadline must include timezone")
    return datetime.now(timezone.utc) < cutoff


def execution_guard(protocol):
    if protocol.get("execution_authorized") is not True:
        raise ValueError("Root must freeze and authorize this necessary label increment")
    if protocol.get("runtime_sha256") != file_sha(__file__):
        raise ValueError("Increment runtime differs from frozen bytes")
    authority = protocol["authority_binding"]
    if file_sha(authority["path"]) != authority["sha256"]:
        raise ValueError("New human authority binding changed")
    plan = protocol["planned"]
    expected = plan_identities(plan["selected"], plan["reference_dates"], plan["excluded_old_identities"])
    if expected != plan:
        raise ValueError("Frozen necessary input manifest changed")
    if not _before_deadline(protocol):
        raise ValueError("New source start is beyond this protocol's authority deadline")
    profile = protocol["mongo_contract"]
    if profile != {"database": "quantaxis_stockdb_research", "source": "stockdb"}:
        raise ValueError("Only the bound local source namespace is supported")
    return load_pure_runtime(protocol["pure_runtime_binding"])


def asof_bindings(code, wanted_dates, records):
    """Latest report at/before each requested date; duplicate latest stays unknown."""
    by_date = defaultdict(list)
    for row in records:
        if row.get("code") != code:
            raise ValueError("Unrequested factor issuer returned")
        by_date[_day(row.get("date"))].append(row)
    dates = sorted(by_date)
    answer = {}
    for day in wanted_dates:
        at = bisect_right(dates, day) - 1
        if at < 0:
            answer[(code, day)] = {"reason": "factor_asof_missing"}
        elif len(by_date[dates[at]]) != 1:
            answer[(code, day)] = {"reason": "factor_latest_asof_duplicate"}
        else:
            answer[(code, day)] = {"row": by_date[dates[at]][0]}
    return answer


def _portable(value):
    if isinstance(value, dict):
        return {str(k): _portable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite_original_number": repr(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"original_type": type(value).__name__, "original_text": str(value)}


def _records_hash(records):
    normalized = [_portable(r) for r in records]
    return digest(sorted(normalized, key=canonical))


def build_labels_from_memory(protocol, raw, factors, pure_runtime):
    """Pure old label semantics plus source-rule-gap annotation, no relabeling."""
    labels = pure_runtime._labels_from_memory({"planned": protocol["planned"]}, raw, factors)
    for item in labels:
        potential = []
        if item["code"] == "000518" and not item["label_complete"]:
            event = next(e for e in protocol["planned"]["events"] if _key(e) == _key(item))
            for d in [item["entry_date"]] + event["future_reference_dates"]:
                if "2026-05-20" <= d < "2026-07-06":
                    for r in raw.get(("000518", d), []):
                        if r.get("is_st") is True:
                            potential.append(d)
        if potential:
            item["known_source_rule_gap"] = {
                "code": "000518", "dates": sorted(set(potential)),
                "reason": "Frozen old ST-band clock does not encode the separately known2026-05-20 special10percent rule; original pending reason preserved, no override or fabricated return."}
    return labels


def read_increment(cfg, protocol):
    """Root invokes once after its own attempt lock. Two read queries per issuer.

    Raw and factor series stay in memory and are released on return. This reader
    does not read the old100 label file, merge results or write any artifact.
    """
    pure = execution_guard(protocol)
    from pymongo import MongoClient
    wanted = [tuple(k) for k in protocol["planned"]["unique_raw_keys"]]
    wanted_set = set(wanted)
    by_code = defaultdict(list)
    for code, day in wanted:
        by_code[code].append(day)
    raw, factors = defaultdict(list), {}
    raw_records, adj_records, query_receipts = [], [], []
    error, stopped = None, False
    profile = protocol["mongo_contract"]
    fields = {k: 1 for k in ["code", "date", "open", "high", "low", "close", "preclose",
                            "is_st", "trade_status", "adjustment", "source_row_sha256"]}
    fields["_id"] = 0
    try:
        with MongoClient(cfg["data"]["mongo_uri"], serverSelectionTimeoutMS=10000, socketTimeoutMS=30000) as client:
            database = client[profile["database"]]
            for code, dates in sorted(by_code.items()):
                for collection in ("stock_day", "stock_adj"):
                    if not _before_deadline(protocol):
                        stopped = True
                        break
                    query = {"source": profile["source"], "code": code,
                             "date": {"$in": sorted(dates)} if collection == "stock_day" else {"$lte": max(dates)}}
                    receipt = {"collection": collection, "code": code, "query_sha256": digest(query),
                               "status": "QUERY_ATTEMPTED", "requested_raw_keys": len(dates) if collection == "stock_day" else None,
                               "started_at_utc": datetime.now(timezone.utc).isoformat()}
                    query_receipts.append(receipt)
                    projection = fields if collection == "stock_day" else {"_id": 0, "code": 1, "date": 1, "adj": 1}
                    records = list(database[collection].find(query, projection))
                    if collection == "stock_day":
                        for row in records:
                            k = (row.get("code"), row.get("date"))
                            if k not in wanted_set or row.get("code") != code:
                                raise ValueError("Unrequested raw identity returned")
                            raw[k].append(row)
                        raw_records.extend(records)
                    else:
                        if any(r.get("date", "9999") > max(dates) for r in records):
                            raise ValueError("Future factor row returned outside query")
                        factors.update(asof_bindings(code, dates, records))
                        adj_records.extend(records)
                    receipt.update(status="QUERY_COMPLETE", received_rows=len(records),
                                   response_rows_sha256=_records_hash(records),
                                   returned_at_utc=datetime.now(timezone.utc).isoformat(),
                                   returned_before_protocol_deadline=_before_deadline(protocol))
                if stopped:
                    break
    except Exception as exc:
        # No retry. Do not leak URI, credentials or exception messages.
        error = type(exc).__name__
        if query_receipts and query_receipts[-1]["status"] != "QUERY_COMPLETE":
            query_receipts[-1].update(status="QUERY_FAILED_OR_AMBIGUOUS", error_type=error)
    labels = build_labels_from_memory(protocol, raw, factors, pure)
    complete_requests = not stopped and error is None and len(query_receipts) == 2 * len(by_code)
    pending = any(not r["label_complete"] and r["reason"] != "known_no_t_lower_touch_not_in_postevent_condition" for r in labels)
    receipt = {"status": "SOURCE_PENDING" if not complete_requests or pending else "NECESSARY_LABEL_INCREMENT_COMPLETE",
               "all_planned_requests_finished": complete_requests, "source_or_label_pending": pending or not complete_requests,
               "fixed_increment_event_denominator": len(labels), "new_label_rows_ascertained": len(labels),
               "completed_conditional_labels": sum(r["label_complete"] for r in labels),
               "known_no_t_touch_events": sum(r["bid_touch_condition_known"] is False for r in labels),
               "requested_issuer_count": len(by_code), "requested_raw_keys": len(wanted),
               "received_raw_rows": len(raw_records), "received_factor_series_rows": len(adj_records),
               "mongo_read_queries_attempted": len(query_receipts), "query_receipts": query_receipts,
               "raw_projected_rows_sha256": _records_hash(raw_records), "factor_series_rows_sha256": _records_hash(adj_records),
               "factor_asof_bindings_sha256": digest(_portable([{ "code": c, "asof": d, **factors[(c,d)]} for c,d in sorted(factors)])),
               "label_rows_sha256": digest(labels), "reason_counts": dict(Counter(r["reason"] for r in labels)),
               "known_source_rule_gap_events": sum("known_source_rule_gap" in r for r in labels),
               "query_exception_type": error, "stopped_by_new_protocol_deadline": stopped,
               "new_source_calls_not_old_vector_reuse": True,
               "historical_overlap_disclosure": "These are newly persisted necessary labels for a newly frozen expanded identity scope. Earlier overlapping full-event research retained aggregates only; this is not reuse or recovery of a1138-row label vector.",
               "old100_labels_read_or_recalculated": False, "old_full1138_study_or_accounts_replayed": False,
               "raw_or_factor_series_saved": False, "global_reader_or_source_modified": False,
               "formal_PIT_or_queue_fill_certified": False, "official_cost": 0}
    return {"label_rows": labels, "source_receipt": receipt}
