"""Bound synthetic evidence exercises the offline pool-review contract.

The 21 ISO dates per month are synthetic sessions, not a real exchange calendar.
No market source, real factor labels, paid run or pool mutation is used here.
"""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from panda_alpha.cli import main
from panda_alpha.pool_admission import CONTRACTS, assess_pool_admission, plan_digest


REPO = Path(__file__).resolve().parents[1]


def member(identifier):
    return {"id": identifier, "version": "v1", "definition_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
            "direction": 1, "effective": True}


class EvidenceCase:
    def __init__(self, directory):
        self.directory = directory
        self.policy = json.loads((REPO / "config/pool-admission.v1.json").read_text(encoding="utf-8"))
        self.policy["minimum_hypotheses"] = 466
        calendar = {f"{year}-{month:02d}": [f"{year}-{month:02d}-{day:02d}" for day in range(1, 22)]
                    for year in (2024, 2025) for month in range(1, 13)}
        self.dates = [day for days in calendar.values() for day in days]
        baseline = [member(f"B{i}") for i in range(5)]
        proposed = deepcopy(baseline) + [member("NEW")]
        self.plan = {"as_of": "2026-10-10", "window": {"start": self.dates[0], "end": self.dates[-1]},
                     "cycle": 5, "groups": 10, "operation": "add", "baseline": baseline,
                     "proposed": proposed, "transitions": [deepcopy(baseline), deepcopy(proposed)],
                     "candidate_ids": ["NEW"], "planned_change_date": "2026-10-12"}
        self.bodies = {
            "contracts": {"checks": {key: {"status": "verified"} for key in CONTRACTS},
                          "calendar_months": deepcopy(calendar),
                          "construction": "winsor_1_99_zscore_direction_equal_top10_long_only"},
            "quality": {"metrics": {"coverage": .95, "periods": 242, "direction_rank_ic": .04,
                                    "folds_positive": 5, "bh_q": .001, "net30": .08, "net50": .05,
                                    "p25_fold_net": .01, "turnover": .1, "size_direction_rank_ic": .04,
                                    "size_net30": .06, "size_net50": .04,
                                    "max_abs_corr_existing_pool": .4, "cumulative_hypotheses": 466}},
            "ledger": {"type": "pool_signal_daily_ledger", "calendar_months": calendar,
                       "benchmark": [{"date": day, "return": .0001 + (1 if i % 2 else -1) * .002}
                                     for i, day in enumerate(self.dates)],
                       "costs": {"0.003": {"baseline": self.returns(.0004), "proposed": self.returns(.0008)},
                                 "0.005": {"baseline": self.returns(.0003), "proposed": self.returns(.0007)}},
                       "standalone": {"NEW": self.standalone("NEW", .0008)}},
            "points": {"months": [{"month": month,
                                   "baseline": {"a_scores": [.005] * 5, "decay": 1, "a_cap": .7},
                                   "proposed": {"a_scores": [.005] * 5 + [.08], "decay": 1, "a_cap": .7}}
                                  for month in calendar]}}
        self.evidence = {"schema_version": 1, "plan": self.plan, "artifacts": {}}
        self.rebind()

    def returns(self, mean):
        return [{"date": day, "net_return": mean + (1 if i % 2 else -1) * .003,
                 "turnover": .01, "fee": .00001} for i, day in enumerate(self.dates)]

    def standalone(self, identifier, mean):
        candidate = next(row for row in self.plan["proposed"] if row["id"] == identifier)
        return {**{key: candidate[key] for key in ("id", "version", "definition_sha256", "direction")},
                "cycle": 5, "groups": 10, "held_group": 1 if candidate["direction"] == 0 else 10,
                "metric": "held_decile_net_absolute_daily_return",
                "costs": {"0.003": self.returns(mean)}}

    def save(self, name):
        body = self.bodies[name]
        body.setdefault("status", "verified")
        body.setdefault("plan_sha256", plan_digest(self.plan))
        path = self.directory / f"{name}.json"
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        path.write_bytes(raw)
        self.evidence["artifacts"][name] = {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}

    def rebind(self):
        for name, body in self.bodies.items():
            body["plan_sha256"] = plan_digest(self.plan)
            self.save(name)

    def assess(self):
        return assess_pool_admission(self.evidence, self.policy, evidence_directory=self.directory)


@pytest.fixture
def case(tmp_path):
    return EvidenceCase(tmp_path)


def test_bound_complete_pool_passes_to_shadow_without_official_B(case):
    result = case.assess()
    assert result["status"] == "ready_for_shadow_validation", result
    assert not result["economic_rejected"]
    assert not result["official_total_points_gain_verified"]
    assert result["metrics"]["B"]["status"] == "pending"
    assert result["pool_operations"] == result["paid_runs"] == 0
    assert len(result["metrics"]["complete_months"]) == 24
    for comparison in result["metrics"]["comparisons"].values():
        assert comparison["proposed"]["CAGR"] > comparison["baseline"]["CAGR"]
        assert comparison["AC_gain"] >= .05


def test_positive_booleans_and_single_factor_sharpe_do_not_admit(case):
    evidence = {"schema_version": 1, "plan": case.plan, "sharpe": 100,
                "monthly_points": {"status": "verified", "gain_positive": True, "same_dates": True},
                "fixed_pool_increment": {"status": "verified", "artifact_sha256": "x"}}
    result = assess_pool_admission(evidence, case.policy, evidence_directory=case.directory)
    assert result["status"] == "pending"
    assert not result["official_total_points_gain_verified"]


