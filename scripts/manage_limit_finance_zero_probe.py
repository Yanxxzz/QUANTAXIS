"""Manual UI accounting for one exact, read-only zero-trade gate observation.

Uses the existing twenty-point mother scope and original stateful mechanism.
No remote save/start method, new alpha registration or financing loader exists
here. The only account read inherited from the controller is a free wallet read.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from manage_limit_finance_strategy_run import (
    ACTIONS, STATEFUL_MECHANISM, ManualStrategyController, bind, read, save,
)
from panda_alpha.platform import ExperimentLedger, fingerprint
from panda_alpha.registry import ResearchRegistry

RELATIVE_UNIT = "research_runs/finance_full_strategy_short_official_20261015/close_phase_ready/zero_gate_probe"
UNIT = ROOT / RELATIVE_UNIT
WINDOW = {"start": "2026-07-01", "end": "2026-07-02"}
PURPOSE = "zero_trade_minute_gate_observation"


def literals(code):
    result = {}
    for node in ast.parse(code).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                result[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return result


def no_order_code(code):
    """Admit callbacks and pure counting only, with one read-only data.get path."""
    tree = ast.parse(code)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    callbacks = {"initialize", "before_trading", "handle_data", "after_trading"}
    names = {n.name for n in functions}
    if not callbacks <= names or len(names) != len(functions) or any(
            n not in callbacks and not n.startswith("_probe_") for n in names):
        raise ValueError("Only frozen zero-gate callbacks and pure _probe_ helpers are permitted")
    guarded_raises = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in tree.body:
            raise ValueError("No order, file, network or deferred imports are permitted")
        if isinstance(node, ast.If):
            guarded_raises.update(id(n) for n in ast.walk(node) if isinstance(n, ast.Raise))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.decorator_list and not node.args.defaults:
            continue
        if isinstance(node, ast.Import) and all(a.name in {"json", "math"} and a.asname is None for a in node.names):
            continue
        if (isinstance(node, ast.ImportFrom) and node.module == "panda_backtest.api.api"
                and node.level == 0 and len(node.names) == 1 and node.names[0].name == "*"):
            continue
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"FROZEN_PROBE_ROSTER", "FROZEN_PROBE_WINDOW"}):
            ast.literal_eval(node.value)
            continue
        raise ValueError("No order, data API, file, network or executable top-level operation is allowed")
    plain_calls = {"str", "float", "int", "bool", "len", "list", "dict", "min", "max",
                   "isinstance", "type", "print", "RuntimeError"} | names
    dictionary_methods = {"get", "values", "items", "setdefault", "update", "isdigit"}
    allowed_attributes = dictionary_methods | {
        "dumps", "floor", "isfinite", "run_info", "frequency", "matching_type",
        "trade_date", "trade_time", "strftime", "_zero_probe", "close", "limit_up", "limit_down", "volume"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Lambda, ast.With, ast.AsyncWith,
                             ast.Global, ast.Nonlocal, ast.Delete, ast.Yield, ast.YieldFrom, ast.Await)):
            raise ValueError("No order, reflection or indirect execution capability is permitted")
        if isinstance(node, ast.Name) and (node.id.startswith("__") or
                any(term in node.id.lower() for term in ("order", "panda_data", "load_input", "__import__"))):
            # JSON observation keys such as no_orders are constants, never executable names.
            raise ValueError("No order or data-loader names are permitted")
        if isinstance(node, ast.Attribute) and node.attr not in allowed_attributes:
            raise ValueError("No order, data API, file, network or reflection attributes are permitted: " + node.attr)
        if isinstance(node, ast.Raise):
            if (id(node) not in guarded_raises or not isinstance(node.exc, ast.Call)
                    or not isinstance(node.exc.func, ast.Name) or node.exc.func.id != "RuntimeError"
                    or len(node.exc.args) != 1 or not isinstance(node.exc.args[0], ast.Constant)
                    or not str(node.exc.args[0].value).startswith("LF_ZERO_GATES ")):
                raise ValueError("Only conditional LF_ZERO_GATES invariant guards may raise")
        if isinstance(node, ast.Call):
            f = node.func
            plain = isinstance(f, ast.Name) and f.id in plain_calls
            base = f.value if isinstance(f, ast.Attribute) else None
            while isinstance(base, (ast.Attribute, ast.Subscript)):
                base = base.value
            base_name = base.id if isinstance(base, ast.Name) else None
            method = (isinstance(f, ast.Attribute) and f.attr in dictionary_methods
                      and (base_name in {"state", "record", "value", "output", "FROZEN_PROBE_ROSTER"}
                           or (base_name == "data" and isinstance(f.value, ast.Name) and f.attr == "get")))
            module = (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                      and (f.value.id, f.attr) in {("json", "dumps"), ("math", "floor"), ("math", "isfinite")})
            clock = (isinstance(f, ast.Attribute) and f.attr == "strftime"
                     and isinstance(f.value, ast.Attribute) and f.value.attr == "trade_time"
                     and isinstance(f.value.value, ast.Name) and f.value.value.id == "context")
            if not (plain or method or module or clock):
                raise ValueError("No order, data API, file, network or reflection calls are permitted")
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "data" and f.attr != "get":
                raise ValueError("Only the existing callback data.get may read a quote")
    return literals(code)


class ZeroGateProbeController(ManualStrategyController):
    def __init__(self, plan_sha, *, root=ROOT, ledger=None, client=None):
        unit = Path(root).resolve() / RELATIVE_UNIT
        super().__init__("target", plan_sha, root=root, unit=unit, ledger=ledger,
                         client=client, reservation=2)
        # Reuse the existing narrow continued-close authority checks, not a new
        # mechanism, config-probe allowance or twenty-point scope.
        self.close_phase = True
        self.category = "validation"

    def frozen(self):
        if bind(self.folder / "batch_plan.json")["sha256"] != self.plan_sha:
            raise ValueError("Frozen zero-gate plan changed")
        plan = read(self.folder / "batch_plan.json")
        expected = {"candidate": self.folder / "candidate.json", "code_file": self.folder / "Strategy.py",
                    "preflight": self.folder / "preflight.json", "protocol": self.unit / "protocol.json"}
        for name in (*expected, "workflow_export"):
            artifact = plan[name]
            path = Path(artifact["path"]).resolve()
            if bind(path) != artifact or (name in expected and path != expected[name].resolve()):
                raise ValueError("Frozen zero-gate binding differs: " + name)
            if name == "workflow_export" and path.parent != self.folder:
                raise ValueError("Workflow UI evidence must belong to the exact target folder")
        candidate = read(plan["candidate"]["path"])
        if (candidate.get("official_task") != "key_confirmation"
                or candidate.get("budget_mechanism_id") != STATEFUL_MECHANISM
                or candidate.get("economic_evaluation_type") != "execution_diagnostic"
                or candidate.get("diagnostic_purpose") != PURPOSE
                or candidate.get("formula") or plan.get("window") != WINDOW
                or plan.get("cycle") != 1 or plan.get("groups") != 1 or plan.get("max_new_runs") != 1):
            raise ValueError("Only the exact two-day no-economic-result original-mechanism probe is allowed")
        code_path = Path(plan["code_file"]["path"])
        if (code_path.read_bytes() != candidate["code"].encode("utf-8")
                or Path(candidate.get("code_path", "")).resolve() != code_path.resolve()):
            raise ValueError("Exact zero-gate submission bytes differ")
        constants = no_order_code(candidate["code"])
        protocol = read(plan["protocol"]["path"])
        original_binding = protocol["original_registered_plan"]
        original_path = self.unit.parent / "target/batch_plan.json"
        if (Path(original_binding["path"]).resolve() != original_path.resolve()
                or bind(original_path) != original_binding):
            raise ValueError("Original completed close-target plan binding differs")
        original = read(original_path)
        if bind(original["candidate"]["path"]) != original["candidate"]:
            raise ValueError("Original registered candidate binding differs")
        source = protocol["source_roster"]
        if (source != original["code_file"] or bind(source["path"]) != source
                or protocol.get("window") != WINDOW or protocol.get("no_orders") is not True
                or protocol.get("no_financing_or_source_reload") is not True
                or protocol.get("economic_results_forbidden") is not True
                or protocol.get("new_economic_contexts") != 0
                or protocol.get("original_economic_identity_key") != original["economic_identity_key"]):
            raise ValueError("Read-only source/zero-context/window contract differs")
        old_roster = literals(Path(source["path"]).read_text(encoding="utf-8"))["FROZEN_ENTRY_ROSTER"]
        expected_roster = {day: sorted(old_roster[day])[:5] for day in (WINDOW["start"], WINDOW["end"])}
        if (protocol.get("roster") != expected_roster
                or constants.get("FROZEN_PROBE_ROSTER") != expected_roster
                or constants.get("FROZEN_PROBE_WINDOW") != WINDOW):
            raise ValueError("Probe roster must retain the original first five on the first two dates")
        if (plan.get("economic_identity_key") != original["economic_identity_key"]
                or plan.get("economic_definition") != original["economic_definition"]
                or plan.get("economic_candidate_id") != read(original["candidate"]["path"])["candidate_id"]):
            raise ValueError("Source-only alias must bind the original close-target economic identity")
        key = fingerprint(candidate, WINDOW, 1, 1)
        proof = read(plan["preflight"]["path"])
        if (key != plan["fingerprint"] or proof.get("fingerprint") != key
                or proof.get("passed") is not False or proof.get("research_replay_passed") is not True
                or proof.get("economic_evaluation_type") != "execution_diagnostic"):
            raise ValueError("Exact read-only research preflight is missing")
        for name in ("candidate", "code_file", "protocol", "workflow_export"):
            if name in proof and proof[name] != plan[name]:
                raise ValueError("Zero-gate preflight source binding differs: " + name)
        self.plan, self.candidate, self.key = plan, candidate, key
        self.category = "validation"
        return plan

    def authority(self, **kwargs):
        authority, artifact, deadline = super().authority(**kwargs)
        if (authority.get("no_order_execution_observation_approved") is not True
                or authority.get("max_run_reservation") != 2
                or Path(authority.get("only_unit", "")).resolve() != self.unit):
            raise ValueError("Exact new human no-order observation approval and two-point estimate are required")
        return authority, artifact, deadline

    def registered(self, *, append=False):
        registry = ResearchRegistry(self.root / "research_runs/research_trials.sqlite3")
        try:
            identity = self.plan["economic_identity_key"]
            entry = registry.registrations().get(identity)
            if (not entry or entry["candidate_id"] != self.plan["economic_candidate_id"]
                    or entry["definition"] != self.plan["economic_definition"]
                    or entry["definition"].get("evaluation_type") != "stateful_account"):
                raise ValueError("Observation must bind the already registered original close-target identity")
            if append:
                registry.append_event("execution_repair", identity, {
                    "phase": "APPROVED_NO_ORDER_ZERO_GATE_OBSERVATION", "fingerprint": self.key,
                    "batch_plan": bind(self.folder / "batch_plan.json"), "new_contexts": 0,
                    "diagnostic_purpose": PURPOSE, "source_only": True,
                }, event_id="manual-ui-zero-gate-observation:" + self.key)
        finally:
            registry.close()

    def process(self, action, **kwargs):
        result = super().process(action, **kwargs)
        result.update(new_contexts=0, source_only=True, economic_backtest_success=False,
                      diagnostic_purpose=PURPOSE)
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
    controller = ZeroGateProbeController(args.plan_sha, ledger=ledger)
    try:
        print(json.dumps(controller.process(args.action, object_id=args.object_id,
                                           receipt_path=args.receipt), ensure_ascii=False))
    finally:
        ledger.db.close()


if __name__ == "__main__":
    main()
