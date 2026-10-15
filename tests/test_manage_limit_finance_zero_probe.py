"""Synthetic no-order observation bookkeeping; no real registry/account/Run."""
import importlib.util
import json
from pathlib import Path

import pytest

from panda_alpha.platform import fingerprint
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key
from panda_alpha.research_budget import request as research_request
from test_manage_limit_finance_strategy_run import prepared

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/manage_limit_finance_zero_probe.py"
SPEC = importlib.util.spec_from_file_location("manual_zero_gate_probe_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ROSTER = {"2026-07-01": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"],
          "2026-07-02": ["000006.SZ", "000007.SZ", "000008.SZ", "000009.SZ", "000010.SZ"]}
CODE = ("from panda_backtest.api.api import *\nimport json\nimport math\n"
        + "FROZEN_PROBE_ROSTER = " + repr(ROSTER) + "\n"
        + "FROZEN_PROBE_WINDOW = " + repr(MODULE.WINDOW) + "\n"
        + "def initialize(context):\n    context._zero_probe = {}\n"
        + "def before_trading(context):\n    return None\n"
        + "def handle_data(context, data):\n    value = context.trade_time.strftime('%Y-%m-%d %H:%M')\n    bar = data.get('000001.SZ')\n"
        + "    print(json.dumps({'close': bar.close}))\n"
        + "def after_trading(context):\n    return None\n")


@pytest.fixture
def observation(prepared):
    old, fake, _, _, ledger = prepared
    unit = old.root / MODULE.RELATIVE_UNIT
    folder = unit / "target"
    original_folder = unit.parent / "target"
    original_folder.mkdir(parents=True)
    folder.mkdir(parents=True)
    original_candidate = MODULE.read(old.folder / "candidate.json")
    original_candidate.update(code="FROZEN_ENTRY_ROSTER = " + repr(ROSTER) + "\n",
                              code_path=str(original_folder / "Strategy.py"))
    (original_folder / "Strategy.py").write_bytes(original_candidate["code"].encode("utf-8"))
    MODULE.save(original_folder / "candidate.json", original_candidate)
    original_window = {"start": "2026-07-01", "end": "2026-09-18"}
    registry = ResearchRegistry(old.root / "research_runs/research_trials.sqlite3")
    registry.record(original_candidate, original_window, 1, 1)
    definition = strategy_definition(original_candidate, original_window, 1, 1)
    identity = trial_key(definition)
    registry.close()
    original_plan = {"candidate": MODULE.bind(original_folder / "candidate.json"),
        "code_file": MODULE.bind(original_folder / "Strategy.py"),
        "economic_identity_key": identity, "economic_definition": definition}
    MODULE.save(original_folder / "batch_plan.json", original_plan)
    protocol = {"original_registered_plan": MODULE.bind(original_folder / "batch_plan.json"),
        "source_roster": original_plan["code_file"], "original_economic_identity_key": identity,
        "window": MODULE.WINDOW, "roster": ROSTER, "no_orders": True,
        "no_financing_or_source_reload": True, "economic_results_forbidden": True,
        "new_economic_contexts": 0}
    MODULE.save(unit / "protocol.json", protocol)
    (folder / "Strategy.py").write_bytes(CODE.encode("utf-8"))
    MODULE.save(folder / "workflow_export.json", {"code": CODE})
    candidate = dict(original_candidate, candidate_id="synthetic-zero-gate-observation", code=CODE,
        code_path=str(folder / "Strategy.py"), official_task="key_confirmation",
        economic_evaluation_type="execution_diagnostic", diagnostic_purpose=MODULE.PURPOSE,
        budget_mechanism_id=MODULE.STATEFUL_MECHANISM,
        local_review_evidence=[MODULE.bind(unit / "protocol.json")])
    MODULE.save(folder / "candidate.json", candidate)
    key = fingerprint(candidate, MODULE.WINDOW, 1, 1)
    MODULE.save(folder / "preflight.json", {"passed": False, "research_replay_passed": True,
        "economic_evaluation_type": "execution_diagnostic", "fingerprint": key})
    plan = {"candidate": MODULE.bind(folder / "candidate.json"),
        "code_file": MODULE.bind(folder / "Strategy.py"), "protocol": MODULE.bind(unit / "protocol.json"),
        "preflight": MODULE.bind(folder / "preflight.json"),
        "workflow_export": MODULE.bind(folder / "workflow_export.json"), "window": MODULE.WINDOW,
        "cycle": 1, "groups": 1, "max_new_runs": 1, "fingerprint": key,
        "economic_identity_key": identity, "economic_definition": definition,
        "economic_candidate_id": original_candidate["candidate_id"]}
    MODULE.save(folder / "batch_plan.json", plan)
    authority = MODULE.read(old.unit / "authorization.json")
    authority.update(short_window_retry_explicitly_approved=True,
        continuation_experiments_explicitly_approved=True, continuation_scope="close_phase_ready",
        max_run_reservation=2, no_order_execution_observation_approved=True, only_unit=str(unit))
    authority["human_instructions"].append("Investigate native zero trades within original twenty points")
    MODULE.save(unit / "authorization.json", authority)
    ledger.db.execute("UPDATE jobs SET actual=18 WHERE fingerprint=?", ("0" * 64,))
    controller = MODULE.ZeroGateProbeController(MODULE.bind(folder / "batch_plan.json")["sha256"],
        root=old.root, ledger=ledger, client=fake)
    _, policy = controller.config()
    meta = research_request(policy, candidate, "validation")
    for i in range(3):
        previous = dict(meta, task="stateful_strategy" if i < 2 else "key_confirmation")
        ledger.db.execute("INSERT INTO jobs(fingerprint,candidate_id,category,day,state,reserved,actual,definition) "
            "VALUES (?,?,?,?,?,?,?,?)", (str(i + 1) * 64, "previous-" + str(i),
            "exploration" if i < 2 else "validation", "2026-10-14", "SETTLED", 8., 0.,
            json.dumps({"research_budget": previous})))
    return controller, fake, folder, ledger


