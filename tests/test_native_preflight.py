import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from panda_alpha.native_preflight import (NativePreflightError, build_native_preflight,
                                          verify_native_preflight, PROJECTION_RESEARCH_PURPOSE)


CODE = '''class Ready(Factor):
    def calculate(self, factors):
        result = factors['close'].groupby(level='date').rank(pct=True)
        result.name = 'value'
        return result
'''
WINDOW = {"start": "2024-01-02", "end": "2024-02-12"}


@pytest.fixture
def prepared(tmp_path):
    dates = pd.bdate_range('2024-01-02', periods=30)
    index = pd.MultiIndex.from_product([dates, [f'S{n:02d}' for n in range(20)]], names=['date', 'symbol'])
    frame = pd.DataFrame({'close': 10 + np.tile(np.arange(20), 30) + np.repeat(np.arange(30), 20) * .01}, index=index)
    fixture = tmp_path / 'market.parquet'
    # Exact row order is part of the input/output contract, even for unsorted data.
    frame.iloc[::-1].reset_index().to_parquet(fixture, index=False)
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'local_test_fixture': True, 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()}))
    contract = tmp_path / 'contract.txt'
    contract.write_text('Observed contract: FactorSeries attribute delegation; input [date,symbol]; result Series value/date filter.')
    path = tmp_path / 'preflight.json'
    candidate = {'candidate_id': 'NP1', 'code': CODE, 'direction': 1, 'native_preflight_path': str(path)}
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, [source], [contract], path)
    assert receipt['passed']
    return candidate, fixture, source, contract, path, receipt


def test_real_date_first_wrapper_and_exact_input_order_are_replayed(prepared):
    candidate, _, _, _, _, receipt = prepared
    proof = verify_native_preflight(candidate, WINDOW, 5, 10)['proof']
    assert proof['finite_window_rows'] == 600 and proof['usable_group_dates'] == 30
    assert proof['input_index_names'] == ['date', 'symbol'] and proof['output_index_restored']
    assert proof == receipt['proof']
    assert 'not remote all-A' in receipt['limitation']


@pytest.mark.parametrize('change', ['code', 'direction', 'window', 'cycle', 'groups'])
def test_receipt_cannot_be_reused_for_a_changed_definition_or_dispatch(prepared, change):
    candidate, *_ = prepared
    candidate, window, cycle, groups = dict(candidate), dict(WINDOW), 5, 10
    if change == 'code': candidate['code'] += '\n# changed executable artifact\n'
    elif change == 'direction': candidate['direction'] = 0
    elif change == 'window': window['end'] = '2024-02-11'
    elif change == 'cycle': cycle = 4
    else: groups = 5
    with pytest.raises(NativePreflightError, match='source or dispatch parameters changed'):
        verify_native_preflight(candidate, window, cycle, groups)


@pytest.mark.parametrize('position', [1, 2, 3])
def test_changed_fixture_source_or_contract_file_is_rejected(prepared, position):
    artifact = prepared[position]
    with artifact.open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(NativePreflightError, match='evidence changed'):
        verify_native_preflight(prepared[0], WINDOW, 5, 10)


@pytest.mark.parametrize('mutation', ['passed_only', 'wrong_counts', 'failed', 'weaken_policy'])
def test_fabricated_or_failed_passed_flag_does_not_authorize_dispatch(prepared, mutation):
    candidate, _, _, _, path, receipt = prepared
    if mutation == 'passed_only': receipt = {'passed': True}
    elif mutation == 'wrong_counts': receipt['proof']['finite_window_rows'] = 999999
    elif mutation == 'failed': receipt['passed'] = False
    else: receipt['policy']['minimum_post_warmup_date_coverage'] = 0
    path.write_text(json.dumps(receipt))
    with pytest.raises(NativePreflightError):
        verify_native_preflight(candidate, WINDOW, 5, 10)


@pytest.mark.parametrize('body, message', [
    ("result = s.reorder_levels(['symbol', 'date']).reindex(s.index)", 'no finite'),
    ("result = s * 0 + 1", 'unsupported'),
    ("result = s.series * 0 + 1", 'constant'),
    ("result = s.series / 0", 'infinite'),
    ("result = s.series.iloc[::-1]", 'exact date-first'),
])
def test_known_native_failures_are_saved_as_failed_proofs_without_account_calls(prepared, body, message):
    candidate, fixture, source, contract, path, _ = prepared
    candidate['code'] = ("class Broken(Factor):\n    def calculate(self, factors):\n"
                         "        s = factors['close']\n        " + body + "\n        result.name = 'value'\n        return result\n")
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, [source], [contract], path)
    assert receipt['passed'] is False
    assert message in receipt['failure']['message']
    with pytest.raises(NativePreflightError, match='no successful replay proof'):
        verify_native_preflight(candidate, WINDOW, 5, 10)


def test_fixture_too_short_for_requested_window_is_rejected(prepared):
    candidate, fixture, source, contract, path, _ = prepared
    frame = pd.read_parquet(fixture)
    frame.loc[frame.date.ge('2024-02-01')].to_parquet(fixture, index=False)
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, [source], [contract], path)
    assert receipt['passed'] is False and 'span' in receipt['failure']['message']


def test_date_or_symbol_coverage_failure_prevents_false_nonempty_success(prepared):
    candidate, fixture, source, contract, path, _ = prepared
    candidate['code'] = CODE.replace("result.name = 'value'", "result.iloc[20:] = float('nan')\n        result.name = 'value'")
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, [source], [contract], path)
    assert receipt['passed'] is False and 'coverage' in receipt['failure']['message']


def test_missing_source_or_contract_evidence_cannot_generate_a_receipt(prepared):
    candidate, fixture, source, contract, path, _ = prepared
    with pytest.raises(NativePreflightError, match='Source and observed-contract evidence'):
        build_native_preflight(candidate, WINDOW, 5, 10, fixture, [], [contract], path)


