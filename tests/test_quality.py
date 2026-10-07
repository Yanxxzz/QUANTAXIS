from copy import deepcopy

from panda_alpha.quality import assess_research_quality


def evidence(net30=.02, net50=-.01, price=.04, relative=-.02):
    runs, reviews = [], []
    for cost, net in ((.003, net30), (.005, net50)):
        reviews.append({"cost": cost, "held_group": 10, "candidate_metrics": {"compounded_return": net},
                        "same_support_market_metrics": {"compounded_return": .03},
                        "candidate_relative_wealth_vs_market": relative, "pool_increment_status": "comparator_not_supplied"})
        runs.append({"name": "NEW07_G10", "cost": cost, "accounting": {
            "price_pnl": price, "fees": price-net, "folds": [{"net_return": .01}, {"net_return": -.01}],
            "concentration": {"top_5_share_of_positive_contributions": .9}}})
    return {"status": "evaluated_research", "candidate_id": "NEW07", "quality": {
        "source_coverage": {"research_source_complete": True}}, "cost_reviews": reviews, "runs": runs, "errors": []}


def test_low_cost_positive_high_cost_negative_never_becomes_verified_good_economics():
    source = evidence()
    before = deepcopy(source)
    result = assess_research_quality(source)
    assert result["layers"]["economics"]["status"] == "cost_sensitive"
    assert result["economic_validation"]["status"] == "pending"
    assert not result["reproducible_signal"]
    assert source == before
    source["cost_reviews"].reverse()
    assert assess_research_quality(source)["status"] == result["status"]


def test_source_and_execution_gaps_cannot_falsify_economics():
    for state in ("source_pending", "execution_error"):
        source = evidence(-.02, -.04, -.01)
        source["status"] = state
        result = assess_research_quality(source)
        assert result["failure_type"] == "missing_data"
        assert result["data_complete"] is False
        assert result["economic_validation"]["status"] == "pending"


def test_negative_joint_payoff_only_rejects_this_supplied_fixed_definition():
    result = assess_research_quality(evidence(-.02, -.04, -.01))
    assert result["status"] == "fixed_definition_weak"
    assert result["economic_validation"] == {"status": "rejected", "scope": "fixed_definition_local_potential_wealth"}
    assert result["formal_admission"] == "requires_independent_evidence_review"
    assert "concentration" in str(result["layers"]["economics"]["cost_reviews"])


def test_missing_comparator_does_not_automatically_claim_increment_or_score():
    result = assess_research_quality(evidence(.04, .02, .05, .01))
    assert result["layers"]["pool_increment"]["status"] == "pending"
    assert result["status"] == "joint_review_required"
    assert result["single_metric_or_weighted_score"] is False
