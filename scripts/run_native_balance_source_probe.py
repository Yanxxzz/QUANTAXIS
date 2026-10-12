"""Explicitly approved single diagnostic, with a cutoff at each mutation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panda_alpha.platform import (
    PandaClient, ExperimentLedger, dispatch, resume, fingerprint,
    read_experiment_jobs, read_research_state, budget_plan,
)
from panda_alpha.research_budget import load_policy, request, guard
from panda_alpha.registry import ResearchRegistry

UNIT = ROOT / 'research_runs/night_research_20261013/native_balance_source_probe'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def bound(item):
    path = Path(item['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('Frozen batch artifact changed: ' + str(path))
    return path


def before_cutoff(deadline):
    if datetime.now(timezone.utc) >= deadline:
        raise ValueError('Night cutoff reached; no new reservation/create/start')


class CutoffClient(PandaClient):
    def __init__(self, config, deadline):
        super().__init__(config)
        self.deadline = deadline

    def create(self, *args, **kwargs):
        before_cutoff(self.deadline)
        return super().create(*args, **kwargs)

    def start(self, *args, **kwargs):
        before_cutoff(self.deadline)
        return super().start(*args, **kwargs)


def approved_grant(authority, plan):
    if (authority.get('status') != 'APPROVED_BY_HUMAN'
            or authority.get('source') != 'direct_user_reply_in_current_chat'
            or not authority.get('human_answer')):
        raise ValueError('Concrete human recharge/risk approval is still pending')
    grant = authority['grant']
    if (grant.get('batch_id') != plan['batch_id']
            or grant.get('fingerprints') != [plan['fingerprint']]
            or grant.get('max_runs') != 1
            or grant.get('accept_estimated_charge_risk') is not True
            or not 0 <= grant.get('max_recharge_credits', -1) <= 8
            or not 8 <= grant.get('max_total_credits', -1) <= 20):
        raise ValueError('Single diagnostic funding scope does not match')
    return grant


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['audit', 'dispatch', 'collect'])
    parser.add_argument('--plan-sha', required=True)
    args = parser.parse_args()
    path = UNIT / 'batch_plan.json'
    if hashlib.sha256(path.read_bytes()).hexdigest() != args.plan_sha:
        raise ValueError('Exact batch plan changed')
    plan = read(path)
    candidate = read(bound(plan['candidate']))
    bound(plan['preflight'])
    key = fingerprint(candidate, plan['window'], plan['cycle'], plan['groups'])
    if key != plan['fingerprint']:
        raise ValueError('Exact executable/window fingerprint changed')
    deadline = datetime.fromisoformat(plan['cutoff_utc'])
    cfg = read(ROOT / 'config/panda-alpha.local.json')
    cfg['research'].update(cycle=plan['cycle'], groups=plan['groups'])
    compute = dict(cfg['compute'])
    # Never inherit a previously consumed authorization from the local config.
    compute.update(daily_credit_limit=20, recharge_credit_limit=0,
                   planning_credit_estimate=8, cost_ceiling=None,
                   cost_ceiling_verified_at=None, cost_ceiling_source=None,
                   server_enforced_cap=False, billing_mode='gift_priority',
                   allow_estimated_billing=True,
                   estimated_billing_batch_authorization=None,
                   recharge_batch_authorization=None)
    cfg['compute'] = compute
    ledger_path = ROOT / 'research_runs/experiments.sqlite3'
    if args.action == 'audit':
        jobs = read_experiment_jobs(ledger_path)
        state = read_research_state(ledger_path)
        policy = load_policy(compute)
        meta = request(policy, candidate, 'source_probe')
        review = guard(policy, meta, jobs, state, 8)
        funds = budget_plan([dict(candidate, experiment_category='source_probe')],
                            compute, plan['funding']['current_wallet'],
                            jobs=jobs, study_state=state)
        print(json.dumps({'research_blocker': review['blocker'],
                          'funding_ready': funds['dispatch_ready'],
                          'funding_blocker': funds['blocker'],
                          'account_calls': 0, 'reservations': 0}))
        return
    if args.action == 'dispatch':
        before_cutoff(deadline)
        authority = read(UNIT / 'authorization.json')
        grant = approved_grant(authority, plan)
        compute.update(recharge_credit_limit=grant['max_recharge_credits'],
                       estimated_billing_batch_authorization=grant,
                       recharge_batch_authorization=grant,
                       planning_cost_source='Conservative8 reserve from owned prior2–8 observations; not server cap',
                       estimate_verified_at=authority['confirmed_at_utc'][:10])
    client = CutoffClient(cfg['platform'], deadline)
    ledger = ExperimentLedger(ledger_path)
    try:
        existing = ledger.get(key)
        if args.action == 'collect':
            if not existing:
                raise ValueError('No existing diagnostic to collect')
            job = existing if existing['state'] == 'SETTLED' else resume(key, ledger, client, UNIT / 'official_results')
        elif existing:
            print('Exact job already exists; inspect/collect, never redispatch')
            return
        else:
            def register():
                before_cutoff(deadline)
                registry = ResearchRegistry(ROOT / 'research_runs/research_trials.sqlite3')
                try:
                    registry.record(candidate, plan['window'], plan['cycle'], plan['groups'])
                    registry.append_event('evaluation', candidate['candidate_id'],
                        {'phase': 'ABOUT_TO_DISPATCH_NATIVE_SOURCE_DIAGNOSTIC',
                         'fingerprint': key, 'new_contexts': 0},
                        event_id='native-balance-source-dispatch:' + key)
                finally:
                    registry.close()
            job = dispatch(candidate, plan['window'], cfg, ledger, client, 'source_probe', before_dispatch=register)
        compact = {k: job.get(k) for k in ['candidate_id', 'fingerprint', 'state', 'reserved', 'actual', 'factor_id', 'run_id']}
        (UNIT / 'official_progress.json').write_text(json.dumps(compact, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(compact, ensure_ascii=False))
    finally:
        ledger.db.close()


if __name__ == '__main__':
    main()
