"""Manual UI ledger for one approved no-order runtime-configuration probe.

The expected outcome is an initialize exception carrying four actual values.
It is not an account backtest, a new alpha, or permission to repeat a failed Run.
"""
from __future__ import annotations

import argparse
import ast
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from manage_limit_finance_strategy_run import (
    ACTIONS, ManualStrategyController, bind, read, save,
)
from panda_alpha.platform import ExperimentLedger, fingerprint
from panda_alpha.registry import ResearchRegistry

UNIT = ROOT / "research_runs/finance_full_strategy_short_official_20261015/config_diagnostic_proposal"
PURPOSE = "stateful_runtime_configuration_echo"
MECHANISM = "stateful_strategy_runtime_configuration_diagnostic"
MARKER = "LF_CONFIG_DIAGNOSTIC_ONLY_NO_ORDERS "
FIELDS = {"frequency", "frequency_type", "matching_type", "matching_type_type"}
_REPR_TYPE_QUOTES = re.compile(
    r'''("(?:frequency_type|matching_type_type)"\s*:\s*"<class )\\'([A-Za-z_][A-Za-z0-9_.]*)\\'(>")''')


def no_order_code(code):
    tree = ast.parse(code)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if {node.name for node in functions} != {"initialize", "handle_data"} or len(functions) != 2:
        raise ValueError("Configuration probe must contain only initialize and handle_data callbacks")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            continue
        if isinstance(node, ast.Import) and [alias.name for alias in node.names] == ["json"]:
            continue
        if (isinstance(node, ast.ImportFrom) and node.module == "panda_backtest.api.api"
                and [alias.name for alias in node.names] == ["*"]):
            continue
        raise ValueError("Configuration probe has an unexpected top-level operation")
    if MARKER not in code or not any(isinstance(node, ast.Raise) for node in functions[0].body):
        raise ValueError("Configuration probe must explicitly raise its diagnostic marker")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            plain = isinstance(node.func, ast.Name) and node.func.id in {"str", "type", "RuntimeError"}
            dumps = (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                     and node.func.value.id == "json" and node.func.attr == "dumps")
            if not plain and not dumps:
                raise ValueError("No order, data, network or other runtime calls are allowed in the probe")


def observed_configuration(receipt):
    """Read exact saved error evidence, never infer configuration from UI labels."""
    artifact = receipt.get("diagnostic_error_artifact")
    if not isinstance(artifact, dict) or bind(artifact["path"]) != artifact:
        raise ValueError("Exact diagnostic error artifact and SHA256 are required")
    text = Path(artifact["path"]).read_text(encoding="utf-8-sig")
    texts = [(text, False)]

    def strings(value):
        if isinstance(value, str):
            texts.append((value, True))
        elif isinstance(value, dict):
            for item in value.values():
                strings(item)
        elif isinstance(value, list):
            for item in value:
                strings(item)

    try:
        strings(json.loads(text))
    except json.JSONDecodeError:
        pass
    observations = {}
    for value, decoded_string in texts:
        for suffix in value.split(MARKER)[1:]:
            try:
                observed, _ = json.JSONDecoder().raw_decode(suffix.lstrip())
            except json.JSONDecodeError:
                if not decoded_string:
                    continue
                # The service embeds exception repr inside an already decoded
                # JSON string.  JSON forbids \\' while Python repr escapes its
                # surrounding single quotes.  Repair only these two type-value
                # patterns, never arbitrary escapes, ordinary fields or raw bytes.
                normalized = _REPR_TYPE_QUOTES.sub(
                    lambda m: m.group(1) + "'" + m.group(2) + "'" + m.group(3), suffix)
                if normalized == suffix:
                    continue
                try:
                    observed, _ = json.JSONDecoder().raw_decode(normalized.lstrip())
                except json.JSONDecodeError:
                    continue
            if (isinstance(observed, dict) and set(observed) == FIELDS
                    and all(isinstance(v, str) and v.strip() for v in observed.values())):
                observations[json.dumps(observed, sort_keys=True)] = observed
    if len(observations) != 1:
        raise ValueError("No unique actual four-field diagnostic exception payload was delivered")
    return next(iter(observations.values()))


