"""Forward admission counterexamples; synthetic data only, no research run."""
from calendar import monthrange
from copy import deepcopy
from datetime import date
import json

from panda_alpha.cli import historical_denominator
from test_pool_admission import EvidenceCase, member


def bound_case(directory):
    case = EvidenceCase(directory)
    case.bodies["quality"]["metrics_by_candidate"] = {"NEW": deepcopy(case.bodies["quality"]["metrics"])}
    case.bodies["contracts"]["calendar_months"] = deepcopy(case.bodies["ledger"]["calendar_months"])
    case.rebind()
    return case


def add_cold_start_shadow(case, days=None):
    full_month = [f"2026-11-{day:02d}" for day in range(1, monthrange(2026, 11)[1] + 1)
                  if date(2026, 11, day).weekday() < 5]
    case.bodies["contracts"]["calendar_months"]["2026-11"] = full_month
    days = days or full_month
    def paths(mean):
        return [{"date": day, "net_return": mean + (.003 if i % 2 else -.003),
                 "turnover": .01, "fee": .00001} for i, day in enumerate(days)]
    reviews = {}
    for side in ("baseline", "proposed"):
        reviews[side] = [{"id": item["id"], "version": item["version"],
                          "definition_sha256": item["definition_sha256"],
                          "source": "cold_start_model", "records": []}
                         for item in case.plan[side]]
    case.bodies["shadow"] = {
        "scope": "frozen_forward_shadow", "type": "pool_signal_daily_ledger",
        "month": "2026-11", "calendar": days,
        "benchmark": [{"date": day, "return": 0.0} for day in days],
        "B_review": {"independent_calendar_status": "verified",
                     "version_freeze_status": "verified", **reviews},
        "A": {"baseline": {"a_scores": [.005] * len(case.plan["baseline"]), "decay": 1, "a_cap": .7},
              "proposed": {"a_scores": [.005 if item["id"] not in case.plan["candidate_ids"] else .08
                                        for item in case.plan["proposed"]], "decay": 1, "a_cap": .7}},
        "costs": {"0.003": {"baseline": paths(.004), "proposed": paths(.005)},
                  "0.005": {"baseline": paths(.0039), "proposed": paths(.0049)}}}
    case.rebind()


def test_two_forward_days_cannot_be_certified_as_a_complete_calendar_month(tmp_path):
    case = bound_case(tmp_path)
    add_cold_start_shadow(case, ["2026-11-02", "2026-11-03"])
    candidate = case.bodies["shadow"]["B_review"]["proposed"][-1]
    candidate["source"] = "shadow_post_freeze"
    candidate["records"] = [
        {"version": "v1", "signal_date": "2026-10-13", "realized_date": "2026-10-20", "ic": .03, "rank_ic": .04},
        {"version": "v1", "signal_date": "2026-10-20", "realized_date": "2026-10-27", "ic": .05, "rank_ic": .06}]
    case.save("shadow")
    result = case.assess()
    assert result["status"] != "eligible_for_review", result
    assert not result.get("forward_validation_verified"), result


def test_no_forward_IC_evidence_cannot_be_replaced_by_cold_start_booleans(tmp_path):
    case = bound_case(tmp_path)
    add_cold_start_shadow(case)
    result = case.assess()
    assert result["status"] != "eligible_for_review", result
    assert not result.get("forward_validation_verified"), result


def test_missing_second_cost_execution_does_not_certify_an_economic_rejection(tmp_path):
    case = bound_case(tmp_path)
    case.bodies["ledger"]["costs"]["0.003"]["proposed"] = case.returns(.0002)
    del case.bodies["ledger"]["costs"]["0.005"]["proposed"][0]["fee"]
    case.save("ledger")
    result = case.assess()
    assert result["status"] == "pending", result
    assert not result["economic_rejected"], result


def test_two_replacements_require_each_candidates_bound_common_quality(tmp_path):
    case = bound_case(tmp_path)
    replacement = deepcopy(case.plan["baseline"][2:]) + [member("NEW"), member("OTHER")]
    case.plan.update(operation="replace", proposed=replacement, candidate_ids=["NEW", "OTHER"],
                     planned_change_date="2026-11-02",
                     transitions=[deepcopy(case.plan["baseline"]), deepcopy(replacement)])
    for row in case.bodies["points"]["months"]:
        row["proposed"]["a_scores"] = [.005] * 3 + [.08] * 2
    # quality.metrics describes only one anonymous survivor. No OTHER quality
    # evidence was supplied, despite rebinding all bytes to the new whole plan.
    case.rebind()
    result = case.assess()
    assert result["status"] == "pending", result
    assert not result["economic_rejected"], result


