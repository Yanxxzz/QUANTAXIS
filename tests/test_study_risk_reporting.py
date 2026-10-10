"""Synthetic reporting regression; no market data, factor definitions or fits."""
import pytest
import pandas as pd

from panda_alpha.attribution import BetaExposure, account_day
from panda_alpha.study import _exposure_day, _exposure_summary, _market_projection_report, replay_portfolio, PotentialQuoteBook


class Facts:
    def __init__(self, beta):
        self.beta = beta

    def risk_fact(self, code, day, field):
        return (self.beta.get(code) if field == "beta" else "sector", day)


def exposure(weights, beta, day="2026-01-05", cash=0):
    return _exposure_day(weights, {code: 1 for code in weights}, cash, 1, Facts(beta), day)


def summary(days):
    return _exposure_summary(days, {"fees": 0.03}, [{"one_side_turnover_proxy": 0.2}])


def test_small_unknown_preserves_complete_none_and_visible_known_component():
    day = exposure({"A": 0.999, "B": 0.001}, {"A": 1.2, "B": None})
    result = summary([day])
    assert day["status"] == "risk_metadata_pending"
    assert result["mean_beta_exposure"] is None
    assert result["mean_known_beta_component"] == pytest.approx(0.999 * 1.2)
    assert result["mean_known_beta_component"] != pytest.approx(1.2)
    assert result["mean_known_beta_wealth_fraction"] == pytest.approx(0.999)
    assert result["mean_unknown_beta_wealth_fraction"] == pytest.approx(0.001)
    assert result["known_beta_coverage_of_invested_wealth"] == pytest.approx(0.999)
    assert result["unknown_beta_days"] == 1
    assert result["unknown_beta_stock_day_count"] == result["unknown_beta_stock_count"] == 1
    assert result["unknown_beta_stock_examples"] == [{"symbol": "B", "unknown_days": 1,
                                                       "first": "2026-01-05", "last": "2026-01-05"}]


def test_all_known_preserves_complete_beta_and_excludes_cash_from_coverage():
    result = summary([exposure({"A": 0.4, "B": 0.4}, {"A": 0.2, "B": -0.4}, cash=0.2)])
    assert result["mean_beta_exposure"] == pytest.approx(-0.08)
    assert result["mean_known_beta_component"] == pytest.approx(-0.08)
    assert result["known_beta_coverage_of_invested_wealth"] == 1
    assert result["mean_known_beta_wealth_fraction"] == pytest.approx(0.8)
    assert result["unknown_beta_days"] == result["unknown_beta_stock_count"] == 0


def test_cash_only_has_zero_exposure_but_no_invested_coverage_claim():
    result = summary([exposure({}, {}, cash=1)])
    assert result["mean_beta_exposure"] == result["mean_known_beta_component"] == 0
    assert result["mean_known_beta_wealth_fraction"] == result["mean_unknown_beta_wealth_fraction"] == 0
    assert result["known_beta_coverage_of_invested_wealth"] is None
    assert result["cash_only_days"] == result["fully_known_beta_days"] == 1
    assert result["unknown_beta_days"] == result["unknown_beta_stock_day_count"] == 0


def test_one_unknown_day_keeps_window_complete_beta_none_and_bounds_gap_storage():
    weights = {str(i): 1 / 12 for i in range(12)}
    first = exposure(weights, {})
    second = exposure({}, {}, day="2026-01-06", cash=1)
    result = summary([first, second])
    assert result["mean_beta_exposure"] is None
    assert result["unknown_beta_days"] == result["fully_known_beta_days"] == 1
    assert result["unknown_beta_stock_count"] == result["unknown_beta_stock_day_count"] == 12
    assert len(result["unknown_beta_stock_examples"]) == 10
    assert result["unknown_beta_stocks_outside_examples"] == 2
    assert result["total_fees_initial_equity_units"] == 0.03
    assert result["mean_one_side_turnover_proxy"] == 0.2


def accounted(**inputs):
    return account_day(day="2026-01-05", previous_nav=1, cash_before=0,
                       quantities_before={"A": 1}, previous_close={"A": 1},
                       open_marks={"A": 1}, close_marks={"A": 1}, trades=[], cash_after=0, **inputs)


def test_unprovided_projection_zero_is_distinct_from_measured_zero():
    absent = accounted()
    measured = accounted(overnight_beta={"A": BetaExposure(0, "2026-01-02")},
                         intraday_beta={"A": BetaExposure(0, "2026-01-02")},
                         overnight_market={"A": 0.01}, intraday_market={"A": 0.02})
    assert absent["market_projection"] == measured["market_projection"] == 0
    unknown = _market_projection_report([absent], inputs_provided=False)
    known = _market_projection_report([measured], inputs_provided=True)
    assert unknown["status"] == "not_provided" and unknown["market_projection_reported"] is None
    assert unknown["internal_accounting_market_projection"] == 0
    assert unknown["internal_value_is_complete_projection"] is False
    assert known["status"] == "provided_complete" and known["market_projection_reported"] == 0
    assert known["internal_value_is_complete_projection"] is True
    assert known["zero_projection_implies_risk_neutrality"] is False
    # Report construction does not touch accounting, trades or NAV.
    for field in ("nav", "nav_change", "cash_after", "price_pnl", "fees", "net_return"):
        assert absent[field] == measured[field]


def test_partial_projection_zero_with_missing_leg_is_not_complete_even_zero_pnl():
    day = accounted(overnight_beta={"A": BetaExposure(0, "2026-01-02")}, overnight_market={"A": 0.01})
    report = _market_projection_report([day], inputs_provided=True)
    assert day["price_pnl"] == day["market_projection"] == 0
    assert report["status"] == "provided_partial_or_unknown"
    assert report["market_projection_reported"] is None


def test_measured_zero_market_move_does_not_claim_beta_neutrality():
    day = accounted(overnight_beta={"A": BetaExposure(2, "2026-01-02")},
                    intraday_beta={"A": BetaExposure(2, "2026-01-02")},
                    overnight_market={"A": 0}, intraday_market={"A": 0})
    report = _market_projection_report([day], inputs_provided=True)
    assert report["status"] == "provided_complete" and report["market_projection_reported"] == 0
    assert report["zero_projection_implies_risk_neutrality"] is False


def test_normal_cash_replay_keeps_nav_and_persists_report_for_compact_consumers():
    run = replay_portfolio("cash_only_fixture", {}, ["2026-01-05", "2026-01-06", "2026-01-07"],
                           PotentialQuoteBook(pd.DataFrame()), 0.005)
    report = run["exposures"]["summary"]
    assert report["mean_beta_exposure"] == report["mean_known_beta_component"] == 0
    assert report["known_beta_coverage_of_invested_wealth"] is None
    assert report["market_projection_report"]["status"] == "not_provided"
    assert report["market_projection_report"]["market_projection_reported"] is None
    assert run["accounting"]["market_projection"] == 0
    assert [day["nav"] for day in run["daily"]] == [1, 1, 1]
    assert run["accounting"]["fees"] == 0
