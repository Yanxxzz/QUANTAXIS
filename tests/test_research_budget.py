"""Money-saving routing and cumulative limits under real SQLite transactions."""
import hashlib
import json
from copy import deepcopy

import pytest

from panda_alpha.platform import (ExperimentLedger, budget_plan, dispatch,
                                 read_research_state)
from panda_alpha.research_budget import load_policy, request, guard, record_review


POLICY = {"study_id": "one_search", "max_total_credits": 60, "max_runs": 20,
          "review_interval_credits": 20, "max_initial_runs_per_mechanism": 2,
          "max_runs_per_mechanism": 4}
WINDOW = {"start": "2024-01-01", "end": "2024-06-30"}
BALANCE = {"total": 100, "gift": 100, "recharge": 0}


def config():
    return {"research": {"cycle": 5, "groups": 10}, "compute": {
        "official_research_policy": deepcopy(POLICY), "daily_credit_limit": 100,
        "recharge_credit_limit": 0, "cost_ceiling": 2, "cost_ceiling_verified_at": "fixture",
        "cost_ceiling_source": "Synthetic backend cap", "server_enforced_cap": True,
        "billing_mode": "gift_only", "allocation_mode": "shared_priority",
        "source_probe_fraction": .3, "exploration_fraction": .4, "validation_fraction": .3}}


def candidate(n=0, **changes):
    return dict({"candidate_id": f"M{n}", "formula": f"RANK(input_{n})", "direction": 1,
                 "mechanism": f"independent_mechanism_{n}", "official_task": "native_screen",
                 "research_question": "Does the frozen mechanism survive native inputs?",
                 "decision_if_pass": "Review costs and actual pool increment locally",
                 "decision_if_fail": "Close this fixed version",
                 "official_information_gap": "Native field coverage is not available locally"}, **changes)


class Client:
    def __init__(self):
        self.calls = []
    def balance(self):
        self.calls.append("balance"); return BALANCE
    def create(self, *args):
        self.calls.append("create"); return "f"
    def start(self, *args):
        self.calls.append("start"); return "r"


class NoAccount:
    def balance(self):
        raise AssertionError("Rejected research must not access the account")


def finish(ledger, job, actual=2, failed=False):
    ledger.transition(job["fingerprint"], "RUNNING", "SETTLED", actual=actual,
                      receipt=json.dumps({"actual": actual, "failed": failed}))


def artifact(tmp_path):
    p = tmp_path / "complete_results.json"
    p.write_text('{"result":"complete frozen experiment review"}')
    return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


@pytest.mark.parametrize("task", ["syntax_check", "cost_sensitivity", "attribution", "correlation", "pool_increment", "stability_replay"])
def test_local_tasks_never_create_runs(tmp_path, task):
    ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValueError, match="local review"):
        dispatch(candidate(official_task=task), WINDOW, config(), ledger, NoAccount())
    assert ledger.jobs() == []


def test_rename_and_window_mutation_do_not_get_new_initial_allowance(tmp_path):
    ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    for n, days in enumerate([20, 60]):
        c = candidate(n, formula=f"RANK(TS_MEAN(close,{days}))", mechanism=f"renamed_{n}")
        job = dispatch(c, dict(WINDOW, end=f"2024-0{n+5}-28"), config(), ledger, Client())
        finish(ledger, job, failed=(n == 1))
    with pytest.raises(ValueError, match="initial-probe"):
        dispatch(candidate(3, formula="RANK(TS_MEAN(close,120))", mechanism="another_name"),
                 WINDOW, config(), ledger, NoAccount())
    assert len(ledger.jobs()) == 2