def test_local_repository_dependency_cannot_hide_a_remote_worker_import_failure(prepared):
    candidate, fixture, source, contract, path, _ = prepared
    candidate['code'] = 'from panda_alpha.native_commonality import native_commonality_code\n' + CODE
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, [source], [contract], path)
    assert receipt['passed'] is False
    assert 'dependency is outside' in receipt['failure']['message']


@pytest.mark.parametrize('conversion, expected_error', [
    ("result = result.ge(.5).astype(float)", 'fewer distinct'),
    ("result = result.where(result.ge(.6), 0.)", 'group coverage'),
])
def test_requested_ten_groups_reject_binary_or_duplicate_quantile_edges_before_account(prepared, tmp_path, conversion, expected_error):
    from panda_alpha.platform import ExperimentLedger, dispatch
    candidate, fixture, source, contract, path, _ = prepared
    candidate['code'] = CODE.replace("result.name = 'value'", conversion + "\n        result.name = 'value'")
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, [source], [contract], path)
    assert receipt['passed'] is False
    assert expected_error in receipt['failure']['message']
    class NoAccount:
        def balance(self): raise AssertionError('Grouping failure accessed the account')
        def create(self, *args): raise AssertionError('Grouping failure created a factor')
    ledger = ExperimentLedger(tmp_path / 'grouping-no-dispatch.sqlite3')
    with pytest.raises(NativePreflightError, match='no successful replay proof'):
        dispatch(candidate, WINDOW, {'research': {'cycle': 5, 'groups': 10}, 'compute': {}}, ledger, NoAccount())
    assert ledger.jobs() == []


def test_continuous_values_form_all_requested_groups_and_state_conservative_boundary(prepared):
    receipt = prepared[-1]
    grouping = receipt['proof']['grouping_smoke_test']
    assert grouping['requested_groups'] == 10 and grouping['quantile_ready_dates'] == 30
    assert receipt['proof']['minimum_distinct_values_on_usable_dates'] == 20
    assert grouping['public_workflow_adds_random_jitter'] is True
    assert grouping['remote_filtering_cleaning_and_jitter_simulated'] is False
    assert 'conservative deterministic no-jitter' in receipt['limitation']


@pytest.mark.parametrize('change', ['code', 'window', 'cycle', 'groups', 'direction', 'fixture', 'source', 'contract', 'forged_proof'])
def test_changed_proofs_block_in_dispatch_before_balance_or_create(prepared, tmp_path, change):
    from panda_alpha.platform import ExperimentLedger, dispatch
    candidate, fixture, source, contract, path, receipt = prepared
    candidate, window = dict(candidate), dict(WINDOW)
    cfg = {'research': {'cycle': 5, 'groups': 10}, 'compute': {}}
    if change == 'code': candidate['code'] += '\n# another artifact\n'
    elif change == 'window': window['end'] = '2024-02-11'
    elif change == 'direction': candidate['direction'] = 0
    elif change in ('cycle', 'groups'): cfg['research'][change] -= 1
    elif change == 'fixture': fixture.write_bytes(fixture.read_bytes() + b'changed')
    elif change == 'source': source.write_text('changed')
    elif change == 'contract': contract.write_text('changed')
    else:
        receipt['proof']['finite_window_rows'] += 1
        path.write_text(json.dumps(receipt))
    class NoAccount:
        def balance(self): raise AssertionError('Preflight failure reached account')
        def create(self, *args): raise AssertionError('Preflight failure created a factor')
    ledger = ExperimentLedger(tmp_path / 'no-paid-job.sqlite3')
    with pytest.raises(NativePreflightError):
        dispatch(candidate, window, cfg, ledger, NoAccount())
    assert ledger.jobs() == []


def _source_subset_fixture(tmp_path, valid_assets=2400, empty_prefix_dates=0):
    dates = pd.bdate_range('2024-01-02', periods=30)
    symbols = [f'S{n:04d}' for n in range(4000)]
    index = pd.MultiIndex.from_product([dates, symbols], names=['date', 'symbol'])
    close = np.tile(10. + np.arange(4000), len(dates))
    close.reshape(len(dates), 4000)[:, valid_assets:] = np.nan
    close[:empty_prefix_dates * 4000] = np.nan
    frame = pd.DataFrame({'close': close}, index=index)
    fixture = tmp_path / 'whole-named-roster.parquet'
    frame.iloc[::-1].reset_index().to_parquet(fixture, index=False)
    # Keep every source-universe row, including the unqualified symbols.
    source_mask = tmp_path / 'source-qualification-mask.parquet'
    frame.assign(source_qualified=frame.close.notna())[['source_qualified']].reset_index().to_parquet(source_mask, index=False)
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'local_test_fixture': True, 'named_roster_size': 4000,
                                 'qualification_mask_is_source_evidence': True}))
    contract = tmp_path / 'contract.txt'
    contract.write_text('Observed date-first FactorSeries attribute-delegation contract.')
    path = tmp_path / 'preflight.json'
    candidate = {'candidate_id': 'source-transfer', 'code': CODE, 'direction': 1,
                 'native_preflight_path': str(path)}
    return candidate, fixture, [source, source_mask], contract, path


def test_default_strict_policy_still_rejects_real_partial_source_coverage(tmp_path):
    candidate, fixture, source, contract, path = _source_subset_fixture(tmp_path)
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, source, [contract], path)
    assert not receipt['passed'] and 'cross-sectional source coverage' in receipt['failure']['message']
    assert receipt['policy'] == {'minimum_post_warmup_date_coverage': .8,
                                 'minimum_median_symbol_coverage': .8}


