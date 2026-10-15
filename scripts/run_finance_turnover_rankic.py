"""Audit, dispatch or collect one frozen external-score native research replay.

The target and control share the existing FINANCE_DOMAIN_VALIDATION20_20261015
mother budget.  This controller never registers another economic definition,
resets that budget, retries an ambiguous mutation or claims native finance input.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from panda_alpha.native_preflight import verify_native_preflight
from panda_alpha.platform import (
    ExperimentLedger, PandaClient, _batch_usage, budget_plan, dispatch,
    fingerprint, resume,
)
from panda_alpha.registry import ResearchRegistry
from panda_alpha.research_budget import limits, load_policy
from prepare_finance_turnover_rankic import read_retained_close_fixture

UNIT = ROOT / "research_runs/finance_turnover_rankic_20261015"
PARENT_BATCH = "FINANCE_DOMAIN_VALIDATION20_20261015"
RESERVATION = 8.0
MOTHER_LIMIT = 20.0


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def bind(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
                    + "\n", encoding="utf-8")


def bound(item):
    path = Path(item["path"]).resolve()
    if bind(path)["sha256"] != item["sha256"]:
        raise ValueError("Frozen artifact changed: " + str(path))
    return path


def utc(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Authority needs an explicit UTC offset")
    return result.astimezone(timezone.utc)


def check_authority(expected=None):
    path = UNIT / "authorization.json"
    if expected is not None and bind(path) != expected:
        raise ValueError("Human authority changed; review latest instruction")
    authority = read(path)
    if (authority.get("parent_batch_id") != PARENT_BATCH
            or authority.get("selected_new_factor") is not True
            or authority.get("max_total_credits") != MOTHER_LIMIT
            or authority.get("accept_estimated_charge_risk") is not True):
        raise ValueError("Current selected-factor mother authority does not match")
    # Preserve the actual human text, rather than inferring permission from time.
    texts = authority.get("human_instructions", authority.get("human_quotes"))
    if texts is None:
        texts = [authority.get("human_instruction"), authority.get("new_human_instruction")]
    if (not isinstance(texts, list) or len(texts) < 2
            or not all(isinstance(text, str) and text.strip() for text in texts)):
        raise ValueError("The two current human instructions must remain bound")
    deadline = utc(authority["execution_valid_until_utc"])
    if datetime.now(timezone.utc) >= deadline:
        raise ValueError("Finite execution validity expired; collect existing Run only")
    # A stored finite controller interval must not silently become indefinite.
    if "recorded_at_utc" in authority:
        recorded = utc(authority["recorded_at_utc"])
        if deadline <= recorded or deadline - recorded > timedelta(hours=2, minutes=1):
            raise ValueError("Execution validity exceeds the frozen two-hour interval")
    return authority, deadline


def check_frozen(folder, plan_sha):
    plan_path = folder / "batch_plan.json"
    if bind(plan_path)["sha256"] != plan_sha:
        raise ValueError("Frozen batch plan changed")
    plan = read(plan_path)
    candidate_path = bound(plan["candidate"])
    if candidate_path != (folder / "candidate.json").resolve():
        raise ValueError("Role candidate path does not match the prepared unit")
    if bound(plan["protocol"]) != (UNIT / "protocol.json").resolve():
        raise ValueError("Shared protocol path differs")
    if bound(plan["preflight"]) != (folder / "preflight.json").resolve():
        raise ValueError("Role preflight path differs")
    candidate = read(candidate_path)
    if (plan.get("cycle") != 5 or plan.get("groups") != 10
            or plan.get("max_new_runs") != 1
            or candidate.get("official_task") != "native_screen"
            or candidate.get("native_preflight_purpose") != "source_qualified_projection_research"
            or candidate.get("formula")):
        raise ValueError("Expected one exact five-day ten-group external-score research replay")
    code_path = Path(candidate.get("code_path", "")).resolve()
    if not code_path.is_file() or code_path.read_bytes() != candidate["code"].encode("utf-8"):
        raise ValueError("Submission file bytes differ from the frozen code")
    if "code_file" in plan and bound(plan["code_file"]) != code_path:
        raise ValueError("Frozen code file binding differs")
    if Path(candidate.get("native_preflight_path", "")).resolve() != (folder / "preflight.json").resolve():
        raise ValueError("Candidate preflight path differs")
    key = fingerprint(candidate, plan["window"], 5, 10)
    if key != plan["fingerprint"]:
        raise ValueError("Code/window/source fingerprint changed")
    return plan, candidate, key


def registered(plan, candidate, *, append=False):
    registry = ResearchRegistry(ROOT / "research_runs/research_trials.sqlite3")
    try:
        identity = plan["economic_identity_key"]
        entry = registry.registrations().get(identity)
        if (not entry or entry["candidate_id"] != candidate["candidate_id"]
                or entry["definition"] != plan["economic_definition"]):
            raise ValueError("Exact economic identity was not preregistered")
        if append:
            registry.append_event("evaluation", identity, {
                "phase": "ABOUT_TO_DISPATCH_FINANCE_TURNOVER_EXTERNAL_SCORE_RANKIC",
                "fingerprint": plan["fingerprint"],
                "preflight": plan["preflight"], "new_contexts": 0,
                "budget_scope": PARENT_BATCH,
                "native_financial_fields_used": False,
                "formal_factor_admission": False,
            }, event_id="finance-turnover-rankic-dispatch:" + plan["fingerprint"])
    finally:
        registry.close()


def check_policy(config, ledger):
    policy = load_policy(config["compute"])
    if (not policy or policy["study_id"] != "pool_candidate_search_20261012"
            or policy["max_total_credits"] != 60 or policy["max_runs"] != 20
            or policy["max_initial_runs_per_mechanism"] != 2
            or policy["max_runs_per_mechanism"] != 4):
        raise ValueError("Existing global research limits must remain unchanged")
    pinned = ledger.research_state().get("policy")
    if pinned is None or pinned != limits(policy):
        raise ValueError("The existing shared study policy must remain pinned")


def child_grant(used, deadline):
    if any(type(used[name]) not in (int, float) or not math.isfinite(used[name])
           or used[name] < 0 for name in ("runs", "total", "recharge")):
        raise ValueError("Mother-scope accounting is invalid")
    # These are absolute cumulative ledger limits, not a fresh twenty points.
    grant = {
        "batch_id": PARENT_BATCH,
        "valid_from": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        "valid_until": deadline.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
        "max_runs": used["runs"] + 1, "max_new_runs": 1,
        "max_total_credits": used["total"] + RESERVATION,
        "max_recharge_credits": used["recharge"] + RESERVATION,
        "new_run_credit_budget": RESERVATION,
        "accept_estimated_charge_risk": True,
    }
    if any(grant[name] > MOTHER_LIMIT for name in
           ("max_runs", "max_total_credits", "max_recharge_credits")):
        raise ValueError("Mother twenty-point scope lacks a conservative eight-point reservation")
    return grant


class FrozenClient(PandaClient):
    def __init__(self, config, guard):
        super().__init__(config)
        self.guard = guard

    def create(self, *args, **kwargs):
        self.guard()
        return super().create(*args, **kwargs)

    def start(self, *args, **kwargs):
        self.guard()
        return super().start(*args, **kwargs)


def collect_job(key, ledger, client, folder):
    existing = ledger.get(key)
    if not existing:
        raise ValueError("No existing owned Run; collection never creates one")
    if not existing.get("run_id"):
        save(folder / "failure_lock.json", {
            "fingerprint": key, "state": existing["state"],
            "factor_id": existing.get("factor_id"),
            "reason": "Ambiguous mutation without an owned Run ID; reconcile the same job",
            "no_automatic_resend": True,
        })
    job = existing if existing["state"] == "SETTLED" else resume(
        key, ledger, client, folder / "official_results")
    if job.get("state") == "SETTLED":
        receipt = json.loads(job["receipt"])
        if receipt.get("status") != 2 or receipt.get("reservation_overrun"):
            save(folder / "failure_lock.json", {
                "fingerprint": key, "run_id": job.get("run_id"),
                "execution_status": receipt.get("status"), "billing_settled": True,
                "reservation_overrun": receipt.get("reservation_overrun", False),
                "no_automatic_resend": True,
            })
        raw = folder / "official_results" / (job["run_id"] + ".json")
        if receipt.get("status") == 2 and not raw.exists():
            save(raw, client.result(job["run_id"]))
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("audit", "dispatch", "collect"))
    parser.add_argument("--role", required=True, choices=("target", "control"))
    parser.add_argument("--plan-sha", required=True)
    args = parser.parse_args()
    folder = UNIT / args.role
    plan, candidate, key = check_frozen(folder, args.plan_sha)
    config = read(ROOT / "config/panda-alpha.local.json")
    config["research"].update(cycle=5, groups=10)
    ledger = ExperimentLedger(ROOT / "research_runs/experiments.sqlite3")
    try:
        check_policy(config, ledger)
        existing = ledger.get(key)
        if args.action == "collect" or existing:
            if args.action == "audit":
                print(json.dumps({"fingerprint": key, "existing_state": existing["state"],
                    "ready": False, "action": "collect_existing_only", "account_calls": 0,
                    "new_runs": 0, "reserved": 0}, ensure_ascii=False))
                return
            job = collect_job(key, ledger, PandaClient(config["platform"]), folder)
        else:
            authority, deadline = check_authority()
            authority_binding = bind(UNIT / "authorization.json")
            if (folder / "failure_lock.json").exists():
                raise ValueError("Exact role failure is locked; no automatic resend")
            registered(plan, candidate)
            verified = verify_native_preflight(candidate, plan["window"], 5, 10,
                                               fixture_reader=read_retained_close_fixture)
            if (verified.get("passed") is not False
                    or verified.get("research_replay_passed") is not True):
                raise ValueError("Actual source-projection research replay is missing")
            used = _batch_usage(ledger.jobs(), PARENT_BATCH)
            grant = child_grant(used, deadline)
            grant["fingerprints"] = [key]
            compute = dict(config["compute"])
            compute.update(daily_credit_limit=20,
                recharge_credit_limit=grant["max_recharge_credits"],
                planning_credit_estimate=RESERVATION, cost_ceiling=None,
                cost_ceiling_verified_at=None, cost_ceiling_source=None,
                server_enforced_cap=False, billing_mode="gift_priority",
                allow_estimated_billing=True,
                estimated_billing_batch_authorization=grant,
                recharge_batch_authorization=grant, allocation_mode="shared_priority",
                planning_cost_source="Conservative eight-point estimate; not a server billing cap",
                estimate_verified_at=datetime.now(timezone.utc).date().isoformat())
            config["compute"] = compute

            def mutation_guard():
                check_authority(authority_binding)
                check_frozen(folder, args.plan_sha)
                check_policy(config, ledger)
                current = _batch_usage(ledger.jobs(), PARENT_BATCH, exclude_key=key)
                if current != used:
                    raise ValueError("Mother-scope usage changed; investigate before the next mutation")
                for name in ("max_runs", "max_total_credits", "max_recharge_credits"):
                    if child_grant(current, deadline)[name] != grant[name]:
                        raise ValueError("Exact child cumulative limits changed")
                total = _batch_usage(ledger.jobs(), PARENT_BATCH)
                if total["total"] > MOTHER_LIMIT or total["recharge"] > MOTHER_LIMIT:
                    raise ValueError("Mother cumulative budget is exceeded")

            client = FrozenClient(config["platform"], mutation_guard)
            mutation_guard()
            wallet = client.balance()
            save(folder / "latest_wallet.json", wallet)
            funds = budget_plan([dict(candidate, experiment_category="exploration")], compute,
                wallet, jobs=ledger.jobs(), study_state=ledger.research_state())
            save(folder / "latest_budget_plan.json", funds)
            if args.action == "audit":
                print(json.dumps({"role": args.role, "fingerprint": key, "scope": used,
                    "wallet": {name: wallet.get(name) for name in
                               ("gift", "free", "recharge", "observed_at", "gift_expires")},
                    "ready": funds["dispatch_ready"], "blocker": funds["blocker"],
                    "reserved": 0, "new_runs": 0, "preflight": "research-only",
                    "execution_valid_until_utc": authority["execution_valid_until_utc"]},
                    ensure_ascii=False))
                return
            if not funds["dispatch_ready"]:
                raise ValueError(funds["blocker"])
            save(folder / "funding_subgrant.json", {
                "source": "current_direct_human_mother_scope_derived_exact_child",
                "mother_authority": authority_binding, "fingerprint": key,
                "max_new_runs": 1, "new_run_credit_budget": RESERVATION,
                "scope_usage_before": used, "ledger_cumulative_grant": grant,
                "execution_valid_until_utc": authority["execution_valid_until_utc"],
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "no_automatic_retry": True,
            })

            def before_dispatch():
                mutation_guard()
                registered(plan, candidate, append=True)

            try:
                mutation_guard()
                job = dispatch(candidate, plan["window"], config, ledger, client,
                    "exploration", before_dispatch=before_dispatch,
                    native_fixture_reader=read_retained_close_fixture)
            except Exception as exc:
                locked = ledger.get(key)
                if locked:
                    save(folder / "failure_lock.json", {
                        "fingerprint": key, "existing_run": locked.get("run_id"),
                        "factor_id": locked.get("factor_id"), "state": locked["state"],
                        "error": str(exc), "no_automatic_resend": True,
                    })
                raise
        compact = {name: job.get(name) for name in
                   ("candidate_id", "fingerprint", "state", "reserved", "actual", "factor_id", "run_id")}
        if job.get("state") == "SETTLED":
            compact["execution_status"] = json.loads(job["receipt"]).get("status")
        save(folder / "official_progress.json", compact)
        print(json.dumps(compact, ensure_ascii=False))
    finally:
        ledger.db.close()


if __name__ == "__main__":
    main()
