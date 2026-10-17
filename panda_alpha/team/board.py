"""SQLite is the only mutable task authority; messages are append-only events."""
from __future__ import annotations

from contextlib import contextmanager
import json
from uuid import uuid4

from .contracts import INFORMATION, ROLES, canonical, digest, now, text_fields, validate_request, verify_artifacts


class Board:
    def __init__(self, ledger, *, initialize=True):
        self.ledger, self.db = ledger, ledger.db
        if not initialize:
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"team_control", "team_tasks", "team_events", "team_grants"}.issubset(tables):
                raise ValueError("Team board is not initialized; use init on the authoritative ledger")
            return
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS team_control (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), mode TEXT NOT NULL, reason TEXT NOT NULL, grant_id TEXT);
            INSERT OR IGNORE INTO team_control VALUES(1,'offline','Formal pilot has not been authorized',NULL);
            CREATE TABLE IF NOT EXISTS team_tasks (
                task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL,
                version INTEGER NOT NULL, fingerprint TEXT UNIQUE NOT NULL,
                request_hash TEXT NOT NULL, request TEXT NOT NULL, freeze TEXT);
            CREATE TABLE IF NOT EXISTS team_events (
                event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, actor TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS team_events_no_update BEFORE UPDATE ON team_events
                BEGIN SELECT RAISE(ABORT, 'team events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS team_events_no_delete BEFORE DELETE ON team_events
                BEGIN SELECT RAISE(ABORT, 'team events are immutable'); END;
            CREATE TABLE IF NOT EXISTS team_grants (
                grant_id TEXT PRIMARY KEY, grant_hash TEXT NOT NULL, payload TEXT NOT NULL);
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def mode(self):
        return dict(self.db.execute("SELECT mode,reason,grant_id FROM team_control WHERE singleton=1").fetchone())

    def task(self, task_id):
        row = self.db.execute("SELECT * FROM team_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown task")
        value = dict(row)
        value["request"] = json.loads(value["request"])
        value["freeze"] = json.loads(value["freeze"]) if value["freeze"] else None
        return value

    def tasks(self):
        return [self.task(r[0]) for r in self.db.execute("SELECT task_id FROM team_tasks ORDER BY rowid")]

    def events(self, task_id):
        return [dict(r, payload=json.loads(r["payload"])) for r in self.db.execute(
            "SELECT * FROM team_events WHERE task_id=? ORDER BY rowid", (task_id,))]

    def event(self, task_id, actor, kind, payload, event_id=None):
        event_id = event_id or uuid4().hex
        self.db.execute("INSERT INTO team_events VALUES(?,?,?,?,?,?)",
                        (event_id, task_id, actor, kind, canonical(payload), now()))
        return event_id

    def expect(self, task_id, version, states):
        task = self.task(task_id)
        if task["version"] != version:
            raise ValueError("Stale task version; read the new packet before replying")
        if task["state"] not in states:
            raise ValueError(f"Action is invalid in state {task['state']}")
        return task

    def advance(self, task_id, state):
        self.db.execute("UPDATE team_tasks SET state=?,version=version+1 WHERE task_id=?", (state, task_id))

    def submit(self, request):
        identity = validate_request(request)
        with self.transaction():
            existing = self.db.execute("SELECT task_id,request_hash FROM team_tasks WHERE task_id=? OR fingerprint=?",
                                       (request["task_id"], identity["fingerprint"])).fetchone()
            if existing:
                if existing["request_hash"] != identity["request_hash"]:
                    raise ValueError(f"Duplicate definition or changed task; reference {existing['task_id']}")
                return self.task(existing["task_id"])
            active = [t for t in self.tasks() if t["owner"] == request["owner"] and t["state"] not in {"closed", "parked"}]
            if len(active) >= 2:
                raise ValueError("Researcher WIP limit is two; finish or park an existing task")
            self.db.execute("INSERT INTO team_tasks VALUES(?,?, 'proposed',1,?,?,?,NULL)",
                            (request["task_id"], request["owner"], identity["fingerprint"], identity["request_hash"], canonical(request)))
            self.event(request["task_id"], request["owner"], "proposal", identity)
        return self.task(request["task_id"])

    def message(self, message):
        """Retry is idempotent by event_id; every new reply binds a task version."""
        text_fields(message, ("event_id", "task_id", "actor", "kind", "request_hash", "claim", "test", "decision"))
        actor, kind = message["actor"], message["kind"]
        if actor not in ROLES or kind not in {"challenge", "response", "review", "result_review", "note"}:
            raise ValueError("Unknown role or message kind")
        verify_artifacts(message.get("artifacts", []), required=kind == "result_review")
        with self.transaction():
            prior = self.db.execute("SELECT payload FROM team_events WHERE event_id=?", (message["event_id"],)).fetchone()
            if prior:
                if prior[0] != canonical(message):
                    raise ValueError("Event identity already has different content")
                return self.task(message["task_id"])
            task = self.task(message["task_id"])
            if task["version"] != message.get("version") or task["request_hash"] != message["request_hash"]:
                raise ValueError("Stale version or request hash")
            events = self.events(task["task_id"])
            parent = next((e for e in events if e["event_id"] == message.get("reply_to")), None)
            states = {"challenge": {"proposed"}, "response": {"challenged"}, "review": {"answered"},
                      "result_review": {"result_ready"}, "note": {"proposed", "challenged", "answered", "reviewed"}}
            if task["state"] not in states[kind]:
                raise ValueError("Message does not match current research stage")
            if kind in {"challenge", "review", "result_review"} and actor != "skeptic":
                raise ValueError("Independent skeptic must challenge and review")
            if kind == "response" and (actor != task["owner"] or not parent or parent["kind"] != "challenge"):
                raise ValueError("Author response must reference the challenge")
            if kind == "review" and (not parent or parent["kind"] != "response"):
                raise ValueError("Review must reference the author response")
            if kind == "result_review" and (not parent or parent["kind"] != "result"):
                raise ValueError("Result review must reference the reconciled result")
            if kind == "result_review" and (not parent["payload"]["artifacts"] or
                                           parent["payload"]["artifacts"][0] not in message["artifacts"]):
                raise ValueError("Review must bind the original reconciled result artifact")
            if kind == "review" and message["decision"] not in {"approve", "park"}:
                raise ValueError("Review decision is approve or park")
            if kind == "result_review" and message["decision"] not in {"continue", "confirm", "source_pending", "reject"}:
                raise ValueError("Result review requires an explicit next decision")
            self.event(task["task_id"], actor, kind, message, message["event_id"])
            state = {"challenge": "challenged", "response": "answered", "review": "reviewed",
                     "result_review": "result_reviewed", "note": task["state"]}[kind]
            if kind == "review" and message["decision"] == "park":
                state = "parked"
            self.advance(task["task_id"], state)
        return self.task(task["task_id"])

    def freeze(self, task_id, version, decision):
        text_fields(decision, ("allocation_reason", "diversity_check"))
        if type(decision.get("priority")) is not int or not 0 <= decision["priority"] <= 100:
            raise ValueError("Priority must be an integer in [0,100]")
        with self.transaction():
            task = self.expect(task_id, version, {"reviewed"})
            if validate_request(task["request"])["request_hash"] != task["request_hash"]:
                raise ValueError("Request changed after review")
            if decision.get("request_hash") != task["request_hash"]:
                raise ValueError("Freeze must bind the reviewed request hash")
            if any(t["owner"] == task["owner"] and t["state"] == "ready" for t in self.tasks()):
                raise ValueError("One ready task per research line")
            self.event(task_id, "lead", "freeze", decision)
            self.db.execute("UPDATE team_tasks SET freeze=? WHERE task_id=?", (canonical(decision), task_id))
            self.advance(task_id, "ready")
        return self.task(task_id)

    def decide(self, task_id, version, decision):
        text_fields(decision, ("decision", "reason"))
        if decision["decision"] not in {"close", "park", "continue", "confirm", "source_pending"}:
            raise ValueError("Unknown lead decision")
        with self.transaction():
            task = self.expect(task_id, version, {"proposed", "challenged", "answered", "reviewed", "ready", "result_reviewed"})
            if task["state"] != "result_reviewed" and decision["decision"] != "park":
                raise ValueError("Only park is available before independent result review")
            self.event(task_id, "lead", "decision", decision)
            self.advance(task_id, "parked" if decision["decision"] in {"park", "source_pending"} else "closed")
        return self.task(task_id)

    def pause(self, reason):
        if not reason.strip():
            raise ValueError("Pause reason is required")
        with self.transaction():
            self.db.execute("UPDATE team_control SET mode='paused',reason=? WHERE singleton=1", (reason,))
            self.event("*", "lead", "pause", {"reason": reason})

    def packet(self, role):
        if role not in ROLES:
            raise ValueError("Unknown role")
        all_tasks = self.tasks()
        relevant = [t for t in all_tasks if role in {"lead", "skeptic"} or t["owner"] == role]
        finished = [t for t in relevant if t["state"] in {"closed", "parked"}]
        tasks = [t for t in relevant if t["state"] not in {"closed", "parked"}] + finished[-2:]
        return {"role": role, "control": self.mode(), "tasks": [dict(t, events=self.events(t["task_id"])[-8:]) for t in tasks],
                "recent_history": [{"task_id": t["task_id"], "mechanism_id": t["request"]["mechanism_id"], "state": t["state"]}
                                   for t in finished[-20:]], "older_history_count": max(0, len(finished) - 20),
                "other_line": [{"task_id": t["task_id"], "mechanism_id": t["request"]["mechanism_id"],
                                "information_family": t["request"]["information_family"], "state": t["state"]}
                               for t in all_tasks[-20:] if role in INFORMATION and t["owner"] != role],
                "instruction": "Reply with version, request_hash and reply_to; propose changes as a new task. Do not write SQLite directly."}