class ConfigProbeController(ManualStrategyController):
    def __init__(self, plan_sha, *, root=ROOT, unit=UNIT, ledger=None, client=None):
        super().__init__("target", plan_sha, root=root, unit=unit, ledger=ledger, client=client)
        self.folder = self.unit  # This single diagnostic has no economic target/control roles.
        self.category = "source_probe"
        self._recording_existing = False

    def frozen(self):
        if bind(self.folder / "batch_plan.json")["sha256"] != self.plan_sha:
            raise ValueError("Frozen configuration-probe plan changed")
        plan = read(self.folder / "batch_plan.json")
        expected = {"candidate": "candidate.json", "code_file": "Strategy.py",
                    "protocol": "proposal.json", "preflight": "preflight.json",
                    "workflow_export": "workflow_export.json"}
        for field, name in expected.items():
            artifact = plan[field]
            if (bind(artifact["path"]) != artifact
                    or Path(artifact["path"]).resolve() != (self.folder / name).resolve()):
                raise ValueError("Frozen configuration-probe binding differs: " + field)
        candidate = read(plan["candidate"]["path"])
        if (candidate.get("official_task") != "source_diagnostic"
                or candidate.get("economic_evaluation_type") != "transport_diagnostic"
                or candidate.get("diagnostic_purpose") != PURPOSE
                or candidate.get("budget_mechanism_id") != MECHANISM
                or plan.get("cycle") != 1 or plan.get("groups") != 1
                or plan.get("max_new_runs") != 1 or candidate.get("formula")):
            raise ValueError("Only the exact no-economic-context runtime configuration probe is allowed")
        code_path = Path(plan["code_file"]["path"]).resolve()
        if (code_path.read_bytes() != candidate["code"].encode("utf-8")
                or Path(candidate.get("code_path", "")).resolve() != code_path):
            raise ValueError("Configuration submission bytes differ")
        no_order_code(candidate["code"])
        proposal = read(plan["protocol"]["path"])
        if proposal.get("code") != plan["code_file"] or proposal.get("new_economic_contexts") != 0:
            raise ValueError("No-order diagnostic proposal source or zero-context classification changed")
        if proposal.get("diagnostic_window") != plan.get("window"):
            raise ValueError("Diagnostic proposal dates differ from the frozen plan")
        window = plan.get("window", {})
        try:
            start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
            valid_window = (start.isoformat() == window["start"]
                            and end.isoformat() == window["end"] and start < end)
        except (ValueError, TypeError, KeyError):
            valid_window = False
        if not valid_window:
            existing = self.ledger.get(plan["fingerprint"]) if self.ledger is not None else None
            if not self._recording_existing or not existing:
                raise ValueError("Diagnostic ISO dates must satisfy start < end before any wallet or new UI action")
        key = fingerprint(candidate, plan["window"], 1, 1)
        proof = read(plan["preflight"]["path"])
        if (key != plan["fingerprint"] or proof.get("fingerprint") != key
                or proof.get("passed") is not False or proof.get("research_replay_passed") is not True
                or proof.get("economic_evaluation_type") != "transport_diagnostic"):
            raise ValueError("Exact offline transport-diagnostic proof is missing")
        for field in expected:
            if field in proof and proof[field] != plan[field]:
                raise ValueError("Diagnostic preflight source binding changed: " + field)
        self.plan, self.candidate, self.key = plan, candidate, key
        self.category = "source_probe"
        return plan

    def authority(self, **kwargs):
        authority, artifact, deadline = super().authority(**kwargs)
        if authority.get("configuration_diagnostic_explicitly_approved") is not True:
            raise ValueError("Explicit new human configuration-diagnostic permission is required")
        return authority, artifact, deadline

    def registered(self, *, append=False):
        registry = ResearchRegistry(self.root / "research_runs/research_trials.sqlite3")
        try:
            identity = self.plan["economic_identity_key"]
            entry = registry.registrations().get(identity)
            if (not entry or entry["candidate_id"] != self.plan["economic_candidate_id"]
                    or entry["definition"] != self.plan["economic_definition"]
                    or entry["definition"].get("evaluation_type") != "stateful_account"):
                raise ValueError("Configuration evidence must bind the already registered short strategy identity")
            if append:
                registry.append_event("execution_repair", identity, {
                    "phase": "APPROVED_NO_ORDER_RUNTIME_CONFIGURATION_PROBE",
                    "diagnostic_purpose": PURPOSE, "fingerprint": self.key,
                    "batch_plan": bind(self.folder / "batch_plan.json"), "new_contexts": 0,
                }, event_id="manual-ui-configuration-diagnostic:" + self.key)
        finally:
            registry.close()

    def process(self, action, *, object_id=None, receipt_path=None):
        # An old invalid-window attempt still requires truthful ID/billing
        # recording; that exception never authorizes reserve/save/start.
        self._recording_existing = action in {"created", "started", "settle"}
        try:
            result = super().process(action, object_id=object_id, receipt_path=receipt_path)
        finally:
            self._recording_existing = False
        result.update(economic_backtest_success=False, diagnostic_purpose=PURPOSE, new_contexts=0)
        if action == "settle":
            receipt = read(receipt_path)
            try:
                observation = observed_configuration(receipt)
                evidence_error = None
            except (ValueError, OSError, KeyError) as exc:
                observation, evidence_error = None, str(exc)
            completed = receipt["final_status"] == "FAILED" and observation is not None
            result.update(diagnostic_completed=completed, observed_configuration=observation,
                          evidence_error=evidence_error,
                          completion_role="Expected initialize error transport only; no account or economic success")
            save(self.folder / "completed_configuration_review.json", result)
        save(self.folder / "official_progress.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--plan-sha", required=True)
    parser.add_argument("--object-id")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    ledger = ExperimentLedger(ROOT / "research_runs/experiments.sqlite3")
    controller = ConfigProbeController(args.plan_sha, ledger=ledger)
    try:
        print(json.dumps(controller.process(args.action, object_id=args.object_id,
                                           receipt_path=args.receipt), ensure_ascii=False))
    except Exception as exc:
        job = ledger.get(controller.key) if controller.key else None
        save(controller.folder / "official_progress.json", {
            "action": args.action, "fingerprint": controller.key,
            "state": job["state"] if job else None, "error": str(exc),
            "external_mutations": 0, "new_contexts": 0,
            "at_utc": datetime.now(timezone.utc).isoformat()})
        raise
    finally:
        ledger.db.close()


if __name__ == "__main__":
    main()
