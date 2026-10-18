"""No model, network or platform calls: exact closure and append-only recovery."""
from copy import deepcopy
import json

import pytest

from panda_alpha.qualifications import (board_state, fixed_id, observe, replace, snapshot, texts)
from panda_alpha.registry import ResearchRegistry
from panda_alpha.team import qualification_migration as migration
from panda_alpha.team.evidence import artifact
from panda_alpha.team.sources import readiness
from test_team_lifecycle import lifecycle, settled, close, reopen, result_message


def changes(state, rows, proof, *, identity="delivery", text="Complete cached logs verified", status="resolved"):
    return {"base_head": state["head"], "replacements": [{
        "supersedes": [{"id": r["id"], "revision": r["revision"]} for r in rows],
        "items": [{"id": identity, "text": text, "status": status}],
        "reason": "Exact legacy assertion replaced by bound evidence", "evidence": [proof]}]}


def source_state(proof):
    return observe(snapshot("task-A"), ["cached logs missing", "native PIT"],
                   event_id="old-review", required=["native PIT"])


def test_changed_wording_closes_exact_id_and_preserves_native_gate(tmp_path):
    path = tmp_path / "logs.json"
    path.write_text('{"complete":true}')
    proof = artifact(path)
    state = source_state(proof)
    log = next(r for r in state["items"] if r["text"] == "cached logs missing")
    update = changes(state, [log], proof)
    revised = replace(state, update, event_id="correction", artifacts=[proof])
    assert texts(revised) == ["native PIT"]
    old = next(r for r in revised["items"] if r["id"] == log["id"])
    assert old["status"] == "superseded" and old["superseded_by"][0]["id"] == "delivery"
    assert texts(state) == ["cached logs missing", "native PIT"]
    assert observe(revised, [log["text"]], event_id="stale-prose") == revised
    assert fixed_id("task-A", log["text"]) != fixed_id("task-B", log["text"])


@pytest.mark.parametrize("damage", ["base", "revision", "foreign_id", "unbound_evidence", "duplicate_target", "collision", "empty_items"])
def test_inexact_or_unbound_replacement_rejected(tmp_path, damage):
    path = tmp_path / "proof.txt"
    path.write_text("proof")
    proof = artifact(path)
    state = source_state(proof)
    log = next(r for r in state["items"] if r["text"] == "cached logs missing")
    update = changes(state, [log], proof)
    group = update["replacements"][0]
    if damage == "base":
        update["base_head"] = "wrong"
    elif damage == "revision":
        group["supersedes"][0]["revision"] = "wrong"
    elif damage == "foreign_id":
        group["supersedes"][0]["id"] = fixed_id("task-B", log["text"])
    elif damage == "unbound_evidence":
        group["evidence"][0] = dict(proof, sha256="0" * 64)
    elif damage == "duplicate_target":
        group["supersedes"] *= 2
    elif damage == "collision":
        group["items"][0]["id"] = fixed_id("task-A", "native PIT")
    else:
        group["items"] = []
    with pytest.raises(ValueError):
        replace(state, update, event_id="review", artifacts=[proof])


def test_mixed_claim_split_keeps_native_gate_and_cannot_cycle(tmp_path):
    path = tmp_path / "proof.txt"
    path.write_text("public repositories differ; actual deployment unknown")
    proof = artifact(path)
    state = observe(snapshot("A"), ["worker binding and cross-repo equivalence"], event_id="old",
                    required=["worker binding and cross-repo equivalence"])
    old = state["items"][0]
    update = changes(state, [old], proof, identity=old["id"], text="Actual deployment still unknown", status="pending")
    update["replacements"][0]["items"].append({"id": "repo-provenance", "text": "Different repository SHAs need not match", "status": "resolved"})
    revised = replace(state, update, event_id="split", artifacts=[proof], preserve_required=True)
    assert texts(revised) == ["Actual deployment still unknown"]
    with pytest.raises(ValueError, match="Stale"):
        replace(revised, update, event_id="repeat", artifacts=[proof])
    bad = deepcopy(update)
    bad["replacements"][0]["items"][0]["status"] = "resolved"
    with pytest.raises(ValueError, match="gate"):
        replace(state, bad, event_id="unsafe-migration", artifacts=[proof], preserve_required=True)
    bad["replacements"][0]["items"] = bad["replacements"][0]["items"][1:]
    with pytest.raises(ValueError, match="gate"):
        replace(state, bad, event_id="erased-gate", artifacts=[proof])


