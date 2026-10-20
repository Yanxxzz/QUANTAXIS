import json

import pandas as pd
import pytest

from panda_alpha.registry import ResearchRegistry
from panda_alpha.team.coordinator import Coordinator
from panda_alpha.team.evidence import artifact
from panda_alpha.team.local_screen import local_first, LocalScreenBlocked
from study_fixture import synthetic_study


@pytest.fixture
def local_env(tmp_path):
    c = Coordinator(tmp_path / 'board.sqlite3')
    r = ResearchRegistry(tmp_path / 'registry.sqlite3')
    task = {'task_id': 'LOCAL', 'fingerprint': 'local-fp', 'request_hash': 'local-request', 'owner': 'market',
            'request': {'candidate': {'candidate_id': 'LOCAL', 'formula': 'close', 'direction': 1},
                        'window': {'start': '2024-01-02', 'end': '2024-02-01'}, 'cycle': 1, 'groups': 2}}
    proof = tmp_path / 'proof.txt'; proof.write_text('synthetic source, never real alpha')
    config = {'admission': {'one_way_costs': [.003, .005]}, 'diversity': {'minimum_assets': 3},
              'research': {'sealed_windows': []}, 'local_screen': {'enabled': True, 'allow_official_fallback': True,
                  'output_dir': str(tmp_path / 'reports'), 'inputs': {}}}
    manifest = {'fingerprint': task['fingerprint'], 'request_hash': task['request_hash'],
                'availability': 'unavailable', 'reason': 'Verified adjusted source unavailable', 'artifacts': [artifact(proof)]}
    yield c, r, task, config, manifest, tmp_path
    c.close(); r.close()


def bind(env, manifest):
    _, _, task, config, _, root = env
    path = root / 'manifest.json'; path.write_text(json.dumps(manifest))
    config['local_screen']['inputs'][task['fingerprint']] = artifact(path)


def test_unavailable_routes_official_once_without_spending(local_env):
    c, r, task, config, manifest, _ = local_env
    bind(local_env, manifest)
    first = local_first(c.board, task, config, r)
    assert first['route'] == 'official_fallback' and first['official_runs'] == 0
    assert local_first(c.board, task, config, r) == first
    assert len(c.board.events('LOCAL')) == 1 and c.ledger.jobs() == [] and r.total == 0


def test_known_operator_mismatch_is_not_a_missing_data_paid_fallback(local_env):
    c, r, task, config, manifest, _ = local_env
    manifest['definition_semantics'] = {'status': 'mismatch', 'artifacts': manifest['artifacts'],
        'reason': 'Actual operator replay differs in shift axis and missing-value behavior'}
    bind(local_env, manifest)
    with pytest.raises(LocalScreenBlocked, match='semantics mismatch'):
        local_first(c.board, task, config, r)
    assert not c.ledger.jobs() and r.total == 0 and not c.board.events('LOCAL')


def test_no_manifest_or_fallback_permission_blocks(local_env):
    c, r, task, config, manifest, _ = local_env
    with pytest.raises(LocalScreenBlocked, match='bound input'):
        local_first(c.board, task, config, r)
    config['local_screen']['allow_official_fallback'] = False
    bind(local_env, manifest)
    with pytest.raises(LocalScreenBlocked, match='await_local_data'):
        local_first(c.board, task, config, r)
    assert c.board.events('LOCAL')[-1]['payload']['route'] == 'await_local_data'


def test_changed_or_wrong_source_cannot_use_cached_route(local_env):
    c, r, task, config, manifest, root = local_env
    manifest['fingerprint'] = 'another-definition'
    bind(local_env, manifest)
    with pytest.raises(LocalScreenBlocked, match='frozen request'):
        local_first(c.board, task, config, r)
    manifest['fingerprint'] = task['fingerprint']; bind(local_env, manifest)
    local_first(c.board, task, config, r)
    (root / 'proof.txt').write_text('changed')
    with pytest.raises(ValueError):
        local_first(c.board, task, config, r)
    assert len(c.board.events('LOCAL')) == 1


def ready(env):
    _, _, task, _, manifest, root = env
    protocol, values, quotes, _ = synthetic_study(names=6, sessions=24, candidate_id='LOCAL')
    task['request']['window'] = {'start': protocol['calendar'][0], 'end': protocol['calendar'][-1]}
    for name, frame in [('quotes', quotes), ('values', values)]:
        path = root / (name + '.parquet'); frame.to_parquet(path, index=False); manifest[name] = artifact(path)
    path = root / 'calendar.json'; path.write_text(json.dumps({'dates': protocol['calendar']}))
    manifest.update(availability='ready', price_adjustment='hfq', calendar=artifact(path), calendar_verified=True)
    manifest['artifacts'] += [manifest['quotes'], manifest['values'], manifest['calendar']]
    return manifest