def test_explicit_source_transfer_replays_partial_coverage_with_honest_scope(tmp_path):
    candidate, fixture, source, contract, path = _source_subset_fixture(tmp_path)
    candidate['native_preflight_purpose'] = 'source_qualified_transfer_research'
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, source, [contract], path)
    assert receipt['passed']
    verified = verify_native_preflight(candidate, WINDOW, 5, 10)
    proof = verified['proof']
    assert proof['input_rows'] == 120000 and proof['input_symbols'] == 4000
    assert proof['median_symbol_coverage'] == pytest.approx(.6)
    assert proof['coverage_evaluation_basis'] == 'all_requested_fixture_dates'
    assert proof['coverage_evaluation_dates'] == proof['usable_group_dates'] == 30
    assert proof['minimum_valid_symbols_on_usable_dates'] == 2400
    assert verified['scope'] == {'purpose': 'source_qualified_transfer_research',
                                'research_only': True, 'full_A_certified': False,
                                'admission_qualified': False,
                                'coverage_denominator': 'all_fixture_symbol_rows'}
    assert receipt['source_evidence'][1]['path'] == str(source[1].resolve())


@pytest.mark.parametrize('valid_assets, expected_pass', [(1999, False), (2000, True)])
def test_source_transfer_asset_floor_is_checked_on_every_requested_date(tmp_path, valid_assets, expected_pass):
    candidate, fixture, source, contract, path = _source_subset_fixture(tmp_path, valid_assets)
    candidate['native_preflight_purpose'] = 'source_qualified_transfer_research'
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, source, [contract], path)
    assert receipt['passed'] is expected_pass
    if not expected_pass:
        assert '2000 valid assets' in receipt['failure']['message']


def test_source_transfer_cannot_crop_empty_early_dates_as_warmup(tmp_path):
    candidate, fixture, source, contract, path = _source_subset_fixture(tmp_path, empty_prefix_dates=1)
    candidate['native_preflight_purpose'] = 'source_qualified_transfer_research'
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, source, [contract], path)
    assert not receipt['passed']
    assert 'every requested date' in receipt['failure']['message']


@pytest.mark.parametrize('change', ['purpose', 'date_policy', 'asset_policy', 'scope', 'source_mask'])
def test_source_transfer_proof_mutations_block_before_account_access(tmp_path, change):
    from panda_alpha.platform import ExperimentLedger, dispatch
    candidate, fixture, source, contract, path = _source_subset_fixture(tmp_path)
    candidate['native_preflight_purpose'] = 'source_qualified_transfer_research'
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, source, [contract], path)
    assert receipt['passed']
    if change == 'purpose': candidate.pop('native_preflight_purpose')
    elif change == 'date_policy': receipt['policy']['minimum_requested_date_coverage'] = .8
    elif change == 'asset_policy': receipt['policy']['minimum_valid_assets_per_requested_date'] = 1999
    elif change == 'scope': receipt['scope']['full_A_certified'] = True
    else: source[1].write_bytes(source[1].read_bytes() + b'changed')
    path.write_text(json.dumps(receipt))
    class NoAccount:
        def balance(self): raise AssertionError('Changed transfer evidence accessed account')
        def create(self, *args): raise AssertionError('Changed transfer evidence created factor')
    ledger = ExperimentLedger(tmp_path / 'no-transfer-dispatch.sqlite3')
    with pytest.raises(NativePreflightError):
        dispatch(candidate, WINDOW, {'research': {'cycle': 5, 'groups': 10}, 'compute': {}}, ledger, NoAccount())
    assert ledger.jobs() == []


@pytest.mark.parametrize('conversion, message', [
    ("result = result.ge(.5).astype(float).where(result.notna())", 'fewer distinct'),
    ("result = factors['close'].series / 0", 'infinite'),
])
def test_source_transfer_preserves_numeric_and_group_guards(tmp_path, conversion, message):
    candidate, fixture, source, contract, path = _source_subset_fixture(tmp_path)
    candidate['native_preflight_purpose'] = 'source_qualified_transfer_research'
    candidate['code'] = CODE.replace("result.name = 'value'", conversion + "\n        result.name = 'value'")
    receipt = build_native_preflight(candidate, WINDOW, 5, 10, fixture, source, [contract], path)
    assert not receipt['passed'] and message in receipt['failure']['message']


PROJECTION_CODE = '''import pandas as pd
class Projected(Factor):
    def calculate(self, factors):
        close = factors['close'].series
        panel = close.unstack('symbol').sort_index()
        result = panel.rolling(259, min_periods=259).mean().stack(dropna=False)
        result = result.reindex(close.index) * factors['source_gate'].series
        result = result[result.index.get_level_values('date') >= pd.Timestamp('2024-12-27')]
        result.name = 'value'
        return result
'''


@pytest.fixture
def projection(tmp_path):
    # A causal arithmetic proxy tests the interface, not real F141 economics.
    # Forty price peers, twenty source-qualified names, and a truthful NaN tail.
    dates = pd.bdate_range('2024-01-02', periods=288)
    assert dates[258] == pd.Timestamp('2024-12-27')
    symbols = [f'P{i:02d}' for i in range(40)]
    index = pd.MultiIndex.from_product([dates, symbols], names=['date', 'symbol'])
    gate = np.where(np.tile(np.arange(40), len(dates)) < 20, 1.0, np.nan)
    gate[np.repeat(np.arange(len(dates)), 40) > 281] = np.nan
    frame = pd.DataFrame({'close': 10 + np.tile(np.arange(40), len(dates)) + np.repeat(np.arange(len(dates)), 40) / 1000,
                          'source_gate': gate}, index=index)
    # Keep the unsorted supplied order; projected output cannot silently sort it.
    frame = frame.iloc[::-1].reset_index()
    fixture = tmp_path / 'source.parquet'
    frame.to_parquet(fixture, index=False)
    calendar = tmp_path / 'independent-calendar.json'
    calendar.write_text(json.dumps({'dates': dates.strftime('%Y-%m-%d').tolist()}))
    mask_frame = frame[frame.date.ge(dates[258])][['date', 'symbol', 'source_gate']].copy()
    mask_frame['source_qualified'] = mask_frame.source_gate.notna()
    mask_frame['expected_finite'] = mask_frame.source_gate.notna()
    mask = tmp_path / 'source-mask.parquet'
    mask_frame.drop(columns='source_gate').to_parquet(mask, index=False)
    source = tmp_path / 'source-lineage.json'
    source.write_text(json.dumps({'test_proxy_only': True, 'mask_basis': 'source gate plus259-input computability; no outcomes'}))
    observed = tmp_path / 'observed-contract.txt'
    observed.write_text('Projection test of source-bound local inputs; no native server data or warmup claim.')
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    window = {'start': dates[0].date().isoformat(), 'end': dates[-1].date().isoformat()}
    evaluation = {'start': dates[258].date().isoformat(), 'end': dates[-1].date().isoformat()}
    contract = {'input_window': window, 'evaluation_window': evaluation,
                'formation_dates': dates[258:282:5].strftime('%Y-%m-%d').tolist(),
                'holding_end': evaluation['end'], 'warmup': {'minimum_prior_price_rows': 258},
                'input_calendar': {'path': str(calendar), 'sha256': digest(calendar)},
                'source_qualification': {'path': str(mask), 'sha256': digest(mask)}}
    receipt = tmp_path / 'projection-preflight.json'
    candidate = {'candidate_id': 'projection-proxy', 'code': PROJECTION_CODE, 'direction': 1,
                 'native_preflight_path': str(receipt),
                 'native_preflight_purpose': PROJECTION_RESEARCH_PURPOSE,
                 'native_evaluation_contract': contract}
    return {'candidate': candidate, 'fixture': fixture, 'frame': frame, 'calendar': calendar,
            'mask': mask, 'source': [source, calendar, mask], 'observed': observed,
            'receipt': receipt, 'window': window, 'dates': dates}