def migration_case(env):
    c, registry, proof, root = env
    task, result = settled(env)
    message = result_message(task, result)
    message["assessment"]["pending"] += ["cached logs missing", "worker binding and SHA equivalence"]
    reviewed = c.board.message(message)
    c.conclude(task["task_id"], reviewed["version"], {"operation_id": "close", "decision": "confirm",
        "reason": "Capability only", "review_event_id": "review"}, registry)
    c.board.pause("Offline maintenance")
    doc = migration.inventory(c, registry)
    doc["operation_id"] = "migration-1"
    record = next(r for r in doc["records"] if r["task"])
    row = next(r for r in record["before"]["items"] if r["text"] == "cached logs missing")
    record["artifacts"] = [proof]
    record["changes"] = changes(record["before"], [row], proof)
    return doc, task, result


def test_migration_preview_apply_retry_preserve_history_budget_and_gates(lifecycle):
    c, registry, _, _ = lifecycle
    doc, task, _ = migration_case(lifecycle)
    before = registry.events()
    board_events = c.board.events(task["task_id"])
    jobs, tasks, control = c.ledger.jobs(), c.board.tasks(), c.board.mode()
    assert migration.migrate(c, registry, doc)["status"] == "READY"
    assert registry.events() == before and c.board.events(task["task_id"]) == board_events
    applied = migration.migrate(c, registry, doc, execute=True)
    assert applied["official_credits"] == applied["official_runs"] == 0
    assert registry.events()[:len(before)] == before
    assert c.board.events(task["task_id"])[:len(board_events)] == board_events
    assert c.ledger.jobs() == jobs and c.board.tasks() == tasks and c.board.mode() == control
    count = len(registry.events())
    migration.migrate(c, registry, doc, execute=True)
    assert len(registry.events()) == count and registry.total == 1
    pending = registry.reconciled_memory()["registry_constraints"]["source_pending"][0]
    board = readiness(c)["tasks"][0]
    assert pending["qualifications"] == board["qualifications"]
    assert "cached logs missing" not in pending["pending"]
    assert "native_input_and_clock" in pending["pending"]
    assert "worker binding and SHA equivalence" in pending["pending"]
    assert board["source_status"] == "pending" and board["economic_status"] == "not_evaluated"
    registry.verify_integrity()


def test_migration_recovers_registry_commit_before_board_commit(lifecycle, monkeypatch):
    c, registry, _, _ = lifecycle
    doc, task, _ = migration_case(lifecycle)
    finish = migration._finish_board
    def interrupted(*args):
        raise InterruptedError("registry committed; process stopped")
    monkeypatch.setattr(migration, "_finish_board", interrupted)
    with pytest.raises(InterruptedError):
        migration.migrate(c, registry, doc, execute=True)
    count = len(registry.events())
    assert c.board.events(task["task_id"])[-1]["kind"] != "qualification_reconciliation"
    monkeypatch.setattr(migration, "_finish_board", finish)
    migration.migrate(c, registry, doc, execute=True)
    assert len(registry.events()) == count
    assert c.board.events(task["task_id"])[-1]["kind"] == "qualification_reconciliation"


def test_migration_registry_transaction_rolls_back_before_receipt(lifecycle, monkeypatch):
    c, registry, _, _ = lifecycle
    doc, task, _ = migration_case(lifecycle)
    before, board = registry.events(), c.board.events(task["task_id"])
    original = registry.append_event
    def interrupted(kind, *args, **kwargs):
        if kind == "reconciliation":
            raise InterruptedError("before registry commit")
        return original(kind, *args, **kwargs)
    monkeypatch.setattr(registry, "append_event", interrupted)
    with pytest.raises(InterruptedError):
        migration.migrate(c, registry, doc, execute=True)
    assert registry.events() == before and c.board.events(task["task_id"]) == board
    monkeypatch.setattr(registry, "append_event", original)
    assert migration.migrate(c, registry, doc, execute=True)["status"] == "APPLIED"


def test_registry_refuses_projection_that_erases_legacy_pending(lifecycle):
    c, registry, _, _ = lifecycle
    doc, _, _ = migration_case(lifecycle)
    record = doc["records"][0]
    payload = migration._payload(doc, record)
    payload["before"] = snapshot(record["before"]["scope"])
    payload["changes"] = None
    payload["qualifications"] = payload["before"]
    with pytest.raises(ValueError, match="omitted or rewrote"):
        registry.append_event("qualification_reconciliation", record["subject"], payload, event_id="invalid")


