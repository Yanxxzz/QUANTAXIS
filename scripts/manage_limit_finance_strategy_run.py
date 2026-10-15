"""Exact-scope local ledger for human-operated official strategy UI actions.

This controller never saves, creates, starts or resumes a remote object.  Its
only account read is PandaClient.balance before audit/reserve.  UI save/start
must each be preceded by the matching local transition and followed by an
observed owned object ID.  Ambiguity is locked to that same fingerprint.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from panda_alpha.platform import (
    ExperimentLedger, PandaClient, _batch_usage, budget_plan, fingerprint,
    settle_receipt,
)
from panda_alpha.registry import ResearchRegistry
from panda_alpha.research_budget import (
    guard as research_guard, limits, load_policy, request as research_request,
)

UNIT = ROOT / "research_runs/finance_full_strategy_official_20261015"
PARENT = "FINANCE_DOMAIN_VALIDATION20_20261015"
RESERVATION = 8.0
TOTAL_LIMIT = 20.0
STATEFUL_MECHANISM = "two_board_financing_stateful_minute_account_migration"
ACTIONS = ("audit", "reserve", "before-save", "created", "before-start", "started", "settle")


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


def utc(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A finite authority timestamp needs an explicit UTC offset")
    return result.astimezone(timezone.utc)


class ManualStrategyController:
    def __init__(self, role, plan_sha, *, root=ROOT, unit=UNIT, ledger=None, client=None):
        self.root, self.unit = Path(root).resolve(), Path(unit).resolve()
        if role not in ("target", "control"):
            raise ValueError("Unknown frozen role")
        self.role, self.folder, self.plan_sha = role, self.unit / role, plan_sha
        self.ledger = ledger
        self.client = client
        self.plan = self.candidate = self.key = None
        self.category = "exploration"

    def frozen(self):
        path = self.folder / "batch_plan.json"
        if bind(path)["sha256"] != self.plan_sha:
            raise ValueError("Frozen batch plan SHA changed")
        plan = read(path)
        for name in ("candidate", "code_file", "protocol", "preflight", "workflow_export"):
            artifact = plan[name]
            if bind(artifact["path"]) != artifact:
                raise ValueError("Frozen artifact changed: " + name)
        if Path(plan["candidate"]["path"]).resolve() != (self.folder / "candidate.json").resolve():
            raise ValueError("Role candidate path differs")
        if Path(plan["preflight"]["path"]).resolve() != (self.folder / "preflight.json").resolve():
            raise ValueError("Role preflight path differs")
        if Path(plan["protocol"]["path"]).resolve() != (self.unit / "protocol.json").resolve():
            raise ValueError("Shared strategy protocol path differs")
        candidate = read(plan["candidate"]["path"])
        task = candidate.get("official_task")
        short = self.unit == self.root / "research_runs/finance_full_strategy_short_official_20261015"
        if (task == "key_confirmation" and
                (not short or candidate.get("budget_mechanism_id") != STATEFUL_MECHANISM)):
            raise ValueError("Reviewed stateful confirmation is limited to the approved short unit and original mechanism")
        if (plan.get("cycle") != 1 or plan.get("groups") != 1
                or plan.get("max_new_runs") != 1
                or task not in {"stateful_strategy", "key_confirmation"}
                or candidate.get("economic_evaluation_type") != "stateful_account"
                or candidate.get("formula")):
            raise ValueError("Expected one stateful account, cycle1/groups1, never a factor group replay")
        code_path = Path(plan["code_file"]["path"]).resolve()
        if (code_path.read_bytes() != candidate["code"].encode("utf-8")
                or Path(candidate.get("code_path", "")).resolve() != code_path):
            raise ValueError("Exact UI submission code bytes differ")
        key = fingerprint(candidate, plan["window"], 1, 1)
        if key != plan["fingerprint"]:
            raise ValueError("Executable/window fingerprint differs")
        proof = read(plan["preflight"]["path"])
        if (proof.get("passed") is not False or proof.get("research_replay_passed") is not True
                or proof.get("economic_evaluation_type") != "stateful_account"
                or proof.get("fingerprint") != key):
            raise ValueError("Bound offline stateful research preflight is missing")
        for name in ("candidate", "code_file", "protocol", "workflow_export"):
            if name in proof and proof[name] != plan[name]:
                raise ValueError("Stateful preflight artifact binding differs: " + name)
        self.plan, self.candidate, self.key = plan, candidate, key
        self.category = "validation" if task == "key_confirmation" else "exploration"
        return plan

    def authority(self, *, enforce_time=True, expected=None):
        path = self.unit / "authorization.json"
        current = bind(path)
        if expected is not None and current != expected:
            raise ValueError("Authority changed after the exact reservation")
        authority = read(path)
        if (authority.get("parent_batch_id") != PARENT
                or authority.get("max_total_credits") != TOTAL_LIMIT
                or authority.get("accept_estimated_charge_risk") is not True
                or authority.get("minute_ioc_migration_accepted") is not True):
            raise ValueError("Current twenty-point authority and minute-IOC acceptance are required")
        quotes = authority.get("human_instructions", authority.get("human_quotes"))
        if (not isinstance(quotes, list) or len(quotes) < 2
                or not all(isinstance(q, str) and q.strip() for q in quotes)):
            raise ValueError("The new request and explicit migration acceptance must remain bound")
        if (self.category == "validation"
                and authority.get("short_window_retry_explicitly_approved") is not True):
            raise ValueError("Reviewed short-window confirmation requires the explicit new human retry approval")
        deadline = utc(authority["execution_valid_until_utc"])
        recorded = utc(authority["recorded_at_utc"])
        if deadline <= recorded or deadline - recorded > timedelta(hours=2, minutes=1):
            raise ValueError("Strategy execution validity must remain within the frozen two-hour interval")
        if enforce_time and not recorded <= datetime.now(timezone.utc) < deadline:
            raise ValueError("Finite execution authority is not currently valid; record/settle existing UI actions only")
        return authority, current, deadline

    def registered(self, *, append=False):
        registry = ResearchRegistry(self.root / "research_runs/research_trials.sqlite3")
        try:
            identity = self.plan["economic_identity_key"]
            entry = registry.registrations().get(identity)
            if (not entry or entry["candidate_id"] != self.candidate["candidate_id"]
                    or entry["definition"] != self.plan["economic_definition"]
                    or entry["definition"].get("evaluation_type") != "stateful_account"):
                raise ValueError("Exact stateful economic identity was not preregistered")
            if append:
                registry.append_event("evaluation", identity, {
                    "phase": "ABOUT_TO_RESERVE_MANUAL_UI_STATEFUL_STRATEGY",
                    "fingerprint": self.key, "batch_plan": bind(self.folder / "batch_plan.json"),
                    "new_contexts": 0, "budget_scope": PARENT,
                }, event_id="manual-ui-strategy-reserve:" + self.key)
        finally:
            registry.close()

    def config(self):
        config = read(self.root / "config/panda-alpha.local.json")
        config["research"].update(cycle=1, groups=1)
        config["compute"] = dict(config["compute"])
        policy = load_policy(config["compute"])
        if (not policy or policy["study_id"] != "pool_candidate_search_20261012"
                or policy["max_total_credits"] != 60 or policy["max_runs"] != 20
                or policy["max_initial_runs_per_mechanism"] != 2
                or policy["max_runs_per_mechanism"] != 4
                or self.ledger.research_state().get("policy") != limits(policy)):
            raise ValueError("Existing global policy and cumulative limits must remain pinned")
        return config, policy

    def active(self, *, allow_self):
        active = [j for j in self.ledger.jobs() if j["state"] not in ("SETTLED", "CANCELLED")]
        if any(not allow_self or j["fingerprint"] != self.key for j in active):
            raise ValueError("Another unresolved job must be reconciled before any new UI action")

    def lock(self, reason):
        job = self.ledger.get(self.key) if self.key else None
        if job:
            save(self.folder / "failure_lock.json", {
                "fingerprint": self.key, "state": job["state"],
                "factor_id": job.get("factor_id"), "run_id": job.get("run_id"),
                "reason": str(reason), "no_automatic_resend": True,
                "action": "Reconcile the same owned workflow/Run; never repeat save/start blindly",
            })

    def existing_guard(self, *, enforce_time):
        job = self.ledger.get(self.key)
        if not job:
            raise ValueError("Exact fingerprint has not been reserved")
        definition = json.loads(job["definition"])
        manual = definition.get("manual_strategy_ui", {})
        if (manual.get("batch_plan") != bind(self.folder / "batch_plan.json")
                or manual.get("workflow_export") != self.plan["workflow_export"]):
            raise ValueError("Reserved manual workflow bindings differ")
        self.authority(enforce_time=enforce_time, expected=manual["mother_authority"])
        _, policy = self.config()
        self.registered()
        self.active(allow_self=True)
        used = _batch_usage(self.ledger.jobs(), PARENT)
        if used["total"] > TOTAL_LIMIT or used["recharge"] > TOTAL_LIMIT:
            raise ValueError("Mother cumulative twenty-point budget is exceeded")
        for grant in definition["billing_authorization"].values():
            if (used["total"] > grant["max_total_credits"]
                    or used["recharge"] > grant["max_recharge_credits"]
                    or used["runs"] > grant["max_runs"]):
                raise ValueError("The exact child cumulative grant is exhausted")
        if enforce_time:
            other_jobs = [j for j in self.ledger.jobs() if j["fingerprint"] != self.key]
            check = research_guard(policy, research_request(policy, self.candidate, self.category),
                                   other_jobs, self.ledger.research_state(), job["reserved"])
            if check["blocker"]:
                raise ValueError(check["blocker"])
        return job

    def preview(self):
        _, authority_binding, deadline = self.authority()
        self.registered()
        self.active(allow_self=False)
        if self.ledger.get(self.key):
            raise ValueError("Exact fingerprint already exists; record/settle only, never reserve twice")
        if (self.folder / "failure_lock.json").exists():
            raise ValueError("Role is failure-locked; no automatic retry")
        config, policy = self.config()
        # Verify every reviewed artifact before even the free account read.
        research_request(policy, self.candidate, self.category)
        used = _batch_usage(self.ledger.jobs(), PARENT)
        grant = {
            "batch_id": PARENT, "fingerprints": [self.key],
            "valid_from": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
            "valid_until": deadline.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            "max_runs": used["runs"] + 1, "max_new_runs": 1,
            "max_total_credits": used["total"] + RESERVATION,
            "max_recharge_credits": used["recharge"] + RESERVATION,
            "new_run_credit_budget": RESERVATION, "accept_estimated_charge_risk": True,
        }
        if any(not math.isfinite(grant[name]) or grant[name] > TOTAL_LIMIT for name in
               ("max_runs", "max_total_credits", "max_recharge_credits")):
            raise ValueError("Existing mother scope lacks an eight-point reservation")
        compute = config["compute"]
        compute.update(daily_credit_limit=TOTAL_LIMIT,
            recharge_credit_limit=grant["max_recharge_credits"],
            planning_credit_estimate=RESERVATION, cost_ceiling=None,
            cost_ceiling_verified_at=None, cost_ceiling_source=None,
            server_enforced_cap=False, billing_mode="gift_priority",
            allow_estimated_billing=True, estimated_billing_batch_authorization=grant,
            recharge_batch_authorization=grant, allocation_mode="shared_priority",
            planning_cost_source="Manual UI exact one-run eight-point estimate, not server cap",
            estimate_verified_at=datetime.now(timezone.utc).date().isoformat())
        client = self.client or PandaClient(config["platform"])
        wallet = client.balance()  # The only permitted account read in this controller.
        save(self.folder / "latest_wallet.json", wallet)
        funds = budget_plan([dict(self.candidate, experiment_category=self.category)], compute,
            wallet, jobs=self.ledger.jobs(), study_state=self.ledger.research_state())
        save(self.folder / "latest_budget_plan.json", funds)
        return config, policy, used, grant, wallet, funds, authority_binding

    def process(self, action, *, object_id=None, receipt_path=None):
        if action not in ACTIONS:
            raise ValueError("Unknown local controller action")
        self.frozen()
        if action in ("audit", "reserve"):
            config, policy, used, grant, wallet, funds, authority_binding = self.preview()
            if action == "reserve":
                if (not funds["dispatch_ready"] or len(funds["selected"]) != 1
                        or funds["selected"][0]["candidate_id"] != self.candidate["candidate_id"]
                        or funds["reservation_credit_estimate"] != RESERVATION):
                    raise ValueError(funds["blocker"] or "No exact stateful budget slot")
                # Refresh all local guards after the wallet read, before reservation.
                self.frozen()
                self.authority(expected=authority_binding)
                self.active(allow_self=False)
                if _batch_usage(self.ledger.jobs(), PARENT) != used:
                    raise ValueError("Mother usage changed before reservation")
                self.registered(append=True)
                permissions = {"estimated_billing": grant, "recharge": grant}
                definition = {
                    "candidate": self.candidate, "window": self.plan["window"], "cycle": 1, "groups": 1,
                    "billing_authorization": permissions, "cost_basis": "authorized_estimate",
                    "cost_ceiling": None, "reservation_credit_estimate": RESERVATION,
                    "daily_credit_limit": TOTAL_LIMIT, "official_test_not_pool_admission": True,
                    "research_budget": research_request(policy, self.candidate, self.category),
                    "manual_strategy_ui": {"batch_plan": bind(self.folder / "batch_plan.json"),
                        "mother_authority": authority_binding,
                        "workflow_export": self.plan["workflow_export"],
                        "preflight": self.plan["preflight"], "external_network_mutations": 0},
                }
                save(self.folder / "funding_subgrant.json", {
                    "mother_authority": authority_binding, "scope_usage_before": used,
                    "grant": grant, "manual_UI_only": True, "no_automatic_retry": True})
                self.ledger.reserve(self.key, self.candidate, definition, self.category,
                                    RESERVATION, TOTAL_LIMIT, wallet)
            result = {"action": action, "fingerprint": self.key,
                "state": self.ledger.get(self.key)["state"] if self.ledger.get(self.key) else None,
                "scope_before": used, "ready": funds["dispatch_ready"], "selected": funds["selected"],
                "blocker": funds["blocker"], "external_mutations": 0}
        else:
            job = self.existing_guard(enforce_time=action in ("before-save", "before-start"))
            if action in ("before-save", "before-start"):
                if (self.folder / "failure_lock.json").exists():
                    raise ValueError("Ambiguous/failed UI attempt is locked; reconcile the same object")
                expected, following = (("RESERVED", "CREATING") if action == "before-save"
                                       else ("CREATED", "DISPATCHING"))
                if job["state"] != expected:
                    self.lock("Repeated or ambiguous " + action)
                    raise ValueError("Unexpected UI state; no repeated save/start is authorized")
                self.ledger.transition(self.key, expected, following)
            elif action in ("created", "started"):
                if (not isinstance(object_id, str)
                        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", object_id) is None):
                    self.lock("Missing or ambiguous observed UI object ID")
                    raise ValueError("An exact observed owned UI object ID is required")
                expected, following, field = (("CREATING", "CREATED", "factor_id") if action == "created"
                                              else ("DISPATCHING", "RUNNING", "run_id"))
                if job["state"] == following and job.get(field) == object_id:
                    pass  # Idempotent recording does not authorize another UI action.
                elif job["state"] != expected:
                    self.lock("Object ID does not reconcile the expected UI state")
                    raise ValueError("Unexpected state or conflicting object ID; investigate the same job")
                else:
                    self.ledger.transition(self.key, expected, following, **{field: object_id})
            elif action == "settle":
                if not receipt_path:
                    raise ValueError("Settlement requires the verified official receipt JSON")
                receipt = read(receipt_path)
                if not job.get("run_id") or receipt.get("run_id") != job["run_id"]:
                    raise ValueError("Receipt does not match the observed owned strategy Run")
                settle_receipt(self.key, self.ledger, receipt)
                if receipt["final_status"] != "SUCCESS" or receipt["actual"] > job["reserved"]:
                    self.lock("Failed/billing-stop or reservation-overrun execution; no automatic resend")
            current = self.ledger.get(self.key)
            result = {"action": action, "external_mutations": 0,
                      **{name: current.get(name) for name in
                         ("candidate_id", "fingerprint", "state", "reserved", "actual", "factor_id", "run_id")}}
        result["at_utc"] = datetime.now(timezone.utc).isoformat()
        save(self.folder / "official_progress.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--role", required=True, choices=("target", "control"))
    parser.add_argument("--plan-sha", required=True)
    parser.add_argument("--object-id")
    parser.add_argument("--receipt")
    parser.add_argument("--unit", choices=("full", "short"), default="full",
                        help="Original full window or explicitly approved three-month comparison")
    args = parser.parse_args()
    ledger = ExperimentLedger(ROOT / "research_runs/experiments.sqlite3")
    unit = (UNIT if args.unit == "full" else
            ROOT / "research_runs/finance_full_strategy_short_official_20261015")
    controller = ManualStrategyController(args.role, args.plan_sha, unit=unit, ledger=ledger)
    try:
        result = controller.process(args.action, object_id=args.object_id, receipt_path=args.receipt)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        job = ledger.get(controller.key) if controller.key else None
        save(controller.folder / "official_progress.json", {
            "action": args.action, "fingerprint": controller.key,
            "state": job["state"] if job else None, "error": str(exc),
            "external_mutations": 0, "at_utc": datetime.now(timezone.utc).isoformat()})
        raise
    finally:
        ledger.db.close()


if __name__ == "__main__":
    main()
