"""Offline acceptance of the cheap path, real gates and durable model budgets."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from panda_alpha.registry import ResearchRegistry
from panda_alpha.team.codex_worker import reserve_model_attempt, work
from panda_alpha.team.context_codec import focus_packet
from panda_alpha.team.coordinator import Coordinator
from panda_alpha.team.demo import artifact, synthetic_request, synthetic_config, synthetic_grant, SyntheticClient, synthetic_assessment
from panda_alpha.team.budget import activate
from panda_alpha.team.host import Host
from panda_alpha.team.runtime import active_roles, worker_config
from panda_alpha.team.runtime_io import read, write


@pytest.fixture
def env(tmp_path):
    proof = tmp_path / "proof.txt"
    proof.write_text("Synthetic bounded workflow evidence, never actual alpha", encoding="utf-8")
    c, r = Coordinator(tmp_path / "team.sqlite3"), ResearchRegistry(tmp_path / "registry.sqlite3")
    yield c, r, artifact(proof), tmp_path
    c.close()
    r.close()


def spec(**changes):
    return dict(dict(objective="Find a reliable distinctive mechanism", workflow="lean", researcher="market",
                max_rounds=1, max_actions=12, max_model_attempts=8,
                deadline=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()), **changes)


def bind_screen(env, task, *, availability="unavailable", semantics=True):
    c, r, proof, root = env
    manifest = {"fingerprint": task["fingerprint"], "request_hash": task["request_hash"], "availability": availability,
                "reason": "Adjusted source unavailable in this synthetic fixture", "artifacts": [proof]}
    if semantics:
        manifest["definition_semantics"] = {"status": "equivalent", "artifacts": [proof]}
    source = root / "input.json"
    write(source, manifest)
    config = synthetic_config()
    config.update(admission={"one_way_costs": [.003, .005]}, diversity={"minimum_assets": 3})
    config["local_screen"] = {"enabled": True, "allow_official_fallback": True, "output_dir": str(root / "reports"),
                              "inputs": {task["fingerprint"]: artifact(source)}}
    path = root / "local.json"
    write(path, config)
    return artifact(path), config


def answer(host, packet, output):
    return host.respond(packet["work_id"], {"work_id": packet["work_id"], "output": output})


def test_single_researcher_without_ceremonial_plan_and_no_candidate_stops(env):
    c, r, _, _ = env
    host = Host(c, r, "lean", spec())
    packet = host.next()["packet"]
    assert (packet["role"], packet["action"]) == ("market", "propose")
    assert Host(c, r, "lean").next()["packet"] == packet
    answer(host, packet, {"decision": "no_candidate", "reason": "No informative idea"})
    assert host.next()["reason"] == "no_informative_candidate"
    assert host.state()["actions"] == 1 and not c.ledger.jobs() and r.total == 0


def test_screen_then_one_independent_review_and_mechanical_freeze(env):
    c, r, proof, _ = env
    task = c.board.submit(synthetic_request("market", proof))
    binding, _ = bind_screen(env, task)
    host = Host(c, r, "lean", spec(seed_tasks=[task["task_id"]], local_screen_config=binding))
    packet = host.next()["packet"]
    assert (packet["role"], packet["action"], packet["review_mode"]) == ("skeptic", "review", "proposal")
    assert packet["local_screen_observation"]["route"] == "official_fallback"
    answer(host, packet, {"claim": "Design only, native source gates remain", "test": "Review frozen definition",
                          "decision": "approve", "artifacts": [proof]})
    # Mechanical freeze finishes even at the model action bound, without a Lead call.
    host.db.execute("UPDATE team_host_sessions SET actions=12 WHERE session_id='lean'")
    assert host.next()["reason"] == "execution_not_enabled"
    frozen = c.board.task(task["task_id"])
    assert frozen["state"] == "ready" and frozen["freeze"]["decider"] == "deterministic_host"
    kinds = [e["kind"] for e in c.board.events(task["task_id"])]
    assert "challenge" not in kinds and "response" not in kinds and "cross_review" not in kinds
    assert not c.ledger.jobs() and r.total == 0


def test_no_input_does_not_spend_critic_turn_or_guess_missing_data(env):
    c, r, proof, _ = env
    task = c.board.submit(synthetic_request("market", proof))
    host = Host(c, r, "lean", spec(seed_tasks=[task["task_id"]]))
    assert host.next()["reason"] == "local_screen_required"
    assert host.state()["actions"] == 0 and not c.ledger.jobs()


@pytest.mark.parametrize("existing_ready", [False, True])
def test_unchecked_definition_is_not_a_lean_missing_data_fallback(env, existing_ready):
    # Historical C1 had no semantics entry; a later public replay found that
    # row-wise REF/MA and NaN-to-zero ranks differed from the local signal.
    c, r, proof, root = env
    task = c.board.submit(synthetic_request("market", proof))
    if existing_ready:
        with c.board.transaction():
            c.board.advance(task["task_id"], "ready")  # Persisted seed from an older design review.
    binding, _ = bind_screen(env, task, semantics=False)
    host = Host(c, r, "lean", spec(seed_tasks=[task["task_id"]], local_screen_config=binding))
    result = host.next()
    assert result["reason"] == "local_screen_blocked"
    assert "semantics evidence" in result["error"]
    assert host.state()["actions"] == 0 and not c.ledger.jobs() and r.total == 0
    assert not any(e["kind"] == "local_screen" for e in c.board.events(task["task_id"]))


def test_seed_only_stops_without_generating_another_candidate(env):
    c, r, proof, _ = env
    task=c.board.submit(synthetic_request('market',proof))
    c.board.decide(task['task_id'],task['version'],{'decision':'park','reason':'Frozen local stopping condition'})
    host=Host(c,r,'only-seed',spec(seed_tasks=[task['task_id']],seed_only=True))
    assert host.next()['reason']=='seed_tasks_complete'
    assert host.state()['actions']==0 and not c.ledger.jobs() and r.total==0


def test_local_reject_parks_without_critic_or_whole_mechanism_rejection(env, monkeypatch):
    c, r, proof, root = env
    task = c.board.submit(synthetic_request("market", proof))
    binding, _ = bind_screen(env, task)
    from panda_alpha.team.local_screen import LocalScreenBlocked
    def local_reject(board, current, *_, **options):
        report = root / "weak.json"
        write(report, {"route": "local_reject", "scope": "fixed definition only"})
        board.event(current["task_id"], "steward", "local_screen", {"report": artifact(report), "route": "local_reject"})
        raise LocalScreenBlocked("Local-first route: local_reject")
    monkeypatch.setattr("panda_alpha.team.local_screen.local_first", local_reject)
    host = Host(c, r, "lean", spec(seed_tasks=[task["task_id"]], local_screen_config=binding))
    next_packet = host.next()["packet"]
    assert next_packet["action"] == "propose" and next_packet["role"] == "market"
    assert c.board.task(task["task_id"])["state"] == "parked"
    assert not r.reconciled_memory()["registry_constraints"]["fixed_rejected"]
    assert host.state()["actions"] == 1 and not c.ledger.jobs()


def test_author_cannot_self_approve_direct_review(env):
    c, _, proof, _ = env
    task = c.board.submit(synthetic_request("market", proof))
    proposal = c.board.events(task["task_id"])[-1]
    message = {"event_id": "direct", "task_id": task["task_id"], "actor": "market", "kind": "review",
               "review_mode": "proposal", "version": task["version"], "request_hash": task["request_hash"],
               "reply_to": proposal["event_id"], "claim": "self approve", "test": "invalid", "decision": "approve", "artifacts": [proof]}
    with pytest.raises(ValueError, match="Independent skeptic"):
        c.board.message(message)
    message.update(actor="skeptic", reply_to="invented-response")
    with pytest.raises(ValueError, match="frozen proposal"):
        c.board.message(message)
    assert c.board.task(task["task_id"])["state"] == "proposed"


def test_result_review_concludes_exactly_once_without_lead(env):
    c, r, proof, root = env
    from panda_alpha.team.demo import freeze_synthetic
    task = freeze_synthetic(c.board, synthetic_request("market", proof))
    config = synthetic_config()
    activate(c.board, synthetic_grant(proof, config))
    client = SyntheticClient()
    c.dispatch(task["task_id"], config, client, r, execute=True)
    c.resume(task["task_id"], client, root / "results")
    host = Host(c, r, "lean", spec(seed_tasks=[task["task_id"]]))
    packet = host.next()["packet"]
    result = packet["task"]["review_targets"]["result"]
    assert packet["action"] == "result_review"
    answer(host, packet, {"claim": "Synthetic delivery only", "test": "No economic or source promotion", "decision": "source_pending",
        "artifacts": result["payload"]["artifacts"], "evidence_contract": result["payload"]["evidence_contract"],
        "assessment": synthetic_assessment()})
    host.next()
    count = len(r.events())
    Host(c, r, "lean").next()
    assert len(r.events()) == count
    assert c.board.task(task["task_id"])["state"] == "parked"
    final = [e for e in c.board.events(task["task_id"]) if e["kind"] == "conclusion"]
    assert len(final) == 1 and final[0]["payload"]["decision"] == "source_pending"
    assert final[0]["payload"]["decider"] == "deterministic_host"
    assert not r.reconciled_memory()["registry_constraints"]["fixed_rejected"]


def test_proposal_can_receive_screen_inputs_without_restarting_or_changing_bounds(env):
    from panda_alpha.team.local_screen import bind_config
    c, r, proof, root = env
    host = Host(c, r, "lean", spec())
    packet = host.next()["packet"]
    request = synthetic_request("market", proof)
    request["task_id"] = packet["task_id"]
    answer(host, packet, {"request": request})
    assert host.next()["reason"] == "local_screen_required"
    task = c.board.task(packet["task_id"])
    binding, _ = bind_screen(env, task)
    before = host.state()
    receipt = bind_config(c.board, task["task_id"], task["version"], binding)
    assert bind_config(c.board, task["task_id"], task["version"], binding) == receipt
    assert host.state() == before
    review = Host(c, r, "lean").next()["packet"]
    assert review["review_mode"] == "proposal" and review["local_screen_observation"]["route"] == "official_fallback"
    answer(host, review, {"claim": "Design only", "test": "Frozen local route checked", "decision": "approve", "artifacts": [proof]})
    assert host.next()["reason"] == "execution_not_enabled"
    # A changed file is blocked before dispatch, even with the old design approval.
    Path(binding["path"]).write_text("changed after review", encoding="utf-8")
    assert host.next(execution=True)["reason"] == "local_screen_blocked"
    assert not c.ledger.jobs()


def test_direct_design_source_reopen_preserves_original_proposal_and_new_evidence(env):
    c, r, proof, root = env
    task = c.board.submit(synthetic_request("market", proof))
    binding, _ = bind_screen(env, task)
    host = Host(c, r, "lean", spec(seed_tasks=[task["task_id"]], local_screen_config=binding))
    packet = host.next()["packet"]
    answer(host, packet, {"claim": "Native source missing", "test": "No invented evidence", "decision": "park", "artifacts": [proof]})
    old = c.board.task(task["task_id"])
    fresh = root / "new.txt"
    fresh.write_text("new partial source, still not PIT", encoding="utf-8")
    proposal = c.board.task_packet(task["task_id"])["review_targets"]["proposal"]
    c.board.reopen_evidence(task["task_id"], old["version"], {"operation_id": "new-source", "request_hash": old["request_hash"],
        "prior_decision_event_id": packet["work_id"], "target_event_id": proposal["event_id"],
        "claim_delta": "One new source", "artifacts": [artifact(fresh)]})
    review = Host(c, r, "lean").next()["packet"]
    assert review["review_mode"] == "proposal"
    with pytest.raises(ValueError, match="new artifacts"):
        answer(host, review, {"claim": "Incomplete", "test": "Missing new attachment", "decision": "park", "artifacts": [proof]})
    corrected = host.next()["packet"]
    answer(host, corrected, {"claim": "New evidence is still partial", "test": "Original proposal and new artifact", "decision": "park",
                              "artifacts": [proof, artifact(fresh)]})
    assert c.board.task(task["task_id"])["state"] == "parked" and not c.ledger.jobs()


def test_focus_keeps_gates_failures_and_current_evidence_without_mutating_packet():
    source = {"task_id": "B", "pending": ["original native PIT"], "definition": {"code": "x" * 9000},
              "qualifications": {"head": "same-head", "items": [{"id": "B.PIT", "revision": 3, "text": "original native PIT",
                  "status": "pending", "history": ["x" * 1000]}]}}
    packet = {"context_policy": "task_scoped_v1", "task": {"task_id": "A", "fingerprint": "a", "qualifications": source},
              "memory": {"registry_constraints": {"source_pending": [source], "fixed_rejected": [{"formula": "must stay"}]},
                         "recent_evaluations": [{"assessment": {"economic_status": "not_evaluated"}, "qualifications": source["qualifications"]}]},
              "role_packet": {"tasks": [source], "other_line": [{"task_id": "unrelated", "text": "x" * 9000}]}}
    original = deepcopy(packet)
    reduced = focus_packet(packet)
    assert packet == original and reduced["task"] == packet["task"]
    assert reduced["memory"]["registry_constraints"]["fixed_rejected"] == [{"formula": "must stay"}]
    brief = reduced["memory"]["registry_constraints"]["source_pending"][0]
    assert brief["pending"] == source["pending"] and brief["qualifications"]["head"] == "same-head"
    assert brief["qualifications"]["items"] == [{"id": "B.PIT", "revision": 3, "text": "original native PIT", "status": "pending"}]
    evaluation = reduced["memory"]["recent_evaluations"][0]
    assert evaluation["assessment"] == {"economic_status": "not_evaluated"}
    assert evaluation["qualifications"] == brief["qualifications"]
    assert len(json.dumps(reduced)) < len(json.dumps(packet)) * .6
    assert reduced["role_packet"]["tasks"] == reduced["role_packet"]["other_line"] == []


def test_real_attempt_budget_counts_retries_and_survives_reopen(tmp_path):
    manifest = {"binding": {"spec": {"max_model_attempts": 2}}}
    assert reserve_model_attempt(tmp_path, manifest, "market", "work1", 1)
    assert reserve_model_attempt(tmp_path, manifest, "market", "work1", 1)  # Same intent.
    assert reserve_model_attempt(tmp_path, manifest, "market", "work1", 2)  # Retry counts.
    assert not reserve_model_attempt(tmp_path, deepcopy(manifest), "skeptic", "work2", 1)
    assert len(read(tmp_path / "model-budget.json")["attempts"]) == 2


def test_oversize_prompt_does_not_launch_or_reserve_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr("panda_alpha.team.codex_worker.active", lambda *_: True)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("No model allowed"))
    manifest = {"binding": {"spec": {"max_model_attempts": 2}}, "workers": {"max_attempts": 2, "max_prompt_bytes": 10}, "context": []}
    result = work(tmp_path, manifest, "market", {"work_id": "too-large", "action": "propose"})
    assert result["reason"] == "prompt_size_limit" and result["attempts"] == []
    assert not (tmp_path / "model-budget.json").exists()


def test_default_profile_is_bounded_and_only_two_roles_are_active(tmp_path):
    root = Path(__file__).resolve().parents[1]
    host = read(root / "config/team-host.example.json")
    assert active_roles(host) == ("market", "skeptic")
    assert active_roles({}) == ("lead", "market", "fundamental", "skeptic")
    config = read(root / "config/team-workers.example.json")
    assert host["max_model_attempts"] == 16 and config["max_attempts"] == 2
    assert config["session_scope"] == "task" and config["reasoning_effort"] == "high"
    config["codex_command"] = [sys.executable]
    path = tmp_path / "workers.json"
    write(path, config)
    assert worker_config(path)[0]["session_scope"] == "task"
    config["max_prompt_bytes"] = True
    write(path, config)
    with pytest.raises(ValueError, match="max_prompt_bytes"):
        worker_config(path)


@pytest.mark.parametrize("mode,expected", [("ok", "no_informative_candidate"), ("retry", "model_session_budget")])
def test_synthetic_cli_only_starts_two_roles_and_caps_actual_retries(env, mode, expected):
    """Real local processes, deterministic CLI fixture; zero real model calls."""
    from panda_alpha.team.runtime import serve
    from panda_alpha.team.runtime_io import owned
    c, r, _, root = env
    Host(c, r, "processes", spec(max_model_attempts=1))
    mode_file = root / "mode.txt"
    mode_file.write_text(mode, encoding="utf-8")
    config = root / "workers.json"
    write(config, {"codex_command": [sys.executable, str(Path(__file__).parent / "fixtures/codex_role_cli.py")],
                   "session_scope": "task", "max_attempts": 2, "turn_timeout": 3, "poll_interval": .05,
                   "retry_backoff": 0, "orphan_timeout": 10, "context_files": [str(mode_file)]})
    runtime = root / "runtime"
    result = serve(root / "team.sqlite3", root / "registry.sqlite3", "processes", config, runtime)
    assert result["result"]["reason"] == expected
    assert not result["components"]["lead"]["launches"] and not result["components"]["fundamental"]["launches"]
    assert len(read(runtime / "model-budget.json")["attempts"]) == 1
    states = [read(path) for path in (runtime / "workers").glob("*/work/*/state.json")]
    assert sum(len(state["attempts"]) for state in states) == 1
    assert not any(owned(read(path)["process"]) for path in (runtime / "components").glob("*/process.json"))
    assert list((runtime / "workers/market/sessions").glob("*.json"))
    assert not (runtime / "workers/market/session.json").exists()
    assert not c.ledger.jobs() and r.total == 0
