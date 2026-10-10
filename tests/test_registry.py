import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from panda_alpha.memory import export_registry_memory
from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "trials.sqlite3"
        self.window = {"start": "20250101", "end": "20260101"}

    def tearDown(self):
        self.temp.cleanup()

    def candidate(self, number=1):
        return {"candidate_id": f"T{number}", "formula": f"MA(CLOSE,{number})", "direction": 1}

    def artifact(self, name, contents="frozen source"):
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def manifest(self, entries, total, events=(), name="manifest.json"):
        document = {"schema_version": 1, "manifest_id": "explicit-seven-studies",
                    "historical_baseline": 415, "registrations": entries, "events": list(events),
                    "checkpoint": {"cumulative_total": total}}
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def entry(self, number):
        return {"unique_identity": f"study-{number}", "candidate_id": f"T{number}",
                "candidate": self.candidate(number), "window": self.window, "cycle": 5,
                "groups": 10, "protocol": self.artifact(f"protocol{number}.json"),
                "registered_before_labels": True}

    def test_legacy_442_and_explicit_seven_become_449_without_double_count(self):
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE metadata(name TEXT PRIMARY KEY,value INTEGER)")
        db.execute("INSERT INTO metadata VALUES ('historical',415)")
        db.execute("CREATE TABLE trials(trial_key TEXT PRIMARY KEY,candidate_id TEXT,definition TEXT,observed_at TEXT)")
        for number in range(1, 28):
            definition = strategy_definition(self.candidate(number), self.window, 5, 10)
            db.execute("INSERT INTO trials VALUES (?,?,?,?)", (trial_key(definition), f"T{number}",
                       json.dumps(definition, sort_keys=True), "2026-10-06"))
        db.commit()
        original = db.execute("SELECT * FROM trials ORDER BY trial_key").fetchall()
        db.close()
        ledger = ResearchRegistry(self.path, 415)
        self.assertEqual(ledger.total, 442)
        manifest = self.manifest([self.entry(n) for n in range(28, 35)], 449)
        before = ledger.head
        plan = ledger.plan_import(manifest)
        self.assertEqual(before, ledger.head)
        self.assertEqual(plan["new_unique"], 7)
        self.assertEqual(plan["status"], "READY")
        ledger.import_manifest(plan)
        self.assertEqual(ledger.total, 449)
        self.assertEqual(ledger.db.execute("SELECT * FROM trials ORDER BY trial_key").fetchall(), original)
        ledger.close()
        restarted = ResearchRegistry(self.path, 415)
        self.assertEqual((restarted.historical, restarted.new_tested, restarted.total), (415, 34, 449))
        repeated = restarted.plan_import(manifest)
        self.assertEqual(repeated["new_unique"], 0)
        restarted.import_manifest(repeated)
        self.assertEqual(restarted.total, 449)
        restarted.close()

    def test_cumulative_total_never_becomes_a_new_historical_increment(self):
        ledger = ResearchRegistry(self.path, 415)
        ledger.record(self.candidate(), self.window, 5, 10)
        plan = ledger.plan_import(self.manifest([], 449))
        self.assertEqual(plan["status"], "UNRESOLVED")
        self.assertEqual(plan["reconciliation"]["expected_total"], 416)
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            ledger.import_manifest(plan)
        self.assertEqual(ledger.total, 416)
        ledger.close()
        with self.assertRaisesRegex(ValueError, "immutable"):
            ResearchRegistry(self.path, 449)

    def test_cost_source_and_execution_repairs_do_not_register_new_strategy(self):
        ledger = ResearchRegistry(self.path, 415)
        candidate = self.candidate()
        self.assertTrue(ledger.record(candidate, self.window, 5, 10))
        self.assertFalse(ledger.record({**candidate, "candidate_id": "RENAMED", "cost": 0.005}, self.window, 5, 10))
        key = next(iter(ledger.registrations()))
        for kind in ("source_pending", "source_repair", "execution_repair", "evaluation"):
            ledger.append_event(kind, key, {"cost": 0.005, "new_hypotheses": 0})
        self.assertEqual(ledger.total, 416)
        self.assertTrue(ledger.record({**candidate, "direction": 0}, self.window, 5, 10))
        self.assertTrue(ledger.record(candidate, {**self.window, "end": "20260201"}, 5, 10))
        self.assertEqual(ledger.total, 418)
        ledger.close()

    def test_structured_portfolio_policies_count_definitions_not_execution_adapters(self):
        ledger = ResearchRegistry(self.path, 415)
        policy = {"candidate_id": "BUFFER", "direction": 0,
                  "strategy_spec": {"baseline": "F141", "entry_percentile": 10, "exit_percentile": 15}}
        ledger.record(policy, self.window, 5, 10)
        self.assertFalse(ledger.record({**policy, "native_code_sha256": "patched"}, self.window, 5, 10))
        self.assertTrue(ledger.record({**policy, "strategy_spec": {**policy["strategy_spec"], "exit_percentile": 20}}, self.window, 5, 10))
        self.assertEqual(ledger.total, 417)
        ledger.close()

    def test_planned_import_detects_changed_proof_and_stale_database(self):
        ledger = ResearchRegistry(self.path, 415)
        entry = self.entry(1)
        manifest = self.manifest([entry], 416)
        plan = ledger.plan_import(manifest)
        Path(entry["protocol"]["path"]).write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            ledger.import_manifest(plan)
        Path(entry["protocol"]["path"]).write_text("frozen source", encoding="utf-8")
        ledger.record(self.candidate(2), self.window, 5, 10)
        with self.assertRaisesRegex(ValueError, "stale"):
            ledger.import_manifest(plan)
        self.assertEqual(ledger.total, 416)
        ledger.close()

    def test_identity_conflict_and_unknown_checkpoint_are_reviewable_failures(self):
        ledger = ResearchRegistry(self.path, 415)
        first, second = self.entry(1), self.entry(2)
        second["unique_identity"] = first["unique_identity"]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            ledger.plan_import(self.manifest([first, second], 417))
        self.assertEqual(ledger.total, 415)
        unresolved = ledger.reconcile(415, ["UNKNOWN"])
        self.assertEqual(unresolved["unmatched_identities"], ["UNKNOWN"])
        self.assertEqual(unresolved["status"], "UNRESOLVED")
        ledger.close()

    def test_sqlite_facts_are_immutable_and_chain_is_verified(self):
        ledger = ResearchRegistry(self.path, 415)
        for statement in ("DELETE FROM research_events", "UPDATE research_events SET subject='fake'"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                ledger.db.execute(statement)
        ledger.verify_integrity()
        event = ledger.events()[0]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            ledger.append_event("baseline", "historical", {"count": 449}, event_id=event["event_id"])
        ledger.close()

    def test_hot_memory_keeps_evidence_and_enforces_human_reopen_separately(self):
        ledger = ResearchRegistry(self.path, 415)
        ledger.record(self.candidate(), self.window, 5, 10)
        key = next(iter(ledger.registrations()))
        with self.assertRaisesRegex(ValueError, "Missing source"):
            ledger.append_event("economic_rejected", key, {"data_complete": False})
        ledger.append_event("source_pending", key, {"data_complete": False})
        ledger.append_event("human_retired", "earnings", {"actor": "human", "instruction": "淘汰该方向", "candidate_ids": ["T1"]})
        base = {"recent_research": [{"summary_sha256": "retained"}], "source_findings": [{"source_sha256": "original"}]}
        exported = export_registry_memory(ledger, base, self.root / "hot.json")
        self.assertEqual(exported["multiple_testing_denominator"], 415)
        self.assertEqual(exported["multiple_testing_total"], 416)
        self.assertEqual(exported["recent_research"], base["recent_research"])
        self.assertEqual(exported["source_findings"], base["source_findings"])
        self.assertNotIn("research_registry", base)
        self.assertEqual(len(exported["registry_constraints"]["source_pending"]), 1)
        ledger.append_event("source_repair", key, {"data_complete": True})
        self.assertEqual(len(ledger.export_hot_memory(base)["registry_constraints"]["human_retired"]), 1)
        with self.assertRaisesRegex(ValueError, "explicit human"):
            ledger.append_event("human_reopened", "earnings", {"actor": "llm", "instruction": "try"})
        ledger.append_event("human_reopened", "earnings", {"actor": "human", "instruction": "重启该方向"})
        snapshot = ledger.export_hot_memory(base)
        self.assertEqual(snapshot["registry_constraints"]["human_retired"], [])
        self.assertEqual(snapshot["registry_constraints"]["source_pending"], [])
        ledger.close()

    def test_empty_registry_preserves_legacy_human_retirement_until_explicit_reopen(self):
        from panda_alpha.evolution import Candidate, ResearchEngine
        ledger = ResearchRegistry(self.path, 415)
        base = {"human_retired_directions": [{"direction": "earnings", "candidate_ids": ["EP05"],
                 "status": "RETIRED_BY_EXPLICIT_USER_DIRECTION", "human_instruction": "淘汰该方向"}]}
        candidate = Candidate("earnings", "disclosure", formula="GUIDANCE_SCORE", candidate_id="EP05")
        self.assertFalse(ResearchEngine(memory=ledger.export_hot_memory(base))._admissible(candidate))
        ledger.append_event("human_reopened", "earnings", {"actor": "human", "instruction": "重启该方向"})
        self.assertTrue(ResearchEngine(memory=ledger.export_hot_memory(base))._admissible(candidate))
        ledger.close()

    def test_verified_evidence_reopen_clears_only_target_fixed_rejection(self):
        ledger = ResearchRegistry(self.path, 415)
        ledger.record(self.candidate(), self.window, 5, 10)
        key = next(iter(ledger.registrations()))
        ledger.append_event("economic_rejected", key, {"data_complete": True})
        ledger.append_event("human_retired", "earnings", {"actor": "human", "instruction": "淘汰", "candidate_ids": ["T1"]})
        with self.assertRaisesRegex(ValueError, "verified source"):
            ledger.append_event("evidence_reopened", key, {"change_type": "renamed"})
        ledger.append_event("evidence_reopened", key, {"change_type": "verified_source_repair", "description": "New reporting date source",
            "verification_summary": "Verified original date", "falsifier": "Reject if repaired sample fails",
            "artifacts": [self.artifact("new-source.json")]})
        snapshot = ledger.export_hot_memory()
        self.assertEqual(snapshot["registry_constraints"]["fixed_rejected"], [])
        self.assertEqual(len(snapshot["registry_constraints"]["human_retired"]), 1)
        self.assertEqual(ledger.total, 416)
        ledger.close()

    def test_unregistered_source_pending_is_preserved_without_a_trial_or_economic_ban(self):
        ledger = ResearchRegistry(self.path, 415)
        ledger.append_event("source_pending", "report-source-probe", {"data_complete": False, "records": 0})
        snapshot = ledger.export_hot_memory()
        self.assertEqual(len(snapshot["registry_constraints"]["source_pending"]), 1)
        self.assertEqual(snapshot["registry_constraints"]["fixed_rejected"], [])
        self.assertEqual(ledger.total, 415)
        with self.assertRaisesRegex(ValueError, "registered strategy"):
            ledger.append_event("economic_rejected", "report-source-probe", {"data_complete": True})
        ledger.close()

    def test_economic_payload_cannot_override_registered_strategy_scope(self):
        ledger = ResearchRegistry(self.path, 415)
        ledger.record(self.candidate(), self.window, 5, 10)
        key, original = next(iter(ledger.registrations().items()))
        forged = strategy_definition(self.candidate(99), self.window, 5, 10)
        ledger.append_event("economic_rejected", key, {"data_complete": True, "definition": forged, "candidate_id": "T99"})
        derived = ledger.export_hot_memory()["registry_constraints"]["fixed_rejected"][0]
        self.assertEqual(derived["definition"], original["definition"])
        self.assertEqual(derived["candidate_id"], original["candidate_id"])
        ledger.close()

    def test_unimported_recent_total_is_exposed_as_unresolved_and_never_double_counted(self):
        ledger = ResearchRegistry(self.path, 415)
        ledger.record(self.candidate(), self.window, 5, 10)
        base = {"multiple_testing_denominator": 415, "multiple_testing_total": 449,
                "recent_research": [{"trial_total": 449, "new_trials": 7}]}
        snapshot = ledger.export_hot_memory(base)
        self.assertEqual(snapshot["multiple_testing_denominator"], 415)
        self.assertEqual(snapshot["multiple_testing_total"], 416)
        check = snapshot["research_registry"]["memory_reconciliation"]
        self.assertEqual(check["status"], "UNRESOLVED")
        self.assertEqual(check["latest_asserted_total"], 449)
        self.assertFalse(check["increment_inferred"])
        ledger.close()

    def test_parallel_writers_append_one_consistent_hash_chain(self):
        ledger = ResearchRegistry(self.path, 415)
        ledger.close()
        barrier = threading.Barrier(2)
        class CoordinatedRegistry(ResearchRegistry):
            @property
            def head(self):
                captured = super().head
                try:
                    barrier.wait(timeout=2)
                except threading.BrokenBarrierError:
                    pass
                return captured
        def append(number):
            writer = CoordinatedRegistry(self.path, 415)
            writer.append_event("source_pending", f"source-{number}", {"data_complete": False})
            writer.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(append, (1, 2)))
        restored = ResearchRegistry(self.path, 415)
        restored.verify_integrity()
        self.assertEqual(len(restored.events()), 3)
        restored.close()


if __name__ == "__main__":
    unittest.main()
