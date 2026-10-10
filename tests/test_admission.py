from panda_alpha.admission import assess_admission


def test_high_proxy_sharpe_is_not_admission():
    result = assess_admission({"sharpe": 10, "rank_ic": .1}, {"one_way_costs": [.003, .005], "minimum_net_sharpe": 2})
    assert result['status'] == 'pending'
    assert result['research_exploration_allowed']


def test_verified_negative_net_returns_reject():
    result = assess_admission({'net_returns': {'0.003': {'status': 'verified', 'artifact_sha256': 'x',
                                                        'sharpe': 3, 'compounded_return': -.1, 'relative_wealth_excess': .05}}},
                              {"one_way_costs": [.003], "minimum_net_sharpe": 2})
    assert result['status'] == 'rejected'


def test_legacy_sharpe_cutoff_cannot_reject_positive_verified_economics():
    required = ("source_coverage", "point_in_time_universe", "execution_audit",
                "size_stress", "fixed_pool_increment", "temporal_stability",
                "actual_factor_diversity", "official_transfer", "direction_parity")
    evidence = {name: {"status": "verified", "artifact_sha256": "x"} for name in required}
    evidence["net_returns"] = {"0.003": {"status": "verified", "artifact_sha256": "x",
                                        "sharpe": .8, "compounded_return": .1,
                                        "relative_wealth_excess": .03}}
    evidence["monthly_points"] = {"status": "verified", "type": "official_complete_month_ledger",
                                  "same_dates": True, "gain_positive": True}
    for policy in ({"one_way_costs": [.003]},
                   {"one_way_costs": [.003], "minimum_net_sharpe": 2}):
        assert assess_admission(evidence, policy)["status"] == "eligible_for_review"
