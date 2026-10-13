"""Register the human-requested member replacements before new signal evaluation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.registry import ResearchRegistry

OLD = ROOT / 'research_runs/gf_resume_20261012/admission_completion'
OUT = ROOT / 'research_runs/gf_member_replacement_20261013'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def artifact(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return {'path': str(path), 'sha256': h.hexdigest()}


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def main():
    OUT.mkdir(exist_ok=True)
    target = OUT / 'protocol.json'
    if target.exists():
        raise ValueError('Protocol already frozen; inspect it instead of registering again')
    old = read(OLD / 'test_protocol.json')
    summary = read(OLD / 'test_summary.json')
    assert summary['status'] == 'ALL_REMAINING_LOCAL_PATHS_COMPLETE' and not summary['errors']
    assert artifact(OLD / 'test_protocol.json')['sha256'] == summary['protocol_sha256']
    assert old['pools']['RETAINED_POOL'] == ['C03', 'C04', 'C16', 'C17', 'F253']
    for item in [*old['bindings'].values(), *old['definitions'].values()]:
        if artifact(item['path']) != item:
            raise ValueError('Original bound source/runtime changed: ' + item['path'])
    bindings = {name: artifact(path) for name, path in {
        'original_protocol': OLD / 'test_protocol.json',
        'original_input_receipt': OLD / 'test_input_receipt.json',
        'original_pool_signals': OLD / 'pool_formation_signals.parquet',
        'original_summary': OLD / 'test_summary.json',
        'original_daily_ledger': OLD / 'test_daily_net_ledger.parquet',
        'runtime': ROOT / 'scripts/run_gf_member_replacement.py',
        'freeze_runtime': Path(__file__),
    }.items()}
    base = old['pools']['RETAINED_POOL']
    changes = {
        'RETAINED_REPLACE_C03_GF': ['GF' if n == 'C03' else n for n in base],
        'RETAINED_REPLACE_C04_GF': ['GF' if n == 'C04' else n for n in base],
        'RETAINED_REMOVE_C03_DIAGNOSTIC': [n for n in base if n != 'C03'],
        'RETAINED_REMOVE_C04_DIAGNOSTIC': [n for n in base if n != 'C04'],
    }
    contract = {
        'original_protocol': bindings['original_protocol'],
        'original_shared_signal_support': bindings['original_pool_signals'],
        'source_scope': 'Exactly the old 32892 date/symbol keys; no mask expansion, future quote selection or new source population',
        'signal_rule': old['protocol_contract']['pool'],
        'baseline_reused_not_replayed': True,
        'new_signal_components': 'Only original C03/C04 full-peer calculations; GF direction-Z recovered from frozen old add/base scores and checked against direct original GF Z',
        'source_limits': old['protocol_contract']['limits'] + [
            'Original5608 full-price peers include308 BJ securities; official own definitions use Shanghai/Shenzhen all-A',
            'TURNOVER units, epsilon and observed-row operator/native parity remain unverified',
            'F253 already contains counterparts of C03 and C04; component inclusion alone is not independent alpha',
        ],
    }
    contexts = [{'candidate_id': 'GF_MEMBER_' + name + '_20261013', 'direction': 1,
                 'strategy_spec': {'kind': 'fixed_member_replacement_or_leave_one_out',
                                   'contract': contract, 'members': members,
                                   'comparison_name': name,
                                   'four_member_diagnostic_not_live_pool': len(members) == 4}}
                for name, members in changes.items()]
    protocol = {
        'id': 'GF_C03_C04_MEMBER_REPLACEMENT_20261013',
        'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
        'human_instruction': '定位一下原因，完成替换对照',
        'bindings': bindings, 'anchors': old['anchors'], 'window': old['window'],
        'costs': old['costs'], 'direction': 1, 'cycle': 5, 'groups': 10,
        'expected_shared_rows': 32892, 'pools': changes, 'contexts': contexts,
        'protocol_contract': contract,
        'analysis': {
            'paired_price_fee_accounting': 'Delta finalNAV equals delta cumulative pricePnL minus delta fees, all measured in the same initialNAV1 units',
            'new_run_attribution': 'Aggregate per-security and contemporaneous known-sector contribution with UNKNOWN retained; never manufacture lost old baseline identity attribution',
            'selection_change': 'Same formation support; target overlap and entered/dropped subsets are descriptive, not proof of executed stock-level causal alpha',
            'paired_uncertainty': 'Resample complete51 rebalance blocks of paired daily log returns,10000 draws,seed20261013,95% percentile interval; retrospective descriptive uncertainty,not formal family-adjusted significance',
            'uniform_complete_result_review': True,
            'no_weight_sign_window_scan': True,
        },
        'baseline_names_reused': ['RETAINED_POOL', 'RETAINED_ADD_GF', 'RETAINED_REPLACE_C16_GF'],
        'official_runs_authorized': 0, 'pool_operations_authorized': 0,
        'night_automation_resumed': False, 'cancelled_2023_preserved': True,
        'new_contexts': 4, 'formal_admission': False,
    }
    save(target, protocol)
    registry = ResearchRegistry(ROOT / 'research_runs/research_trials.sqlite3')
    try:
        before = registry.total
        for candidate in contexts:
            registry.record(candidate, {'start': old['window']['holding_start'],
                                        'end': old['window']['holding_end']}, 5, 10)
        count = registry.total - before
        if count != 4:
            raise ValueError('Expected four new fixed contexts, not duplicate registrations')
        proof = artifact(target)
        registry.append_event('evaluation', protocol['id'], {
            'status': 'FROZEN_BEFORE_NEW_SIGNAL_AND_RESULT_EVALUATION',
            'protocol': proof, 'new_contexts': count, 'official_runs': 0,
            'four_member_diagnostics_only': True,
        }, event_id='gf-member-replacement-freeze:' + proof['sha256'])
        state = {'status': 'FROZEN_WAITING_EXECUTION', 'protocol': proof,
                 'actual_pid': None, 'research_total': registry.total,
                 'registry_head': registry.head, 'official_spent': 0,
                 'automation_status': 'PAUSED', 'next': 'Run exactly the frozen four contexts; no baseline replay'}
        save(OUT / 'state.json', state)
        print(json.dumps(state, ensure_ascii=False))
    finally:
        registry.close()


if __name__ == '__main__':
    main()
