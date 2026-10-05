import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from panda_alpha.memory import compact_workspace


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "fork" / "research_bootstrap"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def evidence(self, factor):
        return next(item for line in (self.output / "evidence.jsonl").read_text().splitlines()
                    if (item := json.loads(line)).get("hypothesis_id") == factor)

    def test_preserves_denominator_and_provenance_without_credentials(self):
        source = self.write("factor_workflow/knowledge_base.json", {
            "named_hypotheses": 415, "multiple_testing_denominator": 415,
            "working_mechanisms": [{"axis": "variance_share", "representatives": ["F141"],
                                    "lesson": "Structural share improves diversity."}],
            "stopped_families": [{"family": "retuning", "examples": ["F01-F03"],
                                  "reason": "Same window grid failed costs."}],
            "account": {"password": "VERY_PRIVATE_PASSWORD", "balance": 900},
        })
        self.write("work/account_balance.json", {"token": "VERY_PRIVATE_TOKEN"})
        self.write("stockdb_factor_eval/results/batch1/decision_packet.json", {
            "cumulative_hypotheses": 141, "settings": {"start": "20210920", "end": "20260918"},
            "candidates": [{"name": "F141", "classification": "rejected",
                            "mechanism": "Variance share", "direction_rank_ic": 0.06,
                            "failure_reasons": "net30<2%;turnover>15%"}],
        })
        before = source.read_bytes()
        manifest = compact_workspace(self.root, self.output)
        memory = json.loads((self.output / "memory.json").read_text())
        self.assertEqual(415, manifest["multiple_testing_denominator"])
        self.assertEqual(before, source.read_bytes())
        item = memory["hypotheses"][0]
        self.assertEqual("variance_share", item["family"])
        evidence = self.evidence("F141")
        self.assertEqual("legacy_gate_failure", evidence["evidence_kind"])
        self.assertEqual("reassess_under_new_data_and_policy", evidence["decision"])
        self.assertEqual("20210920", evidence["source_window"]["start"])
        source_path = self.root / evidence["source_path"]
        self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), evidence["source_sha256"])
        output = "".join(path.read_text() for path in self.output.iterdir())
        self.assertNotIn("VERY_PRIVATE", output)
        self.assertEqual(["F01", "F02", "F03"], memory["no_repeat_rules"][0]["examples"])

    def test_source_failure_and_official_result_remain_distinct(self):
        self.write("work/F412/source_result.json", {"candidate": "F412", "status": "PREFLIGHT_REJECTED",
                                                   "checks": {"coverage": False}})
        self.write("work/F253/official_validation.json", {
            "candidate": "F253", "official_metrics": {"rank_ic": "0.0586"},
            "official_common_gate_fully_verified": False,
            "unverified_official_checks": ["official pool correlation"],
            "net30_excess_proxy_pct": 8.64,
        })
        result = compact_workspace(self.root, self.output)
        self.assertEqual(412, result["multiple_testing_denominator"])
        memory = json.loads((self.output / "memory.json").read_text())
        kinds = {item["hypothesis_id"]: self.evidence(item["hypothesis_id"])["evidence_kind"] for item in memory["hypotheses"]}
        self.assertEqual("source_coverage_failure", kinds["F412"])
        self.assertEqual("official_validation", kinds["F253"])
        official = self.evidence("F253")
        self.assertEqual("official_checks_partial", official["evidence_status"])
        self.assertEqual(8.64, official["metrics"]["net30_excess_proxy_pct"])

    def test_repeated_compaction_is_deterministic_and_excludes_its_output(self):
        self.write("factor_workflow/knowledge_base.json", {"named_hypotheses": 400})
        compact_workspace(self.root, self.output)
        first = {path.name: path.read_bytes() for path in self.output.iterdir()}
        compact_workspace(self.root, self.output)
        self.assertEqual(first, {path.name: path.read_bytes() for path in self.output.iterdir()})

    def test_redacts_incidental_secrets_and_keeps_exact_failure(self):
        self.write("work/F05/formal_result.json", {
            "candidate": "F05", "status": "failed", "error_type": "RuntimeError",
            "mechanism": "cash token=VERY_PRIVATE_TOKEN password=VERY_PRIVATE_PASSWORD test@example.org",
            "failure_reasons": ["coverage_missing"], "direction_rank_ic": "NaN",
        })
        compact_workspace(self.root, self.output)
        text = (self.output / "memory.json").read_text()
        self.assertNotIn("VERY_PRIVATE", text)
        self.assertNotIn("test@example.org", text)
        item = self.evidence("F05")
        self.assertEqual("execution_failure", item["evidence_kind"])
        self.assertEqual(["coverage_missing"], item["failure_reasons"])
        self.assertNotIn("direction_rank_ic", item["metrics"])

    def test_hex_hash_mentions_do_not_inflate_denominator(self):
        self.write("factor_workflow/knowledge_base.json", {"named_hypotheses": 415})
        (self.root / "research_registry.md").write_text("F415 tested; hash f4787abc123. F01-F03 stopped.", encoding="utf-8")
        result = compact_workspace(self.root, self.output)
        self.assertEqual(415, result["multiple_testing_denominator"])
        memory = json.loads((self.output / "memory.json").read_text())
        self.assertNotIn("F4787", [item["hypothesis_id"] for item in memory["hypotheses"]])

    def test_unregistered_source_failure_does_not_disappear_or_add_hypothesis(self):
        self.write("work/source_probe/result.json", {
            "status": "SOURCE_PROBE_STOP_NO_RETRY", "error_type": "RuntimeError",
            "source_records_accepted": 0, "new_hypotheses": 0,
            "native_parity_verified": False,
        })
        result = compact_workspace(self.root, self.output)
        self.assertEqual(0, result["hypothesis_count"])
        memory = json.loads((self.output / "memory.json").read_text())
        finding = memory["source_findings"][0]
        self.assertEqual("RuntimeError", finding["error"])
        self.assertEqual(0, finding["records_accepted"])
        self.assertFalse(finding["verification"]["native_parity_verified"])

    def test_hot_memory_is_thin_and_cold_index_keeps_every_hypothesis(self):
        self.write("stockdb_factor_eval/results/batch1/decision_packet.json", {
            "cumulative_hypotheses": 100,
            "candidates": [{"name": f"F{number:02d}", "mechanism": "Long mechanism " * 70,
                            "classification": "rejected", "failure_reasons": "net30<2%"}
                           for number in range(1, 101)],
        })
        compact_workspace(self.root, self.output)
        memory = json.loads((self.output / "memory.json").read_text())
        self.assertLess((self.output / "memory.json").stat().st_size, 100_000)
        rows = [json.loads(line) for line in (self.output / "evidence.jsonl").read_text().splitlines()]
        self.assertEqual(100, len({item["hypothesis_id"] for item in rows}))
        evidence_ids = {item["evidence_id"] for item in rows}
        for item in memory["hypotheses"]:
            self.assertNotIn("evidence", item)
            self.assertTrue(all(ref["evidence_id"] in evidence_ids for ref in item["evidence_refs"]))

    def test_direction_is_explicit_and_signed_engine_exports_do_not_override_it(self):
        self.write("factor_workflow/batches/batch17.json", {
            "panda_mappings": {"F141": {"direction": 0, "definition": "native.py"}},
        })
        self.write("stockdb_factor_eval/results/batch17_v2_sensitivity/formal_summary.json", {
            "candidate": "F141", "direction": 1,
        })
        self.write("work/F415/plan.json", {
            "candidate": "F415", "parameters": {"direction": 0},
            "source": {"fields": ["close", "turnover"], "min_date": 20200803, "max_date": 20260918},
        })
        self.write("work/F416/formal_result.json", {"candidate": "F416", "mechanism": "Unknown sign"})
        compact_workspace(self.root, self.output)
        hypotheses = {item["hypothesis_id"]: item for item in json.loads((self.output / "memory.json").read_text())["hypotheses"]}
        self.assertEqual(0, hypotheses["F141"]["direction"])
        self.assertEqual("recorded_unambiguous", hypotheses["F141"]["direction_status"])
        self.assertEqual(0, hypotheses["F415"]["direction"])
        self.assertEqual(["close", "turnover"], hypotheses["F415"]["fields"])
        self.assertIsNone(hypotheses["F416"]["direction"])
        self.assertIn("needs_direction_evidence", hypotheses["F416"]["data_requirements"])

    def test_conflicting_binary_directions_require_resolution(self):
        self.write("work/F141/plan.json", {"candidate": "F141", "direction": 0})
        self.write("work/F141/freeze.json", {"candidate": "F141", "direction": 1})
        compact_workspace(self.root, self.output)
        item = json.loads((self.output / "memory.json").read_text())["hypotheses"][0]
        self.assertIsNone(item["direction"])
        self.assertEqual("conflicting_direction_evidence", item["direction_status"])

    def test_registered_code_and_formula_declare_fields_without_execution(self):
        code = self.root / "panda_native_factors" / "f141_transfer.py"
        code.parent.mkdir(parents=True)
        code.write_text('raise RuntimeError("must never execute")\nclose = factors["close"]\n', encoding="utf-8")
        (self.root / "panda_f174_transfer_manifest.md").write_text(
            '- Local factor: `F174`\n- Mode: formula\n- Formula: `MA(AMOUNT/VOLUME-CLOSE,20)`\n- Direction: `1`\n', encoding="utf-8")
        self.write("work/native_candidates/F253/contract.json", {
            "candidate": "F253", "direction": 1, "input_fields": ["close", "turnover", "open"],
        })
        compact_workspace(self.root, self.output)
        items = {item["hypothesis_id"]: item for item in json.loads((self.output / "memory.json").read_text())["hypotheses"]}
        self.assertEqual(["close"], items["F141"]["fields"])
        self.assertEqual(["amount", "close", "volume"], items["F174"]["fields"])
        self.assertEqual(1, items["F174"]["direction"])
        self.assertEqual(["close", "open", "turnover"], items["F253"]["fields"])


if __name__ == "__main__":
    unittest.main()