def _build_projection(case, *, reader=None, fixture=None):
    return build_native_preflight(case['candidate'], case['window'], 5, 10,
                                  fixture or case['fixture'], case['source'], [case['observed']],
                                  case['receipt'], fields=['close', 'source_gate'], fixture_reader=reader)


def test_projection_keeps_whole_evaluation_index_and_truthful_nan_tail(projection):
    receipt = _build_projection(projection)
    assert receipt['passed'] is False and receipt['research_replay_passed'] is True
    verified = verify_native_preflight(projection['candidate'], projection['window'], 5, 10)
    proof = verified['proof']
    assert proof == receipt['proof']
    assert proof['input_rows'] == 288 * 40 and proof['evaluation_output_rows'] == 30 * 40
    assert proof['prior_input_calendar_rows'] == 258
    assert proof['usable_group_dates'] == len(proof['decision_dates']) == 24
    assert len(proof['formation_dates']) == 5
    assert proof['holding_end_rows'] == 40 and proof['holding_end_finite_rows'] == 0
    assert proof['holding_tail_NaN_rows'] == 6 * 40
    assert proof['minimum_distinct_values_on_decision_dates'] == 20
    assert proof['genuine_missing_rows'] == 720
    assert proof['peer_price_support']['positive_prior_price_rows_histogram'] == {'258': 40}
    assert proof['peer_price_support']['full_support_retained_only_in_memory']
    assert proof['peer_full_F141_computability_certified'] is False
    assert verified['scope']['source_limited_exploration'] and not verified['scope']['full_PIT_certified']
    assert not verified['scope']['remote_input_and_formation_grid_certified']


@pytest.mark.parametrize('change', ['full_input', 'sort', 'drop_nans', 'fill_nans', 'early_date_empty', 'nonformation_gap'])
def test_projection_rejects_index_crops_fills_and_decision_grid_drift(projection, change):
    candidate = projection['candidate']
    if change == 'full_input':
        candidate['code'] = candidate['code'].replace("        result = result[result.index.get_level_values('date') >= pd.Timestamp('2024-12-27')]\n", '')
    elif change == 'sort':
        candidate['code'] = candidate['code'].replace("        result.name = 'value'", "        result = result.sort_index()\n        result.name = 'value'")
    elif change == 'drop_nans':
        candidate['code'] = candidate['code'].replace("        result.name = 'value'", "        result = result.dropna()\n        result.name = 'value'")
    elif change == 'fill_nans':
        candidate['code'] = candidate['code'].replace("        result.name = 'value'", "        result = result.fillna(0.0)\n        result.name = 'value'")
    else:
        # Change both true source gate and expected mask. This is an honest
        # missing day, but it still invalidates the frozen rebalance lattice.
        day = projection['dates'][258 if change == 'early_date_empty' else 259]
        frame = pd.read_parquet(projection['fixture'])
        frame.loc[frame.date.eq(day), 'source_gate'] = np.nan
        frame.to_parquet(projection['fixture'], index=False)
        mask = pd.read_parquet(projection['mask'])
        mask.loc[mask.date.eq(day), ['source_qualified', 'expected_finite']] = False
        mask.to_parquet(projection['mask'], index=False)
        candidate['native_evaluation_contract']['source_qualification']['sha256'] = hashlib.sha256(projection['mask'].read_bytes()).hexdigest()
    receipt = _build_projection(projection)
    assert not receipt['research_replay_passed'] and not receipt['passed']
    expected = 'projection' if change in ['full_input', 'sort', 'drop_nans'] else 'mask' if change == 'fill_nans' else 'every decision date'
    assert expected in receipt['failure']['message']


@pytest.mark.parametrize('change', ['missing_global_day', '257_prior_rows', 'drop_eval_end', 'mask_cropped', 'wrong_lattice'])
def test_projection_independent_calendar_mask_and_endpoints_cannot_be_self_certified(projection, change):
    contract = projection['candidate']['native_evaluation_contract']
    if change in ['missing_global_day', 'drop_eval_end']:
        frame = pd.read_parquet(projection['fixture'])
        day = projection['dates'][50] if change == 'missing_global_day' else projection['dates'][-1]
        frame.loc[~frame.date.eq(day)].to_parquet(projection['fixture'], index=False)
    elif change == '257_prior_rows':
        data = json.loads(projection['calendar'].read_text())
        data['dates'] = data['dates'][1:]
        projection['calendar'].write_text(json.dumps(data))
        contract['input_calendar']['sha256'] = hashlib.sha256(projection['calendar'].read_bytes()).hexdigest()
        contract['input_window']['start'] = data['dates'][0]
    elif change == 'mask_cropped':
        mask = pd.read_parquet(projection['mask']).iloc[1:]
        mask.to_parquet(projection['mask'], index=False)
        contract['source_qualification']['sha256'] = hashlib.sha256(projection['mask'].read_bytes()).hexdigest()
    else:
        contract['formation_dates'][1] = projection['dates'][264].date().isoformat()
    receipt = _build_projection(projection)
    assert not receipt['research_replay_passed']
    assert any(word in receipt['failure']['message'] for word in ['calendar', '258', 'every evaluation input row', 'frozen cycle'])


