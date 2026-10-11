"""Inert until exact registration; consume only the four unfinished AR positions."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(OUT))
from ar_structure import consume_pending_ar, base


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def bound(item):
    path = Path(item["path"])
    if sha(path) != item["sha256"]:
        raise ValueError("frozen_pending_AR_dependency_or_input_changed:" + str(path))
    return path


def main():
    cli = argparse.ArgumentParser(); cli.add_argument("--execute", action="store_true")
    cli.add_argument("--protocol", type=Path, default=OUT / "protocol.json"); cli.add_argument("--protocol-sha")
    args = cli.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_FOUR_PENDING_AR_NO_BODY_OR_AMOUNTS_READ"})); return
    if not args.protocol_sha or sha(args.protocol) != args.protocol_sha:
        raise ValueError("exact_root_frozen_pending_AR_protocol_required")
    p = read(args.protocol)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("human_source_dispatch_deadline_reached")
    output = Path(p["output_path"])
    if output.exists(): raise ValueError("pending_AR_receipt_exists_no_implicit_replay")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri()+"?mode=ro", uri=True) as registry:
        event = registry.execute("SELECT payload FROM research_events WHERE kind IN ('source_repair','execution_repair') AND event_id=? AND subject=?",
                                 (p["event_prefix"]+args.protocol_sha, p["registration_subject"])).fetchone()
        if not event or json.loads(event[0]).get("new_contexts") != 0:
            raise ValueError("registered_zero_context_pending_AR_freeze_required")
    for item in [*p["runtime_bindings"].values(), *p["bindings"].values()]: bound(item)
    plan = read(bound(p["bindings"]["source_plan"])); old = read(bound(p["bindings"]["completed17_receipt"]))
    diagnostic = read(bound(p["bindings"]["completed_fixed5_diagnostic"]))
    bycode = {c["code"]: c for c in plan["selected_cases"]}
    prior = {r["source_identity"]["code"]: r for r in old["records"]}
    keys = [{k: bycode[code][k] for k in ("code", "announcement_id", "report_date", "source_record_sha256")} for code in p["selected_codes"]]
    if keys != p["selected_identity_rows"] or len(keys) != 4 or hashlib.sha256(canonical(keys)).hexdigest() != p["selected_identity_sha256"]:
        raise ValueError("exact_four_pending_AR_keys_or_order_changed")
    if p["selected_codes"] != ["000637", "000672", "000533", "000678"]:
        raise ValueError("four_pending_AR_scope_changed")
    diagnosed = {r["source_identity"]["code"]: r for r in diagnostic["records"]}
    if diagnostic["status"] != "COMPLETED_FIXED5_SOURCE_STRUCTURE_DIAGNOSTIC":
        raise ValueError("completed_structure_diagnostic_required")
    for identity in keys:
        code = identity["code"]
        if diagnosed[code]["source_identity"] != identity or diagnosed[code]["status"] != "STRUCTURE_OBSERVED_NO_NEW_SOURCE_QUALIFICATION":
            raise ValueError("diagnosed_source_identity_changed")
        ar = prior[code].get("assets", {}).get("AR", {})
        if ar.get("complete_unique_closing_opening_stock") or ar.get("stock"):
            raise ValueError("completed_or_partial_AR_amount_facts_must_not_be_replayed")
    spec = importlib.util.spec_from_file_location("bound_reader_four_pending_AR", p["bindings"]["shared_reader"]["path"])
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    indexmap = plan["bindings"]["source_indices"]
    if any(indexmap[k] != p["bindings"][k] for k in indexmap): raise ValueError("source_index_map_changed")
    indexes = {item["path"]: read(bound(item)) for item in indexmap.values()}
    records = []; execution_stop = None
    with sqlite3.connect(bound(p["bindings"]["capture_db"]).resolve().as_uri()+"?mode=ro", uri=True) as db:
        for index, identity in enumerate(keys):
            if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
                execution_stop = {"index_zero_based": index, "source_identity": identity, "error_type": "DeadlineReached", "error": "human_source_deadline_reached"}; break
            c = dict(bycode[identity["code"]]); c["source_index_ref"] = indexmap[c["source_index_binding_key"]]
            result = {"source_identity": identity,
                      "prior_source17_status": prior[c["code"]]["status"],
                      "prior_AR_pending": prior[c["code"]].get("assets", {}).get("AR", {}).get("pending_reasons", []),
                      "prior_case_pending": prior[c["code"]].get("pending_reasons", []),
                      "latest_annual_version_metadata": c["latest_annual_version_metadata"],
                      "known_revision_or_later_report_metadata": c["known_revision_or_later_report_metadata"],
                      "source_policy_estimate_scope_version_qualification": "not_reviewed_pending", "source_qualified": False}
            try:
                document, binding, text = reader.read_case(c, db, indexes)
                result["original_metadata_flags"] = document["stock"].get("comparison_change_barriers", [])
                result.update(consume_pending_ar(text, int(c["report_date"][:4]), report=prior[c["code"]].get("report")))
                del document, binding, text
            except ValueError as error:
                result.update(status="source_binding_or_pending_AR_structure", pending_reasons=[str(error)])
            except Exception as error:
                execution_stop = {"index_zero_based": index, "source_identity": identity,
                                  "error_type": type(error).__name__, "error": str(error)[:512]}; break
            records.append(result)
    receipt = {"status": "COMPLETED_FOUR_PENDING_AR_SOURCE_FACTS" if len(records) == 4 and execution_stop is None else "SOURCE_EXECUTION_PENDING_FOUR_AR_NO_IMPLICIT_REPLAY",
               "protocol_sha256": args.protocol_sha, "records": records, "execution_stop": execution_stop,
               "resume_position": {"first_uncompleted_index_zero_based": len(records), "remaining_identity_rows": keys[len(records):], "automatic_resume_allowed": False},
               "new_contexts": 0, "economic_failure_claimed": False, "Q_or_any_factor_computed": False,
               "old17_first3_INV_or_other_sources_replayed": False, "old_receipts_sources_DB_masks_state_registry_changed": False,
               "financial_qualification_created": False, "fulltext_copies": 0, "network_or_official_calls": 0}
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"); temporary.replace(output)
    print(json.dumps({"status": receipt["status"], "records": len(records), "output_sha256": sha(output)}))


if __name__ == "__main__": main()
