"""Deterministic coordination; agents supply hypotheses and reviews, not money."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..platform import ExperimentLedger, dispatch, resume
from .board import Board
from .budget import prepare
from .contracts import TASKS, canonical


class Coordinator:
    def __init__(self, ledger_path, *, read_only=False):
        self.ledger = ExperimentLedger(ledger_path, read_only=read_only)
        try:
            self.board = Board(self.ledger, initialize=not read_only)
        except BaseException:
            self.ledger.db.close()
            raise

    def close(self):
        self.ledger.db.close()

    def plan(self):
        """Offline opportunity queue. A queue position is never spending authority."""
        tasks = self.board.tasks()
        counts = {role: sum(self.ledger.get(t["fingerprint"]) is not None for t in tasks if t["owner"] == role)
                  for role in ("market", "fundamental")}
        ready = [t for t in tasks if t["state"] == "ready"]
        # First eligible opportunity to each information line, then information
        # value (lead priority); stable task order is the final tie breaker.
        ready.sort(key=lambda t: (counts[t["owner"]] > 0, -t["freeze"]["priority"]))
        role_action = {"proposed": ("skeptic", "challenge"), "challenged": (None, "response"),
                       "answered": ("skeptic", "review"), "reviewed": ("lead", "freeze"),
                       "ready": ("lead", "await_grant_or_dispatch"), "submitted": ("lead", "resume_original_run"),
                       "result_ready": ("skeptic", "result_review"), "result_reviewed": ("lead", "decide")}
        return {"control": self.board.mode(), "funding_authorization": False,
                "queue": [t["task_id"] for t in ready], "attempts_by_line": counts,
                "unsettled": [{"fingerprint": j["fingerprint"], "state": j["state"], "run_id": j["run_id"]}
                              for j in self.ledger.jobs() if j["state"] not in {"SETTLED", "CANCELLED"}],
                "work": [{"task_id": t["task_id"], "version": t["version"],
                          "role": role_action[t["state"]][0] or t["owner"], "action": role_action[t["state"]][1]}
                         for t in tasks if t["state"] in role_action]}

    def dispatch(self, task_id, config, client, registry, *, execute=False, native_fixture_reader=None):
        task = self.board.task(task_id)
        existing = self.ledger.get(task["fingerprint"])
        if existing:
            self.adopt_existing(task, existing)
            return existing  # Recover even if grant expired or mode was paused.
        if not execute:
            raise ValueError("Explicit execute is required; plan remains offline")
        configured = prepare(self.board, task, config, simulation=getattr(client, "simulation", False) is True)
        request, candidate = task["request"], task["request"]["candidate"]
        from ..evolution import Candidate, ResearchEngine
        memory = {}
        for name in ("memory", "live_memory"):
            if config["research"].get(name):
                memory.update(json.loads(Path(config["research"][name]).read_text(encoding="utf-8-sig")))
        memory = registry.reconciled_memory(memory)
        engine = ResearchEngine(registry=registry, memory=memory,
                                research_context={k: request[k] for k in ("window", "cycle", "groups")})
        view = Candidate.from_dict(dict(candidate, hypothesis=request["hypothesis"], mechanism=request["mechanism_id"]))
        if not engine.allows_research(view):
            raise ValueError("Existing research memory blocks this definition or retired direction")
        return dispatch(candidate, request["window"], configured, self.ledger, client,
                        category=TASKS[candidate["official_task"]], native_fixture_reader=native_fixture_reader,
                        before_dispatch=lambda: registry.record(candidate, request["window"], request["cycle"], request["groups"]))

    def adopt_existing(self, task, job):
        """Reuse old evidence without registering, charging, or claiming fresh OOS."""
        with self.board.transaction():
            if self.board.task(task["task_id"])["state"] == "ready":
                self.board.event(task["task_id"], "steward", "reuse_existing", {
                    "fingerprint": job["fingerprint"], "run_id": job["run_id"], "state": job["state"],
                    "prior_evidence": True, "new_independent_oos": False})
                self.board.advance(task["task_id"], "submitted")

    def resume(self, task_id, client, output_dir):
        task = self.board.task(task_id)
        existing = self.ledger.get(task["fingerprint"])
        if existing:
            definition = json.loads(existing["definition"] or "{}")
            simulation = definition.get("team_permit", {}).get("environment") == "synthetic"
            if simulation != (getattr(client, "simulation", False) is True):
                raise ValueError("Resume client must match the persisted execution environment")
            self.adopt_existing(task, existing)
        result = resume(task["fingerprint"], self.ledger, client, Path(output_dir))
        job = self.ledger.get(task["fingerprint"])
        if job and job["state"] in {"SETTLED", "CANCELLED"}:
            with self.board.transaction():
                current = self.board.task(task_id)
                if current["state"] == "submitted":
                    artifacts = []
                    for suffix in (".json", ".logs.json"):
                        path = Path(output_dir) / (str(job["run_id"]) + suffix)
                        if path.is_file():
                            artifacts.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                    payload = {"fingerprint": task["fingerprint"],
                                     "run_id": job["run_id"], "state": job["state"], "actual": job["actual"],
                                     "receipt": json.loads(job["receipt"]) if job["receipt"] else None,
                                     "artifacts": artifacts}
                    manifest = Path(output_dir) / (task["fingerprint"] + ".team-result.json")
                    manifest.parent.mkdir(parents=True, exist_ok=True)
                    manifest.write_text(canonical(payload), encoding="utf-8")
                    binding = {"path": str(manifest.resolve()), "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
                    self.board.event(task_id, "steward", "result", dict(payload, artifacts=[binding] + artifacts))
                    self.board.advance(task_id, "result_ready")
        return result
