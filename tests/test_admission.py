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
