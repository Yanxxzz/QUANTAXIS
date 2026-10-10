import math
import statistics

import pytest

from panda_alpha.pool_scoring import monthly_c, new_b_score, normalized_a


def record(signal, realized, ic, rank_ic, version="v1"):
    return {"version": version, "signal_date": signal, "realized_date": realized,
            "ic": ic, "rank_ic": rank_ic}


def b_score(records, direction=1, **changes):
    params = dict(version="v1", effective_date="2026-09-23", direction=direction,
                  as_of="2026-10-10")
    params.update(changes)
    return new_b_score(records, **params)


def test_monthly_c_uses_relative_compounded_wealth_and_absolute_sample_sharpe():
    net = [0.01, -0.02, 0.03]
    benchmark = [0.005, -0.01, 0.002]
    result = monthly_c(net, benchmark, 0.1992)
    growth, market_growth = math.prod(1 + r for r in net), math.prod(1 + r for r in benchmark)
    expected_rex = (growth / market_growth) ** (252 / 3) - 1
    expected_sharpe = statistics.mean(net) / statistics.stdev(net) * math.sqrt(252)
    assert result["Rp"] == pytest.approx(growth - 1)
    assert result["Rb"] == pytest.approx(market_growth - 1)
    assert result["Rex_ann"] == pytest.approx(expected_rex)
    assert result["SR_ann"] == pytest.approx(expected_sharpe)
    assert result["MaxDD_month"] == pytest.approx(0.02)
    assert result["rawC"] == pytest.approx(expected_rex / 0.3 * expected_sharpe * (1 - 1.2 * 0.02))


def test_less_negative_than_market_still_has_negative_absolute_sharpe_and_zero_c():
    result = monthly_c([-0.003, -0.001], [-0.02, -0.01], 0.1992)
    assert result["Rp"] < 0
    assert result["Rex_ann"] > 0
    assert result["SR_ann"] < 0
    assert result["rawC"] < 0
    assert result["NC"] == 0
    assert result["MaxDD_month"] == pytest.approx(1 - 0.997 * 0.999)


def test_turnover_floor_and_monthly_score_clipping():
    net, benchmark = [0.001, 0.002, -0.0001], [0.0, 0.0, 0.0]
    low = monthly_c(net, benchmark, 0.1992)
    floor = monthly_c(net, benchmark, 0.3)
    high = monthly_c(net, benchmark, 0.6)
    assert low["rawC"] == pytest.approx(floor["rawC"])
    assert high["rawC"] == pytest.approx(floor["rawC"] / 2)
    assert low["rawC"] > 0.6
    assert low["NC"] == 1
    losing = monthly_c([-0.003, -0.001], [-0.02, -0.01], 0.3)
    assert 40000 * 0.45 * (low["NC"] + losing["NC"]) == 18000
    assert losing["NC"] == 0


def test_c_with_no_positive_excess_is_zero():
    result = monthly_c([0.001, 0.002], [0.01, 0.02], 0.3)
    assert result["SR_ann"] > 0
    assert result["Rex_ann"] < 0
    assert result["rawC"] == 0
    assert result["NC"] == 0


@pytest.mark.parametrize("net,benchmark,turnover", [
    ([], [], 0.3), ([0.01], [0.01], 0.3),
    ([0.01, 0.02], [0.0], 0.3),
    ([True, 0.02], [0.0, 0.0], 0.3),
    ([None, 0.02], [0.0, 0.0], 0.3),
    ([math.nan, 0.02], [0.0, 0.0], 0.3),
    ([0.01, 0.02], [math.inf, 0.0], 0.3),
    ([-1.0, 0.02], [0.0, 0.0], 0.3),
    ([0.01, 0.02], [-1.01, 0.0], 0.3),
    ([0.01, 0.01], [0.0, 0.0], 0.3),
    ([0.01, 0.02], [0.0, 0.0], -0.1),
    ([0.01, 0.02], [0.0, 0.0], True),
    ([0.01, 0.02], [0.0, 0.0], math.nan),
    ([1e100, 1e101], [0.0, 0.0], 0.3),
])
def test_c_undefined_or_invalid_inputs_are_errors_not_zero(net, benchmark, turnover):
    with pytest.raises(ValueError):
        monthly_c(net, benchmark, turnover)


def test_a_averages_raw_single_scores_before_clipping_and_applies_decay_before_cap():
    assert normalized_a([0.0, 0.16], 1.0, 0.7) == 0.7
    assert normalized_a([0.0, 0.16], 0.9, 1.0) == 0.9
    assert normalized_a([0.02, 0.04], 0.9, 0.7) == pytest.approx(0.3375)


