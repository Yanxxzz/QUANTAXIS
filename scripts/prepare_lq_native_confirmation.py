"""One source/interface preparation for fixed LQ01; no paid or economic dispatch.

Reuse retained full-peer close/amount inputs through an explicitly injected
trusted reader. Preserve the original LQ core bytes and fixed economic window;
only the Factor adapter's returned index is projected to that window.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.native_gf_transfer import read_mongo_gf_fixture
from panda_alpha.native_preflight import build_native_preflight
from panda_alpha.platform import fingerprint

OUT = ROOT / 'research_runs/day_research_20261013/lq_native_confirmation'
OLD = ROOT / 'research_runs/night_research_20261013/lq01_fixed5252_domain'
INPUT_WINDOW = {'start': '2024-06-03', 'end': '2026-09-03'}
EVALUATION_WINDOW = {'start': '2025-08-14', 'end': '2026-09-03'}
CUTOFF_UTC = datetime(2026, 10, 13, 16, tzinfo=timezone.utc)
# Process-local reuse during this one preparation. A controller importing this
# reader has no cached source frame, and re-reads/verifies the mutable view.
_PREPARATION_FRAME = None


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def artifact(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path)}


def bound(binding):
    path = Path(binding['path']).resolve()
    if sha(path) != binding['sha256']:
        raise ValueError('Retained source/software binding changed: ' + str(path))
    return path


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def stage(status, **details):
    write(OUT / 'stage.json', {'status': status,
                              'updated_at_utc': datetime.now(timezone.utc).isoformat(),
                              'official_spent': 0, 'new_economic_contexts': 0, **details})
    print(status, flush=True)


def check_cutoff():
    if datetime.now(timezone.utc) >= CUTOFF_UTC:
        raise ValueError('Day authorization cutoff reached; do not start a new source/preflight unit')


def read_lq_fixture(descriptor_path: Path) -> pd.DataFrame:
    """Trusted local reader, passed explicitly to build/verify, never by strings."""
    global _PREPARATION_FRAME
    descriptor_path = Path(descriptor_path).resolve()
    descriptor = read(descriptor_path)
    if descriptor.get('format') != 'lq01_retained_close_amount_reader_v1':
        raise ValueError('Unknown LQ fixture descriptor')
    for binding in descriptor['bindings'].values():
        bound(binding)
    if descriptor['input_window'] != INPUT_WINDOW:
        raise ValueError('LQ fixture input window changed')
    if _PREPARATION_FRAME is not None:
        return _PREPARATION_FRAME.copy()
    frame = read_mongo_gf_fixture(descriptor['bindings']['close_descriptor']['path'])
    digest = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
    if digest != descriptor['expected_original_close_view_sha256']:
        raise ValueError('Original retained full-peer close view changed')
    if (len(frame) != descriptor['expected_input_rows']
            or frame.symbol.nunique() != descriptor['expected_received_peers']):
        raise ValueError('Original full received input population changed')
    amount = pd.read_parquet(descriptor['bindings']['technical_amount']['path'],
                             columns=['date', 'code', 'amount'],
                             filters=[('date', '>=', 20240603), ('date', '<=', 20260903)])
    amount['date'] = pd.to_datetime(amount.date.astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    amount['code'] = amount.code.astype(str).str.zfill(6)
    amount = amount.rename(columns={'code': 'symbol'})
    if amount.duplicated(['date', 'symbol']).any():
        raise ValueError('Conflicting retained amount identities')
    frame = frame.merge(amount, on=['date', 'symbol'], how='left', validate='one_to_one')
    frame['date'] = pd.to_datetime(frame.date, errors='raise')
    frame['amount'] = pd.to_numeric(frame.amount, errors='coerce')
    if (frame.duplicated(['date', 'symbol']).any()
            or frame.amount.isna().sum() != descriptor['expected_amount_missing_rows']):
        raise ValueError('Retained full-peer amount support changed')
    return frame[['date', 'symbol', 'close', 'amount']].sort_values(
        ['date', 'symbol']).reset_index(drop=True)


def render_native(core):
    suffix = '''

class LQ01NativeConfirmation(Factor):
    def calculate(self, factors):
        # Historical interface confirmation only, not a live output contract.
        result = MarketCostShockProtection().calculate(
            {'close': factors['close'], 'amount': factors['amount']})
        dates = result.index.get_level_values('date')
        projected = result[(dates >= pd.Timestamp('2025-08-14'))
                           & (dates <= pd.Timestamp('2026-09-03'))]
        projected.name = 'value'
        return projected
'''
    code = core + suffix
    ast.parse(code)
    return code


def freeze_software():
    old = read(OLD / 'protocol.json')
    old_receipt = read(OLD / 'input_receipt.json')
    for key in ('descriptor', 'technical_bars', 'model', 'native_code'):
        bound(old['bindings'][key])
    calendar = ROOT / 'research_runs/night_research_20261011/native_gf_adapter_unit/input_calendar.json'
    library = Path(old['bindings']['model']['path'])
    core = library.read_bytes().decode('utf-8')
    original_native = Path(old['bindings']['native_code']['path']).read_bytes().decode('utf-8')
    if original_native.replace('class MarketCostShockProtection(Factor):',
                               'class MarketCostShockProtection:', 1) != core:
        raise ValueError('Independent library core no longer exactly matches original native core')
    # These are received-source descriptors, not new prices or return labels.
    descriptor = {'format': 'lq01_retained_close_amount_reader_v1',
                  'input_window': INPUT_WINDOW,
                  'bindings': {'close_descriptor': old['bindings']['descriptor'],
                               'technical_amount': old['bindings']['technical_bars']},
                  'expected_original_close_view_sha256': old['expected_price_view_hash'],
                  'expected_input_rows': old_receipt['input_frame_rows'],
                  'expected_received_peers': 5608,
                  'expected_amount_missing_rows': old_receipt['input_amount_missing_rows'],
                  'not_actual_native_export': True, 'amount_missing_never_filled': True}
    code = render_native(core)
    descriptor_path, code_path = OUT / 'fixture_descriptor.json', OUT / 'LQ01NativeConfirmation.py'
    code_bytes = code.encode('utf-8')
    for path, expected in ((descriptor_path, descriptor),):
        if path.exists() and read(path) != expected:
            raise ValueError('Existing source descriptor differs; do not overwrite it')
        if not path.exists():
            write(path, expected)
    if code_path.exists() and code_path.read_bytes() != code_bytes:
        raise ValueError('Existing fixed native adapter differs; do not overwrite it')
    if not code_path.exists():
        code_path.write_bytes(code_bytes)
    bindings = {'original_broad_protocol': artifact(OLD / 'protocol.json'),
                'original_broad_input_receipt': artifact(OLD / 'input_receipt.json'),
                'original_core': old['bindings']['model'],
                'original_native_code': old['bindings']['native_code'],
                'source_reader_descriptor': artifact(descriptor_path),
                'independent_input_calendar': artifact(calendar),
                'source_reader_runtime': artifact(__file__),
                'native_adapter_code': artifact(code_path),
                'preflight_harness': artifact(ROOT / 'panda_alpha/native_preflight.py'),
                'retained_public_loader': artifact(ROOT / 'research_runs/residual_commonality_20261006/public_source_audit/factor_loader.py'),
                'retained_public_macro': artifact(ROOT / 'research_runs/residual_commonality_20261006/public_source_audit/macro_factor.py'),
                'declared_field_catalog': artifact('C:/Users/31013/.codex/skills/pandaai-factor-online/references/fields.md')}
    protocol = {'id': 'LQ01_NATIVE_SOURCE_INTERFACE_PREPARATION_20261013',
                'kind': 'source_and_software_only', 'new_economic_contexts': 0,
                'original_economic_definition_changed': False, 'official_spent': 0,
                'input_window': INPUT_WINDOW, 'evaluation_window': EVALUATION_WINDOW,
                'holding_end': EVALUATION_WINDOW['end'], 'formation_dates': old['anchors'],
                'direction': 1, 'cycle': 5, 'groups': 10, 'bindings': bindings,
                'core_preservation': 'Original independent plain class bytes are the exact prefix of the adapter; old native differs only by injected Factor base.',
                'source_qualification_semantics': 'All received local evaluation index rows belong to this bound market-input source. expected_finite comes from unchanged model computation; NaNs remain NaNs. Membership of this index is not market/PIT eligibility certification.',
                'funding_authorization': False, 'registry_registration_performed': False,
                'native_dispatch_preparation_only': True, 'deadline_utc': CUTOFF_UTC.isoformat(),
                'limits': ['Only this fixed historical evaluation projection, not future live output.',
                           'The local received5608 peers include308 BJ. Remote Shanghai/Shenzhen pool and history peers can differ.',
                           'Retained StockDB as-of HFQ close and raw CNY amount are source proxies; actual native input numeric parity and tradeability/label cleaning are unverified.',
                           'No financial source literal/mask, no population selection by forward quotes or returns.',
                           'Old economic returns/cost paths/labels are not replayed by this preparation.']}
    protocol_path = OUT / 'protocol.json'
    if protocol_path.exists():
        if read(protocol_path) != protocol:
            raise ValueError('Frozen source-only software protocol changed; preserve and diagnose the exact difference')
    else:
        write(protocol_path, protocol)
    return protocol, descriptor_path, code, library


def verify_complete(receipt):
    for key in ('protocol', 'candidate', 'preflight', 'source_reader_evidence', 'source_qualification'):
        bound(receipt[key])
    preflight = read(receipt['preflight']['path'])
    if preflight.get('research_replay_passed') is not True:
        raise ValueError('Existing receipt is not a complete native projection preflight')
    print(json.dumps({'status': 'REUSE_COMPLETE_PREPARATION_NO_MODEL_REPLAY',
                      'fingerprint': receipt['fingerprint'], 'preflight': receipt['preflight']}, ensure_ascii=False))


def main():
    global _PREPARATION_FRAME
    OUT.mkdir(parents=True, exist_ok=True)
    complete_path = OUT / 'preparation_receipt.json'
    if complete_path.exists():
        verify_complete(read(complete_path))
        return
    check_cutoff()
    protocol, descriptor_path, code, library = freeze_software()
    for binding in protocol['bindings'].values():
        bound(binding)
    stage('SOURCE_ONLY_SOFTWARE_FROZEN', protocol=artifact(OUT / 'protocol.json'))
    try:
        _PREPARATION_FRAME = read_lq_fixture(descriptor_path)
        frame = _PREPARATION_FRAME.set_index(['date', 'symbol'])
        qualification_path = OUT / 'source_qualification.parquet'
        source_evidence_path = OUT / 'source_reader_evidence.json'
        if qualification_path.exists() and source_evidence_path.exists():
            source_evidence = read(source_evidence_path)
            bound(source_evidence['source_qualification'])
            current_hash = hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()
            if source_evidence['full_input_frame_sha256'] != current_hash:
                raise ValueError('Completed computability mask belongs to a changed input frame')
            stage('REUSE_COMPLETED_SOURCE_COMPUTABILITY_MASK')
        elif qualification_path.exists() or source_evidence_path.exists():
            raise ValueError('Uncommitted source mask/evidence exists; inspect before repeating any computation')
        else:
            stage('BUILD_UNCHANGED_CORE_COMPUTABILITY_MASK_ON_RECEIVED_INPUT')
            spec = importlib.util.spec_from_file_location('unchanged_lq_source_mask_core', library)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            value = module.MarketCostShockProtection().calculate({'close': frame.close, 'amount': frame.amount})
            dates = frame.index.get_level_values('date')
            keep = (dates >= pd.Timestamp(EVALUATION_WINDOW['start'])) & (dates <= pd.Timestamp(EVALUATION_WINDOW['end']))
            expected = frame.index[keep]
            if not value.index.equals(frame.index):
                raise ValueError('Unchanged core does not preserve received input index')
            values = value.to_numpy(dtype=float)[keep]
            mask = pd.DataFrame({'date': expected.get_level_values('date'),
                                 'symbol': expected.get_level_values('symbol'),
                                 'source_qualified': np.ones(len(expected), dtype=bool),
                                 'expected_finite': np.isfinite(values)})
            temporary = qualification_path.with_suffix('.tmp')
            mask.to_parquet(temporary, index=False, compression='zstd')
            temporary.replace(qualification_path)
            source_evidence = {'status': 'REAL_RECEIVED_INPUT_AND_UNCHANGED_CORE_MASK_BOUND',
                               'protocol': artifact(OUT / 'protocol.json'),
                               'source_qualification': artifact(qualification_path),
                               'full_input_rows': len(frame), 'full_received_peers': int(frame.index.get_level_values('symbol').nunique()),
                               'amount_missing_rows': int(frame.amount.isna().sum()),
                               'full_input_frame_sha256': hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest(),
                               'evaluation_rows': len(expected), 'finite_evaluation_rows': int(np.isfinite(values).sum()),
                               'not_actual_native_export': True, 'native_peer_value_parity_certified': False,
                               'native_worker_padding_certified': False, 'full_PIT_certified': False,
                               'forward_quotes_or_return_labels_read': False,
                               'old_economic_replay_performed': False, 'new_economic_contexts': 0, 'official_spent': 0}
            write(source_evidence_path, source_evidence)
            del module, value, values, mask
            gc.collect()
        contract = {'input_window': INPUT_WINDOW, 'evaluation_window': EVALUATION_WINDOW,
                    'holding_end': EVALUATION_WINDOW['end'], 'formation_dates': protocol['formation_dates'],
                    'warmup': {'minimum_prior_price_rows': 258},
                    'input_calendar': protocol['bindings']['independent_input_calendar'],
                    'source_qualification': artifact(qualification_path)}
        candidate = {'candidate_id': 'LQ01_NATIVE_DOMAIN_CONFIRMATION_20261013',
                     'code': code, 'direction': 1, 'category': 'exploration',
                     'experiment_category': 'exploration', 'official_task': 'native_screen',
                     'budget_mechanism_id': 'market_cost_shock_protection',
                     'native_preflight_purpose': 'source_qualified_projection_research',
                     'native_evaluation_contract': contract,
                     'native_preflight_path': str((OUT / 'preflight.json').resolve()),
                     'research_question': 'Does the unchanged signed market-cost-shock beta show useful ranking and group economics under actual native market peers and server data/label cleaning in the same fixed historical evaluation window?',
                     'official_information_gap': 'The broad local model inputs are available, but native Shanghai/Shenzhen peer history, actual close/amount basis and server trading/label cleaning are not numerically certified; local forward fill/status repair is incomplete.',
                     'decision_if_pass': 'Retain the complete native source-domain result and audit costs, risk and actual support before any candidate promotion; this historical projection alone does not establish future production or PIT.',
                     'decision_if_fail': 'Close this exact native source-domain attempt on complete economic evidence; lock the same Run for software/source/ambiguous failure without automatic retry, sign reversal or window/weight scan.',
                     'data_requirements': ['full_a_coverage_pending', 'formal_admission_pending', 'official_pool_increment_pending'],
                     'research_only': True, 'formal_admission': False, 'future_production_ready': False,
                     'native_remote_peer_parity_certified': False, 'full_PIT_certified': False}
        candidate_path = OUT / 'candidate.json'
        if candidate_path.exists() and read(candidate_path) != candidate:
            raise ValueError('Existing exact candidate changed; do not overwrite it')
        if not candidate_path.exists():
            write(candidate_path, candidate)
        preflight_path = OUT / 'preflight.json'
        if preflight_path.exists():
            existing = read(preflight_path)
            if existing.get('research_replay_passed') is True:
                # A completed check must be collected, not recalculated blindly.
                preflight = existing
            else:
                raise ValueError('Prior preflight failure retained; diagnose it before any repeat')
        else:
            check_cutoff()
            stage('BUILD_FULL_REAL_WRAPPED_NATIVE_PREFLIGHT_ONCE')
            preflight = build_native_preflight(
                candidate, INPUT_WINDOW, 5, 10, descriptor_path,
                [OUT / 'protocol.json', source_evidence_path,
                 protocol['bindings']['independent_input_calendar']['path'], qualification_path],
                [library, OUT / 'LQ01NativeConfirmation.py', __file__,
                 ROOT / 'docs/panda_alpha_native_contract.md',
                 protocol['bindings']['retained_public_loader']['path'],
                 protocol['bindings']['retained_public_macro']['path'],
                 protocol['bindings']['declared_field_catalog']['path']], preflight_path,
                fields=['close', 'amount'], fixture_reader=read_lq_fixture)
        key = fingerprint(candidate, INPUT_WINDOW, 5, 10)
        if preflight.get('research_replay_passed') is not True:
            stage('REAL_NATIVE_PREFLIGHT_PENDING', failure=preflight.get('failure'))
            raise ValueError('Actual wrapped full-input preflight did not pass; no paid dispatch permitted')
        if preflight.get('definition_fingerprint') != key:
            raise ValueError('Completed native preflight fingerprint differs from exact candidate')
        receipt = {'status': 'REAL_NATIVE_PROJECTION_PREFLIGHT_COMPLETE_NO_DISPATCH',
                   'protocol': artifact(OUT / 'protocol.json'), 'candidate': artifact(candidate_path),
                   'preflight': artifact(preflight_path), 'source_reader_evidence': artifact(source_evidence_path),
                   'source_qualification': artifact(qualification_path),
                   'native_adapter_code': artifact(OUT / 'LQ01NativeConfirmation.py'),
                   'code_sha256': hashlib.sha256(code.encode('utf-8')).hexdigest(), 'fingerprint': key,
                   'input_window': INPUT_WINDOW, 'evaluation_window': EVALUATION_WINDOW,
                   'direction': 1, 'cycle': 5, 'groups': 10,
                   'trusted_reader': 'scripts.prepare_lq_native_confirmation.read_lq_fixture',
                   'source_only_new_economic_contexts': 0, 'official_spent': 0,
                   'official_calls': 0, 'pool_operations': 0,
                   'production_ready': False, 'full_PIT_certified': False,
                   'native_remote_input_parity_certified': False,
                   'funding_authorization': False, 'economic_dispatch_registered_by_this_unit': False}
        write(complete_path, receipt)
        stage(receipt['status'], fingerprint=key, preflight=receipt['preflight'])
        print(json.dumps(receipt, ensure_ascii=False))
    except Exception as error:
        stage('SOURCE_OR_INTERFACE_PENDING', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        _PREPARATION_FRAME = None
        gc.collect()


if __name__ == '__main__':
    main()
