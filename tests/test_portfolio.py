import pytest
from panda_alpha.portfolio import buffered_low_tail, constrained_equal_weights


def test_incumbent_band_avoids_round_trip_at_decile_boundary():
    scores = {f"{i:03}": float(i) for i in range(100)}
    held = [f"{i:03}" for i in range(5, 15)]
    assert buffered_low_tail(scores, held) == held
    scores['014'] = 99.5
    chosen = buffered_low_tail(scores, held)
    assert '014' not in chosen and '000' in chosen and len(chosen) == 10


def test_unknown_or_ineligible_incumbent_cannot_be_retained():
    scores = {str(i): float(i) for i in range(30)}
    scores['0'] = float('nan')
    chosen = buffered_low_tail(scores, ['0', 'missing', '1'])
    assert '0' not in chosen and 'missing' not in chosen and '1' in chosen


def test_risk_budget_retains_cash_and_never_redistributes_sector_excess():
    codes = ['a', 'b', 'c', 'd']
    result = constrained_equal_weights(codes, dict.fromkeys(codes, 2.),
                                       {'a': 'X', 'b': 'X', 'c': 'Y', 'd': 'Z'})
    assert result['industry_weights']['X'] <= .20 + 1e-12
    assert result['positive_beta_exposure'] <= 1. + 1e-12
    assert sum(result['weights'].values()) + result['cash_weight'] == pytest.approx(1.)
    assert result['cash_weight'] > 0
    assert all(w <= .25 for w in result['weights'].values())


def test_missing_classification_is_visible_and_conservatively_capped():
    result = constrained_equal_weights(['a', 'b'], {'a': .5, 'b': .5},
                                       {'a': float('nan'), 'b': None})
    assert result['unknown_industry_codes'] == ['a', 'b']
    assert result['industry_weights']['UNKNOWN'] == pytest.approx(.20)
    assert result['cash_weight'] == pytest.approx(.80)


def test_missing_beta_stops_risk_policy_instead_of_zero_filling():
    with pytest.raises(ValueError, match='beta'):
        constrained_equal_weights(['a'], {}, {'a': 'X'})


def test_negative_beta_does_not_offset_positive_risk_budget():
    result = constrained_equal_weights(['a', 'b'], {'a': -10., 'b': 10.},
                                       {'a': 'X', 'b': 'Y'}, industry_cap=1.)
    assert result['positive_beta_exposure'] == pytest.approx(1.)
    assert result['cash_weight'] == pytest.approx(.8)