def add_candidate_forward_records(case):
    candidate = case.bodies["shadow"]["B_review"]["proposed"][-1]
    candidate.update(source="shadow_post_freeze", records=[
        {"version": "v1", "signal_date": "2026-11-02", "realized_date": "2026-11-09", "ic": .03, "rank_ic": .04},
        {"version": "v1", "signal_date": "2026-11-09", "realized_date": "2026-11-16", "ic": .05, "rank_ic": .06}])
    case.save("shadow")


def test_removing_a_bad_factor_does_not_require_that_factor_to_pass_admission(tmp_path):
    case = bound_case(tmp_path)
    baseline = deepcopy(case.plan["baseline"]) + [member("BAD")]
    proposed = deepcopy(case.plan["baseline"])
    case.plan.update(operation="remove", baseline=baseline, proposed=proposed,
                     candidate_ids=["BAD"], planned_change_date="2026-11-02",
                     transitions=[deepcopy(baseline), deepcopy(proposed)])
    bad_quality = deepcopy(case.bodies["quality"]["metrics"])
    bad_quality.update(direction_rank_ic=-.1, net30=-.1, net50=-.2,
                       size_net30=-.1, size_net50=-.2, folds_positive=0)
    case.bodies["quality"]["metrics_by_candidate"] = {"BAD": bad_quality}
    for row in case.bodies["points"]["months"]:
        row["baseline"]["a_scores"] = [.005] * 5 + [0.0]
        row["proposed"]["a_scores"] = [.005] * 5
    case.rebind()
    result = case.assess()
    assert result["status"] == "ready_for_shadow_validation", result
    assert not result["economic_rejected"], result
    assert not any(item.startswith("common:") for item in result["failed"]), result
    assert result["pool_operations"] == 0


def test_unchanged_historical_member_A_cannot_be_inflated_for_the_proposed_pool(tmp_path):
    case = bound_case(tmp_path)
    case.bodies["points"]["months"][0]["proposed"]["a_scores"][0] = .08
    case.save("points")
    result = case.assess()
    assert result["status"] == "pending", result
    assert not result["economic_rejected"], result


def test_unchanged_forward_member_B_cannot_use_different_original_records(tmp_path):
    case = bound_case(tmp_path)
    add_cold_start_shadow(case)
    add_candidate_forward_records(case)
    proposed_old_leg = case.bodies["shadow"]["B_review"]["proposed"][0]
    proposed_old_leg.update(source="shadow_post_freeze",
                            records=deepcopy(case.bodies["shadow"]["B_review"]["proposed"][-1]["records"]))
    case.save("shadow")
    result = case.assess()
    assert result["status"] == "pending", result
    assert not result.get("forward_validation_verified"), result
    assert not result["economic_rejected"], result


def test_complete_frozen_forward_evidence_can_reach_review_without_official_claims(tmp_path):
    case = bound_case(tmp_path)
    add_cold_start_shadow(case)
    add_candidate_forward_records(case)
    result = case.assess()
    assert result["status"] == "eligible_for_review", result
    assert result["forward_validation_verified"], result
    assert not result["official_total_points_gain_verified"]
    assert result["pool_operations"] == result["paid_runs"] == 0
    for path in result["forward_validation"]["comparisons"].values():
        assert min(path["score_delta_scenarios"].values()) > 0


def test_cli_denominator_retains_newer_live_memory_than_bootstrap(tmp_path):
    bootstrap, live = tmp_path / "bootstrap.json", tmp_path / "live.json"
    bootstrap.write_text(json.dumps({"multiple_testing_denominator": 415}), encoding="utf-8")
    live.write_text(json.dumps({"multiple_testing_denominator": 466}), encoding="utf-8")
    cfg = {"research": {"history_denominator": 415, "memory": str(bootstrap), "live_memory": str(live)}}
    assert historical_denominator(cfg) == 466
