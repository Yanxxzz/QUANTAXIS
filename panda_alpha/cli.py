"""CLI for data acceptance, memory, planning, evaluation and official experiments."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pandas as pd


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def get_config(path):
    cfg = read(path)
    repo = Path(__file__).resolve().parents[1]
    for name in ("memory", "pool_snapshot"):
        item = cfg["research"].get(name)
        if item and not Path(item).is_absolute():
            cfg["research"][name] = str(repo / item)
    if cfg["data"]["provider"] != "quantaxis":
        raise ValueError("New research has only the QUANTAXIS provider")
    return cfg


def check_evidence_windows(payload, sealed):
    """Inspect source-window metadata before handing evidence to any planner."""
    from .evaluation import check_window
    if isinstance(payload, dict):
        start = payload.get("start", payload.get("start_date"))
        end = payload.get("end", payload.get("end_date"))
        if start and end:
            check_window(start, end, sealed, payload.get("warmup_start"))
        for key, value in payload.items():
            if key in {"date", "trade_date", "datetime"} and isinstance(value, str):
                check_window(value, value, sealed)
            if isinstance(value, (dict, list)):
                check_evidence_windows(value, sealed)
    elif isinstance(payload, list):
        for item in payload:
            check_evidence_windows(item, sealed)


class TrialLedger:
    """Study-wide test denominator; each definition/window counts once across costs."""
    def __init__(self, path, historical):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS trials (trial_key TEXT PRIMARY KEY, candidate_id TEXT, definition TEXT, observed_at TEXT)")
        prior = self.db.execute("SELECT value FROM metadata WHERE name='historical'").fetchone()
        self.historical = max(int(historical), prior[0] if prior else 0)
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('historical',?)", (self.historical,))
        self.db.commit()

    def record(self, candidate, window, cycle, groups):
        from .diversity import canonical_signature
        expression = candidate.get("formula") or candidate.get("code")
        definition = {"signature": canonical_signature(expression), "direction": candidate["direction"],
                      "window": window, "cycle": cycle, "groups": groups}
        serialized = json.dumps(definition, sort_keys=True)
        key = hashlib.sha256(serialized.encode()).hexdigest()
        cursor = self.db.execute("INSERT OR IGNORE INTO trials VALUES (?,?,?,?)",
                                 (key, candidate["candidate_id"], serialized, datetime.now(timezone.utc).isoformat()))
        self.db.commit()
        return cursor.rowcount == 1

    @property
    def new_tested(self):
        return self.db.execute("SELECT count(*) FROM trials").fetchone()[0]

    @property
    def total(self):
        return self.historical + self.new_tested

    def close(self):
        self.db.close()


def historical_denominator(cfg):
    denominator = int(cfg["research"]["history_denominator"])
    memory_path = Path(cfg["research"]["memory"])
    if memory_path.exists():
        denominator = max(denominator, int(read(memory_path).get("multiple_testing_denominator", 0)))
    return denominator


def pool_members(cfg):
    path = cfg["research"].get("pool_snapshot")
    if not path or not Path(path).exists():
        return [], "Configured existing-pool snapshot is missing"
    snapshot = read(path)
    if isinstance(snapshot, list):
        records = snapshot
    else:
        records = next((snapshot[k] for k in ("factors", "candidates", "pool", "members")
                        if isinstance(snapshot.get(k), list)), [])
    return records, None


def historical_definition_requirements(researcher, candidates):
    """An old hypothesis without a recovered direction remains a research direction."""
    originals = {item.get("hypothesis_id"): item for item in researcher.memory.get("hypotheses", [])}
    for candidate in candidates:
        if candidate.provenance != "historical_memory_requires_revalidation":
            continue
        seed = next((entry for entry in candidate.trajectory if entry.get("event") == "memory_seed"), {})
        original = originals.get(seed.get("historical_hypothesis_id"), {})
        if original.get("direction") not in (0, 1):
            candidate.data_requirements = tuple(dict.fromkeys(candidate.data_requirements +
                                                              ("freeze_direction_requires_evidence",)))


def backend_from_config(cfg):
    if not cfg.get("llm", {}).get("enabled"):
        return None
    from .evolution import OpenAICompatibleBackend
    import requests
    llm = cfg["llm"]
    key = os.environ.get(llm["api_key_env"])
    if not key or not llm.get("model"):
        raise ValueError("Enable LLM only after setting its model and API-key environment variable")

    def complete(**payload):
        response = requests.post(llm["base_url"].rstrip("/") + "/chat/completions",
                                 json=payload, headers={"Authorization": "Bearer " + key}, timeout=120)
        response.raise_for_status()
        return response.json()
    return OpenAICompatibleBackend(complete, llm["model"])


def engine(cfg, state_path=None):
    from .evolution import ResearchEngine
    memory = cfg["research"]["memory"]
    if state_path and Path(state_path).exists():
        check_evidence_windows(read(state_path), cfg["research"]["sealed_windows"])
    return ResearchEngine(memory_path=memory if Path(memory).exists() else None,
                          backend=backend_from_config(cfg),
                          max_family_attempts=cfg["research"]["max_family_attempts_without_new_evidence"],
                          state_path=state_path)


def print_compact(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/panda-alpha.local.json")
    parser.add_argument("--trial-ledger", default="research_runs/research_trials.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    compact = sub.add_parser("compact", help="Build traceable research memory without deleting raw evidence")
    compact.add_argument("--workspace", required=True)
    compact.add_argument("--output", default="research_bootstrap")
    plan = sub.add_parser("plan", help="Generate diverse unvalidated hypotheses; no backtest spend")
    plan.add_argument("--count", type=int, default=10)
    plan.add_argument("--output", default="research_runs/generation01.json")
    plan.add_argument("--state", default="research_runs/evolution_state.json")
    evolve = sub.add_parser("evolve", help="Reflect on measured feedback, mutate and cross trajectories")
    evolve.add_argument("--parents", required=True)
    evolve.add_argument("--evidence", required=True)
    evolve.add_argument("--count", type=int, default=10)
    evolve.add_argument("--output", default="research_runs/generation02.json")
    evolve.add_argument("--state", default="research_runs/evolution_state.json")
    coverage = sub.add_parser("coverage", help="Check actual AXIS data and source-retirement blockers")
    coverage.add_argument("--codes", nargs="+", required=True)
    coverage.add_argument("--start", required=True)
    coverage.add_argument("--end", required=True)
    coverage.add_argument("--raw", action="store_true", help="Coverage diagnosis only, no return labels")
    coverage.add_argument("--output", default="research_runs/axis_acceptance.json")
    evaluate = sub.add_parser("evaluate", help="Local next-open proxy and actual factor-value diversity")
    evaluate.add_argument("--candidates", required=True)
    evaluate.add_argument("--codes", nargs="+", required=True)
    evaluate.add_argument("--start", required=True)
    evaluate.add_argument("--end", required=True)
    evaluate.add_argument("--pool-values", help="directory containing date-by-stock .csv.gz factor panels")
    evaluate.add_argument("--output", default="research_runs/local_review")
    account = sub.add_parser("account", help="Free account preflight, no factor runs")
    schedule = sub.add_parser("schedule", help="Preview experimental budget and quotas, no factor runs")
    schedule.add_argument("--candidates", required=True)
    schedule.add_argument("--output", default="research_runs/compute_plan.json")
    launch = sub.add_parser("dispatch", help="Reserve once, persist factor/Run ID, never retry ambiguous dispatch")
    launch.add_argument("--candidates", required=True)
    launch.add_argument("--candidate-id", required=True)
    launch.add_argument("--category", choices=["source_probe", "exploration", "validation"], default="exploration")
    launch.add_argument("--start", required=True)
    launch.add_argument("--end", required=True)
    launch.add_argument("--ledger", default="research_runs/experiments.sqlite3")
    launch.add_argument("--execute", action="store_true")
    reconcile = sub.add_parser("resume", help="Read existing Run ID; never start another execution")
    reconcile.add_argument("--fingerprint", required=True)
    reconcile.add_argument("--ledger", default="research_runs/experiments.sqlite3")
    reconcile.add_argument("--output", default="research_runs/official_results")
    settlement = sub.add_parser("settle", help="Reconcile a final job using a verified official billing artifact")
    settlement.add_argument("--fingerprint", required=True)
    settlement.add_argument("--receipt", required=True)
    settlement.add_argument("--ledger", default="research_runs/experiments.sqlite3")
    admission = sub.add_parser("admission", help="Review final economic evidence independently of exploration")
    admission.add_argument("--evidence", required=True)
    admission.add_argument("--output", default="research_runs/admission_review.json")
    args = parser.parse_args(argv)
    if args.command == "compact":
        from .memory import compact_workspace
        result = compact_workspace(args.workspace, args.output)
        print_compact({"output": str(Path(args.output).resolve()), "denominator": result.get("multiple_testing_denominator"),
                       "status": "compacted; raw evidence preserved"})
        return
    cfg = get_config(args.config)
    if args.command == "admission":
        from .admission import assess_admission
        result = assess_admission(read(args.evidence), cfg["admission"])
        write(args.output, result)
        print_compact(result)
        return
    if args.command == "settle":
        from .platform import settle_receipt, ExperimentLedger
        result = settle_receipt(args.fingerprint, ExperimentLedger(args.ledger), read(args.receipt))
        print_compact({k: result[k] for k in ("fingerprint", "state", "actual", "run_id")})
        return
    if args.command in {"plan", "evolve"}:
        from .evolution import Candidate
        from .evaluation import check_window
        check_window(cfg["research"]["probe_start"], cfg["research"]["probe_end"], cfg["research"]["sealed_windows"])
        researcher = engine(cfg, args.state)
        if args.command == "plan":
            candidates = researcher.propose(min(args.count, cfg["research"]["max_candidates_per_generation"]))
        else:
            parent_payload, feedback = read(args.parents), read(args.evidence)
            check_evidence_windows(parent_payload, cfg["research"]["sealed_windows"])
            check_evidence_windows(feedback, cfg["research"]["sealed_windows"])
            parents = [Candidate.from_dict(c) for c in parent_payload["candidates"]]
            candidates = researcher.evolve(parents, feedback,
                                          min(args.count, cfg["research"]["max_candidates_per_generation"]))
        historical_definition_requirements(researcher, candidates)
        historical = historical_denominator(cfg)
        ledger = TrialLedger(args.trial_ledger, historical)
        result = {"status": "UNVALIDATED_RESEARCH_PLAN", "historical_tested_denominator": ledger.historical,
                  "total_tested_denominator": ledger.total,
                  "new_planned_candidates": len(candidates), "new_tested_candidates": 0,
                  "candidate_definitions": "pending historical source/operator/direction requirements must be resolved before execution",
                  "candidates": [c.to_dict() for c in candidates],
                  "decisions": [d.to_dict() for d in researcher.decisions],
                  "llm_used": bool(cfg["llm"]["enabled"])}
        ledger.close()
        write(args.output, result)
        researcher.save_state(args.state)
        print_compact({"output": str(Path(args.output).resolve()), "candidates": len(candidates), "paid_runs": 0})
        return
    if args.command in {"coverage", "evaluate"}:
        from .data import AxisProvider, migration_gate
        from .evaluation import check_window
        check_window(args.start, args.end, cfg["research"]["sealed_windows"])
        provider = AxisProvider(cfg["data"]["mongo_uri"], cfg["data"]["database"])
        data = provider.daily(args.codes, args.start, args.end,
                              "none" if getattr(args, "raw", False) else cfg["data"]["adjustment"])
        acceptance = migration_gate(data.coverage, require_all_a=cfg["data"]["require_all_a"],
                                    require_delisted=cfg["data"]["require_delisted"],
                                    require_pit_financial=cfg["data"]["require_pit_financial"])
        if args.command == "coverage":
            write(args.output, {"coverage": data.coverage, "migration": acceptance})
            print_compact({"output": args.output, "migration": acceptance})
            return
        calendar = data.coverage.get("calendar", {})
        calendar_problem = None
        if calendar.get("status") != "verified":
            calendar_problem = "Verified exchange trading calendar required before cycle-based return labels"
        elif (not isinstance(calendar.get("sessions"), int) or calendar["sessions"] <= 0
              or (data.frame["date"].nunique() if "date" in data.frame else 0) != calendar["sessions"]
              or data.coverage.get("daily", {}).get("off_calendar_rows", 0)):
            calendar_problem = "Observed panel does not contain every verified trading session or contains off-calendar dates; cycle labels remain pending"
        if calendar_problem:
            output = Path(args.output)
            output.mkdir(parents=True, exist_ok=True)
            payload = {"status": "PENDING", "reason": calendar_problem,
                       "data_coverage": data.coverage, "migration": acceptance,
                       "tested_this_batch": 0, "reports": []}
            write(output / "review.json", payload)
            print_compact({"output": str(output.resolve()), "status": "PENDING", "tested": 0,
                           "reason": payload["reason"]})
            return
        from .evolution import Candidate, FailureEvidence
        from .evaluation import FormulaEvaluator, evaluate_signal
        from .diversity import DiversityPolicy, assess_candidate
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        pool = {}
        known_pool, pool_snapshot_problem = pool_members(cfg)
        if args.pool_values:
            pool = {p.name.split(".")[0]: pd.read_csv(p, index_col=0, parse_dates=True)
                    for p in Path(args.pool_values).glob("*.csv.gz")}
            for panel in pool.values():
                if len(panel):
                    check_window(str(panel.index.min()), str(panel.index.max()), cfg["research"]["sealed_windows"])
        candidates = [Candidate.from_dict(c) for c in read(args.candidates)["candidates"]]
        researcher = engine(cfg)
        evaluator = FormulaEvaluator(data.frame)
        policy = DiversityPolicy(max_abs_correlation=cfg["diversity"]["max_abs_rank_correlation"],
                                 min_assets=cfg["diversity"]["minimum_assets"], min_days=cfg["diversity"]["minimum_dates"])
        reports, evidence = [], {}
        tested = 0
        trial_ledger = TrialLedger(args.trial_ledger, historical_denominator(cfg))
        newly_registered = 0
        for candidate in candidates:
            try:
                if candidate.provenance == "historical_memory_requires_revalidation" or any(
                    "revalidate_legacy" in str(r) or "_pending" in str(r) or "requires_source:" in str(r)
                    or "direction_evidence" in str(r) or "direction_requires_evidence" in str(r)
                    for r in candidate.data_requirements
                ):
                    raise ValueError("Historical source/operator/direction mapping requires explicit revalidation before labels")
                if not candidate.formula:
                    raise ValueError("Multistep Python candidate requires platform sandbox, not local formula interpreter")
                values = evaluator.evaluate(candidate.formula)
                values_path = output / (candidate.candidate_id + ".csv.gz")
                values.to_csv(values_path, compression="gzip")
                defined_members = []
                for member in known_pool:
                    if member.get("formula") or member.get("code"):
                        aliases = [str(member[k]) for k in ("candidate_id", "local_id", "factor_id", "id")
                                   if member.get(k) is not None]
                        identity = next((key for key in aliases if key in pool), aliases[0] if aliases else "unknown")
                        defined_members.append(dict(member, candidate_id=identity))
                diversity = assess_candidate(candidate, values, pool, policy, defined_members).to_dict()
                missing_members = []
                for member in known_pool:
                    ids = {str(member[k]) for k in ("candidate_id", "local_id", "factor_id", "id")
                           if member.get(k) is not None}
                    if not ids.intersection(pool):
                        missing_members.append(next(iter(sorted(ids)), "unidentified_pool_member"))
                if diversity["status"] != "reject" and (missing_members or pool_snapshot_problem):
                    diversity["status"] = "pending"
                    diversity["reasons"].append(pool_snapshot_problem or "Known pool members lack actual factor values: "
                                               + ", ".join(missing_members))
                tested += 1
                newly_registered += int(trial_ledger.record(candidate.to_dict(), {"start": args.start, "end": args.end},
                                                            cfg["research"]["cycle"], cfg["research"]["groups"]))
                reviews = [evaluate_signal(data.frame, values, cfg["research"]["cycle"], candidate.direction, cost,
                                           cfg["research"]["groups"], policy.min_assets)
                           for cost in cfg["admission"]["one_way_costs"]]
                rejected = diversity["status"] == "reject"
                economics = reviews[0]["net"]
                failure = "high_correlation" if rejected else "economic_failure" if economics["compounded_return"] <= 0 else "unknown"
                facts = FailureEvidence(failure_type=failure, metrics={"proxy_net": economics,
                                                                       "research_window": {"start": args.start, "end": args.end}},
                                        correlated_with=tuple(pair["pool_id"] for pair in diversity["correlations"]
                                                              if pair.get("mean_abs_correlation") is not None
                                                              and pair["mean_abs_correlation"] >= policy.max_abs_correlation)
                                        if rejected else (),
                                        reproducible_signal=False, artifacts=(str(values_path),),
                                        economic_validation={"status": "pending"})
                report = {"candidate_id": candidate.candidate_id, "status": "LOCAL_PROXY",
                          "diversity": diversity, "cost_reviews": reviews}
                # Compare each later candidate to earlier evaluated siblings too.
                pool[candidate.candidate_id] = values
            except (ValueError, KeyError, TypeError) as exc:
                facts = FailureEvidence(failure_type="missing_data" if "source" in str(exc).lower() else "runtime",
                                        observations=(str(exc),), metrics={"research_window": {"start": args.start, "end": args.end}},
                                        economic_validation={"status": "pending"})
                report = {"candidate_id": candidate.candidate_id, "status": "PENDING", "reason": str(exc)}
            evidence[candidate.candidate_id] = facts.to_dict()
            report["reflection"] = researcher.reflect(candidate, facts).to_dict()
            reports.append(report)
        write(output / "review.json", {"data_coverage": data.coverage, "migration": acceptance,
                                       "historical_denominator": trial_ledger.historical,
                                       "tested_this_batch": tested, "new_unique_trials": newly_registered,
                                       "new_tested_across_batches": trial_ledger.new_tested,
                                       "total_tested_denominator": trial_ledger.total,
                                       "reports": reports})
        trial_ledger.close()
        write(output / "feedback.json", evidence)
        researcher.save_state(output / "evolution_state.json")
        print_compact({"output": str(output.resolve()), "tested": tested, "admission": "pending", "legacy_retirement": acceptance})
        return
    from .platform import PandaClient, ExperimentLedger, budget_plan, dispatch, fingerprint, resume
    client = PandaClient(cfg["platform"])
    if args.command == "account":
        balance = client.balance()
        count = client.cli("factor_list", "--limit", "1", "--no-detail")["total"]
        print_compact({"balance": balance, "factor_count": count, "runs_started": 0})
    elif args.command == "schedule":
        plan = budget_plan(read(args.candidates)["candidates"], cfg["compute"], client.balance())
        write(args.output, plan)
        print_compact(plan)
    elif args.command == "dispatch":
        from .evaluation import check_window
        check_window(args.start, args.end, cfg["research"]["sealed_windows"])
        candidates = read(args.candidates)["candidates"]
        candidate = next(c for c in candidates if c["candidate_id"] == args.candidate_id)
        if not args.execute:
            print_compact({"candidate": candidate, "fingerprint": fingerprint(candidate, {"start": args.start, "end": args.end},
                                                                               cfg["research"]["cycle"], cfg["research"]["groups"]),
                           "window": {"start": args.start, "end": args.end},
                           "category": args.category, "budget": budget_plan([candidate], cfg["compute"], client.balance()),
                           "runs_started": 0})
        else:
            job = dispatch(candidate, {"start": args.start, "end": args.end}, cfg, ExperimentLedger(args.ledger), client, args.category)
            if job.get("run_id"):
                ledger = TrialLedger(args.trial_ledger, historical_denominator(cfg))
                ledger.record(candidate, {"start": args.start, "end": args.end}, cfg["research"]["cycle"], cfg["research"]["groups"])
                ledger.close()
            print_compact({k: job[k] for k in ("fingerprint", "candidate_id", "state", "factor_id", "run_id")})
    elif args.command == "resume":
        job = resume(args.fingerprint, ExperimentLedger(args.ledger), client, Path(args.output))
        print_compact({k: v for k, v in job.items() if k not in {"definition", "balance_before"}})


if __name__ == "__main__":
    main()
