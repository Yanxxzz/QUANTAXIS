from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from panda_alpha.cli import TrialLedger, check_evidence_windows, main
from panda_alpha.evolution import Candidate


REPO = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def config(self, directory):
        cfg = json.loads((REPO / "config/panda-alpha.example.json").read_text(encoding="utf-8"))
        cfg["research"]["memory"] = str(REPO / "research_bootstrap/memory.json")
        cfg["research"]["live_memory"] = str(directory / "live_memory.json")
        cfg["research"]["pool_snapshot"] = str(directory / "pool.json")
        cfg["research"]["history_denominator"] = 1  # Memory's study total must win.
        cfg["research"]["probe_start"] = "2023-01-02"
        cfg["research"]["probe_end"] = "2023-08-01"
        cfg["research"]["sealed_windows"] = [{"start": "2019-01-01", "end": "2021-12-31"}]
        cfg["diversity"] = {"max_abs_rank_correlation": 0.7, "minimum_assets": 3, "minimum_dates": 3}
        cfg["research"]["groups"] = 2
        (directory / "pool.json").write_text(json.dumps({"factors": [{"candidate_id": "official-id", "local_id": "F66"}]}), encoding="utf-8")
        config = directory / "config.json"
        config.write_text(json.dumps(cfg), encoding="utf-8")
        return config, cfg

    def run_cli(self, config, ledger, args):
        with redirect_stdout(io.StringIO()), patch("requests.post", side_effect=AssertionError("Unexpected network")):
            main(["--config", str(config), "--trial-ledger", str(ledger)] + args)

    def test_real_compact_memory_plan_and_evolve_are_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            state, plan, next_plan = (directory / name for name in ("state.json", "plan.json", "next.json"))
            ledger = directory / "trials.sqlite3"
            self.run_cli(config, ledger, ["plan", "--count", "10", "--state", str(state), "--output", str(plan)])
            planned = json.loads(plan.read_text(encoding="utf-8"))
            historical = [c for c in planned["candidates"] if c["provenance"] == "historical_memory_requires_revalidation"]
            self.assertTrue(historical, "The real historical directions should survive compaction")
            ids = {entry.get("historical_hypothesis_id") for c in historical for entry in c["trajectory"] if entry["event"] == "memory_seed"}
            self.assertIn("F141", ids)
            self.assertGreaterEqual(planned["total_tested_denominator"], 415)
            self.assertEqual(planned["new_tested_candidates"], 0)
            self.assertFalse(planned["llm_used"])
            self.assertTrue(all(c["economic_status"] == "pending" for c in planned["candidates"]))
            feedback = directory / "feedback.json"
            feedback.write_text(json.dumps({c["candidate_id"]: {"failure_type": "missing_data", "data_complete": False,
                                                                  "metrics": {"research_window": {"start": "2023-01-02", "end": "2023-08-01"}}}
                                            for c in planned["candidates"]}), encoding="utf-8")
            self.run_cli(config, ledger, ["evolve", "--parents", str(plan), "--evidence", str(feedback),
                                          "--count", "4", "--state", str(state), "--output", str(next_plan)])
            evolved = json.loads(next_plan.read_text(encoding="utf-8"))
            self.assertTrue(evolved["decisions"])
            self.assertTrue(all(d["action"] == "new_source" for d in evolved["decisions"]))
            self.assertTrue(all(c["economic_status"] == "pending" for c in evolved["candidates"]))

    def test_sealed_feedback_and_loaded_state_block_before_llm(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            state = directory / "state.json"
            state.write_text(json.dumps({"schema_version": 1, "candidates": [], "decisions": [],
                                         "source_window": {"start": "2020-01-01", "end": "2020-05-01"}}), encoding="utf-8")
            with patch("panda_alpha.cli.backend_from_config", side_effect=AssertionError("Backend called before sealed guard")):
                with self.assertRaisesRegex(ValueError, "sealed"):
                    self.run_cli(config, directory / "trials.sqlite3", ["plan", "--state", str(state), "--output", str(directory / "plan.json")])
            with self.assertRaisesRegex(ValueError, "sealed"):
                check_evidence_windows({"evidence": {"metrics": {"research_window": {"start": "2020-01-01", "end": "2020-02-01"}}}}, cfg["research"]["sealed_windows"])

    def test_trial_denominator_is_cumulative_and_does_not_count_cost_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trials.sqlite3"
            c1 = Candidate("idea", "mechanism", formula="CLOSE/OPEN").to_dict()
            c2 = Candidate("another", "other", formula="HIGH/LOW").to_dict()
            window = {"start": "2023-01-01", "end": "2023-02-01"}
            ledger = TrialLedger(path, 415)
            self.assertTrue(ledger.record(c1, window, 5, 10))
            self.assertFalse(ledger.record(c1, window, 5, 10))
            self.assertEqual(ledger.total, 416)
            ledger.close()
            ledger = TrialLedger(path, 1)
            self.assertTrue(ledger.record(c2, window, 5, 10))
            self.assertEqual(ledger.total, 417)
            ledger.close()

    def test_human_retirement_blocks_evaluation_before_formula_or_new_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            Path(cfg["research"]["live_memory"]).write_text(json.dumps({"human_retired_directions": [{
                "direction": "earnings", "status": "RETIRED_BY_EXPLICIT_USER_DIRECTION",
                "candidate_ids": ["EP05"], "instruction": "retire this direction"}]}), encoding="utf-8")
            candidate = Candidate("retired", "earnings", formula="RANK(CLOSE)", candidate_id="EP05")
            plan = directory / "candidates.json"
            plan.write_text(json.dumps({"candidates": [candidate.to_dict()]}), encoding="utf-8")
            frame = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=12), "symbol": "000001",
                                  "open": 10., "close": 10., "high": 11., "low": 9., "volume": 1000.,
                                  "amount": 10000., "adjustment": "hfq"})
            class Provider:
                def __init__(self, *args): pass
                def daily(self, *args):
                    return SimpleNamespace(frame=frame, coverage={"calendar": {"status": "verified", "sessions": 12}})
            with patch("panda_alpha.data.AxisProvider", Provider), patch("panda_alpha.data.migration_gate", return_value={}), \
                    patch("panda_alpha.evaluation.FormulaEvaluator.evaluate", side_effect=AssertionError("Retired formula ran")):
                self.run_cli(config, directory / "trials.sqlite3", ["evaluate", "--candidates", str(plan), "--codes", "000001",
                    "--start", "2023-01-02", "--end", "2023-01-17", "--output", str(directory / "review")])
            review = json.loads((directory / "review/review.json").read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", review["reports"][0]["status"])
            self.assertEqual(0, review["tested_this_batch"])
            self.assertEqual(415, review["total_tested_denominator"])

    def test_retired_dispatch_rejects_before_account_balance(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            Path(cfg["research"]["live_memory"]).write_text(json.dumps({"human_retired_directions": [{
                "direction": "earnings", "candidate_ids": ["EP05"], "status": "RETIRED_BY_EXPLICIT_USER_DIRECTION",
                "instruction": "retire this direction"}]}), encoding="utf-8")
            candidate = Candidate("retired", "earnings", formula="RANK(CLOSE)", candidate_id="EP05")
            plan = directory / "plan.json"
            plan.write_text(json.dumps({"candidates": [candidate.to_dict()]}), encoding="utf-8")
            with patch("panda_alpha.platform.PandaClient.balance", side_effect=AssertionError("Account read")), \
                    patch("panda_alpha.platform.dispatch", side_effect=AssertionError("Dispatch called")):
                with self.assertRaisesRegex(ValueError, "constraint"):
                    self.run_cli(config, directory / "trials.sqlite3", ["dispatch", "--candidates", str(plan),
                        "--candidate-id", "EP05", "--start", "2023-01-02", "--end", "2023-01-17", "--execute"])

    def test_formula_cli_runs_shared_two_cost_study_and_explicit_fixed_comparator(self):
        from study_fixture import synthetic_study
        protocol, source, frame, _ = synthetic_study()
        # Different initial prices make cross-sectional ranks informative from
        # the first decision, without adding any future data to the signal.
        scale = frame.symbol.map({f"S{i:02d}": 1+i/36 for i in range(36)})
        for field in ("open", "high", "low", "close", "raw_open", "raw_high", "raw_low", "raw_close"):
            frame[field] *= scale
        class Provider:
            def __init__(self, *args): pass
            def daily(self, *args):
                return SimpleNamespace(frame=frame, coverage={"calendar": {"status": "verified", "sessions": 72},
                                                              "daily": {"off_calendar_rows": 0}})
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            cfg["research"]["groups"] = 10
            cfg["diversity"]["minimum_assets"] = 30
            config.write_text(json.dumps(cfg), encoding="utf-8")
            (directory / "pool.json").write_text(json.dumps({"factors": [{"candidate_id": "REFERENCE11", "direction": 0,
                                                                        "formula": "SYNTHETIC_REFERENCE_PERMUTATION"}]}), encoding="utf-8")
            values_dir = directory / "pool_values"
            values_dir.mkdir()
            reference = source.copy()
            reference["value"] = reference.symbol.map({f"S{i:02d}": (17*i)%36 for i in range(36)})
            reference.pivot(index="date", columns="symbol", values="value").to_csv(values_dir / "REFERENCE11.csv.gz", compression="gzip")
            candidate = Candidate("rank level", "synthetic momentum", formula="RANK(CLOSE)", candidate_id="ORBIT07")
            plan = directory / "plan.json"
            plan.write_text(json.dumps({"candidates": [candidate.to_dict()]}), encoding="utf-8")
            with patch("panda_alpha.data.AxisProvider", Provider), patch("panda_alpha.data.migration_gate", return_value={}):
                self.run_cli(config, directory / "trials.sqlite3", ["evaluate", "--candidates", str(plan), "--codes", "S00",
                    "--start", protocol["calendar"][0], "--end", protocol["calendar"][-1],
                    "--pool-values", str(values_dir), "--benchmark-id", "REFERENCE11", "--output", str(directory / "review")])
            review = json.loads((directory / "review/review.json").read_text(encoding="utf-8"))["reports"][0]
            result = json.loads(Path(review["study_path"]).read_text(encoding="utf-8"))
            self.assertEqual(42, len(result["runs"]))
            self.assertEqual([.003, .005], [r["cost"] for r in review["cost_reviews"]])
            self.assertTrue(all(r["pool_increment_status"] == "paired_potential_wealth_evaluated" for r in review["cost_reviews"]))
            self.assertEqual("potential_fixed_pair_reviewed", review["quality"]["layers"]["pool_increment"]["status"])
            self.assertFalse(result["quality"]["formal_admission"]["eligible"])

    def test_evaluate_known_pool_missing_values_stays_pending_and_repeated_batch_does_not_reset(self):
        dates = pd.bdate_range("2023-01-02", periods=12)
        prices = 20 * np.exp(np.cumsum(np.random.default_rng(12).normal(0.001, 0.01, (12, 6)), axis=0))
        frame = pd.DataFrame([{"date": date, "symbol": str(j), "open": prices[i, j], "close": prices[i, j],
                               "high": prices[i, j] + 1, "low": prices[i, j] - 1, "volume": 1000,
                               "amount": 1000 * prices[i, j], "adjustment": "qfq"}
                              for i, date in enumerate(dates) for j in range(6)])
        class Provider:
            def __init__(self, *args):
                pass
            def daily(self, *args):
                return SimpleNamespace(frame=frame, coverage={"calendar": {"status": "verified", "sessions": len(dates)},
                                                              "daily": {"off_calendar_rows": 0}})
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            candidate = Candidate("level", "test axis", formula="RANK(CLOSE)")
            legacy = Candidate("legacy", "old source", formula="RANK(VOLUME)",
                               provenance="historical_memory_requires_revalidation",
                               data_requirements=("legacy_operator_mapping_pending",))
            candidates = directory / "candidates.json"
            candidates.write_text(json.dumps({"candidates": [candidate.to_dict(), legacy.to_dict()]}), encoding="utf-8")
            ledger = directory / "trials.sqlite3"
            args = ["evaluate", "--candidates", str(candidates), "--codes", "0", "--start", "2023-01-02", "--end", "2023-01-17"]
            with patch("panda_alpha.data.AxisProvider", Provider), patch("panda_alpha.data.migration_gate", return_value={"status": "pending"}):
                self.run_cli(config, ledger, args + ["--output", str(directory / "first")])
                self.run_cli(config, ledger, args + ["--output", str(directory / "second")])
            first = json.loads((directory / "first/review.json").read_text(encoding="utf-8"))
            second = json.loads((directory / "second/review.json").read_text(encoding="utf-8"))
            self.assertEqual(first["reports"][0]["diversity"]["status"], "pending")
            self.assertIn("lack actual factor values", " ".join(first["reports"][0]["diversity"]["reasons"]))
            self.assertEqual(first["reports"][1]["status"], "PENDING")
            self.assertEqual(first["reports"][1]["reflection"]["action"], "new_source")
            self.assertIn("revalidation", first["reports"][1]["reason"])
            self.assertFalse((directory / "first" / (legacy.candidate_id + ".csv.gz")).exists())
            self.assertEqual(first["total_tested_denominator"], 416)
            self.assertEqual(second["total_tested_denominator"], 416)
            self.assertEqual(second["new_unique_trials"], 0)

    def test_unverified_calendar_reports_pending_before_any_label_evaluation(self):
        class Provider:
            def __init__(self, *args):
                pass
            def daily(self, *args):
                return SimpleNamespace(frame=pd.DataFrame(), coverage={"calendar": {"status": "pending", "source": "union_of_observed_dates"}})
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            with patch("panda_alpha.data.AxisProvider", Provider), patch("panda_alpha.data.migration_gate", return_value={"can_retire_legacy": False}), \
                    patch("panda_alpha.evaluation.FormulaEvaluator", side_effect=AssertionError("Labels must not be evaluated")):
                self.run_cli(config, directory / "trials.sqlite3", ["evaluate", "--candidates", "unused.json", "--codes", "000001",
                                                                   "--start", "2023-01-02", "--end", "2023-01-17", "--output", str(directory / "review")])
            result = json.loads((directory / "review/review.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "PENDING")
            self.assertEqual(result["tested_this_batch"], 0)
            self.assertEqual(result["data_coverage"]["calendar"]["status"], "pending")

    def test_verified_calendar_with_missing_whole_session_cannot_compress_cycle_labels(self):
        class Provider:
            def __init__(self, *args):
                pass
            def daily(self, *args):
                return SimpleNamespace(frame=pd.DataFrame({"date": pd.to_datetime(["2023-01-02", "2023-01-04"])}),
                                       coverage={"calendar": {"status": "verified", "sessions": 3},
                                                 "daily": {"off_calendar_rows": 0}})
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config, cfg = self.config(directory)
            with patch("panda_alpha.data.AxisProvider", Provider), patch("panda_alpha.data.migration_gate", return_value={"can_retire_legacy": False}), \
                    patch("panda_alpha.evaluation.FormulaEvaluator", side_effect=AssertionError("A missing session must not disappear from shift labels")):
                self.run_cli(config, directory / "trials.sqlite3", ["evaluate", "--candidates", "unused.json", "--codes", "000001",
                                                                   "--start", "2023-01-02", "--end", "2023-01-04", "--output", str(directory / "review")])
            result = json.loads((directory / "review/review.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "PENDING")
            self.assertIn("every verified trading session", result["reason"])
            self.assertEqual(result["tested_this_batch"], 0)


if __name__ == "__main__":
    unittest.main()