@pytest.mark.parametrize("scores,decay,cap", [
    ([], 1.0, 0.7), ([None], 1.0, 0.7), ([True], 1.0, 0.7),
    ([math.nan], 1.0, 0.7), ([-0.01], 1.0, 0.7),
    ([0.01], True, 0.7), ([0.01], 1.1, 0.7), ([0.01], 1.0, -0.1),
])
def test_invalid_or_missing_a_cannot_be_promoted(scores, decay, cap):
    with pytest.raises(ValueError):
        normalized_a(scores, decay, cap)


def test_new_b_uses_absolute_mean_rank_ic_and_pearson_icir_with_strict_direction_win():
    records = [record("2026-09-23", "2026-09-30", 0.02, 0.10),
               record("2026-09-30", "2026-10-08", 0.04, 0.20),
               record("2026-10-08", "2026-10-09", 0.06, -0.15)]
    result = b_score(records)
    assert result["status"] == "scored"
    assert result["n"] == 3
    assert result["icir"] == pytest.approx(2.0)
    assert result["ic_win_rate"] == pytest.approx(2 / 3)
    assert result["rawB"] == pytest.approx(abs((0.10 + 0.20 - 0.15) / 3) * 2 * 2 / 3)
    assert result["official_minimum_sample_verified"] is False
    negative = [{**r, "ic": -r["ic"], "rank_ic": -r["rank_ic"]} for r in records]
    assert b_score(negative, direction=0)["rawB"] == pytest.approx(result["rawB"])


def test_b_cold_start_and_fewer_than_three_completed_samples_remain_pending():
    assert b_score([]) == {"status": "cold_start", "rawB": None, "n": 0,
                          "reason": "No completed post-effective IC records"}
    one = [record("2026-09-23", "2026-09-30", 0.04, 0.1)]
    assert b_score(one)["status"] == "pending"
    assert b_score(one)["rawB"] is None
    # A nonzero two-observation variance is mathematically computable, but
    # does not reach the current backend's three-observation B maturity rule.
    two = one + [record("2026-09-30", "2026-10-08", 0.06, 0.2)]
    assert b_score(two)["status"] == "pending"
    assert b_score(two)["rawB"] is None
    assert b_score(two)["n"] == 2
    assert "three completed records" in b_score(two)["reason"]


def test_b_three_completed_observations_with_zero_ic_dispersion_remain_pending():
    records = [record("2026-09-23", "2026-09-30", 0.04, 0.1),
               record("2026-09-30", "2026-10-08", 0.04, 0.2),
               record("2026-10-08", "2026-10-09", 0.04, -0.1)]
    result = b_score(records)
    assert result["status"] == "pending"
    assert result["rawB"] is None
    assert result["n"] == 3
    assert "zero IC sample dispersion" in result["reason"]


@pytest.mark.parametrize("records", [
    [record("2026-09-22", "2026-09-30", 0.04, 0.1)],
    [record("2026-09-23", "2026-09-30", 0.04, 0.1, "old")],
    [record("2026-09-23", "2026-09-23", 0.04, 0.1)],
    [record("2026-10-08", "2026-10-12", 0.04, 0.1)],
    [record("2026-09-23", None, 0.04, 0.1)],
    [record("20260923", "2026-09-30", 0.04, 0.1)],
    [record("2026-09-23", "2026-09-30", math.nan, 0.1)],
    [record("2026-09-23", "2026-09-30", 0.04, True)],
    [record("2026-09-23", "2026-09-30", 1.01, 0.1)],
    [record("2026-09-23", "2026-09-30", 0.04, 0.1),
     record("2026-09-23", "2026-10-08", 0.05, 0.2)],
    [record("2026-09-23", "2026-10-08", 0.04, 0.1),
     record("2026-09-30", "2026-10-08", 0.05, 0.2)],
    [record("2026-09-30", "2026-10-08", 0.04, 0.1),
     record("2026-09-23", "2026-09-30", 0.05, 0.2)],
])
def test_old_future_missing_mixed_or_duplicate_b_records_are_rejected(records):
    with pytest.raises(ValueError):
        b_score(records)


@pytest.mark.parametrize("changes", [
    {"version": ""}, {"direction": True}, {"direction": -1},
    {"as_of": None}, {"effective_date": "2026-02-30"},
])
def test_b_requires_explicit_valid_version_cutoff_and_direction(changes):
    with pytest.raises(ValueError):
        b_score([], **changes)


def test_b_as_of_is_mandatory_and_future_effective_version_has_no_new_evidence():
    with pytest.raises(TypeError):
        new_b_score([], "v1", "2026-09-23", 1)
    assert b_score([], effective_date="2026-10-12")["status"] == "cold_start"
