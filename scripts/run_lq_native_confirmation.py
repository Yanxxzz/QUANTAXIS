"""One prepared LQ native confirmation within the human's midnight scope."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.platform import PandaClient, ExperimentLedger, dispatch, resume, fingerprint
from panda_alpha.registry import ResearchRegistry
from prepare_lq_native_confirmation import read_lq_fixture

UNIT = ROOT / 'research_runs/day_research_20261013/lq_native_confirmation'
DAY = UNIT.parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def bound(item):
    path = Path(item['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('Exact prepared artifact changed: ' + str(path))
    return path


def before_deadline(deadline):
    if datetime.now(timezone.utc) >= deadline:
        raise ValueError('Human midnight cutoff reached; no new reservation/create/start')


class DeadlineClient(PandaClient):
    def __init__(self, config, deadline):
        super().__init__(config)
        self.deadline = deadline

    def create(self, *args, **kwargs):
        before_deadline(self.deadline)
        return super().create(*args, **kwargs)

    def start(self, *args, **kwargs):
        before_deadline(self.deadline)
        return super().start(*args, **kwargs)


def grant_for(authority, plan):
    if (authority.get('status') != 'APPROVED_BY_HUMAN'
            or authority.get('source') != 'direct_user_reply_in_current_chat'
            or not authority.get('human_answer')):
        raise ValueError('Concrete recharge/estimated-charge approval is pending')
    grant = authority['grant']
    if (grant['batch_id'] != 'DAY_NEW_DIRECTION30_20261013'
            or grant['fingerprints'] != [plan['fingerprint']]
            or grant['max_runs'] != 1
            or grant['accept_estimated_charge_risk'] is not True
            or not 0 <= grant['max_recharge_credits'] <= 8
            or not 8 <= grant['max_total_credits'] <= 30):
        raise ValueError('Permission does not match this one prepared LQ test')
    return grant


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['audit', 'dispatch', 'collect'])
    parser.add_argument('--plan-sha', required=True)
    args = parser.parse_args()
    path = UNIT / 'batch_plan.json'
    if hashlib.sha256(path.read_bytes()).hexdigest() != args.plan_sha:
        raise ValueError('Prepared LQ batch plan changed')
    plan = read(path)
    candidate = read(bound(plan['candidate']))
    bound(plan['preflight'])
    key = fingerprint(candidate, plan['window'], plan['cycle'], plan['groups'])
    if key != plan['fingerprint']:
        raise ValueError('Executable/window fingerprint changed')
    authority = read(DAY / 'authorization.json')
    if (authority['scope_id'] != 'DAY_NEW_DIRECTION30_20261013'
            or authority['max_total_credits'] != 30
            or authority['deadline_utc'] != '2026-10-13T16:00:00+00:00'):
        raise ValueError('Human day scope has changed; review latest instruction')
    deadline = datetime.fromisoformat(authority['deadline_utc'])
    cfg = read(ROOT / 'config/panda-alpha.local.json')
    cfg['research'].update(cycle=plan['cycle'], groups=plan['groups'])
    cfg['compute'] = dict(cfg['compute'])
    cfg['compute'].update(daily_credit_limit=30, recharge_credit_limit=0,
        planning_credit_estimate=8, cost_ceiling=None, cost_ceiling_verified_at=None,
        cost_ceiling_source=None, server_enforced_cap=False,
        billing_mode='gift_priority', allow_estimated_billing=True,
        estimated_billing_batch_authorization=None, recharge_batch_authorization=None,
        allocation_mode='shared_priority')
    if args.action == 'dispatch':
        before_deadline(deadline)
        batch_authority = read(UNIT / 'funding_authorization.json')
        grant = grant_for(batch_authority, plan)
        cfg['compute'].update(recharge_credit_limit=grant['max_recharge_credits'],
            estimated_billing_batch_authorization=grant,
            recharge_batch_authorization=grant,
            planning_cost_source='Owned same-interface settlements2–8; conservative8 estimate, not server ceiling',
            estimate_verified_at=batch_authority['confirmed_at_utc'][:10])
    ledger = ExperimentLedger(ROOT / 'research_runs/experiments.sqlite3')
    try:
        existing = ledger.get(key)
        if args.action == 'audit':
            print(json.dumps({'fingerprint': key, 'existing_state': existing.get('state') if existing else None,
                              'recharge_approved_in_day_scope': authority.get('confirmed_recharge_credits', 0),
                              'deadline_utc': authority['deadline_utc'], 'account_calls': 0,
                              'dispatch_performed': False}))
            return
        client = DeadlineClient(cfg['platform'], deadline)
        if args.action == 'collect':
            if not existing:
                raise ValueError('There is no LQ Run to collect')
            job = existing if existing['state'] == 'SETTLED' else resume(key, ledger, client, UNIT / 'official_results')
        elif existing:
            print('Exact job already exists; collect this Run, never dispatch a duplicate')
            return
        else:
            def register():
                before_deadline(deadline)
                r = ResearchRegistry(ROOT / 'research_runs/research_trials.sqlite3')
                try:
                    r.record(candidate, plan['window'], plan['cycle'], plan['groups'])
                    r.append_event('evaluation', candidate['candidate_id'], {
                        'phase': 'ABOUT_TO_DISPATCH_NATIVE_LQ_SOURCE_SCOPE_CONFIRMATION',
                        'fingerprint': key, 'new_contexts_from_event': 0,
                        'budget_scope': authority['scope_id'], 'actual_native_peers_unknown': True},
                        event_id='native-lq-dispatch:' + key)
                finally:
                    r.close()
            job = dispatch(candidate, plan['window'], cfg, ledger, client, 'exploration',
                           before_dispatch=register, native_fixture_reader=read_lq_fixture)
        compact = {k: job.get(k) for k in ['candidate_id', 'fingerprint', 'state', 'reserved', 'actual', 'factor_id', 'run_id']}
        (UNIT / 'official_progress.json').write_text(json.dumps(compact, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(compact, ensure_ascii=False))
    finally:
        ledger.db.close()


if __name__ == '__main__':
    main()