def test_changed_batch_and_date_still_use_one_credit_limit(tmp_path):
    cfg = config(); cfg["compute"]["official_research_policy"].update(max_total_credits=6, review_interval_credits=6)
    ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    for n in range(3):
        job = dispatch(candidate(n), WINDOW, cfg, ledger, Client()); finish(ledger, job)
        # Actual past dates and different funding-batch metadata do not change scope.
        definition = json.loads(ledger.get(job["fingerprint"])["definition"])
        definition["billing_authorization"] = {"fixture": {"batch_id": f"batch{n}"}}
        ledger.db.execute("UPDATE jobs SET day=?,definition=? WHERE fingerprint=?",
                          (f"2026-10-0{n+1}", json.dumps(definition), job["fingerprint"]))
    with pytest.raises(ValueError, match="credit limit"):
        dispatch(candidate(4), WINDOW, cfg, ledger, NoAccount())


def test_review_is_free_requires_settled_evidence_and_unlocks_next_stage(tmp_path):
    ledger, cfg = ExperimentLedger(tmp_path / "jobs.sqlite3"), config()
    for n in range(10):
        job = dispatch(candidate(n), WINDOW, cfg, ledger, Client()); finish(ledger, job)
    with pytest.raises(ValueError, match="review required at 20"):
        dispatch(candidate(11), WINDOW, cfg, ledger, NoAccount())
    ev = {"study_id": POLICY["study_id"], "decision": "redirect",
          "findings": "Ten mechanisms reviewed; fixed weak versions closed",
          "next_steps": "Test a new independent mechanism", "artifacts": [artifact(tmp_path)]}
    before = len(ledger.jobs())
    preview = record_review(ledger, POLICY, ev)
    assert not preview["recorded"] and ledger.research_state()["reviews"] == []
    receipt = record_review(ledger, POLICY, ev, execute=True)
    assert receipt["official_calls"] == receipt["new_economic_contexts"] == 0
    assert len(ledger.jobs()) == before
    assert dispatch(candidate(11), WINDOW, cfg, ledger, Client())["state"] == "RUNNING"
    with pytest.raises(ValueError, match="settled executions"):
        record_review(ledger, POLICY, ev, execute=True)


def test_active_study_cannot_reset_by_renaming_disabling_or_increasing_cap(tmp_path):
    ledger, cfg = ExperimentLedger(tmp_path / "jobs.sqlite3"), config()
    job = dispatch(candidate(), WINDOW, cfg, ledger, Client()); finish(ledger, job)
    for change in [{"study_id": "new_name"}, {"max_total_credits": 120}]:
        modified = deepcopy(cfg); modified["compute"]["official_research_policy"].update(change)
        with pytest.raises(ValueError, match="pinned"):
            dispatch(candidate(1), WINDOW, modified, ledger, NoAccount())
    modified = deepcopy(cfg); modified["compute"].pop("official_research_policy")
    with pytest.raises(ValueError, match="cannot be disabled"):
        dispatch(candidate(1), WINDOW, modified, ledger, NoAccount())
    with pytest.raises(ValueError, match="requires research_budget"):
        ledger.reserve("bypass", candidate(1), {}, "exploration", 2, 100, BALANCE)


def test_key_confirmation_uses_real_evidence_and_total_family_limit(tmp_path):
    cfg = config(); ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    for n in range(4):
        c = candidate(n, formula=f"RANK(TS_MEAN(close,{20+n}))", mechanism="one_mechanism")
        category = "exploration"
        if n >= 2:
            c.update(official_task="key_confirmation", local_review_evidence=[artifact(tmp_path)])
            category = "validation"
        job = dispatch(c, WINDOW, cfg, ledger, Client(), category); finish(ledger, job)
    c = candidate(5, formula="RANK(TS_MEAN(close,100))", official_task="key_confirmation",
                  local_review_evidence=[artifact(tmp_path)])
    with pytest.raises(ValueError, match="family run limit"):
        dispatch(c, WINDOW, cfg, ledger, NoAccount(), "validation")
    c["local_review_evidence"][0]["sha256"] = "0"*64
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        dispatch(c, WINDOW, cfg, ledger, NoAccount(), "validation")


