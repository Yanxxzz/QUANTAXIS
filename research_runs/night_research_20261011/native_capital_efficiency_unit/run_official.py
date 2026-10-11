"""One frozen capitalE experiment under the originalshared gift60 scope; no retry."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse
import hashlib
import json
import os
import sys

UNIT = Path(__file__).resolve().parent
NIGHT = UNIT.parent
ROOT = NIGHT.parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.platform import (PandaClient, ExperimentLedger, dispatch, resume,
                                  fingerprint, _batch_usage, _wallet_funding_models)
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bound(item):
    path = Path(item["path"])
    if sha(path) != item["sha256"]:
        raise ValueError("Frozen official binding changed: " + path.name)
    return path


def compact(job):
    return {k: job.get(k) for k in ("candidate_id", "fingerprint", "state", "reserved",
                                    "actual", "factor_id", "run_id")}


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    temporary.replace(path)


class CheckedClient(PandaClient):
    preparing_dispatch = False
    deadline_utc = None
    observed_gift_deadline_utc = None

    def balance(self):
        value = super().balance()
        if self.preparing_dispatch:
            if self.deadline_utc is None or datetime.now(timezone.utc) >= self.deadline_utc:
                raise ValueError("Human deadline reached before account mutation")
            if not _wallet_funding_models(value) or value["gift"] < 16:
                raise ValueError("Fresh gift wallet/headroom unavailable")
            expiry = datetime.fromisoformat(str(value["gift_expires"]))
            now = datetime.now(timezone.utc)
            zones = (timezone(timedelta(hours=8)), timezone.utc)
            expiries = [expiry.astimezone(timezone.utc)] if expiry.tzinfo else [expiry.replace(tzinfo=z).astimezone(timezone.utc) for z in zones]
            self.observed_gift_deadline_utc = min(expiries)
            alive = all(e > now for e in expiries)
            if not alive:
                raise ValueError("Gift validity unavailable; do not dispatch")
        return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["dispatch", "resume"])
    parser.add_argument("--plan-sha", required=True)
    args = parser.parse_args()
    path = UNIT / "official_plan.json"
    if sha(path) != args.plan_sha:
        raise ValueError("Official plan changed")
    plan = read(path)
    for binding in plan["bindings"].values():
        bound(binding)
    if sha(__file__) != plan["bindings"]["runner"]["sha256"]:
        raise ValueError("Official runtime changed")
    protocol = read(plan["bindings"]["protocol"]["path"])
    candidate = read(plan["bindings"]["candidate"]["path"])
    candidate["code"] = bound(plan["bindings"]["code"]).read_bytes().decode("utf-8")
    proof = read(plan["bindings"]["native_preflight"]["path"])
    key = fingerprint(candidate, candidate["window"], 5, 10)
    if candidate["candidate_id"] != "WC03_E_CAPITAL_EFFICIENCY_NATIVE_CONSERVATIVE_20261011" or candidate.get("direction") != 1:
        raise ValueError("This runner owns only the single registered positiveE definition")
    preparation = read(plan["bindings"]["preparation_review"]["path"])
    if not all(preparation.get(k) is True for k in ("controls_reuse_allowed", "shared_finite_mask_exact", "source_qualification_exact", "original_f_formation_keys_exact", "all_251_decisions_group_ready")):
        raise ValueError("Exact original full-source controls cannot be reused")
    if (key != plan["fingerprint"] or proof["definition_fingerprint"] != key
            or proof.get("research_replay_passed") is not True
            or candidate["strategy_spec"] != protocol["candidate_draft"]["strategy_spec"]):
        raise ValueError("Actual candidate/real preflight differs from registered definition")
    config = read(ROOT / "config/panda-alpha.local.json")
    config["compute"].update(read(NIGHT / "compute_overrides.json")["compute"])
    config["compute"]["estimated_billing_batch_authorization"] = plan["batch_authorization"]
    config["compute"].pop("recharge_batch_authorization", None)
    if (config["compute"].get("recharge_credit_limit") != 0 or config["compute"].get("billing_mode") != "gift_only"
            or config["compute"].get("planning_credit_estimate") != 8 or config["compute"].get("daily_credit_limit") != 60
            or plan["batch_authorization"]["batch_id"] != "GF_NIGHT_GIFT60_20261011"
            or plan["batch_authorization"]["max_total_credits"] != 60
            or plan["batch_authorization"]["max_recharge_credits"] != 0
            or plan["batch_authorization"]["max_runs"] != 30 or plan["max_new_paid_runs"] != 1):
        raise ValueError("Original shared gift60/zero-recharge reservation contract changed")
    assert config["research"]["cycle"] == 5 and config["research"]["groups"] == 10
    candidate.update(plan["research_decision"])
    candidate["data_requirements"] = ["full_a_coverage_pending", "official_pool_increment_pending",
                                      "formal_admission_pending", "post_effective_B_pending"]
    ledger = ExperimentLedger(ROOT / "research_runs/experiments.sqlite3")
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    registered_key = trial_key(strategy_definition(protocol["candidate_draft"], protocol["evaluation_window"], 5, 10))
    if registered_key not in registry.registrations():
        raise ValueError("Economic context was not registered")
    client = CheckedClient(config["platform"])
    client.deadline_utc = datetime.fromisoformat(plan["deadline_utc"])
    def before_dispatch():
        now = datetime.now(timezone.utc)
        if now >= client.deadline_utc:
            raise ValueError("Human dispatch deadline reached")
        if client.observed_gift_deadline_utc is not None and now >= client.observed_gift_deadline_utc:
            raise ValueError("Gift expiry reached before account mutation")
    response, error = None, None
    try:
        if args.action == "dispatch":
            if datetime.now(timezone.utc) >= datetime.fromisoformat(plan["deadline_utc"]):
                raise ValueError("Human research deadline reached")
            if (UNIT / "official_dispatch_lock.json").exists():
                raise ValueError("Locked owned Run: collect only, never redispatch")
            existing = ledger.get(key)
            if existing:
                response = compact(existing)
            else:
                jobs = ledger.jobs()
                usage = _batch_usage(jobs, plan["batch_authorization"]["batch_id"])
                if any(j["candidate_id"] == candidate["candidate_id"] for j in jobs):
                    raise ValueError("ThisE definition already owns an attempt; sameowned Run only")
                if (any(j["state"] not in ("SETTLED", "CANCELLED") for j in jobs)
                        or usage["total"] + 8 > 60 or usage["recharge"] != 0):
                    raise ValueError("Shared ledger/budget not reconciled")
                registry.append_event("execution_repair", registered_key, {
                    "official_plan": {"path": str(path), "sha256": args.plan_sha},
                    "actual_fingerprint": key, "code": plan["bindings"]["code"],
                    "native_preflight": plan["bindings"]["native_preflight"],
                    "new_contexts": 0, "official_run_limit": 1}, event_id="capital-e-dispatch-binding:"+key)
                client.preparing_dispatch = True
                response = compact(dispatch(candidate, candidate["window"], config, ledger, client,
                    "exploration", native_fixture_reader=read_mongo_gf_fixture,
                    before_dispatch=before_dispatch))
                client.preparing_dispatch = False
        else:
            result = resume(key, ledger, client, UNIT / "official_results")
            response = compact(result) if "definition" in result else result
            job = ledger.get(key)
            settlement = json.loads(job["receipt"]) if job and job.get("receipt") else {}
            if settlement:
                response["settlement"] = settlement
            if (settlement.get("status") in (3, 6) or response.get("platform_status") in (3, 6)
                    or response.get("state") == "BILLING_REVIEW"):
                save(UNIT / "official_dispatch_lock.json", {"run_id": job.get("run_id"),
                    "reason": "Owned Run failure or billing anomaly; no automatic retry"})
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
        job = ledger.get(key)
        if job and job["state"] not in ("SETTLED", "CANCELLED"):
            save(UNIT / "official_dispatch_lock.json", {"run_id": job.get("run_id"),
                "state": job["state"], "reason": type(exc).__name__, "same_owned_run_only": True})
        raise
    finally:
        usage = _batch_usage(ledger.jobs(), plan["batch_authorization"]["batch_id"])
        job = ledger.get(key)
        stamp = datetime.now(timezone.utc).isoformat()
        progress = {"action": args.action, "observed_at_utc": stamp, "pid": os.getpid(),
                    "response": response, "error": error, "job": compact(job) if job else None,
                    "official_scope_usage": usage, "registry_total": registry.total}
        save(UNIT / "dispatch_progress.json", progress)
        # Root owns shared state/memory; this runner persists only its owned progress.
        ledger.db.close()
        registry.close()
        print(json.dumps(progress, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
