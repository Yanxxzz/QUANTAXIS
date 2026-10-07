import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from panda_alpha.native_preflight import (NativePreflightError, build_native_preflight,
                                          verify_native_preflight)


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