def test_projection_extra_public_preload_is_explicit_and_peer_gaps_remain_visible(projection):
    frame = projection['frame']
    extra = frame[frame.date.eq(projection['dates'][0])].copy()
    extra['date'] = pd.Timestamp('2023-12-29')
    frame = pd.concat([frame, extra], ignore_index=True)
    # One unavailable early peer observation does not disqualify other peers,
    # but its reduced support cannot be described as259 continuous prices.
    frame.loc[frame.symbol.eq('P39') & frame.date.eq(projection['dates'][10]), 'close'] = np.nan
    frame.to_parquet(projection['fixture'], index=False)
    receipt = _build_projection(projection)
    assert receipt['research_replay_passed']
    proof = receipt['proof']
    assert proof['additional_prebuild_input_dates'] == 1
    support = proof['peer_price_support']
    assert support['positive_prior_price_rows_histogram'] == {'258': 39, '257': 1}
    assert support['missing_prior_price_rows_histogram'] == {'0': 39, '1': 1}
    assert support['consecutive_prices_through_first_evaluation_histogram']['248'] == 1


def test_projection_contract_and_source_mask_are_fingerprinted_not_borrowed_from_default(projection):
    receipt = _build_projection(projection)
    candidate = projection['candidate']
    original = candidate['native_evaluation_contract']['formation_dates']
    candidate['native_evaluation_contract']['formation_dates'] = original[:-1]
    with pytest.raises(NativePreflightError, match='parameters changed'):
        verify_native_preflight(candidate, projection['window'], 5, 10)
    candidate['native_evaluation_contract']['formation_dates'] = original
    receipt['passed'] = True  # Research success cannot masquerade as old passed.
    projection['receipt'].write_text(json.dumps(receipt))
    with pytest.raises(NativePreflightError, match='no successful replay proof'):
        verify_native_preflight(candidate, projection['window'], 5, 10)


def test_projection_trusted_descriptor_replay_requires_same_explicit_reader_and_real_frame(projection):
    descriptor = projection['fixture'].with_suffix('.descriptor.json')
    descriptor.write_text(json.dumps({'source': 'test memory', 'no_market_copy': True}))
    frame = projection['frame'].copy()
    def trusted_reader(path):
        assert path == descriptor
        return frame.copy()
    receipt = _build_projection(projection, reader=trusted_reader, fixture=descriptor)
    assert receipt['research_replay_passed'] and receipt['fixture_format'] == 'trusted_reader_descriptor'
    assert receipt['fixture_reader']['qualname'].endswith('trusted_reader')
    verified = verify_native_preflight(projection['candidate'], projection['window'], 5, 10, fixture_reader=trusted_reader)
    assert verified['proof'] == receipt['proof']
    with pytest.raises(NativePreflightError, match='explicit trusted fixture reader'):
        verify_native_preflight(projection['candidate'], projection['window'], 5, 10)
    def other_reader(path):
        return frame.copy()
    with pytest.raises(NativePreflightError, match='reader code or identity changed'):
        verify_native_preflight(projection['candidate'], projection['window'], 5, 10, fixture_reader=other_reader)
    # Output ranks can be unchanged despite changed raw history: input frame
    # hash independently binds the actual trusted reader result.
    frame.loc[frame.symbol.eq('P39'), 'close'] *= 2
    with pytest.raises(NativePreflightError, match='no longer matches'):
        verify_native_preflight(projection['candidate'], projection['window'], 5, 10, fixture_reader=trusted_reader)


@pytest.mark.parametrize('change', ['mask_artifact', 'mask_qualification', 'holding_end', 'receipt_policy'])
def test_projection_changed_artifacts_or_endpoint_policy_rejected(projection, change):
    receipt = _build_projection(projection)
    contract = projection['candidate']['native_evaluation_contract']
    if change == 'mask_artifact':
        projection['mask'].write_bytes(projection['mask'].read_bytes() + b'changed')
    elif change == 'mask_qualification':
        mask = pd.read_parquet(projection['mask'])
        mask.loc[mask.expected_finite, 'source_qualified'] = False
        mask.to_parquet(projection['mask'], index=False)
    elif change == 'holding_end':
        contract['holding_end'] = projection['dates'][-2].date().isoformat()
    else:
        receipt['policy']['minimum_decision_date_coverage'] = .8
        projection['receipt'].write_text(json.dumps(receipt))
    with pytest.raises(NativePreflightError):
        verify_native_preflight(projection['candidate'], projection['window'], 5, 10)


def test_projection_cannot_relabel_missing_decision_dates_as_holding_tail(projection):
    projection['candidate']['native_evaluation_contract']['formation_dates'].pop()
    receipt = _build_projection(projection)
    assert not receipt['research_replay_passed']
    assert 'every frozen cycle' in receipt['failure']['message']


def test_projection_definition_fingerprint_matches_actual_platform_contract(projection):
    from panda_alpha.native_preflight import _definition, _digest
    from panda_alpha.platform import fingerprint
    candidate, window = projection['candidate'], projection['window']
    assert _digest(_definition(candidate, window, 5, 10)) == fingerprint(candidate, window, 5, 10)
    # A legacy default candidate retains its prior definition shape even if
    # unrelated new contract metadata is present.
    legacy = {k: v for k, v in candidate.items() if k != 'native_preflight_purpose'}
    old = {key: legacy.get(key) for key in ('code', 'formula', 'direction')}
    old.update(window=window, cycle=5, groups=10)
    assert _digest(_definition(legacy, window, 5, 10)) == _digest(old)
    assert fingerprint(legacy, window, 5, 10) == _digest(old)


