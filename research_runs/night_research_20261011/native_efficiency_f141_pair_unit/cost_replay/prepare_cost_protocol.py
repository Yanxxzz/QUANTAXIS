"""Bind completed EF native preparation for exactly two deferred cost paths.

Default is inert. Explicit execution reads definitions/proof metadata and file
hashes only; it never reads signal columns, prices, labels or wealth metrics.
Root must register the generated cost protocol before the cost driver runs.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
UNIT = OUT.parent
NIGHT = UNIT.parent
CONTEXT = "WC03_EFFICIENCY_F141_EQUAL_SIGNAL_NATIVE_CONSERVATIVE_20261011"
OLD_E_PROTOCOL_SHA = "c1b7feb983941749f2ca0b17c8b5f4978ada310e21add3646621022218312951"
OLD_E_SUMMARY_SHA = "df356816a6afd5bcd953bc0c681683fa171139917531ff02a96b5216c12421ae"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for part in iter(lambda: handle.read(4*1024*1024), b""):
            h.update(part)
    return h.hexdigest()


def artifact(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path)}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def bound(item):
    if artifact(item["path"]) != item:
        raise ValueError("Completed source/runtime binding changed: " + item["path"])
    return Path(item["path"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--native-protocol-sha")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_NO_NATIVE_OR_ECONOMIC_DATA_READ",
                          "next": "completed registered EF native preparation, then explicit --execute --native-protocol-sha"}))
        return
    if not args.native_protocol_sha:
        parser.error("--execute requires the exact root-registered --native-protocol-sha")
    destination = OUT / "protocol.json"
    if destination.exists():
        raise ValueError("Cost protocol already exists; preserve its original freeze and do not overwrite")
    native_binding = artifact(UNIT / "protocol.json")
    if native_binding["sha256"] != args.native_protocol_sha:
        raise ValueError("Registered native protocol changed")
    native = read(native_binding["path"])
    e_cost = NIGHT / "native_capital_efficiency_unit/cost_replay"
    original_e_binding = artifact(e_cost / "protocol.json")
    if original_e_binding["sha256"] != OLD_E_PROTOCOL_SHA:
        raise ValueError("Original completed E cost protocol changed")
    inherited = read(original_e_binding["path"])
    bindings = {key: value for key, value in inherited["bindings"].items()
                if not key.startswith("E_") and key != "driver"}
    for item in bindings.values():
        bound(item)
    bindings["driver"] = artifact(OUT / "run_pair_cost_replay.py")
    bindings["EF_protocol"] = native_binding
    for key, name in (("EF_signals", "EF_formation_signals.parquet"),
                      ("EF_preparation", "preparation_review.json"),
                      ("EF_preflight", "EF_native_preflight.json"),
                      ("EF_candidate", "EF_candidate.json"),
                      ("EF_code", "WC03EfficiencyF141Pair.py")):
        bindings[key] = artifact(UNIT / name)
    # Read only native provenance/group-readiness metadata, never cache values.
    prep = read(bindings["EF_preparation"]["path"])
    proof = read(bindings["EF_preflight"]["path"])
    candidate = read(bindings["EF_candidate"]["path"])
    old_input = read(bindings["original_input_receipt"]["path"])
    required = ("controls_reuse_allowed", "shared_finite_mask_exact", "source_qualification_exact",
                "original_f_formation_keys_exact", "all_251_decisions_group_ready",
                "original_SOURCE_and_all_peer_F_method_preserved", "holding_tail_true_NaN",
                "immutable_E_SOURCE_and_all_old_gates_preserved", "only_native_class_and_component_AST_changed",
                "no_final_Z_or_signal_return_blend")
    error = prep.get("max_absolute_saved_E_minus_F_control_reference_error")
    if (any(prep.get(key) is not True for key in required)
            or prep.get("status") != "EF_FULL_SCOPE_NATIVE_READY_REUSE_GF_F_MARKET"
            or prep.get("protocol_sha256") != native_binding["sha256"]
            or isinstance(error, bool) or not isinstance(error, (int, float)) or not 0 <= error <= 2e-12
            or candidate.get("candidate_id") != CONTEXT or candidate.get("direction") != 1
            or candidate.get("dispatch_ready") is not True
            or candidate.get("code_sha256") != bindings["EF_code"]["sha256"]
            or proof.get("research_replay_passed") is not True
            or proof["code_sha256"] != bindings["EF_code"]["sha256"]
            or proof["proof"]["input_frame_sha256"] != prep["old_input_frame_sha256"]
            or prep["old_input_frame_sha256"] != old_input["native_outputs"]["GF"]["input_frame_sha256"]
            or proof["proof"]["output_sha256"] != prep["output_sha256"]
            or (prep["formation_dates"], prep["common_anchor_rows"], prep["holding_days"],
                prep["financial_report_identities"], prep["financial_source_rows"]) != (51, 32905, 256, 1736, 161366)
            or native["candidate_draft"]["candidate_id"] != CONTEXT
            or native["candidate_draft"]["strategy_spec"]["formation_dates"] != inherited["anchors"]):
        raise ValueError("Actual fixed EF/native/source support not ready; no protocol or control replay allowed")
    if artifact(e_cost / "wealth_summary.json")["sha256"] != OLD_E_SUMMARY_SHA:
        raise ValueError("Completed standalone E wealth summary changed")
    bindings["E_control_summary"] = artifact(e_cost / "wealth_summary.json")
    bindings["E_control_ledger"] = artifact(e_cost / "E_daily_net_ledger.parquet")
    bindings["E_cost_protocol"] = original_e_binding
    bindings["E_cost_driver"] = inherited["bindings"]["driver"]
    bound(bindings["E_cost_driver"])
    references = {
        "GF_F_official_review": artifact(NIGHT / "native_source_conservative_unit/official_pair_review.json"),
        "E_official_review": artifact(NIGHT / "native_capital_efficiency_unit/official_review.json"),
    }
    bindings.update(references)
    now = datetime.now(timezone.utc)
    if now >= datetime.fromisoformat(native["deadline_utc"]):
        raise ValueError("User research dispatch deadline reached")
    protocol = {
        "id": "EF_FIXED_SAME_SOURCE_30_50BPS_COST_RECOMPUTATION_20261011",
        "status": "FROZEN_EF_COST_RECOMPUTATION_AWAIT_ROOT_EXECUTION",
        "prepared_at_utc": now.isoformat(), "deadline_utc": native["deadline_utc"],
        "driver_sha256": bindings["driver"]["sha256"], "economic_context": CONTEXT,
        "registered_context": prep["registered_context"], "direction": 1,
        "new_economic_candidate_registrations": 0, "bindings": bindings,
        "window": inherited["window"], "anchors": inherited["anchors"],
        "cycle": 5, "groups": 10, "minimum_assets": inherited["minimum_assets"],
        "costs": [0.003, 0.005], "held_groups": {"EF": "10"},
        "official_references_only": references,
        "runtime_reporting_note": inherited["runtime_reporting_note"],
        "source_scope": inherited["source_scope"],
        "definition": "EF=.5*(ZE+Z(-F_pct)), direction1/top10, no final Z or return blend. Exactly completed original financial and common F support; no formula/weight/sign/window/scope change.",
        "control_reuse_prerequisites": list(required) + [
            "actual EF32905 ordered date/symbol keys identical original GF/F; native-reference maximum error<=2e-12",
            "same 51next-open formations and256dates2025-08-15..2026-09-03; any support mismatch pending root, never rerun controls"],
        "replay": "Only EF:0.003/EF:0.005. Same original cash, true next-open, sell-before-buy, fee-aware cash scaling, legal halt carry, terminal open liquidation attempt and all blocked unsold quantities/risk facts. Targets frozen before quote/result reads; existing price/risk caches referenced only.",
        "comparison": "Only after both EF paths complete, read original completed GF/F141_CONTROL/COMMON_MARKET and standalone E cost summaries/ledgers on exactly256dates. Official review files are references only; their additive statistics are not costed transaction NAV.",
        "retention": "Reuse one actual native EF51formation cache; one512row EF daily ledger and compact wealth summary. Atomic checkpoint after each whole path, completed paths never replayed; after complete checkpoint contains references and no duplicate daily rows.",
        "new_contexts": 0, "official_spent": 0, "controls_recomputed": 0,
        "formal_admission": False, "full_PIT": False, "official_NAV": False,
        "fresh_blind_OOS": False, "source_limited_local_daily_bar_execution_proxy": True,
        "not_actual_auction_or_capacity": True, "no_partial_results_used_for_tuning": True,
        "risk_metadata_unknowns_remain_pending": True,
        "execution_requires_root_registry_event_after_this_protocol_freeze": True,
    }
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(protocol, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(destination)
    print(json.dumps({"status": protocol["status"], "protocol": artifact(destination),
                      "execute_after_root_registration": "python -X utf8 -B run_pair_cost_replay.py --execute --protocol-sha " + sha(destination)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
