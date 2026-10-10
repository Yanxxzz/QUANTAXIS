"""Quota-aware official experimentation, distinct from portfolio admission.

SQLite reservations are atomic. An ambiguous dispatch is never automatically
retried. Local reservations are not a server-side billing cap; gift-only dispatch
requires evidence of an enforceable cap, or an explicit batch authorization for
the risk of an estimated charge. This distinction is visible in every plan.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import Any


def beijing_day() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def fingerprint(candidate: dict, window: dict, cycle: int, groups: int) -> str:
    definition = {k: candidate.get(k) for k in ("code", "formula", "direction")}
    definition.update(window=window, cycle=cycle, groups=groups)
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


class PandaClient:
    def __init__(self, config: dict):
        self.config = config

    def _invoke(self, command: list[str], timeout: int = 60) -> dict:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, env=env)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            # Do not expose arbitrary CLI stderr, which might include account data.
            raise RuntimeError(f"PandaAI returned invalid JSON (exit {result.returncode})") from None
        if not data.get("success", True):
            raise RuntimeError("PandaAI request failed; inspect the host CLI separately")
        return data

    def cli(self, *args: str, timeout: int = 60) -> dict:
        config = self.config
        if config.get("python") and config.get("site_packages"):
            entry = "import sys; sys.path.insert(0,sys.argv.pop(1)); import cli; cli.main()"
            command = [config["python"], "-X", "utf8", "-B", "-c", entry, config["site_packages"]]
        else:
            command = list(config.get("cli_command", ["pandaai-cli"]))
        return self._invoke(command + ["--json", *args], timeout)

    def balance(self) -> dict:
        raw = self.cli("balance")["balance"]
        return {"total": float(raw["computingPower"]), "gift": float(raw.get("giftBalance", 0)),
                "free": float(raw.get("freeBalance", 0)), "recharge": float(raw.get("rechargeBalance", 0)),
                "gift_expires": raw.get("nextExpireAt"), "observed_at": datetime.now(timezone.utc).isoformat()}

    def create(self, candidate: dict, window: dict, cycle: int, groups: int, name: str) -> str:
        mode = "--formula" if candidate.get("formula") else "--code"
        source = candidate.get("formula") or candidate.get("code")
        if not source:
            raise ValueError("Candidate has no executable definition")
        if mode == "--code" and candidate.get("code_path"):
            path = Path(candidate["code_path"]).resolve()
            if not path.is_file() or path.read_bytes().decode("utf-8") != source:
                raise ValueError("Candidate file differs from the frozen code definition")
            # Documented file mode keeps large financial-source literals out
            # of the Windows command line without changing the definition.
            mode, source = "--file", str(path)
        data = self.cli("factor_create", mode, source, "--name", name,
                        "--start-date", window["start"].replace("-", ""),
                        "--end-date", window["end"].replace("-", ""),
                        "--adjustment-cycle", str(cycle), "--group-number", str(groups),
                        "--factor-direction", str(candidate["direction"]))
        return data["factor_id"]

    def _bridge(self, action: str, object_id: str) -> dict:
        if not self.config.get("python") or not self.config.get("site_packages"):
            raise ValueError("Resume-safe dispatch needs platform.python and platform.site_packages")
        return self._invoke([self.config["python"], "-X", "utf8", "-B",
                             str(Path(__file__).with_name("platform_worker.py")),
                             self.config["site_packages"], action, object_id])

    def start(self, factor_id: str) -> str:
        return self._bridge("start", factor_id)["run_id"]

    def status(self, run_id: str) -> Any:
        return self._bridge("status", run_id)["status"]

    def logs(self, run_id: str) -> dict:
        return self._bridge("logs", run_id)

    def result(self, run_id: str) -> dict:
        return self.cli("factor_result", run_id, timeout=480)


class ExperimentLedger:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            fingerprint TEXT PRIMARY KEY, candidate_id TEXT, category TEXT, day TEXT,
            state TEXT, reserved REAL DEFAULT 0, actual REAL, factor_id TEXT, run_id TEXT,
            definition TEXT, balance_before TEXT, receipt TEXT)""")

    def get(self, key: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE fingerprint=?", (key,)).fetchone()
        return dict(row) if row else None

    def jobs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY rowid")]

    def reserve(self, key: str, candidate: dict, definition: dict, category: str,
                ceiling: float, daily_limit: float, balance: dict, day: str | None = None):
        if category not in ("source_probe", "exploration", "validation"):
            raise ValueError("Unknown research category")
        if not math.isfinite(ceiling) or ceiling <= 0:
            raise ValueError("A positive current cost ceiling is required")
        day = day or beijing_day()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.get(key):
                raise ValueError("This exact candidate/window is already reserved; resume it")
            active = self.db.execute("SELECT count(*) FROM jobs WHERE state NOT IN ('SETTLED','CANCELLED')").fetchone()[0]
            if active:
                raise ValueError("Reconcile the prior job before dispatching another")
            spent = self.db.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM jobs WHERE day=? AND state!='CANCELLED'", (day,)).fetchone()[0]
            if spent + ceiling > daily_limit + 1e-9:
                raise ValueError("Daily experiment budget exhausted")
            if ceiling > balance["total"]:
                raise ValueError("Account balance below reservation")
            self.db.execute("INSERT INTO jobs (fingerprint,candidate_id,category,day,state,reserved,definition,balance_before) VALUES (?,?,?,?,?,?,?,?)",
                            (key, candidate["candidate_id"], category, day, "RESERVED", ceiling,
                             json.dumps(definition, sort_keys=True), json.dumps(balance)))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def transition(self, key: str, expected: str, state: str, **fields):
        allowed = {"factor_id", "run_id", "receipt", "actual"}
        if set(fields) - allowed:
            raise ValueError("Invalid ledger fields")
        assignments = ["state=?"] + [f"{k}=?" for k in fields]
        result = self.db.execute(f"UPDATE jobs SET {','.join(assignments)} WHERE fingerprint=? AND state=?",
                                 (state, *fields.values(), key, expected))
        if result.rowcount != 1:
            raise ValueError("Job changed concurrently or has an unexpected state")


def _batch_authorization(authorization: Any, amount_field: str, minimum: float,
                         key: str | None = None, estimated_risk: bool = False) -> dict:
    if (not isinstance(authorization, dict) or authorization.get("day") != beijing_day()
            or not isinstance(authorization.get("batch_id"), str) or not authorization["batch_id"].strip()):
        raise ValueError("Today's explicit scoped batch authorization is required")
    fingerprints = authorization.get("fingerprints")
    if (not isinstance(fingerprints, list) or not fingerprints or
            any(not isinstance(value, str) or len(value) != 64 or
                any(character not in "0123456789abcdef" for character in value) for value in fingerprints)):
        raise ValueError("Batch authorization requires exact experiment fingerprints")
    amount = authorization.get(amount_field)
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount < minimum:
        raise ValueError(f"Batch authorization {amount_field} must cover the configured credit limit")
    if key is not None and key not in fingerprints:
        raise ValueError("This definition/window is outside the authorized batch fingerprints")
    if estimated_risk and authorization.get("accept_estimated_charge_risk") is not True:
        raise ValueError("The concrete batch must explicitly accept estimated-charge risk")
    return authorization


def budget_plan(candidates: list[dict], compute: dict, balance: dict) -> dict:
    ceiling = compute.get("cost_ceiling")
    if ceiling is not None and (type(ceiling) not in (int, float) or not math.isfinite(ceiling) or ceiling <= 0):
        raise ValueError("cost_ceiling must be a positive finite number or null")
    for name in ("daily_credit_limit", "recharge_credit_limit"):
        amount = compute.get(name, 0)
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    verified = bool(compute.get("cost_ceiling_verified_at") and compute.get("cost_ceiling_source"))
    enforceable = compute.get("server_enforced_cap", False)
    estimated_risk_requested = compute.get("allow_estimated_billing", False)
    estimated_risk_authorized = False
    authorization_problem = None
    if estimated_risk_requested:
        try:
            _batch_authorization(compute.get("estimated_billing_batch_authorization"), "max_total_credits",
                                 float(compute["daily_credit_limit"]), estimated_risk=True)
            estimated_risk_authorized = True
        except ValueError as exc:
            authorization_problem = str(exc)
    if compute.get("recharge_credit_limit", 0) > 0:
        try:
            _batch_authorization(compute.get("recharge_batch_authorization"), "max_recharge_credits",
                                 float(compute["recharge_credit_limit"]))
        except ValueError as exc:
            authorization_problem = str(exc)
    limit = min(float(compute["daily_credit_limit"]), balance["gift"] + float(compute.get("recharge_credit_limit", 0)))
    ready = bool(ceiling and verified and not authorization_problem and (enforceable or estimated_risk_authorized))
    estimate = compute.get("planning_credit_estimate", ceiling)
    if estimate is not None and (type(estimate) not in (int, float) or not math.isfinite(estimate) or estimate <= 0):
        raise ValueError("planning_credit_estimate must be a positive finite number")
    slots = int(limit // (ceiling if ready else estimate)) if (ready or estimate) else 0
    fractions = {k: compute[f"{k}_fraction"] for k in ("source_probe", "exploration", "validation")}
    if any(type(f) not in (int, float) or not math.isfinite(f) or f < 0 for f in fractions.values()) or not math.isclose(sum(fractions.values()), 1):
        raise ValueError("Research category fractions must be nonnegative and sum to one")
    quota = {k: max(1, round(slots * v)) if slots else 0 for k, v in fractions.items()}
    ordered, counters = [], dict.fromkeys(quota, 0)
    for c in candidates:
        category = c.get("experiment_category", "exploration")
        if category not in quota:
            raise ValueError("Unknown experiment category")
        if len(ordered) < slots and counters[category] < quota[category]:
            ordered.append({"candidate_id": c["candidate_id"], "category": category})
            counters[category] += 1
    return {"date_beijing": beijing_day(), "budget": limit, "cost_ceiling": ceiling,
            "dispatch_ready": ready, "server_enforced_cap": enforceable,
            "planning_only_estimate": None if ready else estimate,
            "blocker": None if ready else authorization_problem or "Current pricing/cap evidence or estimated-billing authorization required",
            "authorization_scope": "Exact fingerprints are checked again at dispatch",
            "quota": quota, "selected": ordered, "unspent_slots": max(0, slots - len(ordered)),
            "admission_gates_apply_at": "validation/admission, never source probes or exploration selection"}


def dispatch(candidate: dict, window: dict, config: dict, ledger: ExperimentLedger, client: PandaClient,
             category: str = "exploration", *, before_dispatch=None) -> dict:
    research, compute = config["research"], config["compute"]
    cycle, groups = research["cycle"], research["groups"]
    if candidate.get("direction") not in (0, 1):
        raise ValueError("Direction must have frozen evidence before execution")
    requirements = candidate.get("data_requirements", [])
    if (candidate.get("source", "").startswith("historical") or
        candidate.get("provenance") == "historical_memory_requires_revalidation" or
        any("requires_source:" in str(r) or "revalidate_legacy" in str(r) or "_pending" in str(r)
            or "direction_evidence" in str(r) or "direction_requires_evidence" in str(r) for r in requirements)):
        raise ValueError("Resolve legacy operator/source/direction evidence before official execution")
    key = fingerprint(candidate, window, cycle, groups)
    if ledger.get(key):
        return ledger.get(key)  # Zero duplicate dispatches on resume.
    native_preflight = None
    if candidate.get("code") and not candidate.get("formula"):
        from .native_preflight import verify_native_preflight
        native_preflight = verify_native_preflight(candidate, window, cycle, groups)
    recharge_authorization = None
    if compute.get("recharge_credit_limit", 0) > 0:
        recharge_authorization = _batch_authorization(
            compute.get("recharge_batch_authorization"), "max_recharge_credits",
            float(compute["recharge_credit_limit"]), key)
    if compute.get("allow_estimated_billing", False):
        risk_authorization = _batch_authorization(
            compute.get("estimated_billing_batch_authorization"), "max_total_credits",
            float(compute["daily_credit_limit"]), key, estimated_risk=True)
        if recharge_authorization and recharge_authorization["batch_id"] != risk_authorization["batch_id"]:
            raise ValueError("Recharge and estimated-billing permissions must refer to the same confirmed batch")
    balance = client.balance()
    plan = budget_plan([dict(candidate, experiment_category=category)], compute, balance)
    if not plan["dispatch_ready"] or not plan["selected"]:
        raise ValueError(plan["blocker"] or "No authorized budget slot")
    ceiling = float(compute["cost_ceiling"])
    if compute["billing_mode"] == "gift_only" and balance["gift"] < ceiling:
        raise ValueError("Gift balance cannot fund the next reservation")
    definition = {"candidate": candidate, "window": window, "cycle": cycle, "groups": groups}
    if native_preflight is not None:
        definition["native_preflight"] = native_preflight
    if before_dispatch is not None:
        # Definition registration precedes any account mutation or service run.
        # A failed registration must not reserve funds or create a service job.
        before_dispatch()
    ledger.reserve(key, candidate, definition, category, ceiling, plan["budget"], balance)
    # Unknown responses remain ambiguous; do not silently retry account mutations.
    ledger.transition(key, "RESERVED", "CREATING")
    factor_id = client.create(candidate, window, cycle, groups, "PA-" + key[:16])
    ledger.transition(key, "CREATING", "CREATED", factor_id=factor_id)
    ledger.transition(key, "CREATED", "DISPATCHING")
    run_id = client.start(factor_id)
    ledger.transition(key, "DISPATCHING", "RUNNING", run_id=run_id)
    return ledger.get(key)


def settle_receipt(key: str, ledger: ExperimentLedger, receipt: dict) -> dict:
    """Reconcile an ambiguous/zero-charge execution with an explicit billing receipt.

    The caller must verify the attached official billing record. Balance deltas
    alone are insufficient to release a zero-charge or midnight-crossing job.
    """
    job = ledger.get(key)
    if not job or job["state"] == "SETTLED":
        raise ValueError("Unknown/already settled job")
    amount = receipt.get("actual")
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
        raise ValueError("Receipt must state a finite nonnegative charge")
    if (not receipt.get("official_receipt_path") or not receipt.get("official_receipt_sha256") or
            not receipt.get("verified_by") or receipt.get("run_id") != job["run_id"] or
            receipt.get("final_status") not in ("SUCCESS", "FAILED", "BILLING_STOP")):
        raise ValueError("Final status, verified receipt and matching Run ID required")
    artifact = Path(receipt["official_receipt_path"])
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != receipt["official_receipt_sha256"]:
        raise ValueError("Official billing artifact has changed")
    ledger.transition(key, job["state"], "SETTLED", actual=amount, receipt=json.dumps(receipt))
    return ledger.get(key)


def resume(key: str, ledger: ExperimentLedger, client: PandaClient, output_dir: Path) -> dict:
    job = ledger.get(key)
    if not job:
        raise ValueError("Unknown job")
    if not job["run_id"]:
        return {"state": job["state"], "action": "Read factor_list/factor_info and reconcile ambiguous mutation before retrying"}
    if job["state"] == "SETTLED":
        return job
    status = client.status(job["run_id"])
    if status not in (2, 3, 6):
        return {"state": job["state"], "platform_status": status, "run_id": job["run_id"]}
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / (job["run_id"] + ".json")
    if status == 2 and not result_path.exists():
        result_path.write_text(json.dumps(client.result(job["run_id"]), ensure_ascii=False), encoding="utf-8")
    failure = {}
    if status == 3:
        log_path = output_dir / (job["run_id"] + ".logs.json")
        try:
            if not log_path.exists():
                log_path.write_text(json.dumps(client.logs(job["run_id"]), ensure_ascii=False), encoding="utf-8")
            logs = json.loads(log_path.read_text(encoding="utf-8"))
            failure = {"failure_log_path": str(log_path),
                       "failure_log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
                       "failure_logs_complete": logs.get("complete", False),
                       "node_errors": logs.get("errors", [])}
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
            # A read-only diagnostic failure must not obscure billing settlement.
            failure = {"failure_logs_pending": True,
                       "failure_log_action": "Read the same owned Run ID's logs; do not redispatch"}
    before, after = json.loads(job["balance_before"]), client.balance()
    actual = before["total"] - after["total"]
    if not math.isfinite(actual) or actual <= 0:
        return {"state": "BILLING_PENDING", "run_id": job["run_id"], "platform_status": status,
                "action": "Re-query balance after settlement; no re-dispatch or new reservation", **failure}
    receipt = {"run_id": job["run_id"], "status": status, "actual": actual,
               "gift_delta": before["gift"] - after["gift"],
               "recharge_delta": before["recharge"] - after["recharge"],
               "attribution": "serial balance delta, provisional; external spend/grants may confound",
               "reservation_overrun": actual > job["reserved"], "day_before": job["day"], "day_after": beijing_day(),
               **failure}
    if receipt["day_before"] != receipt["day_after"]:
        return {"state": "BILLING_REVIEW", "receipt": receipt, "action": "Daily expiry/grant prevents automatic balance-delta settlement"}
    if actual > job["reserved"] or receipt["recharge_delta"] > 0:
        return {"state": "BILLING_REVIEW", "receipt": receipt, "action": "Review budget overrun/recharge debit; future dispatch remains locked"}
    ledger.transition(key, job["state"], "SETTLED", actual=actual, receipt=json.dumps(receipt))
    return ledger.get(key)