def test_projection_does_not_execute_a_reader_declared_in_untrusted_candidate(projection):
    projection['candidate']['fixture_reader'] = "__import__('os').remove('anything')"
    receipt = _build_projection(projection)
    assert not receipt['research_replay_passed'] and receipt['fixture_format'] == 'parquet'
    assert 'explicit keyword' in receipt['failure']['message']
    assert 'fixture_reader' not in receipt


FINANCIAL_DIAGNOSTIC_PURPOSE = 'financial_source_diagnostic_research'
FINANCIAL_DIAGNOSTIC_CODE = '''import numpy as np
import pandas as pd
class ReportedProvision(Factor):
    def calculate(self, factors):
        index = factors['close'].index
        provision = pd.Series(factors['estimated_liabilities'].to_numpy(), index=index)
        assets = pd.Series(factors['total_assets'].to_numpy(), index=index)
        valid = np.isfinite(provision) & provision.ge(0) & np.isfinite(assets) & assets.gt(0)
        result = provision.where(valid) / assets.where(valid)
        result.name = 'value'
        return result
'''


def _financial_diagnostic_fixture(tmp_path, finite=40):
    dates = pd.bdate_range(WINDOW['start'], WINDOW['end'])
    symbols = [f'SYN{n:03d}' for n in range(200)]
    index = pd.MultiIndex.from_product([dates, symbols], names=['date', 'symbol'])
    provision = np.tile(np.r_[np.arange(finite, dtype=float), np.full(200 - finite, np.nan)], len(dates))
    frame = pd.DataFrame({'close': 1., 'estimated_liabilities': provision, 'total_assets': 100.}, index=index)
    fixture = tmp_path / 'synthetic-sparse-financial.parquet'
    frame.iloc[::-1].reset_index().to_parquet(fixture, index=False)
    manifest = tmp_path / 'synthetic-source-manifest.json'
    manifest.write_text(json.dumps({'synthetic_test_only': True, 'nominal_symbols': symbols,
                                   'nominal_period': 'currentFY25_only_fixture',
                                   'fixture_dates': dates.strftime('%Y-%m-%d').tolist()}))
    observed = tmp_path / 'observed-interface.txt'
    observed.write_text('Existing local date-first/to_numpy delegation; synthetic interfaces, no native source claim.')
    path = tmp_path / 'sparse-preflight.json'
    candidate = {'candidate_id': 'synthetic-financial-diagnostic', 'code': FINANCIAL_DIAGNOSTIC_CODE,
                 'direction': 0, 'native_preflight_path': str(path),
                 'native_preflight_purpose': FINANCIAL_DIAGNOSTIC_PURPOSE,
                 'native_diagnostic_contract': {'nominal_symbols': symbols,
                     'nominal_period': 'currentFY25_only_fixture',
                     'source_manifest': {'path': str(manifest), 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}}}
    return candidate, fixture, manifest, observed, path


def _build_financial(case, source=None):
    candidate, fixture, manifest, observed, path = case
    return build_native_preflight(candidate, WINDOW, 5, 10, fixture,
                                  [manifest] if source is None else source, [observed], path,
                                  fields=['close', 'estimated_liabilities', 'total_assets'])


def test_sparse_financial_diagnostic_keeps_200_denominator_without_strict_pass(tmp_path):
    case = _financial_diagnostic_fixture(tmp_path)
    receipt = _build_financial(case)
    assert receipt['research_replay_passed'] is True and receipt['passed'] is False
    verified = verify_native_preflight(case[0], WINDOW, 5, 10)
    proof = verified['proof']
    assert proof == receipt['proof']
    assert proof['input_symbols'] == proof['nominal_symbols_per_date'] == 200
    assert proof['minimum_valid_symbols_on_usable_dates'] == 40
    assert proof['median_symbol_coverage'] == pytest.approx(.2)
    assert proof['genuine_missing_rows'] == 30 * 160
    assert proof['coverage_evaluation_dates'] == proof['usable_group_dates'] == 30
    assert not verified['scope']['full_A_certified'] and not verified['scope']['full_PIT_certified']
    assert not verified['scope']['admission_qualified']
    strict = dict(case[0])
    strict.pop('native_preflight_purpose')
    receipt = build_native_preflight(strict, WINDOW, 5, 10, case[1], [case[2]], [case[3]], case[4],
                                    fields=['close', 'estimated_liabilities', 'total_assets'])
    assert not receipt['passed'] and 'cross-sectional source coverage' in receipt['failure']['message']


@pytest.mark.parametrize('change', ['missing_nominal_row', 'shrink_roster', 'only_tail_dates',
                                   'missing_interior_date', 'first_date_missing', 'constant', 'fewer_than_twenty',
                                   'duplicate_quantile_edges', 'fill_missing'])
