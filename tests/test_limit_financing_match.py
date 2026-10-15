import pytest

from panda_alpha.limit_financing_match import match_financing_domain


def row(code, supported, size=1., turnover=1., date='2024-01-02', **extra):
    return dict(code=code, date=date, finance_supported=supported,
                size_ratio=size, turnover_ratio=turnover, **extra)


def test_clock_board_and_outcomes_cannot_select_matches():
    rows = [row('000001', True), row('000002', False), row('600001', False),
            row('000003', False, date='2024-01-03')]
    expected = match_financing_domain(rows)
    assert len(expected) == 1 and expected[0]['unconfirmed_code'] == '000002'
    assert match_financing_domain([dict(r, future_return=-1e9*i) for i, r in enumerate(reversed(rows))]) == expected


def test_cardinality_precedes_distance_no_replacement():
    # Greedy 001->003 would strand 002; the global optimum preserves two pairs.
    rows = [row('000001', True, 1), row('000002', True, .5),
            row('000003', False, 1), row('000004', False, 2)]
    matches = match_financing_domain(rows)
    assert {(p['reported_code'], p['unconfirmed_code']) for p in matches} == {
        ('000001', '000004'), ('000002', '000003')}


def test_empty_one_side_and_caliper_do_not_force_matches():
    assert match_financing_domain([row('000001', True), row('000002', False, 2.00001)]) == []
    assert match_financing_domain([row('000001', True)]) == []
    assert match_financing_domain([]) == []


def test_minimum_distance_and_order_independence():
    rows = [row('000001', True, 1), row('000002', True, 1.5),
            row('000003', False, 1.01), row('000004', False, 1.49)]
    result = match_financing_domain(rows)
    assert [p['unconfirmed_code'] for p in result] == ['000003', '000004']
    assert result == match_financing_domain(list(reversed(rows)))


@pytest.mark.parametrize('bad', [0., -1., float('nan'), float('inf'), True])
def test_invalid_covariates_not_imputed(bad):
    with pytest.raises(ValueError):
        match_financing_domain([row('000001', True, size=bad)])


def test_duplicate_and_unknown_support_rejected():
    with pytest.raises(ValueError):
        match_financing_domain([row('000001', True), row('000001', False)])
    with pytest.raises(ValueError):
        match_financing_domain([row('000001', None)])
