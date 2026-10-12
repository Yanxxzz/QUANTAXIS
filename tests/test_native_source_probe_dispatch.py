import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import pytest


path = Path(__file__).resolve().parents[1] / 'scripts/run_native_balance_source_probe.py'
spec = importlib.util.spec_from_file_location('source_probe_dispatch', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unanswered_proposal_is_not_authorization():
    with pytest.raises(ValueError, match='pending'):
        module.approved_grant({'status': 'PENDING', 'proposed_recharge': 8}, {})


def test_past_cutoff_stops_both_account_mutations(monkeypatch):
    calls = []
    monkeypatch.setattr(module.PandaClient, 'create', lambda *a, **kw: calls.append('create'))
    monkeypatch.setattr(module.PandaClient, 'start', lambda *a, **kw: calls.append('start'))
    client = module.CutoffClient({}, datetime(2020, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='cutoff'):
        client.create({}, {}, 5, 10, 'test')
    with pytest.raises(ValueError, match='cutoff'):
        client.start('owned-factor')
    assert calls == []


def test_grant_cannot_expand_one_run_or_fingerprint():
    plan = {'batch_id': 'NIGHT', 'fingerprint': 'a' * 64}
    a = {'status': 'APPROVED_BY_HUMAN', 'source': 'direct_user_reply_in_current_chat',
         'human_answer': 'approve', 'grant': {'batch_id': 'NIGHT', 'fingerprints': ['b' * 64],
         'max_runs': 1, 'max_recharge_credits': 8, 'max_total_credits': 20,
         'accept_estimated_charge_risk': True}}
    with pytest.raises(ValueError, match='scope'):
        module.approved_grant(a, plan)
