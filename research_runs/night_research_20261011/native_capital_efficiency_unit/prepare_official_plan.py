"""Deferred exactE plan generator: local completed receipts only, no API calls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

UNIT = Path(__file__).resolve().parent
NIGHT = UNIT.parent
ROOT = NIGHT.parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.platform import fingerprint, read_experiment_jobs, _batch_usage, _job_definition
from panda_alpha.registry import strategy_definition, trial_key

SCOPE = "GF_NIGHT_GIFT60_20261011"
CANDIDATE = "WC03_E_CAPITAL_EFFICIENCY_NATIVE_CONSERVATIVE_20261011"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact(path):
    return {"path": str(path), "sha256": sha(path)}


def scope_snapshot(jobs):
    scoped = []
    for job in jobs:
        billing = _job_definition(job).get("billing_authorization", {})
        if any(isinstance(a, dict) and a.get("batch_id") == SCOPE for a in billing.values()):
            if job["state"] != "SETTLED":
                raise ValueError("Prior shared-scope attempt is unresolved")
            receipt = json.loads(job["receipt"]) if job.get("receipt") else {}
            if receipt.get("recharge_delta") != 0 or receipt.get("gift_delta") != job["actual"]:
                raise ValueError("Prior shared-scope funding not reconciled")
            scoped.append({"fingerprint": job["fingerprint"], "state": job["state"],
                           "actual": job["actual"], "gift_delta": receipt["gift_delta"], "recharge_delta": 0})
    if any(j["state"] not in ("SETTLED", "CANCELLED") for j in jobs):
        raise ValueError("Another official job requires collection first")
    usage = _batch_usage(jobs, SCOPE)
    if usage["recharge"] != 0 or usage["total"] + 8 > 60 or usage["runs"] + 1 > 30:
        raise ValueError("Original shared gift scope cannot cover this one8point estimate")
    return scoped, usage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-sha", required=True)
    args = parser.parse_args()
    output = UNIT / "official_plan.json"
    if output.exists():
        raise ValueError("Completed official plan exists; no implicit replacement")
    protocol_path = UNIT / "protocol.json"
    if sha(protocol_path) != args.protocol_sha:
        raise ValueError("Exact Root-frozen E economic protocol required")
    protocol = read(protocol_path)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(protocol["deadline_utc"]):
        raise ValueError("Human research deadline reached")
    candidate_path = UNIT / "E_candidate.json"
    candidate = read(candidate_path)
    code_path = UNIT / "WC03CapitalEfficiency.py"
    if sha(code_path) != candidate["code_sha256"]:
        raise ValueError("Actual E code changed")
    candidate["code"] = code_path.read_bytes().decode("utf-8")
    if candidate["candidate_id"] != CANDIDATE or candidate.get("direction") != 1 or candidate.get("dispatch_ready") is not True:
        raise ValueError("Only one real-preflight-ready E candidate is permitted")
    if candidate["strategy_spec"] != protocol["candidate_draft"]["strategy_spec"]:
        raise ValueError("Actual E definition differs from registered prototype")
    key = fingerprint(candidate, candidate["window"], 5, 10)
    proof_path = UNIT / "E_native_preflight.json"
    proof = read(proof_path)
    prep_path = UNIT / "preparation_review.json"
    preparation = read(prep_path)
    if proof.get("research_replay_passed") is not True or proof.get("definition_fingerprint") != key:
        raise ValueError("Real completed E preflight fingerprint required; no placeholders")
    required = ("controls_reuse_allowed", "shared_finite_mask_exact", "source_qualification_exact",
                "original_f_formation_keys_exact", "all_251_decisions_group_ready")
    if not all(preparation.get(k) is True for k in required) or preparation.get("common_anchor_rows") != 32905:
        raise ValueError("New E cannot claim original full-source control equivalence")
    definition = strategy_definition(protocol["candidate_draft"], protocol["evaluation_window"], 5, 10)
    registered_key = trial_key(definition)
    if definition != protocol["registration_definition"] or registered_key != protocol["registration_trial_key"]:
        raise ValueError("Root registration prototype changed")
    with sqlite3.connect((ROOT / "research_runs/research_trials.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT payload FROM research_events WHERE kind='registration' AND subject=?", (registered_key,)).fetchall()
        if not any((p := json.loads(r[0])).get("candidate_id") == CANDIDATE and p.get("definition") == definition for r in rows):
            raise ValueError("One E context must be registered before official-plan creation")
    jobs = read_experiment_jobs(ROOT / "research_runs/experiments.sqlite3")
    if any(j["candidate_id"] == CANDIDATE for j in jobs):
        raise ValueError("E already owns an official attempt; collect its same Run")
    scoped, usage = scope_snapshot(jobs)
    authorization = read(NIGHT / "authorization.json")
    extension = read(NIGHT / "day_extension_authorization.json")
    compute = read(NIGHT / "compute_overrides.json")
    if (authorization["scope_id"] != SCOPE or authorization["max_total_credits"] != 60
            or authorization["max_recharge_credits"] != 0 or authorization["max_runs"] != 30
            or authorization["normal_estimated_gift_research_dispatch_authorized"] is not True
            or extension["batch_id"] != SCOPE or extension["normal_gift_research_dispatch_authorized"] is not True
            or compute["compute"]["recharge_credit_limit"] != 0):
        raise ValueError("Existing human gift60/zero-recharge authorization required")
    old_keys = [j["fingerprint"] for j in scoped]
    bindings = {"runner": artifact(UNIT / "run_official.py"), "plan_generator": artifact(Path(__file__)),
        "protocol": artifact(protocol_path), "candidate": artifact(candidate_path), "code": artifact(code_path),
        "native_preflight": artifact(proof_path), "preparation_review": artifact(prep_path),
        "original_authorization": artifact(NIGHT / "authorization.json"),
        "day_extension": artifact(NIGHT / "day_extension_authorization.json"),
        "compute_overrides": artifact(NIGHT / "compute_overrides.json"),
        "fixture_reader_module": artifact(ROOT / "panda_alpha/native_gf_transfer.py"),
        "platform_runtime": artifact(ROOT / "panda_alpha/platform.py"),
        "preflight_runtime": artifact(ROOT / "panda_alpha/native_preflight.py")}
    plan = {"id": "CAPITAL_E_SINGLE_OFFICIAL_MECHANISM_TEST_20261011",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "deadline_utc": protocol["deadline_utc"],
        "fingerprint": key, "registration_trial_key": registered_key,
        "max_new_paid_runs": 1, "auto_retry": False, "scope_id": SCOPE,
        "batch_authorization": {"batch_id": SCOPE, "valid_from": authorization["instruction_date_beijing"],
            "valid_until": "2026-10-11", "fingerprints": [*old_keys, key],
            "max_total_credits": 60, "max_recharge_credits": 0, "max_runs": 30,
            "accept_estimated_charge_risk": True},
        "human_permission_interpretation": "Normal gift-only estimated research authorized by original60 and the dayextension; no recharge, scope reset, old20 migration, servicehardcap or >60 permission. Same sharedscopeactual plus unresolvedreservations governs allfuture directions.",
        "gift_runtime_guard": {"conservative_reservation": 8, "minimum_fresh_gift": 16,
            "fresh_balance_required_at_dispatch": True, "generator_does_not_query_balance": True,
            "gift_expiry_timezone_unknown": "Parse actual fresh expiry; ifnaive requirebothUTCandUTC+8 interpretationsalive. Earlier observed18:32:22 is not presumedalive or refreshedhere.",
            "expiry_rechecked_before_create_start_from_fresh_balance_observation": True,
            "gift_hard_isolation_or_service_fee_cap_verified": False},
        "research_decision": {"research_question": "Does source-bound annual revenue growth relative to total-asset expansion offer standalone payoff and incremental financial information on the exact original conservative GF/F141 source support?",
            "decision_if_pass": "Review completecost/source/stability/conditionalinformation andgenuine later independent evidence before admission; no pooloperation.",
            "decision_if_fail": "Keep the fixedcomplete result; no sign/window/rank/weight rescue or paidfailure retry."},
        "controls": "No F ormarket officialdispatch. Reuse oldcompletedF onlywhere realpreflight certifies exactoriginal32905support; sharedcostcontrols retained without replay. Alreadyexposed51periods, notfreshOOS.",
        "ledger_usage_at_freeze": usage, "prior_same_scope_settlements": scoped,
        "old_twenty_credit_scope_included": False, "wallet_refreshed_by_generator": False,
        "new_contexts_registered_by_generator": 0, "bindings": bindings,
        "shared_state_registry_ledger_memory_written_by_generator": False}
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"official_plan": artifact(output), "fingerprint": key,
        "scope_prior_actual": usage, "max_new_paid_runs": 1,
        "balance_API_calls": 0, "registry_or_ledger_writes": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