def test_source_diagnostic_does_not_require_full_local_financial_history(tmp_path):
    c = candidate(official_task="source_diagnostic", data_requirements=["native_financial_period_pending"])
    ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    assert dispatch(c, WINDOW, config(), ledger, Client(), "source_probe")["state"] == "RUNNING"
    with pytest.raises(ValueError, match="legacy operator/source"):
        dispatch(candidate(1, data_requirements=["native_financial_period_pending"]),
                 WINDOW, config(), ledger, NoAccount())


def test_atomic_reservation_rechecks_limit_and_rejects_forged_scope(tmp_path):
    cfg = config(); policy = cfg["compute"]["official_research_policy"]
    policy.update(max_total_credits=2, review_interval_credits=2)
    ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    c = candidate(); meta = request(policy, c, "exploration")
    ledger.reserve("first", c, {"research_budget": meta}, "exploration", 2, 100, BALANCE)
    ledger.transition("first", "RESERVED", "SETTLED", actual=2)
    other = candidate(1); fake = request(policy, other, "exploration"); fake["study_id"] = "other"
    with pytest.raises(ValueError, match="metadata differs"):
        ledger.reserve("forged", other, {"research_budget": fake}, "exploration", 2, 100, BALANCE)
    with pytest.raises(ValueError, match="credit limit"):
        ledger.reserve("second", other, {"research_budget": request(policy, other, "exploration")},
                       "exploration", 2, 100, BALANCE)


def test_preview_routes_local_work_and_reuses_cumulative_state(tmp_path):
    absent = tmp_path / "absent.sqlite3"
    assert read_research_state(absent) == {} and not absent.exists()
    items = [candidate(), candidate(1, official_task="cost_sensitivity"), candidate(2)]
    plan = budget_plan(items, config()["compute"], BALANCE)
    assert plan["local_review"][0]["candidate_id"] == "M1"
    assert [x["candidate_id"] for x in plan["selected"]] == ["M0", "M2"]
    assert not plan["research_budget"]["funding_authorization"]


def test_run_limit_and_existing_run_reuse_do_not_depend_on_new_funding(tmp_path):
    cfg = config(); cfg["compute"]["official_research_policy"]["max_runs"] = 1
    ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    job = dispatch(candidate(), WINDOW, cfg, ledger, Client()); finish(ledger, job)
    assert dispatch(candidate(), WINDOW, cfg, ledger, NoAccount())["state"] == "SETTLED"
    with pytest.raises(ValueError, match="run limit"):
        dispatch(candidate(1), WINDOW, cfg, ledger, NoAccount())


def test_charged_cancellation_and_unsettled_reservation_are_not_free():
    policy = dict(POLICY, max_total_credits=6, review_interval_credits=6)
    meta = request(policy, candidate(), "exploration")
    jobs = [{"fingerprint": "failed", "state": "CANCELLED", "actual": 4,
             "reserved": 2, "run_id": "r", "definition": json.dumps({"research_budget": meta})},
            {"fingerprint": "pending", "state": "BILLING_PENDING", "actual": None,
             "reserved": 2, "definition": json.dumps({"research_budget": meta})}]
    result = guard(policy, None, jobs, {}, 0)
    assert result["usage"] == {"credits": 6, "runs": 2, "unsettled": 2}
    assert result["blocker"] == "Shared study credit limit reached"


def test_candidate_roundtrip_preserves_routing_and_free_review_is_not_admission():
    from panda_alpha.evolution import Candidate
    c = candidate(hypothesis="Frozen hypothetical mechanism")
    saved = Candidate.from_dict(c).to_dict()
    assert saved["official_task"] == c["official_task"]
    assert saved["official_information_gap"] == c["official_information_gap"]
    assert request(POLICY, saved, "exploration")["mechanism_id"] == c["mechanism"]


@pytest.mark.parametrize("value", [0, -1, float("nan"), True])
def test_invalid_limits_are_rejected(value):
    p = dict(POLICY, max_total_credits=value)
    with pytest.raises(ValueError):
        load_policy({"official_research_policy": p})
