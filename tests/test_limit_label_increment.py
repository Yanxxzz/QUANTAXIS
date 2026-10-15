"""Public adapter import/guard tests; no retained research files or database."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from panda_alpha import limit_label_increment as m

REFS = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07',
        '2025-01-08', '2025-01-09', '2025-01-10', '2025-01-13']


def test_public_plan_keeps_tail_and_refuses_already_completed_identity():
    rows = [{'entry_date': '2025-01-06', 'code': '000001'},
            {'entry_date': '2025-01-13', 'code': '000001'}]
    plan = m.plan_identities(rows, REFS)
    assert len(plan['events']) == 2 and plan['tail_incomplete_events'] == 1
    assert plan['events'][0]['future_reference_dates'] == REFS[3:8]
    with pytest.raises(ValueError):
        m.plan_identities(rows, REFS, excluded_old_identities=[rows[0]])


def test_public_asof_adapter_does_not_choose_future_or_hide_duplicate():
    records = [{'code': '000001', 'date': '2025-01-02', 'adj': 1},
               {'code': '000001', 'date': '2025-01-08', 'adj': 2},
               {'code': '000001', 'date': '2025-01-08', 'adj': 2}]
    out = m.asof_bindings('000001', ['2025-01-01', '2025-01-06', '2025-01-09'], records)
    assert out[('000001', '2025-01-01')]['reason'] == 'factor_asof_missing'
    assert out[('000001', '2025-01-06')]['row']['adj'] == 1
    assert out[('000001', '2025-01-09')]['reason'] == 'factor_latest_asof_duplicate'


def test_public_guard_checks_manifest_and_new_authority_before_source(tmp_path, monkeypatch):
    authority = tmp_path / 'authority.json'
    authority.write_text('synthetic authorized local study', encoding='utf-8')
    protocol = {
        'execution_authorized': True,
        'runtime_sha256': m.file_sha(Path(m.__file__)),
        'authority_binding': {'path': str(authority), 'sha256': m.file_sha(authority)},
        'planned': m.plan_identities([{'entry_date': '2025-01-06', 'code': '000001'}], REFS),
        'mongo_contract': {'database': 'quantaxis_stockdb_research', 'source': 'stockdb'},
        'pure_runtime_binding': {'path': 'caller-supplied', 'sha256': m.PURE_SHA},
    }
    sentinel = SimpleNamespace()
    monkeypatch.setattr(m, 'load_pure_runtime', lambda binding: sentinel)
    assert m.execution_guard(protocol) is sentinel
    protocol['planned']['events'][0]['future_reference_dates'] = []
    with pytest.raises(ValueError, match='manifest'):
        m.execution_guard(protocol)


def test_public_pure_runtime_requires_exact_bound_hash_without_loading_private_file():
    with pytest.raises(ValueError, match='binding'):
        m.load_pure_runtime({'path': 'not-a-retained-input', 'sha256': '0' * 64})
