import pandas as pd
import pytest

from panda_alpha.execution_status import add_suspension_statuses
from panda_alpha.study import PotentialQuoteBook, StudyExecutionError


def observation(day='2025-10-09'):
    return dict(symbol='603843', date=day, trade_status=0,
                source_status='source_explicit_suspension', evidence_sha256='a' * 64)


def book():
    return PotentialQuoteBook(pd.DataFrame([dict(symbol='603843', date='2025-09-30',
                                                open=5.4, close=5.43, trade_status=1)]))


def test_status_blocks_order_and_values_at_strict_previous_close():
    q = book()
    assert add_suspension_statuses(q, [observation()]) == 1
    assert q.state('603843', '2025-10-09', 'buy') == 'blocked_suspension'
    assert q.price('603843', '2025-10-09', 'open') == (5.43, True)
    assert 'open' not in q.rows[('603843', '2025-10-09')]
    with pytest.raises(StudyExecutionError, match='unknown_quote'):
        q.price('603843', '2025-10-10', 'open')


def test_conflicting_quote_is_preserved_and_no_partial_sidecar_applied():
    q = book()
    with pytest.raises(ValueError, match='conflicts'):
        add_suspension_statuses(q, [observation(), observation('2025-09-30')])
    assert ('603843', '2025-10-09') not in q.rows
    assert q.price('603843', '2025-09-30', 'close') == (5.43, False)


def test_sidecar_cannot_introduce_a_filled_price():
    q = book()
    with pytest.raises(ValueError, match='cannot supply open'):
        add_suspension_statuses(q, [{**observation(), 'open': 5.43}])
    assert ('603843', '2025-10-09') not in q.rows
