"""Read-only audit of the bounded 2026-10-20 history, never a research launch.

Requires the operator's original workspace artifacts; creates only --output.
Reuses historical acceptance and existing study/quality code. No data collector,
model, platform client, trial registration or new candidate return evaluation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from panda_alpha.quality import assess_research_quality
from panda_alpha.registry import ResearchRegistry, verify_artifact
from panda_alpha.research_budget import usage
from panda_alpha.study import PotentialQuoteBook, StudyExecutionError, _quotes_frame
from panda_alpha.team.evidence import artifact
from panda_alpha.team.local_screen import local_first, LocalScreenBlocked


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ledger_digest(path):
    """Hash logical rows in a read transaction, including immutable history."""
    result = {}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            sha, count = hashlib.sha256(), 0
            for row in db.execute(f"SELECT * FROM {quoted} ORDER BY rowid"):
                sha.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode())
                sha.update(b"\n")
                count += 1
            result[table] = {"rows": count, "sha256": sha.hexdigest()}
    return result


class NoLabels:
    def events(self, _):
        raise AssertionError("Unverified definition must stop before report caching")

    def record(self, *_):
        raise AssertionError("Historical audit must not register another trial")


def audit(workspace):
    night = workspace / "work/night_factor_20261020"
    ledger = workspace / "QUANTAXIS/research_runs/experiments.sqlite3"
    registry = ledger.with_name("research_trials.sqlite3")
    before = {p.name: ledger_digest(p) for p in (ledger, registry)}
    refs = []

    def bound(path):
        refs.append(artifact(path))
        return read(path)

    with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        jobs = [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY rowid")]
        control = dict(db.execute("SELECT * FROM team_control").fetchone())
        policy = json.loads(db.execute("SELECT policy FROM research_budget_control").fetchone()[0])
        tasks = {r["fingerprint"]: dict(r) for r in db.execute("SELECT * FROM team_tasks")}
    used = usage(jobs, policy["study_id"])
    assert control["mode"] == "paused" and used["unsettled"] == 0
    with_registry = ResearchRegistry(registry, read_only=True)
    try:
        total = with_registry.total
        memory = with_registry.reconciled_memory()["registry_constraints"]
    finally:
        with_registry.close()

    # 1. Reuse the original exact-arithmetic acceptance, not 1000 new trials.
    numeric = bound(workspace / "work/candidate_stability_20261019/v2/numeric-acceptance.json")
    for name in ("module", "test_source", "stable_code", "failed_v1_code", "original_failed_code",
                 "confirmed_reviewer_counterexample"):
        verify_artifact(numeric[name])
        refs.append(numeric[name])
    assert numeric["counterexample_repaired_ranks"] == {"a": .75, "b": .75}
    assert numeric["fraction_exact_reference_cases"] == 1000
    assert all(c["spurious_rank_days"] == 0 for c in numeric["four_constant_cases"])

    # 2. Actual old C1 input has no positive semantics check. The new lean gate
    # must block it before reading labels, caching a route or calling a critic.
    semantics = bound(night / "native-operator-counterexamples.json")
    for name in ("source", "macro_source", "inputs", "source_receipt"):
        verify_artifact(semantics[name])
        refs.append(semantics[name])
    manifest_path = night / "case-recovery/input-manifest.json"
    manifest = bound(manifest_path)
    for ref in manifest["artifacts"]:
        verify_artifact(ref)
    assert "definition_semantics" not in manifest
    row = tasks[manifest["fingerprint"]]
    assert manifest["request_hash"] == row["request_hash"]
    task = {**row, "request": json.loads(row["request"])}
    config = {"local_screen": {"enabled": True, "inputs": {task["fingerprint"]: artifact(manifest_path)}}}
    try:
        local_first(NoLabels(), task, config, NoLabels(), require_semantics=True)
    except LocalScreenBlocked as error:
        blocked = str(error)
    else:
        raise AssertionError("Unchecked C1 reached local labels or paid fallback")
    assert "semantics evidence" in blocked

    # 3. Missing stock/date is not suspension, a zero return, or economic failure.
    source = bound(night / "local-data/capture.json")
    for name in ("calendar", "quotes"):
        if name in source:
            verify_artifact(source[name])
    quote_path = night / "local-data/quotes.parquet"
    refs.append(artifact(quote_path))
    q = pd.read_parquet(quote_path)
    calendar = bound(night / "local-data/calendar.json")["dates"]
    book = PotentialQuoteBook(_quotes_frame(q))
    try:
        book.price("000697", "2025-09-24", "open")
    except StudyExecutionError as error:
        quote_error = str(error)
    else:
        raise AssertionError("Unknown quote became an executable price")
    assert quote_error.startswith("unknown_quote:")
    saved = bound(night / "volume-coupling/price-comparison-study.json")
    quality = assess_research_quality(saved["study"])
    assert quality["status"] == "execution_pending"
    assert quality["economic_validation"]["status"] == "pending"
    dates = pd.to_datetime(q.date).dt.strftime("%Y-%m-%d")
    grid = pd.MultiIndex.from_product([calendar, sorted(q.symbol.unique())], names=["date", "symbol"])
    missing = grid.difference(pd.MultiIndex.from_arrays([dates, q.symbol], names=["date", "symbol"]))
    halt = bound(night / "local-data/halt-probes/date-code-validation.json")
    halt_matches = [{"code": x["code"], "day": x["day"], "exact_interval_matches": len(x["exact_interval_matches"]),
                     "applied_to_frozen_inputs": x["qualification_applied"]} for x in halt["queries"]]
    for x in halt["queries"]:
        verify_artifact({"path": x["raw_path"], "sha256": x["sha256"]})

    # Read original report vectors and recompute the originally frozen decision.
    score_path = night / "score_report.py"
    refs.append(artifact(score_path))
    module_spec = importlib.util.spec_from_file_location("frozen_report_score", score_path)
    scorer = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(scorer)
    protocol = bound(night / "protocol-v2.json")
    evaluation_calendar = bound(Path(protocol["calendar"]["path"]))["dates"]
    reports = []
    night_jobs = [j for j in jobs if j["day"] == "2026-10-20"]
    folders = {"6ad65d85b8f0d75493c88158": "online-r1", "6ad679ac9e9d797cfb2d4f46": "native-predepletion-domain",
               "6ad6846e2d2f6998fc1c4020": "native-max-lottery-recovery", "6ad69900812a2a13b96479bd": "native-volume-coupling"}
    for job in night_jobs:
        folder = night / folders[job["run_id"]]
        receipt = bound(folder / "runtime/results" / (job["fingerprint"] + ".team-result.json"))
        for ref in receipt["artifacts"]:
            verify_artifact(ref)
            refs.append(ref)
        report_path = folder / "runtime/results" / (job["run_id"] + ".json")
        raw = read(report_path) if report_path.is_file() else None
        request = json.loads(tasks[job["fingerprint"]]["request"])
        evaluated = (scorer.inspect(raw, request["window"], request["candidate"]["direction"],
                                    protocol["first_pass"], evaluation_calendar) if raw else
                     {"status": "pending", "goal_met": False, "reason": "Technical failure; no return report; retained original logs"})
        status = raw.get("status") if raw else receipt["receipt"]["status"]
        if status == 2:
            assert evaluated == bound(folder / "goal-evaluation.json")
        else:
            assert evaluated["status"] == "pending"
        reports.append({"run_id": job["run_id"], "candidate_id": job["candidate_id"],
                        "charge": job["actual"], "report_status": status, "evaluation": evaluated})

    attempts, actions = Counter(), Counter()
    worker_states, usage_snapshots = [], []
    for path in sorted(night.glob("*/runtime/workers/*/work/*/state.json")):
        state = read(path)
        attempts[path.parents[2].name] += len(state["attempts"])
        worker_states.append({"work_id": path.parent.name, "state": artifact(path),
                              "attempts": len(state["attempts"]),
                              "spawn_receipts": sum(bool(a.get("process")) for a in state["attempts"])})
        logs = []
        for attempt in state["attempts"]:
            log = path.parent / str(attempt["number"]) / "events.jsonl"
            logs.append(artifact(log))
            thread = None
            for line in log.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("type") == "thread.started":
                    thread = event["thread_id"]
                if event.get("type") == "turn.completed":
                    usage_snapshots.append({"thread_id": thread, "cumulative_usage": event["usage"], "source": str(log)})
        worker_states[-1]["raw_event_logs"] = logs
    assert sum(attempts.values()) == sum(r["spawn_receipts"] for r in worker_states)
    with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
        sessions = {read(p)["binding"]["session"] for p in night.glob("*/runtime/manifest.json")}
        issued = {work_id: (session, json.loads(packet)["action"]) for work_id, session, packet in
                  db.execute("SELECT work_id,session_id,packet FROM team_host_work")}
        for row in worker_states:
            session, action = issued[row["work_id"]]
            assert session in sessions
            actions[action] += row["attempts"]
    after = {p.name: ledger_digest(p) for p in (ledger, registry)}
    assert before == after, "Authoritative ledger changed during audit; do not reuse this result"
    return {"schema": "lean-raw-evidence-audit-v1", "baseline_pr": 18,
            "baseline_commit": "225ad582f0e9f70b17782e9ac596671ccca78221",
            "research_stopped": True, "new_worker_attempts": 0, "new_official_runs": 0,
            "new_candidate_return_labels": False, "new_trial_registration": False,
            "ledger_before": before, "ledger_after": after, "registry_total": total,
            "budget": {"policy": policy, "usage": used, "remaining_credits": policy["max_total_credits"] - used["credits"],
                       "remaining_runs": policy["max_runs"] - used["runs"], "new_authorization": False},
            "historical_workers": {"attempts": dict(attempts), "model_attempt_actions": dict(actions), "raw_states": worker_states,
                                   "cumulative_usage_snapshots": usage_snapshots,
                                   "quota_attribution": "attempts are not quota units; resumed-thread token counters are cumulative"},
            "historical_reports": reports,
            "counterexamples": [
                {"name": "numeric-null", "result": "existing exact-moment acceptance reused; current code/test hashes match",
                 "new_economic_evidence": False},
                {"name": "definition-semantics", "result": blocked,
                 "historical_replays": semantics["actual_input_replays"], "production_deployment_certified": False},
                {"name": "missing-quote", "result": quote_error, "quality_status": quality["status"],
                 "economic_status": quality["economic_validation"]["status"], "missing_grid_keys": len(missing),
                 "halt_matches": halt_matches, "frozen_inputs_changed": False}],
            "source_sample": {"quotes": len(q), "symbols": q.symbol.nunique(), "sessions": len(calendar),
                              "missing_keys": [{"date": d, "symbol": s} for d, s in missing],
                              "all_a_or_provider_actual_input": False},
            "constraint_counts": {k: len(v) for k, v in memory.items() if isinstance(v, list)},
            "artifacts": list({r["path"]: r for r in refs}.values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.workspace)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "counterexamples": len(result["counterexamples"]),
                      "budget": result["budget"], "authority_unchanged": True}, ensure_ascii=False))
