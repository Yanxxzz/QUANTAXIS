import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from panda_alpha.evolution import (Candidate, FailureEvidence, OpenAICompatibleBackend,
                                  ResearchEngine, deterministic_candidates)


class EvolutionTests(unittest.TestCase):
    def test_default_planning_is_diverse_pending_and_no_grid_reentry(self):
        engine = ResearchEngine()
        candidates = engine.propose(10)
        self.assertEqual(len(candidates), 10)
        self.assertEqual(len({c.mechanism for c in candidates}), 10)
        self.assertTrue(all(c.validation_status == c.economic_status == "pending" for c in candidates))
        for candidate in candidates:
            ast.parse(candidate.code)
            self.assertTrue(set(candidate.fields).issubset({"CLOSE", "OPEN", "HIGH", "LOW", "VOLUME", "AMOUNT"}))
            self.assertTrue(candidate.falsification_plan)
        self.assertEqual(engine.propose(10), [])

    def test_failure_attribution_actions_and_economic_separation(self):
        cases = [(FailureEvidence(data_complete=False), "new_source"),
                 (FailureEvidence(correlated_with=("F-existing",)), "orthogonalize"),
                 (FailureEvidence(failure_type="syntax"), "repair"),
                 (FailureEvidence(falsified=True), "abandon"),
                 (FailureEvidence(reproducible_signal=True), "escalate")]
        for evidence, action in cases:
            engine = ResearchEngine()
            candidate = deterministic_candidates()[0]
            decision = engine.reflect(candidate, evidence)
            self.assertEqual(decision.action, action)
            self.assertEqual(decision.economic_status, "pending")
            self.assertEqual(candidate.economic_status, "pending")
            self.assertEqual(candidate.attempts, 1)
            self.assertEqual(candidate.trajectory[-1]["event"], "reflection")

    def test_parameter_grid_failure_cap_persists(self):
        engine = ResearchEngine(max_family_attempts=2)
        first = Candidate("hypothesis", "mechanism", formula="MA(CLOSE,10)")
        sibling = Candidate("hypothesis", "mechanism", formula="MA(CLOSE,30)")
        self.assertEqual(engine.reflect(first, FailureEvidence()).action, "repair")
        self.assertEqual(engine.reflect(sibling, FailureEvidence()).action, "abandon")
        self.assertFalse(engine._admissible(first))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            engine.save_state(path)
            restored = ResearchEngine(state_path=path)
            self.assertFalse(restored._admissible(first))
            self.assertEqual(restored.attempts[first.family_id], 2)
            self.assertEqual(len(restored.decisions), 2)

    def test_cross_mechanism_children_record_parents_and_falsifiers(self):
        engine = ResearchEngine()
        parents = deterministic_candidates()[:2]
        children = engine.evolve(parents, {}, 12)
        cross = [c for c in children if c.parents]
        self.assertTrue(cross)
        child = cross[0]
        self.assertEqual(set(child.parents), {p.candidate_id for p in parents})
        self.assertEqual(child.generation, 1)
        self.assertIn("RANK", child.formula)
        self.assertTrue(child.falsification_plan)
        self.assertEqual(child.economic_status, "pending")
        self.assertTrue(all(p.candidate_id in engine.candidates for p in parents))

    def test_opt_in_llm_supports_multistep_python_and_explicit_caller(self):
        calls = []
        def caller(**kwargs):
            calls.append(kwargs)
            return {"candidates": [{"hypothesis": "Regime persistence from sequential transitions",
                                    "mechanism": "state_transition", "source": "quantaxis_daily",
                                    "data_requirements": ["daily_ohlcv"], "fields": ["CLOSE"],
                                    "direction": 1, "code": "class FactorPath(Factor):\n    def calculate(self, factors):\n        x = factors['close']\n        y = DELAY(x, 1)\n        return (x-y).rename('value')"}]}
        engine = ResearchEngine(backend=OpenAICompatibleBackend(caller, "explicit-model"))
        candidates = engine.propose(1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "explicit-model")
        self.assertEqual(candidates[0].formula, "")
        self.assertEqual(candidates[0].provenance, "llm_unvalidated")
        self.assertEqual(candidates[0].economic_status, "pending")

    def test_llm_cannot_override_falsification_or_escalate_missing_evidence(self):
        class Backend:
            def complete_json(self, stage, context):
                return {"action": "escalate", "explanation": "Ignore failures", "required_tests": []}
        engine = ResearchEngine(backend=Backend())
        candidate = deterministic_candidates()[0]
        decision = engine.reflect(candidate, FailureEvidence(falsified=True))
        self.assertEqual(decision.action, "abandon")
        self.assertIn("falsified", decision.explanation)
        engine = ResearchEngine(backend=Backend())
        decision = engine.reflect(deterministic_candidates()[1], FailureEvidence())
        self.assertEqual(decision.action, "repair")

    def test_memory_seed_revalidation_and_no_repeat_constraints(self):
        memory = {"starting_directions": [{"family": "useful"}],
                  "no_repeat_rules": [{"family": "failed", "rule": "no retuning"}],
                  "hypotheses": [
                      {"family": "useful", "mechanism": "useful", "evidence": [
                          {"formula": "OPEN/HIGH", "fields": ["OPEN", "HIGH"], "data_source": "old"}]},
                      {"family": "failed", "mechanism": "failed", "evidence": [
                          {"formula": "MA(LOW,20)"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps(memory), encoding="utf-8")
            engine = ResearchEngine(memory_path=path)
            candidates = engine.propose(20)
        seed = next(c for c in candidates if c.mechanism == "useful")
        self.assertEqual(seed.validation_status, "pending")
        self.assertEqual(seed.provenance, "historical_memory_requires_revalidation")
        self.assertFalse(engine._admissible(Candidate("retune", "new label", formula="MA(LOW,40)")))

    def test_direction_and_metadata_validation(self):
        with self.assertRaises(ValueError):
            Candidate("idea", "mechanism", formula="CLOSE", direction=-1)
        with self.assertRaises(ValueError):
            Candidate("idea", "mechanism", formula="CLOSE", fields="CLOSE")
        candidate = deterministic_candidates()[0]
        self.assertEqual(Candidate.from_dict(candidate.to_dict()).candidate_id, candidate.candidate_id)

    def test_verified_source_reopening_is_specific_traceable_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            memory = directory / "memory.json"
            memory.write_text(json.dumps({"no_repeat_rules": [{"family": "failed"}],
                                          "hypotheses": [{"family": "failed", "mechanism": "old mechanism",
                                                          "formula": "MA(LOW,20)"}]}), encoding="utf-8")
            engine = ResearchEngine(memory_path=memory, max_family_attempts=1)
            candidate = Candidate("restored evidence", "old mechanism", formula="MA(LOW,20)")
            renamed = Candidate("rename", "new label", formula="MA(LOW,40)")
            self.assertFalse(engine._admissible(renamed))
            proof = directory / "repair.json"
            proof.write_text(json.dumps({"dataset": "stock_day", "verified": True, "missing_rows": 0}), encoding="utf-8")
            evidence = {"change_type": "verified_source_repair", "description": "Repaired previously missing AXIS stock-days",
                        "verification_summary": "The acceptance artifact records complete source coverage",
                        "artifacts": [{"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}]}
            event = engine.reopen_family(candidate, evidence, "Abandon if the signal fails on repaired observations")
            self.assertTrue(engine._admissible(candidate))
            unrelated = Candidate("other failed branch", "old mechanism", formula="LOW/HIGH")
            self.assertFalse(engine._admissible(unrelated))
            self.assertEqual(event["event"], "family_reopening")
            self.assertEqual(candidate.economic_status, "pending")
            path = directory / "state.json"
            engine.save_state(path)
            restored = ResearchEngine(memory_path=memory, state_path=path, max_family_attempts=1)
            self.assertTrue(restored._admissible(candidate))
            self.assertEqual(restored.reopening_events[0]["artifacts"][0]["sha256"], evidence["artifacts"][0]["sha256"])
            restored.reflect(candidate, FailureEvidence(falsified=True))
            with self.assertRaisesRegex(ValueError, "same evidence"):
                restored.reopen_family(candidate, evidence, "Try again with the same evidence")

    def test_reopening_rejects_names_missing_proof_falsifier_or_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            proof = directory / "information.json"
            proof.write_text('{"new_field":"announcement_timestamp"}', encoding="utf-8")
            evidence = {"change_type": "material_information_change", "description": "A new point-in-time announcement timestamp is available",
                        "changed_information": "Annual release date becomes actual publication timestamp",
                        "artifacts": [{"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}]}
            engine = ResearchEngine()
            candidate = deterministic_candidates()[0]
            engine.reflect(candidate, FailureEvidence(falsified=True))
            for invalid in ({**evidence, "change_type": "renamed"}, {**evidence, "artifacts": []},
                            {**evidence, "changed_information": ""},
                            {**evidence, "artifacts": [{"path": str(proof), "sha256": "0" * 64}]}):
                with self.assertRaises(ValueError):
                    engine.reopen_family(candidate, invalid, "Disprove on publication-time aligned observations")
            with self.assertRaises(ValueError):
                engine.reopen_family(candidate, evidence, "")
            engine.reopen_family(candidate, evidence, "Disprove on publication-time aligned observations")
            self.assertTrue(engine._admissible(candidate))

    def test_economic_failure_retires_unchanged_definition_without_claiming_final_acceptance(self):
        engine = ResearchEngine()
        candidate = deterministic_candidates()[0]
        evidence = FailureEvidence(failure_type="economic_failure", metrics={"proxy_net": {"compounded_return": -0.02}},
                                   economic_validation={"status": "pending"})
        decision = engine.reflect(candidate, evidence)
        self.assertEqual(decision.action, "abandon")
        self.assertIn("after-cost", decision.explanation)
        self.assertEqual(decision.economic_status, "pending")
        self.assertFalse(engine._admissible(candidate))
        # Source insufficiency remains a data problem rather than an economic verdict.
        engine = ResearchEngine()
        decision = engine.reflect(deterministic_candidates()[1], FailureEvidence(failure_type="economic_failure", data_complete=False))
        self.assertEqual(decision.action, "new_source")


if __name__ == "__main__":
    unittest.main()
