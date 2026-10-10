"""Balanced review grades supplied increments without relaxing provenance."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from panda_alpha.admission import assess_admission
from test_pool_admission import EvidenceCase


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def case(tmp_path):
    case = EvidenceCase(tmp_path)
    case.policy = json.loads((REPO / "config/pool-admission.v2.json").read_text(encoding="utf-8"))
    case.policy["minimum_hypotheses"] = 466
    return case


def small_increment(case):
    case.bodies["ledger"]["costs"]["0.003"]["proposed"] = case.returns(.000405)
    case.bodies["ledger"]["costs"]["0.005"]["proposed"] = case.returns(.000305)
    for row in case.bodies["points"]["months"]:
        row["proposed"]["a_scores"] = [.005] * 5 + [.0052]
    case.save("ledger")
    case.save("points")


def test_full_original_targets_receive_strong_increment_grade(case):
    result = assess_admission(case.evidence, case.policy, evidence_directory=case.directory)
    assert result["status"] == "ready_for_shadow_validation", result
    assert result["tier"] == "strong_increment"
    assert not result["review_items"]
    assert result["pool_operations"] == result["paid_runs"] == 0
    assert not result["official_total_points_gain_verified"]


def test_active_policy_has_no_standalone_absolute_sharpe_target(case):
    assert "legacy_paid_confirmation" not in case.policy
    assert "minimum_net_sharpe" not in case.policy
    sharpe_policy = case.policy["single_factor_sharpe_policy"]
    assert sharpe_policy["minimum"] is None
    assert sharpe_policy["preferred_target"] is None
    example = json.loads((REPO / "config/panda-alpha.example.json").read_text(encoding="utf-8"))
    assert "minimum_net_sharpe" not in example["admission"]
    assert "legacy_policy_path" not in example["admission"]


def test_small_real_increment_below_old_targets_can_enter_shadow(case):
    small_increment(case)
    result = case.assess()
    assert result["status"] == "ready_for_shadow_validation", result
    assert result["tier"] == "small_increment_review"
    primary = result["metrics"]["comparisons"]["0.003"]
    assert 0 < primary["Sharpe_delta"] < .05
    assert 0 < primary["AC_gain"] < .05
    assert primary["CAGR_delta"] > 0
    assert result["review_items"]
    assert not result["economic_rejected"]
    assert result["metrics"]["B"]["status"] == "pending"
    assert not result["official_total_points_gain_verified"]


def test_v1_keeps_rejecting_the_same_below_target_evidence(case):
    small_increment(case)
    legacy = json.loads((REPO / "config/pool-admission.v1.json").read_text(encoding="utf-8"))
    result = assess_admission(case.evidence, legacy, evidence_directory=case.directory)
    assert result["status"] == "rejected"
    assert "portfolio_increment@0.003" in result["failed"]
    assert "tier" not in result


def test_return_improvement_with_larger_drawdown_requires_tradeoff_review(case):
    for paths in case.bodies["ledger"]["costs"].values():
        for i, row in enumerate(paths["proposed"]):
            row["net_return"] += (1 if i % 2 else -1) * .001
    case.save("ledger")
    result = case.assess()
    assert result["status"] == "ready_for_shadow_validation", result
    assert result["tier"] == "tradeoff_review"
    assert any(item.startswith("risk_deterioration") for item in result["review_items"])
    assert not result["economic_rejected"]


def test_50bp_deterioration_is_a_review_item_when_standard_cost_value_survives(case):
    case.bodies["ledger"]["costs"]["0.005"]["proposed"] = case.returns(.0002)
    case.save("ledger")
    result = case.assess()
    assert result["status"] == "ready_for_shadow_validation", result
    assert result["tier"] == "tradeoff_review"
    assert "cost_stress_return_declined@0.005" in result["review_items"]


def test_shorter_certified_history_is_reviewed_without_inventing_missing_months(case):
    ledger = case.bodies["ledger"]
    months = list(ledger["calendar_months"])[:12]
    ledger["calendar_months"] = {month: ledger["calendar_months"][month] for month in months}
    case.bodies["contracts"]["calendar_months"] = deepcopy(ledger["calendar_months"])
    allowed = {day for days in ledger["calendar_months"].values() for day in days}
    ledger["benchmark"] = [row for row in ledger["benchmark"] if row["date"] in allowed]
    for paths in ledger["costs"].values():
        for side in paths:
            paths[side] = [row for row in paths[side] if row["date"] in allowed]
    case.bodies["points"]["months"] = [row for row in case.bodies["points"]["months"] if row["month"] in months]
    case.plan["window"]["end"] = max(allowed)
    case.rebind()
    result = case.assess()
    assert result["status"] == "ready_for_shadow_validation", result
    assert result["tier"] == "tradeoff_review"
    assert "recent_window_shorter_than_target" in result["review_items"]
    assert len(result["metrics"]["complete_months"]) == 12


def test_saturated_points_do_not_reject_real_return_improvement(case):
    for cost, old_mean in (("0.003", .001), ("0.005", .0009)):
        case.bodies["ledger"]["costs"][cost] = {
            "baseline": case.returns(old_mean), "proposed": case.returns(old_mean + .0002)}
    for row in case.bodies["points"]["months"]:
        row["proposed"]["a_scores"] = [.005] * 6
    case.save("ledger")
    case.save("points")
    result = case.assess()
    assert result["status"] == "ready_for_shadow_validation", result
    assert result["tier"] == "tradeoff_review"
    for comparison in result["metrics"]["comparisons"].values():
        assert comparison["CAGR_delta"] > 0
        assert comparison["AC_delta"] == 0
        assert all(row["NC"] == 1 for row in comparison["months"]["baseline"].values())
    assert any("saturated" in item for item in result["review_items"])


def test_no_explanatory_increment_is_still_rejected(case):
    for paths in case.bodies["ledger"]["costs"].values():
        paths["proposed"] = deepcopy(paths["baseline"])
    for row in case.bodies["points"]["months"]:
        row["proposed"]["a_scores"] = [.005] * 6
    case.save("ledger")
    case.save("points")
    result = case.assess()
    assert result["status"] == "rejected"
    assert result["tier"] == "rejected"
    assert "no_explanatory_pool_increment" in result["failed"]
    assert result["economic_rejected"]


def test_complete_dominated_pool_evidence_is_still_an_economic_failure(case):
    case.bodies["ledger"]["costs"]["0.003"]["proposed"] = case.returns(.00025)
    case.bodies["ledger"]["costs"]["0.005"]["proposed"] = case.returns(.00015)
    for row in case.bodies["points"]["months"]:
        row["proposed"]["a_scores"] = [.005] * 5 + [.0001]
    case.save("ledger")
    case.save("points")
    result = case.assess()
    assert result["status"] == "rejected", result
    assert "no_explanatory_pool_increment" in result["failed"]


def test_nonpositive_standard_cost_profit_cannot_be_promoted_by_a_score(case):
    case.bodies["ledger"]["costs"]["0.003"]["proposed"] = case.returns(-.0001)
    case.save("ledger")
    result = case.assess()
    assert result["status"] == "rejected"
    assert "portfolio@0.003" in result["failed"]


@pytest.mark.parametrize("missing", ["source", "ledger", "stale_plan"])
def test_missing_or_stale_evidence_remains_pending_not_a_review_pass(case, missing):
    if missing == "source":
        case.bodies["contracts"]["checks"]["source_coverage"]["status"] = "pending"
        case.save("contracts")
    elif missing == "ledger":
        del case.evidence["artifacts"]["ledger"]
    else:
        case.bodies["points"]["plan_sha256"] = "0" * 64
        case.save("points")
    result = case.assess()
    assert result["status"] == "pending"
    assert result["tier"] == "pending"
    assert not result["economic_rejected"]


def test_candidate_quality_floor_is_not_relaxed_by_balanced_pool_grading(case):
    case.bodies["quality"]["metrics"]["direction_rank_ic"] = .001
    case.save("quality")
    result = case.assess()
    assert result["status"] == "rejected"
    assert "common:direction_rank_ic" in result["failed"]
    assert result["research_exploration_allowed"]
    assert result["official_experiment_qualification"] == "separate_research_and_budget_review"


def test_independent_calendar_and_shared_A_are_still_required(case):
    case.bodies["contracts"]["calendar_months"]["2024-01"].pop()
    case.save("contracts")
    assert case.assess()["status"] == "pending"
    case.bodies["contracts"]["calendar_months"] = deepcopy(case.bodies["ledger"]["calendar_months"])
    case.save("contracts")
    case.bodies["points"]["months"][0]["proposed"]["a_scores"][0] = .1
    case.save("points")
    assert case.assess()["status"] == "pending"


def test_illegal_four_effective_transition_is_never_a_tradeoff(case):
    stage = deepcopy(case.plan["baseline"])
    stage[0]["effective"] = False
    case.plan["transitions"].insert(1, stage)
    case.rebind()
    result = case.assess()
    assert result["status"] == "rejected"
    assert "illegal_pool_transition" in result["failed"]


def attach_shadow(case, *, candidate_cold=False):
    days = [f"2026-11-{day:02d}" for day in range(1, 22)]
    case.bodies["contracts"]["calendar_months"]["2026-11"] = days
    case.save("contracts")
    def returns(mean):
        return [{"date": day, "net_return": mean + (1 if i % 2 else -1) * .003,
                 "turnover": .01, "fee": .00001} for i, day in enumerate(days)]
    def b_rows(members):
        rows = []
        for member in members:
            row = {"id": member["id"], "version": member["version"], "definition_sha256": member["definition_sha256"],
                   "source": "cold_start_model", "effective_date": "2026-09-01", "records": []}
            if member["id"] == "NEW" and not candidate_cold:
                row["source"] = "shadow_post_freeze"
                row["records"] = [
                    {"version": "v1", "signal_date": "2026-11-01", "realized_date": "2026-11-06", "ic": .03, "rank_ic": .03},
                    {"version": "v1", "signal_date": "2026-11-06", "realized_date": "2026-11-11", "ic": .05, "rank_ic": .05}]
            rows.append(row)
        return rows
    case.bodies["shadow"] = {
        "scope": "frozen_forward_shadow", "type": "pool_signal_daily_ledger", "month": "2026-11", "calendar": days,
        "benchmark": [{"date": day, "return": .0001 + (1 if i % 2 else -1) * .002} for i, day in enumerate(days)],
        "costs": {"0.003": {"baseline": returns(.0004), "proposed": returns(.000405)},
                  "0.005": {"baseline": returns(.0003), "proposed": returns(.000305)}},
        "A": {"baseline": {"a_scores": [.005] * 5, "decay": 1, "a_cap": .7},
              "proposed": {"a_scores": [.005] * 5 + [.0052], "decay": 1, "a_cap": .7}},
        "B_review": {"independent_calendar_status": "verified", "version_freeze_status": "verified",
                     "baseline": b_rows(case.plan["baseline"]), "proposed": b_rows(case.plan["proposed"])}}
    case.save("shadow")


def test_two_shadow_IC_records_allow_manual_review_not_statistical_certification(case):
    small_increment(case)
    attach_shadow(case)
    result = case.assess()
    assert result["status"] == "eligible_for_review", result
    assert not result["statistical_validation_verified"]
    assert not result["official_total_points_gain_verified"]
    assert result["forward_validation"]["manual_review_required"]


def test_all_cold_shadow_does_not_fabricate_forward_evidence(case):
    small_increment(case)
    attach_shadow(case, candidate_cold=True)
    result = case.assess()
    assert result["status"] == "pending"
    assert not result["economic_rejected"]
    assert not result["official_total_points_gain_verified"]
