"""Read-only verification of the stopped single-mechanism research artifacts.

Requires the original local evidence. Does not collect data, evaluate another
candidate, register trials, write ledgers, invoke workers or call the platform.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panda_alpha.evaluation import performance
from panda_alpha.peer_diffusion import native_code
from panda_alpha.quality import assess_research_quality
from panda_alpha.registry import verify_artifact


def verify(work: Path) -> dict:
    refs = {}

    def read(relative):
        path = work / relative
        refs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def bindings(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                ref = verify_artifact(value)
                refs[ref["path"]] = ref["sha256"]
            for child in value.values():
                bindings(child)
        elif isinstance(value, list):
            for child in value:
                bindings(child)

    for relative in ("candidate/frozen-protocol.json", "candidate/request.json",
                     "local/method.json", "local/input-manifest.json",
                     "local/signal-acceptance.json", "local/results.json",
                     "local/control-results.json", "inputs-v2/capture.json"):
        bindings(read(relative))
    request = read("candidate/request.json")
    constants = {node.targets[0].id: ast.literal_eval(node.value)
                 for node in ast.parse(request["candidate"]["code"]).body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {"CALENDAR", "SYMBOLS"}}
    assert native_code(constants["CALENDAR"], constants["SYMBOLS"]) == request["candidate"]["code"]

    screen = read("local/screen.json")
    quality = assess_research_quality(screen["study"])
    assert quality["status"] == screen["quality"]["status"] == "fixed_definition_weak"
    assert screen["route"] == "local_reject"
    held = []
    for review in screen["study"]["cost_reviews"]:
        name = f"{screen['study']['candidate_id']}_G{review['held_group']:02d}"
        run = next(r for r in screen["study"]["runs"]
                   if r["name"] == name and r["cost"] == review["cost"])
        daily = run["daily"]
        metrics = performance(pd.Series([d["net_return"] for d in daily], dtype=float))
        for key, value in metrics.items():
            assert math.isclose(value, review["candidate_metrics"][key], rel_tol=1e-12, abs_tol=1e-12)
        price = math.fsum(d["price_pnl"] for d in daily)
        fees = math.fsum(d["fees"] for d in daily)
        assert math.isclose(price - fees, daily[-1]["nav"] - 1, abs_tol=1e-12)
        assert math.isclose(price, run["accounting"]["price_pnl"], abs_tol=1e-12)
        assert price <= 0 and review["candidate_relative_wealth_vs_market"] <= 0
        held.append({"cost": review["cost"], "metrics": metrics,
                     "price_pnl": price, "fees": fees,
                     "relative_wealth_vs_market": review["candidate_relative_wealth_vs_market"],
                     "engine_accounting_folds": run["accounting"]["folds"]})

    controls = read("local/control-results.json")
    values = np.array([np.nan if r["coefficient"] is None else r["coefficient"]
                       for r in controls["formation_rows"]])
    n = len(values)
    rng = np.random.default_rng(20261020)
    draws = []
    for _ in range(9999):
        starts = rng.integers(0, n - 4 + 1, size=(n + 3) // 4)
        sample = np.concatenate([values[s:s + 4] for s in starts])[:n]
        if np.isfinite(sample).any():
            draws.append(float(np.nanmean(sample)))
    assert np.array_equal(np.quantile(draws, [.025, .975]), controls["ci95"])
    assert float(np.nanmean(values)) == controls["mean_coefficient"]
    assert not controls["material_effect_excluded"]
    stop = read("final-host-stop.json")
    assert not stop["enabled"] and not stop["can_start"] and not stop["unsettled_jobs"]
    assert not any(stop["components"].values()) and stop["live_model_processes"] == 0
    assert stop["actions"] == 0 and stop["reason"] == "seed_tasks_complete"
    return {"status": "verified_stopped_fixed_local_failure", "bindings": refs,
            "held_cost_paths": held, "conditional_mean": controls["mean_coefficient"],
            "conditional_ci95": controls["ci95"], "conditional_rejection": False,
            "new_candidate_evaluation": False, "new_ledger_writes": False,
            "new_worker_attempts": 0, "new_website_runs": 0,
            "limits": ["Saved ledger replay; no independent source or production-input certification",
                       "No new sealed out-of-sample or current-pool increment proof"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.work.resolve())
    # Never overwrite an original artifact or a prior verification.
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"status": result["status"], "verified_artifacts": len(result["bindings"])}))
