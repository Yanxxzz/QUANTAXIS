"""Offline, append-only qualification migration with a recoverable two-store receipt."""
from __future__ import annotations

from copy import deepcopy

from ..qualifications import board_state, digest, legacy_registry_state, replace, snapshot, validate
from ..registry import strategy_definition, trial_key, verify_artifact
from .contracts import canonical


def _cursor(board, task_id):
    events = board.events(task_id)
    return {"event_id": events[-1]["event_id"], "sha256": digest(events)} if events else None


def _identity(task):
    return {k: task[k] for k in ("task_id", "request_hash", "fingerprint", "state", "version")}


def inventory(c, registry):
    """Read-only migration input. Unknown historical prose stays a pending opaque item."""
    memory = registry.reconciled_memory()
    pending = {p["trial_key"] for p in memory["registry_constraints"]["source_pending"]}
    bases = {}
    for event in registry.events():
        if event["subject"] in pending and event["kind"] in {
                "source_pending", "evaluation", "qualification_reconciliation"}:
            bases[event["subject"]] = event
    task_states = {}
    for task in c.board.tasks():
        request = task["request"]
        key = trial_key(strategy_definition(request["candidate"], request["window"], request["cycle"], request["groups"]))
        task_states[key] = (task, board_state(task, c.board.events(task["task_id"])))
    records = []
    for key in sorted(set(bases) | set(task_states)):
        base, task_pair = bases.get(key), task_states.get(key)
        prior = legacy_registry_state(base) if base else None
        task, state = task_pair if task_pair else (None, prior)
        if prior and task:
            # Preserve every registry item, including qualifications absent from a board review.
            rows = {r["id"]: deepcopy(r) for r in state["items"]}
            for old in prior["items"]:
                row = deepcopy(old)
                row["required"] = old["required"] or rows.get(old["id"], {}).get("required", False)
                row["revision"] = digest({k: v for k, v in row.items() if k != "revision"})
                rows[old["id"]] = row
            state = snapshot(task["task_id"], rows.values())
        records.append({"subject": key, "base_event": {k: base[k] for k in ("event_id", "event_hash")} if base else None,
                        "task": _identity(task) if task else None,
                        "board_cursor": _cursor(c.board, task["task_id"]) if task else None,
                        "before": state, "changes": None, "artifacts": []})
    return {"schema": "qualification-migration-v1", "operation_id": "CHOOSE-A-UNIQUE-ID",
            "registry_head": registry.head, "records": records}


def _event_id(document, record):
    return "qualification:" + document["operation_id"] + ":" + digest(record["subject"])[:24]


def _payload(document, record):
    state = (replace(record["before"], record["changes"], event_id=_event_id(document, record),
                     artifacts=record["artifacts"], preserve_required=True)
             if record.get("changes") else validate(record["before"]))
    for proof in record["artifacts"]:
        verify_artifact(proof)
    return dict(record, qualifications=state, operation_id=document["operation_id"],
                source_status_changed=False, pool_admission=False)


def migrate(c, registry, document, *, execute=False):
    if (document.get("schema") != "qualification-migration-v1"
            or not document.get("operation_id") or document["operation_id"] == "CHOOSE-A-UNIQUE-ID"):
        raise ValueError("Migration needs its schema and a unique operation_id")
    if c.board.mode()["mode"] not in {"paused", "offline"}:
        raise ValueError("Qualification maintenance requires stopped research")
    receipt_id = "qualification-migration:" + document["operation_id"]
    existing = next((e for e in registry.events() if e["event_id"] == receipt_id), None)
    if existing:
        if existing["payload"]["document"] != document:
            raise ValueError("Migration identity already has different content")
    else:
        current = inventory(c, registry)
        stripped = deepcopy(document)
        stripped["operation_id"] = current["operation_id"]
        for rec in stripped["records"]:
            rec["changes"], rec["artifacts"] = None, []
        if stripped != current:
            raise ValueError("Migration inventory is stale or modified; generate a fresh preview")
    payloads = [_payload(document, rec) for rec in document["records"]]
    # Validate every board cursor before any write, including recovery after registry commit.
    for payload in payloads:
        if payload["task"]:
            task_id = payload["task"]["task_id"]
            task = c.board.task(task_id)
            event_id = _event_id(document, payload)
            prior = next((e for e in c.board.events(task_id) if e["event_id"] == event_id), None)
            if prior:
                if prior["payload"] != payload:
                    raise ValueError("Board migration identity conflicts")
            elif (task["state"] not in {"parked", "closed"} or _identity(task) != payload["task"]
                  or _cursor(c.board, task_id) != payload["board_cursor"]):
                raise ValueError("Migration task changed or is not stopped")
    if execute:
        if not existing:
            registry.db.execute("BEGIN IMMEDIATE")
            try:
                if registry.head != document["registry_head"]:
                    raise ValueError("Migration registry head changed")
                for payload in payloads:
                    if payload["base_event"]:
                        registry.append_event("qualification_reconciliation", payload["subject"], payload,
                                              event_id=_event_id(document, payload), commit=False)
                registry.append_event("reconciliation", receipt_id, {"document": document}, event_id=receipt_id, commit=False)
                registry.db.commit()
            except BaseException:
                registry.db.rollback()
                raise
        _finish_board(c, document, payloads)
    return {"status": "APPLIED" if execute else "READY", "operation_id": document["operation_id"],
            "records": len(payloads), "tasks": sum(p["task"] is not None for p in payloads),
            "registry_total": registry.total, "registry_head": registry.head,
            "pending_before": sum(sum(r["status"] == "pending" for r in p["before"]["items"]) for p in payloads),
            "pending_after": sum(sum(r["status"] == "pending" for r in p["qualifications"]["items"]) for p in payloads),
            "research_started": False, "official_runs": 0, "official_credits": 0}


def _finish_board(c, document, payloads):
    with c.board.transaction():
        for payload in payloads:
            if payload["task"]:
                task_id = payload["task"]["task_id"]
                event_id = _event_id(document, payload)
                old = c.board.db.execute("SELECT payload FROM team_events WHERE event_id=?", (event_id,)).fetchone()
                if old:
                    if old[0] != canonical(payload):
                        raise ValueError("Board migration identity conflicts")
                else:
                    if (_identity(c.board.task(task_id)) != payload["task"]
                            or _cursor(c.board, task_id) != payload["board_cursor"]):
                        raise ValueError("Migration task changed before board commit")
                    c.board.event(task_id, "steward", "qualification_reconciliation", payload, event_id)