def test_real_local_engine_two_costs_and_idempotent_registration(local_env):
    c, r, task, config, _, _ = local_env
    manifest = ready(local_env)
    quotes = pd.read_parquet(manifest['quotes']['path'])
    quotes['date'] = pd.to_datetime(quotes.date)
    quotes['factor_date'] = pd.to_datetime(quotes.date)
    quotes.to_parquet(manifest['quotes']['path'], index=False)
    manifest['quotes'] = artifact(manifest['quotes']['path'])
    manifest['artifacts'] = [manifest['quotes'], manifest['values'], manifest['calendar']]
    bind(local_env, manifest)
    result = local_first(c.board, task, config, r)
    assert result['route'] == 'official_validation'
    assert [x['cost'] for x in result['study']['cost_reviews']] == [.003, .005]
    assert result['preparation']['protocol']['direction'] == 1
    assert result['study']['official_runs'] == 0 and r.total == 1 and not c.ledger.jobs()
    local_first(c.board, task, config, r)
    assert r.total == 1 and len(c.board.events('LOCAL')) == 1


def test_local_weak_stops_paid_dispatch_without_sign_rescue(local_env):
    c, r, task, config, _, root = local_env
    manifest = ready(local_env)
    quotes = pd.read_parquet(manifest['quotes']['path'])
    dates = sorted(quotes.date.unique())
    # All stocks lose wealth; high-score stocks lose more. Both cost paths,
    # price contribution and same-support market comparison must be weak.
    for i, day in enumerate(dates):
        mask = quotes.date.eq(day)
        for row in quotes.index[mask]:
            n = int(quotes.at[row, 'symbol'][1:])
            price = 10 * (1 - .002 - .001 * n) ** i
            for column, scale in [('open', 1), ('close', .9998), ('high', 1.002), ('low', .998),
                                  ('raw_open', 1), ('raw_close', .9998), ('raw_high', 1.002), ('raw_low', .998)]:
                quotes.at[row, column] = price * scale
    quotes.to_parquet(root / 'weak.parquet', index=False)
    manifest['quotes'] = artifact(root / 'weak.parquet')
    manifest['artifacts'].append(manifest['quotes']); bind(local_env, manifest)
    with pytest.raises(LocalScreenBlocked, match='local_reject'):
        local_first(c.board, task, config, r)
    assert c.board.events('LOCAL')[-1]['payload']['local_status'] == 'fixed_definition_weak'
    assert task['request']['candidate']['direction'] == 1 and not c.ledger.jobs()


def test_financial_pit_unavailable_does_not_invent_local_returns(local_env):
    c, r, task, config, _, _ = local_env
    manifest = ready(local_env); task['owner'] = 'fundamental'; bind(local_env, manifest)
    result = local_first(c.board, task, config, r)
    assert result['route'] == 'official_fallback' and 'PIT' in result['reason']
    assert 'study' not in result and r.total == 0


def test_invalid_adjustment_metadata_is_error_not_fallback(local_env):
    c, r, task, config, _, _ = local_env
    manifest = ready(local_env); manifest['price_adjustment'] = 'qfq'; bind(local_env, manifest)
    with pytest.raises(LocalScreenBlocked, match='disagrees'):
        local_first(c.board, task, config, r)
    assert not c.board.events('LOCAL') and not c.ledger.jobs()


@pytest.mark.parametrize('memory_blocks', [False, True])
def test_offline_host_routes_without_grant_models_or_remote_executor(local_env, monkeypatch, memory_blocks):
    from datetime import datetime, timedelta, timezone
    from panda_alpha.team.demo import freeze_synthetic, synthetic_request
    from panda_alpha.team.host import Host
    c, r, _, config, manifest, root = local_env
    task = freeze_synthetic(c.board, synthetic_request('market', manifest['artifacts'][0]))
    manifest.update(fingerprint=task['fingerprint'], request_hash=task['request_hash'])
    source = root / 'offline-input.json'; source.write_text(json.dumps(manifest))
    config['local_screen']['inputs'] = {task['fingerprint']: artifact(source)}
    path = root / 'offline-config.json'; path.write_text(json.dumps(config))
    host = Host(c, r, 'local-only', {'objective': 'Offline local first', 'max_actions': 2, 'max_rounds': 1,
        'deadline': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'seed_tasks': [task['task_id']], 'local_screen_config': artifact(path)})
    if memory_blocks:
        from panda_alpha.evolution import ResearchEngine
        monkeypatch.setattr(ResearchEngine, 'allows_research', lambda *args: False)
    result = host.next()
    if memory_blocks:
        assert result['reason'] == 'local_screen_blocked' and 'research memory' in result['error']
        assert not any(e['kind'] == 'local_screen' for e in c.board.events(task['task_id']))
    else:
        assert result['reason'] == 'execution_not_enabled'
        assert c.board.events(task['task_id'])[-1]['payload']['route'] == 'official_fallback'
    assert host.state()['actions'] == 0 and c.board.mode()['mode'] == 'offline' and not c.ledger.jobs()
