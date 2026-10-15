"""Synthetic ledger/UI recording only: no real account, source or paid calls."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from panda_alpha.platform import ExperimentLedger, fingerprint
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key
from panda_alpha.research_budget import bind_policy, limits

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/manage_limit_finance_strategy_run.py"
SPEC = importlib.util.spec_from_file_location("manual_strategy_controller_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeWallet:
    def __init__(self):
        self.calls = 0

    def balance(self):
        self.calls += 1
        return {"total": 100., "gift": 0., "free": 0., "recharge": 100.,
                "observed_at": datetime.now(timezone.utc).isoformat(), "gift_expires": None}

    def __getattr__(self, name):
        raise AssertionError("No remote workflow method may be called: " + name)


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "repo"
    unit = root / "research_runs/unit"
    folder = unit / "target"
    folder.mkdir(parents=True)
    policy = {"study_id": "pool_candidate_search_20261012", "max_total_credits": 60,
              "max_runs": 20, "review_interval_credits": 20,
              "max_initial_runs_per_mechanism": 2, "max_runs_per_mechanism": 4}
    config = {"research": {"cycle": 1, "groups": 1}, "platform": {}, "compute": {
        "official_research_policy": policy, "source_probe_fraction": .2,
        "exploration_fraction": .6, "validation_fraction": .2}}
    MODULE.save(root / "config/panda-alpha.local.json", config)
    now = datetime.now(timezone.utc)
    authority = {"parent_batch_id": MODULE.PARENT, "max_total_credits": 20,
        "accept_estimated_charge_risk": True, "minute_ioc_migration_accepted": True,
        "human_instructions": ["Test the complete stateful strategy", "Accept minute IOC migration"],
        "recorded_at_utc": (now - timedelta(minutes=1)).isoformat(),
        "execution_valid_until_utc": (now + timedelta(minutes=119)).isoformat()}
    MODULE.save(unit / "authorization.json", authority)
    MODULE.save(unit / "protocol.json", {"scope": "synthetic-no-official-action"})
    code = "def strategy():\n    return 'synthetic-stateful'\n"
    (folder / "strategy.py").write_bytes(code.encode("utf-8"))
    MODULE.save(folder / "workflow_export.json", {"nodes": [{"code": code}]})
    candidate = {"candidate_id": "synthetic-manual-strategy", "code": code,
        "code_path": str((folder / "strategy.py").resolve()), "direction": 1,
        "official_task": "stateful_strategy", "economic_evaluation_type": "stateful_account",
        "budget_mechanism_id": "synthetic-stateful-mechanism", "research_question": "Does it execute?",
        "decision_if_pass": "Review the complete report", "decision_if_fail": "Lock the same Run",
        "official_information_gap": "Native stateful callback execution"}
    window = {"start": "2024-01-02", "end": "2026-09-18"}
    MODULE.save(folder / "candidate.json", candidate)
    key = fingerprint(candidate, window, 1, 1)
    MODULE.save(folder / "preflight.json", {"passed": False, "research_replay_passed": True,
        "economic_evaluation_type": "stateful_account", "fingerprint": key})
    registry = ResearchRegistry(root / "research_runs/research_trials.sqlite3", historical=415)
    registry.record(candidate, window, 1, 1)
    definition = strategy_definition(candidate, window, 1, 1)
    registry.close()
    plan = {"candidate": MODULE.bind(folder / "candidate.json"),
        "code_file": MODULE.bind(folder / "strategy.py"),
        "protocol": MODULE.bind(unit / "protocol.json"),
        "preflight": MODULE.bind(folder / "preflight.json"),
        "workflow_export": MODULE.bind(folder / "workflow_export.json"),
        "cycle": 1, "groups": 1, "max_new_runs": 1, "fingerprint": key,
        "window": window, "economic_identity_key": trial_key(definition),
        "economic_definition": definition}
    MODULE.save(folder / "batch_plan.json", plan)
    ledger = ExperimentLedger(root / "research_runs/experiments.sqlite3")
    bind_policy(ledger.db, policy)
    old_definition = {"billing_authorization": {"estimated_billing": {
        "batch_id": MODULE.PARENT}, "recharge": {"batch_id": MODULE.PARENT}},
        "research_budget": {"study_id": policy["study_id"],
        "mechanism_id": "old", "family_id": "old", "task": "native_screen", "limits": limits(policy)}}
    ledger.db.execute("INSERT INTO jobs(fingerprint,candidate_id,category,day,state,reserved,actual,definition) "
        "VALUES (?,?,?,?,?,?,?,?)", ("0" * 64, "old-synthetic", "exploration", "2026-10-14",
        "SETTLED", 8., 6., json.dumps(old_definition)))
    fake = FakeWallet()
    controller = MODULE.ManualStrategyController("target", MODULE.bind(folder / "batch_plan.json")["sha256"],
        root=root, unit=unit, ledger=ledger, client=fake)
    try:
        yield controller, fake, folder, unit, ledger
    finally:
        ledger.db.close()


def to_running(controller):
    controller.process("reserve")
    controller.process("before-save")
    controller.process("created", object_id="owned-workflow-1")
    controller.process("before-start")
    controller.process("started", object_id="owned-run-1")


def test_audit_preserves_existing_mother_usage_without_reserving(prepared):
    c, fake, folder, _, ledger = prepared
    result = c.process("audit")
    assert result["scope_before"] == {"total": 6., "recharge": 6., "runs": 1}
    assert result["ready"] and fake.calls == 1
    assert ledger.get(c.key) is None
    assert len(ledger.jobs()) == 1


def test_manual_sequence_records_ids_but_never_performs_remote_mutations(prepared):
    c, fake, folder, _, ledger = prepared
    to_running(c)
    job = ledger.get(c.key)
    assert (job["state"], job["factor_id"], job["run_id"]) == (
        "RUNNING", "owned-workflow-1", "owned-run-1")
    assert job["reserved"] == 8 and fake.calls == 1
    grant = MODULE.read(folder / "funding_subgrant.json")["grant"]
    assert (grant["max_total_credits"], grant["max_new_runs"], grant["max_runs"]) == (14., 1, 2)
    assert grant["max_recharge_credits"] == 14.
    assert MODULE.read(folder / "official_progress.json")["external_mutations"] == 0


def test_repeated_before_save_locks_same_job_without_retry(prepared):
    c, fake, folder, _, ledger = prepared
    c.process("reserve")
    c.process("before-save")
    with pytest.raises(ValueError, match="Unexpected UI state"):
        c.process("before-save")
    assert ledger.get(c.key)["state"] == "CREATING"
    assert MODULE.read(folder / "failure_lock.json")["no_automatic_resend"] is True
    with pytest.raises(ValueError, match="unresolved|already exists"):
        c.process("reserve")
    assert len(ledger.jobs()) == 2 and fake.calls == 1


def test_expired_authority_rejects_before_wallet_or_reservation(prepared):
    c, fake, _, unit, ledger = prepared
    a = MODULE.read(unit / "authorization.json")
    a["recorded_at_utc"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    a["execution_valid_until_utc"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    MODULE.save(unit / "authorization.json", a)
    with pytest.raises(ValueError, match="not currently valid"):
        c.process("reserve")
    assert fake.calls == 0 and len(ledger.jobs()) == 1


def test_exact_submission_bytes_checked_before_wallet(prepared):
    c, fake, folder, _, ledger = prepared
    with (folder / "strategy.py").open("a", encoding="utf-8") as f:
        f.write("# changed\n")
    with pytest.raises(ValueError, match="Frozen artifact changed"):
        c.process("reserve")
    assert fake.calls == 0 and len(ledger.jobs()) == 1


def test_only_matching_verified_official_artifact_can_settle_zero(prepared):
    c, fake, folder, _, ledger = prepared
    to_running(c)
    evidence = folder / "owned-official-billing.json"
    MODULE.save(evidence, {"run_id": "owned-run-1", "official_actual": 0})
    receipt = {"actual": 0., "run_id": "wrong-run", "final_status": "SUCCESS",
        "official_receipt_path": str(evidence.resolve()),
        "official_receipt_sha256": MODULE.bind(evidence)["sha256"], "verified_by": "synthetic-test-only"}
    receipt_path = folder / "settlement.json"
    MODULE.save(receipt_path, receipt)
    with pytest.raises(ValueError, match="does not match"):
        c.process("settle", receipt_path=receipt_path)
    assert ledger.get(c.key)["state"] == "RUNNING"
    receipt["run_id"] = "owned-run-1"
    MODULE.save(receipt_path, receipt)
    c.process("settle", receipt_path=receipt_path)
    assert ledger.get(c.key)["state"] == "SETTLED"
    assert ledger.get(c.key)["actual"] == 0.
    assert fake.calls == 1
