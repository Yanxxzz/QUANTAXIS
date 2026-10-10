"""Joint exploratory quality feedback; formal admission remains independent."""
from __future__ import annotations

import math


def _finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def assess_research_quality(study: dict, diversity: dict | None = None) -> dict:
    """Keep source, potential execution, economics and pool evidence distinct.

    This review has no aggregate score or automatic formal admission. A fixed
    definition is weak only when both cost paths, price contribution and the
    common market comparison jointly support that observation. Missing data
    cannot be interpreted as a negative economic result.
    """
    diversity = diversity or {"status": "pending", "reasons": ["No pool factor panels"]}
    coverage = study.get("quality", {}).get("source_coverage", {})
    if isinstance(coverage, list):
        complete = bool(coverage) and all(r.get("candidate_group_status") == "groups_available" for r in coverage)
    else:
        complete = coverage.get("research_source_complete", False)
    observations, pending = [], []
    reviews = [dict(r) for r in sorted(study.get("cost_reviews", []), key=lambda r: r["cost"])]
    layers = {"source": {"status": "verified_supplied_scope" if complete else "pending", "coverage": coverage},
              "execution": {"status": "pending", "actual_auction_fills_verified": False},
              "economics": {"status": "pending", "cost_reviews": reviews},
              "diversity": diversity,
              "pool_increment": {"status": "pending"},
              "risk_exposure": study.get("exposures", {"status": "pending"})}
    result = {"layers": layers, "observations": observations, "pending": pending,
              "failure_type": "unknown", "data_complete": complete,
              "economic_validation": {"status": "pending", "scope": "fixed_definition_local_potential_wealth"},
              "formal_admission": "requires_independent_evidence_review",
              "reproducible_signal": False, "single_metric_or_weighted_score": False}
    if not complete or study.get("status") == "source_pending":
        pending.append("Complete source coverage and independent calendar")
        result.update(status="source_pending", failure_type="missing_data", data_complete=False)
        return result
    if study.get("status") == "execution_error" or study.get("errors"):
        pending.append("Potential execution input or complete group ledger")
        observations.extend(str(r.get("error", "")) for r in study.get("errors", []))
        result.update(status="execution_pending", failure_type="missing_data", data_complete=False)
        return result
    if len(reviews) != 2 or any(not r.get("candidate_metrics") or not r.get("same_support_market_metrics") for r in reviews):
        pending.append("Both costs and same-support market wealth")
        result.update(status="quality_pending")
        return result
    layers["execution"]["status"] = "potential_daily_bar_ledger_complete"
    candidate_id = study["candidate_id"]
    held_runs = []
    for review in reviews:
        name = f"{candidate_id}_G{review['held_group']:02d}"
        run = next((r for r in study.get("runs", []) if r.get("name") == name and r.get("cost") == review["cost"]), None)
        if run is None or not run.get("accounting"):
            pending.append("Held-side continuous account and chronological folds")
            result.update(status="quality_pending")
            return result
        held_runs.append(run)
        folds = run["accounting"]["folds"]
        review["quality_fold_positive_count"] = sum(f["net_return"] > 0 for f in folds)
        review["quality_fold_count"] = len(folds)
        review["quality_concentration"] = run["accounting"]["concentration"]
        review["quality_price_pnl"] = run["accounting"]["price_pnl"]
        review["quality_fees"] = run["accounting"]["fees"]
    exposure_runs = [r.get("exposures", {"status": "risk_metadata_pending"}) for r in held_runs]
    layers["risk_exposure"] = {
        "status": "verified_supplied_exposures" if all(r.get("status") == "supplied_risk_metadata_available" for r in exposure_runs) else "pending",
        "interpretation": "Supplied as-of metadata and close wealth diagnostics; not causal or whole-market certification",
        "cost_paths": [{"cost": review["cost"], "exposures": exposure} for review, exposure in zip(reviews, exposure_runs)]}
    economics = layers["economics"]
    all_positive = all(r["candidate_metrics"]["compounded_return"] > 0 for r in reviews)
    all_negative = all(r["candidate_metrics"]["compounded_return"] <= 0 for r in reviews)
    relative = [r.get("candidate_relative_wealth_vs_market") for r in reviews]
    relative_negative = all(_finite(r) and r <= 0 for r in relative)
    price_negative = all(r["accounting"]["price_pnl"] <= 0 for r in held_runs)
    economics["status"] = "positive_at_both_costs" if all_positive else "negative_at_both_costs" if all_negative else "cost_sensitive"
    economics["standalone_market_relative"] = relative
    economics["all_groups"] = study.get("all_groups", {})
    economics["chronological_folds"] = study.get("folds", {})
    observations.append(f"Held-side economics are {economics['status']}; stage and contribution evidence retained")
    if all(r.get("pool_increment_status") == "paired_potential_wealth_evaluated" for r in reviews):
        layers["pool_increment"] = {"status": "potential_fixed_pair_reviewed", "comparisons": [
            {k: r.get(k) for k in ("cost", "comparator_metrics", "pair_metrics", "candidate_comparator_daily_return_correlation", "pair_rule")} for r in reviews]}
    else:
        pending.append("Explicit fixed comparator and same-support pool increment")
    if diversity.get("status") == "pending":
        pending.append("Actual existing-pool factor values")
    if layers["risk_exposure"].get("status") != "verified_supplied_exposures":
        pending.append("Point-in-time industry and beta exposure evidence")
    if any(r.get("terminal_liquidation_pending") for r in held_runs):
        pending.append("Blocked final exits remain marked holdings")
    if all_negative and relative_negative and price_negative:
        result.update(status="fixed_definition_weak", failure_type="economic_failure",
                      economic_validation={"status": "rejected", "scope": "fixed_definition_local_potential_wealth"})
        observations.append("Both net cost paths, price PnL and same-support relative wealth are weak; this does not falsify an entire mechanism")
    elif diversity.get("status") == "reject":
        result.update(status="redundant_information", failure_type="high_correlation")
    else:
        result.update(status="joint_review_required")
    return result
