"""Synthetic transport diagnostic; no real registry, account, wallet or Run."""
import importlib.util
import json
from pathlib import Path

import pytest

from panda_alpha.platform import fingerprint
from panda_alpha.registry import ResearchRegistry
from test_manage_limit_finance_strategy_run import prepared  # Temporary-only fixture.

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/manage_limit_finance_strategy_config_probe.py"
SPEC = importlib.util.spec_from_file_location("manual_configuration_probe_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CODE = '''from panda_backtest.api.api import *
import json
def initialize(context):
    info = context.run_info
    observed = {'frequency': str(info.frequency), 'frequency_type': str(type(info.frequency)),
                'matching_type': str(info.matching_type), 'matching_type_type': str(type(info.matching_type))}
    raise RuntimeError('LF_CONFIG_DIAGNOSTIC_ONLY_NO_ORDERS ' + json.dumps(observed, sort_keys=True))
def handle_data(context, data):
    raise RuntimeError('Configuration diagnostic must stop in initialize; no orders permitted')
'''


@pytest.fixture
def diagnostic(prepared):
    original, fake, old_folder, old_unit, ledger = prepared
    old_plan = MODULE.read(old_folder / "batch_plan.json")
    folder = old_unit / "config_diagnostic_proposal"
    folder.mkdir()
    (folder / "Strategy.py").write_bytes(CODE.encode("utf-8"))
    window = {"start": "2026-09-17", "end": "2026-09-18"}
    MODULE.save(folder / "proposal.json", {"code": MODULE.bind(folder / "Strategy.py"),
                                          "new_economic_contexts": 0, "diagnostic_window": window})
    MODULE.save(folder / "workflow_export.json", {"diagnostic_code": CODE})
    candidate = MODULE.read(old_folder / "candidate.json")
    candidate.update(candidate_id="synthetic-runtime-configuration-diagnostic", code=CODE,
                     code_path=str((folder / "Strategy.py").resolve()),
                     official_task="source_diagnostic", economic_evaluation_type="transport_diagnostic",
                     diagnostic_purpose=MODULE.PURPOSE, budget_mechanism_id=MODULE.MECHANISM)
    MODULE.save(folder / "candidate.json", candidate)
    key = fingerprint(candidate, window, 1, 1)
    MODULE.save(folder / "preflight.json", {"passed": False, "research_replay_passed": True,
        "economic_evaluation_type": "transport_diagnostic", "fingerprint": key})
    a = MODULE.read(old_unit / "authorization.json")
    a["configuration_diagnostic_explicitly_approved"] = True
    a["human_instructions"].append("Approve one configuration diagnostic and compatible pair")
    MODULE.save(folder / "authorization.json", a)
    plan = {"candidate": MODULE.bind(folder / "candidate.json"),
        "code_file": MODULE.bind(folder / "Strategy.py"), "protocol": MODULE.bind(folder / "proposal.json"),
        "workflow_export": MODULE.bind(folder / "workflow_export.json"),
        "preflight": MODULE.bind(folder / "preflight.json"), "window": window,
        "cycle": 1, "groups": 1, "max_new_runs": 1, "fingerprint": key,
        "economic_identity_key": old_plan["economic_identity_key"],
        "economic_definition": old_plan["economic_definition"],
        "economic_candidate_id": MODULE.read(old_folder / "candidate.json")["candidate_id"]}
    MODULE.save(folder / "batch_plan.json", plan)
    ledger.db.execute("UPDATE jobs SET actual=10 WHERE fingerprint=?", ("0" * 64,))
    c = MODULE.ConfigProbeController(MODULE.bind(folder / "batch_plan.json")["sha256"],
        root=original.root, unit=folder, ledger=ledger, client=fake)
    return c, fake, folder, ledger


def running(c):
    c.process("reserve")
    c.process("before-save")
    c.process("created", object_id="owned-diag-workflow")
    c.process("before-start")
    c.process("started", object_id="owned-diag-run")


def receipt(folder, *, payload):
    log = folder / "official-error.json"
    MODULE.save(log, {"run_id": "owned-diag-run", "error": payload})
    value = {"actual": 2., "run_id": "owned-diag-run", "final_status": "FAILED",
        "official_receipt_path": str(log.resolve()), "official_receipt_sha256": MODULE.bind(log)["sha256"],
        "verified_by": "synthetic-test-only", "diagnostic_error_artifact": MODULE.bind(log)}
    path = folder / "settlement.json"
    MODULE.save(path, value)
    return path


def test_expected_failed_probe_completes_only_with_four_actual_observations(diagnostic):
    c, fake, folder, ledger = diagnostic
    before = ResearchRegistry(c.root / "research_runs/research_trials.sqlite3")
    total = before.total
    before.close()
    running(c)
    observed = {"frequency": "1m", "frequency_type": "<class 'str'>",
                "matching_type": "1", "matching_type_type": "<class 'int'>"}
    result = c.process("settle", receipt_path=receipt(folder, payload=MODULE.MARKER + json.dumps(observed)))
    assert result["diagnostic_completed"] is True
    assert result["economic_backtest_success"] is False
    assert result["observed_configuration"] == observed
    job = ledger.get(c.key)
    assert job["state"] == "SETTLED" and job["actual"] == 2
    assert job["category"] == "source_probe"
    assert json.loads(job["definition"])["research_budget"]["mechanism_id"] == MODULE.MECHANISM
    after = ResearchRegistry(c.root / "research_runs/research_trials.sqlite3")
    assert after.total == total and after.new_tested == 1
    assert after.events()[-1]["kind"] == "execution_repair"
    after.close()
    assert MODULE.read(folder / "failure_lock.json")["no_automatic_resend"] is True
    assert fake.calls == 1


def test_billing_settles_but_missing_payload_is_not_diagnostic_completion(diagnostic):
    c, _, folder, ledger = diagnostic
    running(c)
    result = c.process("settle", receipt_path=receipt(folder, payload="Failed before initialize"))
    assert result["diagnostic_completed"] is False
    assert result["observed_configuration"] is None
    assert ledger.get(c.key)["actual"] == 2


def test_probe_cannot_gain_order_capability():
    with pytest.raises(ValueError, match="No order"):
        MODULE.no_order_code(CODE.replace("    info = context.run_info", "    order_shares('000001', 100)\n    info = context.run_info"))


def test_missing_new_human_approval_stops_before_wallet_or_reservation(diagnostic):
    c, fake, folder, ledger = diagnostic
    a = MODULE.read(folder / "authorization.json")
    a["configuration_diagnostic_explicitly_approved"] = False
    MODULE.save(folder / "authorization.json", a)
    with pytest.raises(ValueError, match="Explicit new human"):
        c.process("reserve")
    assert fake.calls == 0 and ledger.get(c.key) is None


def test_same_day_window_is_rejected_before_wallet(diagnostic):
    c, fake, folder, ledger = diagnostic
    plan = MODULE.read(folder / "batch_plan.json")
    plan["window"] = {"start": "2026-09-18", "end": "2026-09-18"}
    proposal = MODULE.read(folder / "proposal.json")
    proposal["diagnostic_window"] = plan["window"]
    MODULE.save(folder / "proposal.json", proposal)
    plan["protocol"] = MODULE.bind(folder / "proposal.json")
    MODULE.save(folder / "batch_plan.json", plan)
    c.plan_sha = MODULE.bind(folder / "batch_plan.json")["sha256"]
    with pytest.raises(ValueError, match="start < end"):
        c.process("audit")
    assert fake.calls == 0 and ledger.get(c.key) is None


def test_two_day_window_and_proposal_match_are_accepted_offline(diagnostic):
    c, fake, _, ledger = diagnostic
    c.frozen()
    assert c.plan["window"] == {"start": "2026-09-17", "end": "2026-09-18"}
    assert fake.calls == 0 and ledger.get(c.key) is None
