"""Deferred compact E review after both fixed cost paths; no API or replay."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "native_source_conservative_unit"
RUN = "6acb53f59e9d797cfb2d3770"
OFFICIAL_SHA = "5fcf1c52f347aca322019089bf7f6082f2292e3b962739f0cb9adbc9ad856c2d"
PROTOCOL_SHA = "067264130c8467dcb8a4c53ecf75dea59098d2327fdbaf97636a2fd674663505"
COST_PROTOCOL_SHA = "c1b7feb983941749f2ca0b17c8b5f4978ada310e21add3646621022218312951"
COST_SUMMARY_SHA = "df356816a6afd5bcd953bc0c681683fa171139917531ff02a96b5216c12421ae"
COST_LEDGER_SHA = "44fe647a1122f479a8fbae05511c8f768c79559b9fac73f2b344353bb10fda9f"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def binding(path):
    return {"path": str(Path(path).resolve()), "sha256": sha(path)}


def checked(path, expected):
    if sha(path) != expected:
        raise ValueError("Frozen complete review input changed: " + Path(path).name)
    return read(path)


def dates(chart):
    values = [value[:10] for value in chart["x"][0]["data"]]
    if len(chart["x"]) != 1 or len(values) != 51 or values != sorted(set(values)):
        raise ValueError("Exactly51 complete ordered official dates required; no crop")
    return values


def series(chart, name):
    matches = [entry for entry in chart["y"] if entry["name"] == name]
    if len(matches) != 1:
        raise ValueError("Unique complete official series required: " + name)
    values = np.asarray(matches[0]["data"], dtype=float)
    if values.shape != (51,) or not np.isfinite(values).all():
        raise ValueError("Complete finite51 official series required")
    return values


def pct(value):
    return float(str(value).rstrip("%")) / 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--cost-summary-sha", default=COST_SUMMARY_SHA)
    parser.add_argument("--cost-ledger-sha", default=COST_LEDGER_SHA)
    parser.add_argument("--cost-protocol-sha", default=COST_PROTOCOL_SHA)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DEFERRED_COMPLETE_COST_GATE_NO_RESULTS_READ", "run_id": RUN}))
        return
    if not all([args.cost_summary_sha, args.cost_ledger_sha, args.cost_protocol_sha]):
        raise ValueError("Root must bind complete two-path cost summary/ledger/protocol SHAs")
    output = HERE / "official_review.json"
    if output.exists():
        raise ValueError("Completed E review exists; no repeat or overwrite")
    # This gate precedes any E official sequence or comparative economic read.
    cost_path = HERE / "cost_replay/wealth_summary.json"
    cost = checked(cost_path, args.cost_summary_sha)
    cost_protocol = checked(HERE / "cost_replay/protocol.json", args.cost_protocol_sha)
    if (cost.get("status") != "BOTH_FIXED_E_COST_PATHS_EVALUATED" or cost.get("errors")
            or cost.get("anchors") != 51 or cost.get("holding_days") != 256 or cost.get("controls_recomputed") != 0
            or sorted((r["name"], r["cost"]) for r in cost["results"]) != [("E", .003), ("E", .005)]
            or cost_protocol["costs"] != [.003, .005] or cost_protocol["held_groups"] != {"E": "10"}):
        raise ValueError("Both full fixed E cost paths required before review")
    ledger_path = HERE / "cost_replay/E_daily_net_ledger.parquet"
    if sha(ledger_path) != args.cost_ledger_sha or binding(ledger_path) != cost["ledger"]:
        raise ValueError("Complete E cost ledger binding mismatch")
    E_daily = pd.read_parquet(ledger_path)
    if sorted(E_daily.cost.unique()) != [.003, .005] or set(E_daily.name) != {"E"}:
        raise ValueError("Exactly two E cost ledgers required")
    expected_days = None
    for rate in [.003, .005]:
        day = E_daily[E_daily.cost.eq(rate)].date.tolist()
        if len(day) != 256 or day != sorted(set(day)) or (day[0], day[-1]) != ("2025-08-15", "2026-09-03"):
            raise ValueError("Full256day cost path required")
        if expected_days is not None and day != expected_days:
            raise ValueError("E cost scenarios have different holding days")
        expected_days = day
    protocol = checked(HERE / "protocol.json", PROTOCOL_SHA)
    candidate = read(HERE / "E_candidate.json")
    if candidate["direction"] != 1 or candidate["strategy_spec"] != protocol["candidate_draft"]["strategy_spec"]:
        raise ValueError("Fixed standalone total-assets E definition changed")
    if sha(HERE / "WC03CapitalEfficiency.py") != candidate["code_sha256"]:
        raise ValueError("Actual bound E source code changed")
    prep = read(HERE / "preparation_review.json")
    if any(prep.get(key) is not True for key in ["controls_reuse_allowed", "shared_finite_mask_exact", "original_f_formation_keys_exact", "all_251_decisions_group_ready"]):
        raise ValueError("Original exact-support controls cannot be reused")
    paths = {"E": HERE / "official_results" / (RUN + ".json"),
             "legacy_GF": OLD / "official_results/6acaa3e9cd820fa2a40a9bc7.json",
             "F141_direction0": OLD / "official_results/6acaa45acd820fa2a40a9bca.json"}
    checked(paths["E"], OFFICIAL_SHA)
    results = {name: read(path) for name, path in paths.items()}
    dispatch = read(HERE / "dispatch_progress.json")
    settlement = dispatch["response"]["settlement"]
    if (dispatch["job"]["state"] != "SETTLED" or dispatch["job"]["run_id"] != RUN
            or settlement["status"] != 2 or settlement["gift_delta"] != 2 or settlement["recharge_delta"] != 0):
        raise ValueError("Owned E success and gift-only settlement required")
    formations = dates(results["E"]["factor_analysis"]["query_return_chart"])
    if formations != candidate["native_evaluation_contract"]["formation_dates"]:
        raise ValueError("Original fixed51 native phase changed")
    contexts, periods, ranks, benchmarks = {}, {}, {}, {}
    for name, result in results.items():
        if result.get("success") is not True or result.get("status") != 2:
            raise ValueError("Complete successful official control required")
        analysis = result["factor_analysis"]
        for key in ["query_return_chart", "query_factor_excess_chart", "query_ic_sequence_chart", "query_rank_ic_sequence_chart"]:
            if dates(analysis[key]) != formations:
                raise ValueError("Official date alignment differs")
        direction = 0 if name == "F141_direction0" else 1
        held_group = "组1" if direction == 0 else "组10"
        aligned = -1 if direction == 0 else 1
        held = series(analysis["query_return_chart"], held_group)
        excess = series(analysis["query_factor_excess_chart"], held_group)
        ic = series(analysis["query_ic_sequence_chart"], "IC")
        rank = series(analysis["query_rank_ic_sequence_chart"], "Rank_IC")
        benchmarks[name] = held - excess
        periods[name] = np.diff(np.r_[0., held])
        ranks[name] = aligned * rank
        table = next(t for t in analysis["query_group_return_analysis"] if t["group"] == held_group.replace("组", "分组"))
        contexts[name] = {"direction": direction, "held_chart_group": held_group,
                          "published_IC": {x["indicator"]: x["factor_value"] for x in analysis["query_factor_analysis_data"]},
                          "published_held_headline": analysis["query_one_group_data"], "held_table": table,
                          "direction_aligned_IC_mean": float(aligned * ic.mean()),
                          "direction_aligned_RankIC_mean": float(aligned * rank.mean()),
                          "held_final_additive_return": float(held[-1]), "held_final_additive_excess": float(excess[-1])}
    benchmark = benchmarks["E"]
    gap = max(float(np.max(np.abs(x - benchmark))) for x in benchmarks.values())
    if gap > 2e-7:
        raise ValueError("Official common benchmark differs; do not silently compare excess")
    benchmark_periods = np.diff(np.r_[0., benchmark])
    prior_folds = read(OLD / "official_pair_review.json")["contexts"][0]["descriptive_five_contiguous_folds"]
    folds = []
    for number, positions in enumerate(np.array_split(np.arange(51), 5), 1):
        prior = prior_folds[number - 1]
        if (len(positions), formations[positions[0]], formations[positions[-1]]) != (prior["n"], prior["first"], prior["last"]):
            raise ValueError("Inherited five fold boundaries changed")
        folds.append({"fold": number, "n": len(positions), "first": prior["first"], "last": prior["last"],
                      "benchmark_additive_return": float(benchmark_periods[positions].sum()),
                      "contexts": {name: {"additive_return": float(periods[name][positions].sum()),
                                          "additive_excess": float((periods[name][positions] - benchmark_periods[positions]).sum()),
                                          "direction_aligned_RankIC_mean": float(ranks[name][positions].mean())} for name in contexts}})
    old_cost_path = OLD / "cost_replay/wealth_summary.json"
    if binding(old_cost_path) != cost["original_controls"]:
        raise ValueError("Completed original cost controls changed")
    old_cost = read(old_cost_path)
    if old_cost["status"] != "ALL_SIX_FIXED_PATHS_COST_WEALTH_EVALUATED" or old_cost["errors"]:
        raise ValueError("Complete old cost control paths required")
    old_daily_path = OLD / "cost_replay/daily_net_ledger.parquet"
    if binding(old_daily_path) != cost["original_control_ledger"]:
        raise ValueError("Original cost ledger changed")
    old_daily = pd.read_parquet(old_daily_path)
    cost_contexts, cost_comparisons = {}, []
    records = [*cost["results"], *old_cost["results"]]
    for record in records:
        name = {"GF": "legacy_GF", "F141_CONTROL": "F141_direction0"}.get(record["name"], record["name"])
        exposure = record["exposure_summary"]
        cost_contexts.setdefault(name, {})[str(record["cost"])] = {
            "metrics": record["metrics"], "fees_initial_equity_units": record["fees"],
            "maximum_industry_wealth_fraction": exposure["maximum_industry_wealth_fraction"],
            "mean_one_side_turnover_proxy": exposure["mean_one_side_turnover_proxy"],
            "mean_unknown_beta_wealth_fraction": exposure["mean_unknown_beta_wealth_fraction"],
            "top10_share_of_positive_contributions": record["concentration"]["top_10_share_of_positive_contributions"],
            "terminal_open_position_count": len(record["terminal_open_positions"]),
            "terminal_unliquidated_fraction": record["terminal_unliquidated_fraction"],
            "terminal_liquidation_pending": record["terminal_liquidation_pending"],
            "accounting_max_error": record["accounting_max_error"], "risk_metadata_status": record["risk_metadata_status"],
            "fixed_daily_cost_folds": record["folds"]}
    if set(cost_contexts) != {"E", "legacy_GF", "F141_direction0", "COMMON_MARKET"}:
        raise ValueError("Exactly E/originalGF/F/market cost contexts required")
    for rate in [.003, .005]:
        ep = E_daily[E_daily.cost.eq(rate)].set_index("date")
        for raw_name, name in [("GF", "legacy_GF"), ("F141_CONTROL", "F141_direction0"), ("COMMON_MARKET", "COMMON_MARKET")]:
            cp = old_daily[old_daily.name.eq(raw_name) & old_daily.cost.eq(rate)].set_index("date")
            if not ep.index.equals(cp.index):
                raise ValueError("Whole E/control holding calendar differs")
            em, cm = cost_contexts["E"][str(rate)]["metrics"], cost_contexts[name][str(rate)]["metrics"]
            cost_comparisons.append({"cost": rate, "control": name,
                                     "E_minus_compounded_return": em["compounded_return"] - cm["compounded_return"],
                                     "relative_terminal_wealth": float(ep.nav.iloc[-1] / cp.nav.iloc[-1] - 1),
                                     "E_minus_Sharpe": None if em["sharpe"] is None or cm["sharpe"] is None else em["sharpe"] - cm["sharpe"],
                                     "E_minus_MaxDD": em["max_drawdown"] - cm["max_drawdown"],
                                     "daily_net_return_correlation": float(ep.net_return.corr(cp.net_return))})
    primary = cost_contexts["E"]["0.005"]["metrics"]
    F = cost_contexts["F141_direction0"]["0.005"]["metrics"]
    positive_sorting = contexts["E"]["direction_aligned_RankIC_mean"] > 0
    usable_returns = primary["compounded_return"] > 0
    beats_F = primary["compounded_return"] > F["compounded_return"] and primary["sharpe"] is not None and F["sharpe"] is not None and primary["sharpe"] > F["sharpe"]
    decision = ("Standalone E shows positive directional sorting and positive50bp costed wealth on this exposed window; complete F/GF/market comparisons and risks are reported. Independent conditional information, fresh evidence and formal admission remain pending."
                if positive_sorting and usable_returns else
                "Standalone E does not jointly show positive directional sorting and positive50bp costed wealth under the fixed definition. Retain the complete falsification evidence without sign/window/weight rescue or pool promotion.")
    review = {"id": "CAPITAL_E_COMPLETE51_AND_FIXED_TWO_COST_PATH_REVIEW_20261011", "status": "COMPLETE_FIXED_E_MECHANISM_REVIEW",
              "completed_at_utc": datetime.now(timezone.utc).isoformat(), "run_id": RUN,
              "bindings": {"protocol": binding(HERE / "protocol.json"), "review_script": binding(Path(__file__)),
                           "candidate": binding(HERE / "E_candidate.json"), "source": binding(HERE / "WC03CapitalEfficiency.py"),
                           "complete_official_results": {name: binding(path) for name, path in paths.items()},
                           "cost_summary": binding(cost_path), "cost_ledger": binding(ledger_path), "cost_protocol": binding(HERE / "cost_replay/protocol.json"),
                           "original_cost_summary": binding(old_cost_path), "original_cost_ledger": binding(old_daily_path)},
              "official_additive": {"periods": 51, "formation_first": formations[0], "formation_last": formations[-1],
                                    "same51dates": True, "benchmark_max_cross_run_difference": gap,
                                    "benchmark_final_additive": float(benchmark[-1]), "contexts": contexts,
                                    "five_descriptive_folds_inherited": folds,
                                    "units": "Official additive cumulative cycle return and its first differences; uncosted, not compounded transaction NAV"},
              "cost_daily_compounded": {"primary_cost": .005, "sensitivity_cost": .003, "days": 256, "anchors": 51,
                                        "contexts": cost_contexts, "E_comparisons": cost_comparisons,
                                        "units": "Local compounded daily transaction wealth; fees in initial-capital units; complete original control paths reused"},
              "quality_checks": {"directional_sorting_positive": positive_sorting, "positive50bp_compounded_return": usable_returns,
                                 "50bp_return_and_Sharpe_above_same_source_F": beats_F}, "quality_decision": decision,
              "settlement": settlement, "shared_scope_usage_observed": dispatch["official_scope_usage"],
              "limitations": ["Previously exposed historical51period mechanism test, not fresh blind OOS or causal independent alpha certification",
                              "Original strict G/nativeF-qualified source cohort retained; not unrestricted all-A capital efficiency",
                              "Official additive statistics and local daily costed wealth are separate evaluation contracts",
                              "Same-source StockDB QFQ/daily-bar execution proxy; no full PIT, true auction/capacity or server holdings parity claim",
                              "Original terminal residual positions and unknown beta/risk metadata remain explicitly disclosed",
                              "Inherited five folds are descriptive, not retuned holdouts; multiple-testing/formal coverage and pool increment remain pending",
                              "Wallet settlement is provisional serial balance attribution, not per-Run invoice"],
              "original_files_rewritten": False, "new_official_calls": 0, "new_experiments": 0,
              "parameter_search": False, "formal_admission": False, "pool_operation": False, "shared_state_written": False}
    output.write_text(json.dumps(review, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"review": binding(output), "status": review["status"], "quality_decision": decision}, ensure_ascii=False))


if __name__ == "__main__":
    main()
