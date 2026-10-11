"""Default-inert AR stock facts for one frozen identity-only source plan."""
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
ROOT = OUT.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OUT.parent / "remaining17_template_v1" / "ar_pending_structure_repair_v1"))
from ar_structure import consume_pending_ar

KEY = ["code", "announcement_id", "report_date", "source_record_sha256"]
POSTHOC = {"300432", "300571", "301313"}
PARENT_SHA = "32de6edd562368a60fbb3f957462facd262387bebf6d5e483b187068c424b4bb"


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
        raise ValueError("frozen_next80_AR_binding_changed:" + str(path))
    return path


def validated_cases(plan, parent, p):
    """Metadata only: no flags/financial amounts/outcomes select the cohort."""
    cases = plan["selected_cases"]
    identities = [{field: c[field] for field in KEY} for c in cases]
    digest = hashlib.sha256(canonical(identities)).hexdigest()
    if len(cases) != 80 or len({c["code"] for c in cases}) != 80:
        raise ValueError("exact_80_unique_source_issuers_required")
    if cases != sorted(cases, key=lambda c: (c["code"], c["report_date"], c["announcement_id"], c["source_record_sha256"])):
        raise ValueError("frozen_stable_source_identity_order_required")
    excluded = POSTHOC | {c["code"] for c in parent["selected_cases"]}
    if any(c["code"] in excluded or c["report_date"] != "2025-12-31" for c in cases):
        raise ValueError("old20_posthoc_or_non_FY25_source_exclusion_required")
    if identities != p["selected_identity_rows"] or digest != p["selected_identity_sha256"] or digest != plan["selected_identity_sha256"]:
        raise ValueError("frozen_next80_exact_KEY_digest_changed")
    if plan.get("parent_source_plan_sha256") != PARENT_SHA or plan["bindings"]["parent_source_plan"] != p["bindings"]["parent_source_plan"]:
        raise ValueError("original_FY25_source_population_parent_binding_required")
    if plan["bindings"]["source_indices"] != parent["bindings"]["source_indices"]:
        raise ValueError("original_retained_index_lineage_changed")
    return cases, identities


def receipt(p, records, stop, identities):
    return {"status": "COMPLETED_NEXT80_AR_SOURCE_FACTS" if len(records) == 80 and stop is None else "SOURCE_EXECUTION_PENDING_NEXT80_AR_NO_IMPLICIT_REPLAY",
            "protocol_sha256": p["_protocol_sha256"], "records": records, "execution_stop": stop,
            "resume_position": {"completed_records": len(records), "first_uncompleted_index_zero_based": len(records),
                                "remaining_identity_rows": identities[len(records):], "automatic_resume_allowed": False},
            "new_contexts": 0, "Q_or_any_factor_computed": False, "financial_qualification_created": False,
            "old20_first3_remaining17_INV_or_other_sources_replayed": False,
            "source_masks_old_receipts_DB_state_registry_Git_changed": False, "economic_failure_claimed": False,
            "new_native_PDF_database_or_price_copies": 0, "network_or_official_calls": 0}


def consume_cases(cases, read_case, deadline_reached, checkpoint):
    records = []; stop = None
    for index, case in enumerate(cases):
        identity = {field: case[field] for field in KEY}
        if deadline_reached():
            stop = {"index_zero_based": index, "source_identity": identity, "error_type": "DeadlineReached", "error": "human_source_deadline_reached"}; break
        result = {"source_identity": identity,
                  "latest_annual_version_metadata": case.get("latest_annual_version_metadata"),
                  "known_revision_or_later_report_metadata": case.get("known_revision_or_later_report_metadata"),
                  "original_source_population_only_not_AR_certification": True,
                  "policy_estimate_consolidation_restatement_version_and_AR_qualification": "unreviewed_pending",
                  "empty_metadata_flags_mean_clean": False, "source_qualified": False}
        try:
            document, binding, text = read_case(case)
            result["original_metadata_flags"] = document["stock"].get("comparison_change_barriers", [])
            result.update(consume_pending_ar(text, 2025, report=None))
            del document, binding, text
        except ValueError as error:
            result.update(status="source_binding_or_AR_template_pending", pending_reasons=[str(error)])
        except Exception as error:
            stop = {"index_zero_based": index, "source_identity": identity, "error_type": type(error).__name__, "error": str(error)[:512]}; break
        records.append(result)
        checkpoint(records)
    return records, stop


def save(path, result):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def main():
    cli = argparse.ArgumentParser(); cli.add_argument("--execute", action="store_true")
    cli.add_argument("--protocol", type=Path, default=OUT / "protocol_draft.json"); cli.add_argument("--protocol-sha")
    args = cli.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_NEXT80_AR_NO_PLAN_OR_BODY_AMOUNT_READ"})); return
    if not args.protocol_sha or sha(args.protocol) != args.protocol_sha:
        raise ValueError("exact_root_frozen_next80_AR_protocol_required")
    p = read(args.protocol)
    if p.get("status") != "FROZEN_NEXT80_AR_AWAIT_ROOT_REGISTRATION" or not p.get("bindings", {}).get("next80_source_plan") or len(p.get("selected_identity_rows", [])) != 80:
        raise ValueError("draft_missing_frozen_next80_plan_cannot_execute")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("human_source_dispatch_deadline_reached")
    output = Path(p["output_path"])
    if output.exists(): raise ValueError("next80_AR_receipt_exists_no_implicit_replay")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri()+"?mode=ro", uri=True) as db:
        event = db.execute("SELECT payload FROM research_events WHERE kind IN ('source_repair','execution_repair') AND event_id=? AND subject=?",
                           (p["event_prefix"]+args.protocol_sha, p["registration_subject"])).fetchone()
        if not event or json.loads(event[0]).get("new_contexts") != 0:
            raise ValueError("root_registered_zero_context_next80_source_freeze_required")
    for item in [*p["runtime_bindings"].values(), *p["bindings"].values()]: bound(item)
    parent = read(bound(p["bindings"]["parent_source_plan"])); plan = read(bound(p["bindings"]["next80_source_plan"]))
    cases, identities = validated_cases(plan, parent, p)
    spec = importlib.util.spec_from_file_location("bound_retained_next80_AR_reader", p["bindings"]["shared_reader"]["path"])
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    indexmap = plan["bindings"]["source_indices"]
    if any(indexmap[k] != p["bindings"][k] for k in indexmap): raise ValueError("frozen_source_index_map_changed")
    indexes = {item["path"]: read(bound(item)) for item in indexmap.values()}
    prepared = []
    for source in cases:
        c = dict(source); c["source_index_ref"] = indexmap[c["source_index_binding_key"]]; prepared.append(c)
    p["_protocol_sha256"] = args.protocol_sha
    def checkpoint(records):
        progress = receipt(p, records, None, identities)
        progress["status"] = "RUNNING_NEXT80_AR_SOURCE_FACTS"
        save(output, progress)
    with sqlite3.connect(bound(p["bindings"]["capture_db"]).resolve().as_uri()+"?mode=ro", uri=True) as db:
        records, stop = consume_cases(prepared, lambda c: reader.read_case(c, db, indexes),
                                     lambda: datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]), checkpoint)
    result = receipt(p, records, stop, identities); save(output, result)
    print(json.dumps({"status": result["status"], "records": len(records), "output_sha256": sha(output)}))


if __name__ == "__main__": main()