def test_financial_diagnostic_rejects_sparse_source_shortcuts(tmp_path, change):
    case = _financial_diagnostic_fixture(tmp_path, finite=19 if change == 'fewer_than_twenty' else 40)
    candidate, fixture, manifest, _, _ = case
    frame = pd.read_parquet(fixture)
    if change == 'missing_nominal_row':
        frame = frame.drop(frame.index[0])
    elif change == 'shrink_roster':
        data = json.loads(manifest.read_text())
        data['nominal_symbols'] = data['nominal_symbols'][:40]
        manifest.write_text(json.dumps(data))
        candidate['native_diagnostic_contract']['nominal_symbols'] = data['nominal_symbols']
        candidate['native_diagnostic_contract']['source_manifest']['sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        frame = frame[frame.symbol.isin(data['nominal_symbols'])]
    elif change == 'only_tail_dates':
        frame = frame[frame.date.ge(frame.date.max() - pd.Timedelta(days=7))]
    elif change == 'missing_interior_date':
        frame = frame[frame.date.ne(sorted(frame.date.unique())[10])]
    elif change == 'first_date_missing':
        frame.loc[frame.date.eq(frame.date.min()), 'estimated_liabilities'] = np.nan
    elif change == 'constant':
        frame.loc[frame.estimated_liabilities.notna(), 'estimated_liabilities'] = 1.
    elif change == 'duplicate_quantile_edges':
        frame.loc[frame.estimated_liabilities.lt(31), 'estimated_liabilities'] = 0.
    elif change == 'fill_missing':
        candidate['code'] = candidate['code'].replace("        result.name = 'value'",
                                                     "        result = result.fillna(0.)\n        result.name = 'value'")
    frame.to_parquet(fixture, index=False)
    receipt = _build_financial(case)
    assert receipt['passed'] is False and receipt['research_replay_passed'] is False
    with pytest.raises(NativePreflightError, match='no successful replay proof'):
        verify_native_preflight(candidate, WINDOW, 5, 10)


def test_financial_diagnostic_twenty_finite_is_the_fixed_boundary_and_zero_is_observed(tmp_path):
    case = _financial_diagnostic_fixture(tmp_path, finite=20)
    receipt = _build_financial(case)
    assert receipt['research_replay_passed'] and not receipt['passed']
    assert receipt['proof']['minimum_valid_symbols_on_usable_dates'] == 20
    assert receipt['proof']['median_symbol_coverage'] == pytest.approx(.1)
    assert receipt['proof']['minimum_distinct_values_on_usable_dates'] == 20
    assert verify_native_preflight(case[0], WINDOW, 5, 10)['proof'] == receipt['proof']


@pytest.mark.parametrize('change', ['passed_flag', 'research_flag', 'policy', 'proof', 'scope',
                                   'manifest_bytes', 'missing_manifest_binding', 'contract_period',
                                   'contract_roster', 'purpose'])
def test_financial_diagnostic_verification_recomputes_bound_proof_not_flags(tmp_path, change):
    case = _financial_diagnostic_fixture(tmp_path)
    candidate, _, manifest, _, path = case
    receipt = _build_financial(case)
    assert receipt['research_replay_passed'] and not receipt['passed']
    if change == 'passed_flag':
        receipt['passed'] = True
    elif change == 'research_flag':
        receipt['research_replay_passed'] = False
    elif change == 'policy':
        receipt['policy']['minimum_finite_outputs_every_requested_date'] = 19
    elif change == 'proof':
        receipt['proof']['median_symbol_coverage'] = 1.
    elif change == 'scope':
        receipt['scope']['full_PIT_certified'] = True
    elif change == 'manifest_bytes':
        manifest.write_bytes(manifest.read_bytes() + b'changed')
    elif change == 'missing_manifest_binding':
        substitute = tmp_path / 'unrelated-source.json'
        substitute.write_text('{}')
        receipt['source_evidence'] = [{'path': str(substitute), 'sha256': hashlib.sha256(substitute.read_bytes()).hexdigest()}]
    elif change == 'contract_period':
        candidate['native_diagnostic_contract']['nominal_period'] = 'mrq1_assumed_quarter'
    elif change == 'contract_roster':
        candidate['native_diagnostic_contract']['nominal_symbols'][0] = 'REPLACED'
    else:
        candidate.pop('native_preflight_purpose')
    path.write_text(json.dumps(receipt))
    with pytest.raises(NativePreflightError):
        verify_native_preflight(candidate, WINDOW, 5, 10)


def test_financial_diagnostic_manifest_must_be_source_evidence_at_build(tmp_path):
    case = _financial_diagnostic_fixture(tmp_path)
    unrelated = tmp_path / 'unrelated.json'
    unrelated.write_text('{}')
    receipt = _build_financial(case, source=[unrelated])
    assert not receipt['research_replay_passed'] and not receipt['passed']
    assert 'manifest must be bound in source evidence' in receipt['failure']['message']


def test_financial_diagnostic_contract_fingerprint_matches_platform_without_changing_legacy(tmp_path):
    from panda_alpha.native_preflight import _definition, _digest
    from panda_alpha.platform import fingerprint
    candidate = _financial_diagnostic_fixture(tmp_path)[0]
    assert _digest(_definition(candidate, WINDOW, 5, 10)) == fingerprint(candidate, WINDOW, 5, 10)
    legacy = dict(candidate)
    legacy.pop('native_preflight_purpose')
    definition = {key: legacy.get(key) for key in ('code', 'formula', 'direction')}
    definition.update(window=WINDOW, cycle=5, groups=10)
    assert _digest(_definition(legacy, WINDOW, 5, 10)) == fingerprint(legacy, WINDOW, 5, 10) == _digest(definition)


MINORITY_DIAGNOSTIC_FIELDS = ['close', 'net_profit', 'minority_profit',
                              'net_profit_parent_company', 'total_assets']
MINORITY_DIAGNOSTIC_CODE = '''import numpy as np
import pandas as pd
class MinorityComparison(Factor):
    def calculate(self, factors):
        index = factors['close'].index
        profit = pd.Series(factors['net_profit'].to_numpy(), index=index)
        minority = pd.Series(factors['minority_profit'].to_numpy(), index=index)
        parent = pd.Series(factors['net_profit_parent_company'].to_numpy(), index=index)
        assets = pd.Series(factors['total_assets'].to_numpy(), index=index)
        valid = (np.isfinite(profit) & np.isfinite(minority) & np.isfinite(parent)
                 & np.isfinite(assets) & profit.gt(0) & assets.gt(0))
        with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
            minority_share = minority.where(valid) / profit.where(valid)
            parent_asset_return = parent.where(valid) / assets.where(valid)
        common = valid & np.isfinite(minority_share) & np.isfinite(parent_asset_return)
        result = SELECTED.where(common)
        result.name = 'value'
        return result
'''


def _minority_financial_diagnostic_fixture(tmp_path, selected):
    candidate, fixture, manifest, observed, path = _financial_diagnostic_fixture(tmp_path)
    dates = pd.bdate_range(WINDOW['start'], WINDOW['end'])
    symbols = candidate['native_diagnostic_contract']['nominal_symbols']
    index = pd.MultiIndex.from_product([dates, symbols], names=['date', 'symbol'])
    profit, assets = np.full(200, np.nan), np.full(200, np.nan)
    minority, parent = np.full(200, np.nan), np.full(200, np.nan)
    profit[:40], assets[:40] = 100., 100.
    minority[:40], parent[:40] = np.arange(40, dtype=float), 100. - np.arange(40, dtype=float)
    minority[0], parent[0] = -1., 101.
    minority[1], parent[1] = 2., -2.
    profit[2:5] = [0., -1., np.nan]
    assets[5:8] = [0., -1., np.nan]
    minority[8], parent[9] = np.nan, np.nan
    profit[10], minority[10] = 1e-308, 1e308  # only candidate ratio overflows
    assets[11], parent[11] = 1e-308, 1e308  # only control ratio overflows
    profit[12], minority[13], parent[14], assets[15] = np.nan, np.nan, np.nan, np.nan
    minority[16] = 0.
    frame = pd.DataFrame({'close': 1., 'net_profit': np.tile(profit, len(dates)),
                          'minority_profit': np.tile(minority, len(dates)),
                          'net_profit_parent_company': np.tile(parent, len(dates)),
                          'total_assets': np.tile(assets, len(dates))}, index=index)
    frame.iloc[::-1].reset_index().to_parquet(fixture, index=False)
    candidate['code'] = MINORITY_DIAGNOSTIC_CODE.replace('SELECTED', selected)
    candidate['direction'] = 0 if selected == 'minority_share' else 1
    return candidate, fixture, manifest, observed, path


def _build_minority_financial(case, fields=None):
    candidate, fixture, manifest, observed, path = case
    return build_native_preflight(candidate, WINDOW, 5, 10, fixture, [manifest], [observed], path,
                                  fields=MINORITY_DIAGNOSTIC_FIELDS if fields is None else fields)


@pytest.mark.parametrize('selected', ['minority_share', 'parent_asset_return'])
def test_minority_financial_diagnostic_preserves_common_support_and_negative_amounts(tmp_path, selected):
    case = _minority_financial_diagnostic_fixture(tmp_path, selected)
    receipt = _build_minority_financial(case)
    assert receipt['research_replay_passed'] and not receipt['passed']
    proof = receipt['proof']
    assert proof['input_symbols'] == proof['nominal_symbols_per_date'] == 200
    assert proof['minimum_valid_symbols_on_usable_dates'] == 26
    assert proof['finite_window_rows'] == 30 * 26
    assert proof['genuine_missing_rows'] == 30 * 174
    assert proof['median_symbol_coverage'] == pytest.approx(.13)
    assert proof['usable_group_dates'] == proof['coverage_evaluation_dates'] == 30
    assert not proof['full_A_certified'] and not proof['full_PIT_certified']
    assert not proof['native_period_or_PIT_certified'] and not proof['admission_qualified']
    assert verify_native_preflight(case[0], WINDOW, 5, 10)['proof'] == proof


@pytest.mark.parametrize('selected,change', [('minority_share', 'candidate_only_mask'),
                                          ('parent_asset_return', 'control_only_mask'),
                                          ('minority_share', 'drop_negative_minority'),
                                          ('parent_asset_return', 'drop_negative_parent'),
                                          ('minority_share', 'fill_missing'),
                                          ('minority_share', 'allow_negative_group_profit')])
def test_minority_financial_diagnostic_rejects_changed_joint_mask(tmp_path, selected, change):
    case = _minority_financial_diagnostic_fixture(tmp_path, selected)
    candidate = case[0]
    if change == 'candidate_only_mask':
        candidate['code'] = candidate['code'].replace(' & np.isfinite(parent_asset_return)', '')
    elif change == 'control_only_mask':
        candidate['code'] = candidate['code'].replace('np.isfinite(minority_share) & ', '')
    elif change == 'drop_negative_minority':
        candidate['code'] = candidate['code'].replace('result.name', 'result = result.where(minority.ge(0))\n        result.name')
    elif change == 'drop_negative_parent':
        candidate['code'] = candidate['code'].replace('result.name', 'result = result.where(parent.ge(0))\n        result.name')
    elif change == 'fill_missing':
        candidate['code'] = candidate['code'].replace('result.name', 'result = result.fillna(0.)\n        result.name')
    else:
        candidate['code'] = candidate['code'].replace(' & profit.gt(0)', '')
    receipt = _build_minority_financial(case)
    assert not receipt['research_replay_passed'] and not receipt['passed']
    assert 'invalid financial inputs as NaN' in receipt['failure']['message']


def test_minority_financial_diagnostic_rejects_unknown_field_sets(tmp_path):
    case = _minority_financial_diagnostic_fixture(tmp_path, 'minority_share')
    candidate, fixture, _, _, _ = case
    frame = pd.read_parquet(fixture)
    frame['operating_revenue'] = 100.
    frame.to_parquet(fixture, index=False)
    candidate['code'] = candidate['code'].replace("        index = factors['close'].index",
                                                "        other = factors['operating_revenue']\n        index = factors['close'].index")
    receipt = _build_minority_financial(case, MINORITY_DIAGNOSTIC_FIELDS + ['operating_revenue'])
    assert not receipt['research_replay_passed'] and not receipt['passed']
    assert 'exact supported provision or minority inputs set' in receipt['failure']['message']


def test_minority_financial_diagnostic_retains_early_infinite_input_rejection(tmp_path):
    case = _minority_financial_diagnostic_fixture(tmp_path, 'minority_share')
    frame = pd.read_parquet(case[1])
    frame.loc[frame.symbol.eq('SYN000'), 'net_profit'] = np.inf
    frame.to_parquet(case[1], index=False)
    receipt = _build_minority_financial(case)
    assert not receipt['research_replay_passed'] and not receipt['passed']
    assert 'infinite input values' in receipt['failure']['message']