@pytest.mark.parametrize("failure", ["content_changed", "wrong_sha", "old_plan", "missing_file"])
def test_unbound_or_missing_artifact_stays_pending(case, failure):
    if failure == "content_changed":
        (case.directory / "ledger.json").write_text("{}", encoding="utf-8")
    elif failure == "wrong_sha":
        case.evidence["artifacts"]["ledger"]["sha256"] = "0" * 64
    elif failure == "old_plan":
        case.bodies["ledger"]["plan_sha256"] = "0" * 64
        case.save("ledger")
    else:
        case.evidence["artifacts"]["ledger"]["path"] = "not-present.json"
    result = case.assess()
    assert result["status"] == "pending"
    assert not result["economic_rejected"]


@pytest.mark.parametrize("failure", ["different_date", "nan", "bool", "missing_fee"])
def test_incomplete_or_non_numeric_pool_ledger_stays_pending(case, failure):
    row = case.bodies["ledger"]["costs"]["0.003"]["proposed"][0]
    if failure == "different_date":
        row["date"] = "2024-01-22"
    elif failure == "nan":
        row["net_return"] = float("nan")
    elif failure == "bool":
        row["net_return"] = True
    else:
        del row["fee"]
    case.save("ledger")
    result = case.assess()
    assert result["status"] == "pending"
    assert not result["economic_rejected"]


def test_four_effective_members_in_a_transition_reject(case):
    stage = deepcopy(case.plan["baseline"])
    stage[0]["effective"] = False
    case.plan["transitions"].insert(1, stage)
    case.rebind()
    result = case.assess()
    assert result["status"] == "rejected"
    assert "illegal_pool_transition" in result["failed"]
    assert not result["economic_rejected"]


@pytest.mark.parametrize("new_mean", [.0004, .000405])
def test_no_30bp_gain_or_insufficient_sharpe_increment_reject(case, new_mean):
    case.bodies["ledger"]["costs"]["0.003"]["proposed"] = case.returns(new_mean)
    case.save("ledger")
    result = case.assess()
    assert result["status"] == "rejected"
    assert result["economic_rejected"]
    assert "portfolio_increment@0.003" in result["failed"]


def test_50bp_cost_stress_cannot_worsen_net_return(case):
    case.bodies["ledger"]["costs"]["0.005"]["proposed"] = case.returns(.0002)
    case.save("ledger")
    result = case.assess()
    assert result["status"] == "rejected"
    assert "cost_stress@0.005" in result["failed"]


def test_A_dilution_can_outweigh_a_better_return_path(case):
    for row in case.bodies["points"]["months"]:
        row["baseline"]["a_scores"] = [.04] * 5
        row["proposed"]["a_scores"] = [.04] * 5 + [.0001]
    case.bodies["ledger"]["costs"]["0.003"]["proposed"] = case.returns(.0004128)
    case.bodies["ledger"]["costs"]["0.005"]["proposed"] = case.returns(.0003128)
    case.save("ledger")
    case.save("points")
    result = case.assess()
    assert result["status"] == "rejected"
    assert any(reason.startswith("monthly_AC_increment") for reason in result["failed"])


def test_two_replacements_need_evidence_for_the_whole_plan(case):
    replacement = deepcopy(case.plan["baseline"][2:]) + [member("NEW"), member("OTHER")]
    case.plan.update(operation="replace", proposed=replacement, candidate_ids=["NEW", "OTHER"],
                     planned_change_date="2026-11-02", transitions=[deepcopy(case.plan["baseline"]), replacement])
    # Keep the valid artifact bytes bound to the old one-addition plan.
    result = case.assess()
    assert result["status"] == "pending"
    assert not result["economic_rejected"]


def test_sleeve_return_average_cannot_replace_signal_pool_ledger(case):
    case.bodies["ledger"]["type"] = "average_single_factor_returns"
    case.save("ledger")
    assert case.assess()["status"] == "pending"


def test_missing_source_is_pending_without_an_economic_failure(case):
    case.bodies["contracts"]["checks"]["source_coverage"]["status"] = "pending"
    case.save("contracts")
    result = case.assess()
    assert result["status"] == "pending"
    assert not result["economic_rejected"]


def test_incomplete_calendar_month_is_not_a_false_points_win(case):
    case.bodies["points"]["months"].pop()
    case.save("points")
    result = case.assess()
    assert result["status"] == "pending"
    assert not result["official_total_points_gain_verified"]


def test_cli_resolves_artifacts_at_evidence_directory_without_network_or_trials(case, tmp_path):
    nested = tmp_path / "separate_evidence"
    nested.mkdir()
    evidence = deepcopy(case.evidence)
    for artifact in evidence["artifacts"].values():
        source = case.directory / artifact["path"]
        (nested / source.name).write_bytes(source.read_bytes())
    evidence_path = nested / "review_input.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    config = json.loads((REPO / "config/panda-alpha.example.json").read_text(encoding="utf-8"))
    config["research"]["memory"] = str(tmp_path / "absent_memory.json")
    config["research"]["history_denominator"] = 466
    config["research"]["sealed_windows"] = []
    config["admission"]["policy_file"] = str(tmp_path / "absent_configured_policy.json")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output, trials = tmp_path / "review.json", tmp_path / "must_not_exist.sqlite3"
    with redirect_stdout(io.StringIO()), patch("requests.sessions.Session.request", side_effect=AssertionError("No network")):
        main(["--config", str(config_path), "--trial-ledger", str(trials), "admission", "--policy",
              str(REPO / "config/pool-admission.v1.json"), "--evidence", str(evidence_path), "--output", str(output)])
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "ready_for_shadow_validation", result
    assert not trials.exists()
    assert not result["official_total_points_gain_verified"]
    assert result["pool_operations"] == result["paid_runs"] == 0
