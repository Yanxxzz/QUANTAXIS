"""Durable intent plus idempotent registry facts: recover across two SQLite files."""
from __future__ import annotations

import json
from pathlib import Path

from ..platform import SOFT_RESEARCH_PENDING
from ..registry import strategy_definition, trial_key
from .contracts import canonical, text_fields, verify_artifacts
from .evidence import assessment_contract, read_artifact, required_qualifications, validate_assessment


def registry_path(registry):
    return str(Path(registry.db.execute("PRAGMA database_list").fetchone()[2]).resolve())


def conclude(c, task_id, version, decision, registry):
    text_fields(decision, ("operation_id", "decision", "reason", "review_event_id"))
    path = registry_path(registry)
    with c.board.transaction():
        previous = c.board.db.execute("SELECT * FROM team_finalizations WHERE operation_id=?", (decision["operation_id"],)).fetchone()
        if previous:
            row = dict(previous)
            if row["task_id"] != task_id or row["registry_path"] != path or json.loads(row["payload"])["decision"] != decision:
                raise ValueError("Conclusion identity already has different content")
        else:
            task = c.board.expect(task_id, version, {"result_reviewed"})
            events = c.board.events(task_id)
            review = [e for e in events if e["kind"] == "result_review"][-1]
            if review["event_id"] != decision["review_event_id"]:
                raise ValueError("Conclusion must bind the current independent result review")
            result = next(e for e in events if e["event_id"] == review["payload"]["reply_to"])
            environment = json.loads(c.ledger.get(task["fingerprint"])["definition"] or "{}").get("team_permit", {}).get("environment", "unknown")
            assessment = validate_assessment(review["payload"], task, result, expected_environment=environment)
            allowed = {"confirm": "confirm", "continue": "continue", "source_pending": "source_pending", "reject": "close"}
            if decision["decision"] != allowed[review["payload"]["decision"]]:
                raise ValueError("Lead conclusion cannot promote or replace independent review")
            request = task["request"]
            subject = trial_key(strategy_definition(request["candidate"], request["window"], request["cycle"], request["groups"]))
            if subject not in registry.registrations():
                raise ValueError("Conclusion requires the original registered experiment; it cannot register a new one")
            memory = registry.reconciled_memory()
            prior = next((r for r in memory["registry_constraints"]["source_pending"] if r["trial_key"] == subject), {})
            earlier = [e for e in registry.events() if e["kind"] == "evaluation" and e["subject"] == subject]
            resolved = set(earlier[-1]["payload"].get("resolved_qualifications", [])) if earlier else set()
            resolved = (resolved | set(assessment["resolved"])) - set(assessment["pending"])
            binding = assessment_contract(review["payload"], result)
            required = required_qualifications(task, read_artifact(binding))
            remaining = ((set(prior.get("pending", [])) | required) - resolved) | set(assessment["pending"])
            if assessment["source_status"] == "verified" and remaining - SOFT_RESEARCH_PENDING:
                raise ValueError("Unresolved historical source qualifications cannot be silently cleared")
            payload = {"task_id": task_id, "fingerprint": task["fingerprint"], "request_hash": task["request_hash"],
                       "run_id": result["payload"]["run_id"], "review_event_id": review["event_id"],
                       "decision": decision, "assessment": assessment, "pending": sorted(remaining), "resolved_qualifications": sorted(resolved),
                       "artifacts": review["payload"]["artifacts"], "evidence_contract": binding,
                       "data_complete": assessment["source_status"] == "verified",
                       "pool_admission": False, "new_independent_oos": False, "attempt_preserved": True}
            facts = [{"kind": "evaluation", "subject": subject, "payload": payload}]
            if remaining:
                facts.append({"kind": "source_pending", "subject": subject, "payload": payload})
            elif prior:
                facts.append({"kind": "source_repair", "subject": subject, "payload": payload})
            if assessment["economic_status"] == "rejected":
                facts.append({"kind": "economic_rejected", "subject": subject, "payload": payload})
            value = {"decision": decision, "facts": facts, "review": review["payload"], "result": result,
                     "task_identity": {k: task[k] for k in ("task_id", "fingerprint", "request_hash")}}
            c.board.db.execute("INSERT INTO team_finalizations VALUES (?,?,?,?, 'pending')",
                               (decision["operation_id"], task_id, path, canonical(value)))
            c.board.event(task_id, "lead", "conclusion_prepared", {"operation_id": decision["operation_id"], "registry_path": path})
            c.board.advance(task_id, "finalizing")
            row = {"operation_id": decision["operation_id"], "task_id": task_id, "registry_path": path,
                   "payload": canonical(value), "state": "pending"}
    return _finish(c, row, registry)


def _finish(c, row, registry):
    if row["registry_path"] != registry_path(registry):
        raise ValueError("Pending conclusion belongs to a different scientific registry")
    if row["state"] == "done":
        return c.board.task(row["task_id"])
    value = json.loads(row["payload"])
    task = c.board.task(row["task_id"])
    if task["state"] != "finalizing" or any(task[k] != v for k, v in value["task_identity"].items()):
        raise ValueError("Pending conclusion task changed")
    environment = json.loads(c.ledger.get(task["fingerprint"])["definition"] or "{}").get("team_permit", {}).get("environment", "unknown")
    validate_assessment(value["review"], task, value["result"], expected_environment=environment)
    registry.db.execute("BEGIN IMMEDIATE")
    try:
        for fact in value["facts"]:
            verify_artifacts(fact["payload"]["artifacts"])
            registry.append_event(fact["kind"], fact["subject"], fact["payload"],
                event_id=row["operation_id"] + ":" + fact["kind"], commit=False)
        registry.db.commit()
    except BaseException:
        registry.db.rollback()
        raise
    return _commit_board(c, row, value)


def _commit_board(c, row, value):
    with c.board.transaction():
        current = c.board.db.execute("SELECT state FROM team_finalizations WHERE operation_id=?", (row["operation_id"],)).fetchone()
        if current[0] != "done":
            decision = value["decision"]
            c.board.event(row["task_id"], "lead", "conclusion", decision, row["operation_id"])
            c.board.advance(row["task_id"], "parked" if decision["decision"] == "source_pending" else "closed")
            c.board.db.execute("UPDATE team_finalizations SET state='done' WHERE operation_id=?", (row["operation_id"],))
    return c.board.task(row["task_id"])


def recover(c, registry):
    rows = [dict(r) for r in c.board.db.execute("SELECT * FROM team_finalizations WHERE state='pending' ORDER BY rowid")]
    return [_finish(c, row, registry) for row in rows]