@pytest.mark.parametrize("damage", ["stale_board", "stale_registry", "edited_evidence", "omitted_record", "edited_before"])
def test_stale_or_tampered_migration_writes_nothing(lifecycle, damage):
    c, registry, proof, _ = lifecycle
    doc, task, _ = migration_case(lifecycle)
    if damage == "stale_board":
        with c.board.transaction():
            c.board.event(task["task_id"], "steward", "maintenance_note", {"reason": "new fact"})
    elif damage == "stale_registry":
        registry.append_event("reconciliation", "new fact", {})
    elif damage == "edited_evidence":
        from pathlib import Path
        Path(proof["path"]).write_text("changed")
    elif damage == "omitted_record":
        doc["records"] = []
    else:
        doc["records"][0]["before"]["items"] = []
    before, board = registry.events(), c.board.events(task["task_id"])
    with pytest.raises(ValueError):
        migration.migrate(c, registry, doc, execute=True)
    assert registry.events() == before and c.board.events(task["task_id"]) == board


def test_new_review_uses_ids_and_keeps_correction_in_memory(lifecycle):
    c, registry, proof, _ = lifecycle
    doc, task, result = migration_case(lifecycle)
    migration.migrate(c, registry, doc, execute=True)
    current = c.board.task(task["task_id"])
    repair = reopen(lifecycle, current, result)
    fresh = c.board.reopen_evidence(task["task_id"], current["version"], repair)
    message = result_message(fresh, result, event_id="later-review")
    message.update(decision="source_pending", artifacts=message["artifacts"] + repair["artifacts"])
    message["assessment"]["resolved"] = ["worker binding and SHA equivalence"]
    with pytest.raises(ValueError, match="fixed IDs"):
        c.board.message(message)
    state = c.board.task_packet(task["task_id"])["qualifications"]
    old = next(r for r in state["items"] if r["text"] == "worker binding and SHA equivalence")
    # Split the obsolete mixed wording; never resolve the actual deployment gate.
    message["qualification_changes"] = changes(state, [old], repair["artifacts"][0],
        identity="public-repository-provenance", text="Separate repositories identified")
    reviewed = c.board.message(message)
    assert c.board.message(message) == reviewed  # Derived snapshot does not break retry identity.
    c.conclude(task["task_id"], reviewed["version"], {"operation_id": "later-close", "decision": "source_pending",
        "reason": "Keep native gates", "review_event_id": "later-review"}, registry)
    pending = registry.reconciled_memory()["registry_constraints"]["source_pending"][0]
    assert "cached logs missing" not in pending["pending"]
    assert "worker binding and SHA equivalence" not in pending["pending"]
    assert "worker_deployment_binding" in pending["pending"]
    assert pending["qualifications"] == readiness(c)["tasks"][0]["qualifications"]


def test_unstructured_legacy_record_is_numbered_without_inventing_resolution(lifecycle):
    c, registry, _, _ = lifecycle
    registry.append_event("source_pending", "old-unregistered-direction", {"reason": "Historical PIT unknown"})
    before = registry.events()
    doc = migration.inventory(c, registry)
    doc["operation_id"] = "opaque-legacy"
    migration.migrate(c, registry, doc, execute=True)
    row = registry.reconciled_memory()["registry_constraints"]["source_pending"][0]
    assert len(row["qualifications"]["items"]) == 1
    assert row["qualifications"]["items"][0]["status"] == "pending"
    assert row["reason"] == "Historical PIT unknown" and registry.total == 0
    assert registry.events()[:len(before)] == before


def test_registry_read_only_does_not_initialize_missing_file(tmp_path):
    import sqlite3
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        ResearchRegistry(path, read_only=True)
    assert not path.exists()


def test_old_key_cannot_reopen_a_renamed_resolved_id(lifecycle):
    from panda_alpha.qualifications import review_state
    c, _, proof, _ = lifecycle
    task, result = settled(lifecycle)
    state = c.board.task_packet(task["task_id"])["qualifications"]
    # A non-native receipt has an old text-derived ID and a new display text.
    state = observe(state, ["receipt missing"], event_id="old")
    old = next(r for r in state["items"] if r["text"] == "receipt missing")
    state = replace(state, changes(state, [old], proof, identity=old["id"], text="Receipt archived"),
                    event_id="fixed", artifacts=[proof])
    events = [{"event_id": "fixed", "kind": "qualification_reconciliation", "payload": {"qualifications": state}}]
    message = result_message(task, result)
    message["assessment"]["pending"].append("receipt missing")
    with pytest.raises(ValueError, match="Reopening"):
        review_state(task, events, message, {"unknown": []})