def test_remaining_two_uses_original_fourth_mechanism_attempt_without_new_economic_identity(observation):
    # This is the no-observed-estimated-charge branch, not a forecast for a
    # production ledger whose recent authorized-estimate actual charge was four.
    c, fake, folder, ledger = observation
    registry = ResearchRegistry(c.root / "research_runs/research_trials.sqlite3")
    before = registry.total
    registry.close()
    c.process("reserve")
    job = ledger.get(c.key)
    definition = json.loads(job["definition"])
    assert job["reserved"] == 2 and job["category"] == "validation"
    assert definition["research_budget"]["mechanism_id"] == MODULE.STATEFUL_MECHANISM
    assert definition["research_budget"]["limits"]["max_initial_runs_per_mechanism"] == 2
    assert definition["research_budget"]["limits"]["max_runs_per_mechanism"] == 4
    grant = MODULE.read(folder / "funding_subgrant.json")["grant"]
    assert grant["max_total_credits"] == 20 and grant["max_new_runs"] == 1
    registry = ResearchRegistry(c.root / "research_runs/research_trials.sqlite3")
    assert registry.total == before
    event = registry.events()[-1]
    assert event["kind"] == "execution_repair" and event["payload"]["new_contexts"] == 0
    registry.close()
    assert fake.calls == 1


def test_order_or_data_reload_attempt_is_rejected_before_wallet_and_reservation(observation):
    c, fake, folder, ledger = observation
    for forbidden in ("order_shares('000001.SZ', 100)", "context.load_inputs()", "open('prices.json')",
                      "getattr(context, 'order_shares')", "__import__('socket')", "import requests"):
        modified = CODE.replace("    bar = data.get('000001.SZ')", "    " + forbidden + "\n    bar = data.get('000001.SZ')")
        with pytest.raises(ValueError, match="No order"):
            MODULE.no_order_code(modified)
    candidate = MODULE.read(folder / "candidate.json")
    candidate["code"] = CODE.replace("    bar = data.get('000001.SZ')", "    order_shares('000001.SZ', 100)")
    MODULE.save(folder / "candidate.json", candidate)
    (folder / "Strategy.py").write_bytes(candidate["code"].encode("utf-8"))
    plan = MODULE.read(folder / "batch_plan.json")
    plan.update(candidate=MODULE.bind(folder / "candidate.json"), code_file=MODULE.bind(folder / "Strategy.py"))
    MODULE.save(folder / "batch_plan.json", plan)
    c.plan_sha = MODULE.bind(folder / "batch_plan.json")["sha256"]
    with pytest.raises(ValueError, match="No order"):
        c.process("reserve")
    assert fake.calls == 0 and ledger.get(plan["fingerprint"]) is None


def test_recent_authorized_estimate_four_blocks_remaining_two_without_reservation(observation):
    c, fake, folder, ledger = observation
    # Preserve mother actual18 while representing its most recent four-point
    # estimated-billing settlement, rather than omitting cost_basis as above.
    old = json.loads(ledger.get("0" * 64)["definition"])
    ledger.db.execute("UPDATE jobs SET actual=14 WHERE fingerprint=?", ("0" * 64,))
    recent = json.loads(ledger.get("3" * 64)["definition"])
    recent.update(cost_basis="authorized_estimate", billing_authorization=old["billing_authorization"])
    ledger.db.execute("UPDATE jobs SET actual=4,definition=? WHERE fingerprint=?",
                      (json.dumps(recent), "3" * 64))
    before = ledger.jobs()
    with pytest.raises(ValueError, match="No exact stateful budget slot"):
        c.process("reserve")
    funds = MODULE.read(folder / "latest_budget_plan.json")
    assert funds["budget"] == 2 and funds["reservation_credit_estimate"] == 4
    assert funds["selected"] == []
    assert fake.calls == 1  # A free wallet read does not authorize UI save/start.
    assert ledger.get(c.key) is None and ledger.jobs() == before
    assert not (folder / "funding_subgrant.json").exists()
