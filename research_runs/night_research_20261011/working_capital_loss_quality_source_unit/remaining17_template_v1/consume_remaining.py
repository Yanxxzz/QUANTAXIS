"""Default-inert, registered source-only consumption of the fixed remaining17."""
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
from source_templates import SourcePending, consume


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def bound(item):
    p = Path(item["path"])
    if sha(p) != item["sha256"]:
        raise ValueError("frozen_dependency_or_input_changed:" + str(p))
    return p


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--protocol", type=Path, default=OUT / "protocol.json")
    parser.add_argument("--protocol-sha")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_SOURCE17_NO_BODY_AMOUNT_OR_ECONOMIC_READ"})); return
    if not args.protocol_sha or sha(args.protocol) != args.protocol_sha:
        raise ValueError("exact_root_frozen_source_protocol_required")
    p = read(args.protocol)
    if p["status"] != "FROZEN_REMAINING17_SOURCE_TEMPLATE_AWAIT_ROOT_REGISTRATION":
        raise ValueError("source_only_protocol_status_pending")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
        raise ValueError("human_source_dispatch_deadline_reached")
    output = Path(p["output_path"])
    if output.exists():
        raise ValueError("source17_receipt_exists_no_implicit_rerun")
    with sqlite3.connect(Path(p["registry_path"]).resolve().as_uri() + "?mode=ro", uri=True) as registry:
        event = registry.execute("SELECT payload FROM research_events WHERE kind IN ('source_repair','execution_repair') AND event_id=? AND subject=?",
                                 (p["event_prefix"] + args.protocol_sha, p["registration_subject"])).fetchone()
        if not event or read_payload(event[0]).get("new_contexts") != 0:
            raise ValueError("root_registered_zero_context_source_freeze_required")
    for item in [*p["runtime_bindings"].values(), *p["bindings"].values()]:
        bound(item)
    plan = read(bound(p["bindings"]["source_plan"]))
    original = read(bound(p["bindings"]["initial_source_facts"]))
    cases = [c for c in plan["selected_cases"] if c["code"] not in p["excluded_completed_three"]]
    keys = [{k: c[k] for k in ("code", "announcement_id", "report_date", "source_record_sha256")} for c in cases]
    if len(cases) != 17 or keys != p["selected_identity_rows"]:
        raise ValueError("fixed_remaining17_order_or_exactKEY_changed")
    if hashlib.sha256(canonical(keys)).hexdigest() != p["selected_identity_sha256"]:
        raise ValueError("fixed_remaining17_identity_digest_changed")
    if set(p["excluded_completed_three"]) != {"000157", "000403", "000409"}:
        raise ValueError("completed_three_exclusion_changed")
    reader_spec = importlib.util.spec_from_file_location("bound_retained_source_reader_remaining17", p["bindings"]["shared_reader"]["path"])
    reader = importlib.util.module_from_spec(reader_spec); reader_spec.loader.exec_module(reader)
    indices = plan["bindings"]["source_indices"]
    for key, item in indices.items():
        if item != p["bindings"][key]:
            raise ValueError("source_index_binding_map_changed")
    indexes = {item["path"]: read(bound(item)) for item in indices.values()}
    inherited = {r["source_identity"]["code"]: r for r in original["records"]}
    records = []; errors = []
    with sqlite3.connect(bound(p["bindings"]["capture_db"]).resolve().as_uri() + "?mode=ro", uri=True) as db:
        for source in cases:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(p["deadline_utc"]):
                errors.append({"code": source["code"], "phase": "dispatch", "reason": "human_source_deadline_reached"}); break
            c = dict(source); c["source_index_ref"] = indices[c["source_index_binding_key"]]
            result = {"source_identity": {k: c[k] for k in ("code", "announcement_id", "report_date", "source_record_sha256", "capture_gzip_sha256", "retained_text_sha256")},
                      "prior_source20_status": inherited[c["code"]]["status"],
                      "prior_source20_pending_reasons": inherited[c["code"]].get("pending_reasons", []),
                      "known_revision_or_later_report_metadata": c["known_revision_or_later_report_metadata"],
                      "latest_annual_version_metadata": c["latest_annual_version_metadata"],
                      "source_policy_estimate_scope_version_qualification": "not_reviewed_pending",
                      "source_qualified": False, "empty_flags_mean_clean": False}
            try:
                document, binding, text = reader.read_case(c, db, indexes)
                result["original_metadata_flags"] = document["stock"].get("comparison_change_barriers", [])
                result.update(consume(text, int(c["report_date"][:4])))
                del document, binding, text
            except (SourcePending, ValueError) as exc:
                result.update(status="source_binding_or_template_pending", pending_reasons=[str(exc)])
            except Exception as exc:
                errors.append({"code": c["code"], "phase": "runtime", "reason": type(exc).__name__ + ":" + str(exc)})
                result.update(status="execution_pending_not_economic_failure")
                records.append(result); break
            records.append(result)
    receipt = {"status": "COMPLETED_SOURCE_ONLY_REMAINING17_TEMPLATE_FACTS" if len(records) == 17 and not errors else "SOURCE_EXECUTION_PENDING_NO_IMPLICIT_REPLAY",
               "protocol_sha256": args.protocol_sha, "runtime_bindings": p["runtime_bindings"],
               "plan_binding": p["bindings"]["source_plan"], "records": records, "errors": errors,
               "new_contexts": 0, "official_calls": 0, "network_calls": 0,
               "old20_or_completed3_receipts_replaced": False, "source_masks_DB_or_shared_state_changed": False,
               "no_Q_or_other_factor_computed": True, "financial_qualification_created": False,
               "full_text_or_PDF_copies": 0, "economic_failure_claimed": False}
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"status": receipt["status"], "records": len(records), "errors": errors, "output_sha256": sha(output)}, ensure_ascii=False))


def read_payload(raw):
    return json.loads(raw)


if __name__ == "__main__":
    main()
