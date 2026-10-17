"""Audited cap amendments preserve grandfathered usage and execution gates."""
from copy import deepcopy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from panda_alpha.platform import ExperimentLedger
from panda_alpha.research_budget import (amend_policy, amendment_snapshot, bind_policy,
                                         guard, read_state, record_review, usage)
from panda_alpha.team.board import Board
from panda_alpha.team.__main__ import main


POLICY = {"study_id": "existing_search", "max_total_credits": 60, "max_runs": 20,
          "review_interval_credits": 20, "max_initial_runs_per_mechanism": 2,
          "max_runs_per_mechanism": 4}


def add_job(ledger, key, actual=2, study="existing_search", state="SETTLED"):
    definition = {"research_budget": {"study_id": study, "limits": POLICY,
                  "mechanism_id": key, "family_id": key, "task": "native_screen"}}
    ledger.db.execute("INSERT INTO jobs (fingerprint,candidate_id,state,reserved,actual,definition,run_id,receipt) "
                      "VALUES (?,?,?,?,?,?,?,?)", (key, key, state, 8, actual,
                      json.dumps(definition), "original-run-" + key, '{"original":true}'))


@pytest.fixture
def prepared(tmp_path):
    path = tmp_path / "original.sqlite3"
    ledger = ExperimentLedger(path)
    bind_policy(ledger.db, POLICY)
    for n in range(25):
        add_job(ledger, f"study-{n}", actual=6 if n == 24 else 2)
    for n in range(8):
        add_job(ledger, f"other-{n}", actual=3, study="older_search")
    for milestone in (20, 40):
        ledger.db.execute("INSERT INTO research_budget_reviews VALUES (?,?)",
                          (str(milestone), json.dumps({"milestone": milestone, "decision": "continue"})))
    approval = tmp_path / "approval.txt"
    approval.write_text("Synthetic test operator authorizes only this fixture's same-study 86/29 amendment.")
    change = {"amendment_id": "extend-existing", "status": "authorized", "ledger_path": str(path),
              "expected_snapshot": amendment_snapshot(ledger),
              "new_policy": dict(POLICY, max_total_credits=86, max_runs=29),
              "reason": "Four bounded follow-ups; keep all prior attempts and review rules",
              "approval": {"path": str(approval), "sha256": hashlib.sha256(approval.read_bytes()).hexdigest()}}
    yield ledger, change
    ledger.db.close()


def test_preserves_all_original_jobs_reviews_grants_and_pause(prepared):
    ledger, change = prepared
    board = Board(ledger)
    board.pause("User stopped before official execution")
    ledger.db.execute("INSERT INTO team_grants VALUES ('old','old-hash','{}')")
    jobs, reviews, control = ledger.jobs(), read_state(ledger.db)["reviews"], board.mode()
    result = amend_policy(ledger, change, execute=True)
    assert result["recorded"] and not result["funding_authorization"]
    assert result["before"]["usage"] == result["after"]["usage"] == {"credits": 54.0, "runs": 25, "unsettled": 0}
    assert result["after"]["all_job_count"] == 33
    assert ledger.jobs() == jobs and read_state(ledger.db)["reviews"] == reviews
    assert board.mode() == control
    assert tuple(ledger.db.execute("SELECT * FROM team_grants").fetchone()) == ('old', 'old-hash', '{}')
    assert read_state(ledger.db)["policy"] == change["new_policy"]
    assert guard(change["new_policy"], None, jobs, read_state(ledger.db), 8)["blocker"] is None


def test_preview_is_read_only_and_does_not_initialize_team_or_audit(prepared, capsys):
    ledger, change = prepared
    change["status"] = "draft"
    path = change["ledger_path"]
    data = Path(path).with_suffix('.json')
    data.write_text(json.dumps(change))
    before = amendment_snapshot(ledger)
    tables = list(ledger.db.execute("SELECT name FROM sqlite_master ORDER BY name"))
    main(["amend-budget", "--ledger", path])
    assert json.loads(capsys.readouterr().out) == before
    main(["amend-budget", "--ledger", path, "--input", str(data)])
    assert not json.loads(capsys.readouterr().out)["recorded"]
    assert amendment_snapshot(ledger) == before
    assert list(ledger.db.execute("SELECT name FROM sqlite_master ORDER BY name")) == tables
    readonly = ExperimentLedger(path, read_only=True)
    try:
        assert not amend_policy(readonly, change)["recorded"]
    finally:
        readonly.db.close()


def test_draft_is_not_applicable_and_missing_ledger_is_not_created(prepared, tmp_path):
    ledger, change = prepared
    change["status"] = "draft"
    with pytest.raises(ValueError, match="explicit authorization"):
        amend_policy(ledger, change, execute=True)
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(SystemExit):
        main(["amend-budget", "--ledger", str(missing), "--input", 'unused.json'])
    assert not missing.exists()


def test_idempotent_replay_does_not_restore_old_usage_or_policy(prepared):
    ledger, change = prepared
    first = amend_policy(ledger, change, execute=True)
    add_job(ledger, "later", actual=2)
    repeated = amend_policy(ledger, change, execute=True)
    assert repeated["idempotent"] and repeated["request_hash"] == first["request_hash"]
    assert usage(ledger.jobs(), POLICY["study_id"])["runs"] == 26
    assert ledger.db.execute("SELECT count(*) FROM research_budget_amendments").fetchone()[0] == 1
    changed = dict(change, reason="different request under same ID")
    with pytest.raises(ValueError, match="different content"):
        amend_policy(ledger, changed, execute=True)


@pytest.mark.parametrize("mutation", ["policy", "charge", "receipt", "review", "additional_job"])
def test_stale_snapshot_rejected_inside_transaction(prepared, mutation):
    ledger, change = prepared
    if mutation == "policy":
        ledger.db.execute("UPDATE research_budget_control SET policy=?", (json.dumps(dict(POLICY, max_runs=21)),))
    elif mutation == "charge":
        ledger.db.execute("UPDATE jobs SET actual=7 WHERE fingerprint='study-24'")
    elif mutation == "receipt":
        ledger.db.execute("UPDATE jobs SET receipt='updated evidence' WHERE fingerprint='study-24'")
    elif mutation == "review":
        ledger.db.execute("INSERT INTO research_budget_reviews VALUES ('extra','{}')")
    else:
        add_job(ledger, "new")
    with pytest.raises(ValueError, match="Stale"):
        amend_policy(ledger, change, execute=True)
    assert not ledger.db.in_transaction


@pytest.mark.parametrize("replacement", [
    {"study_id": "fresh_search"}, {"review_interval_credits": 30},
    {"max_initial_runs_per_mechanism": 3}, {"max_runs_per_mechanism": 8},
    {"max_total_credits": 59}, {"max_runs": 24}, {"max_runs": True},
    {"max_total_credits": 60, "max_runs": 20}])
def test_caps_only_no_renaming_weakening_or_excluding_history(prepared, replacement):
    ledger, change = prepared
    change["new_policy"].update(replacement)
    with pytest.raises(ValueError):
        amend_policy(ledger, change, execute=True)
    assert read_state(ledger.db)["policy"] == POLICY


def test_approval_hash_and_ledger_path_are_bound(prepared):
    ledger, change = prepared
    other = deepcopy(change)
    other['ledger_path'] += '.copy'
    with pytest.raises(ValueError, match="different ledger"):
        amend_policy(ledger, other, execute=True)
    change['approval']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        amend_policy(ledger, change, execute=True)
    assert read_state(ledger.db)['policy'] == POLICY


@pytest.mark.parametrize("blocker", ["study_unsettled", "other_unsettled", "active_team"])
def test_unsettled_jobs_and_active_team_must_be_stopped(prepared, blocker):
    ledger, change = prepared
    if blocker == 'active_team':
        Board(ledger)
        ledger.db.execute("UPDATE team_control SET mode='pilot'")
    else:
        add_job(ledger, 'pending', actual=None, study='existing_search' if blocker == 'study_unsettled' else 'older_search', state='RUNNING')
        change['expected_snapshot'] = amendment_snapshot(ledger)
    with pytest.raises(ValueError, match='Reconcile|Pause'):
        amend_policy(ledger, change, execute=True)


def test_audit_is_append_only_and_failed_append_rolls_back_policy(prepared):
    ledger, change = prepared
    ledger.db.execute("CREATE TABLE research_budget_amendments (amendment_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, payload TEXT NOT NULL)")
    ledger.db.execute("CREATE TRIGGER inject_failure BEFORE INSERT ON research_budget_amendments BEGIN SELECT RAISE(ABORT,'audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='audit failure'):
        amend_policy(ledger, change, execute=True)
    assert read_state(ledger.db)['policy'] == POLICY
    assert ledger.db.execute('SELECT count(*) FROM research_budget_amendments').fetchone()[0] == 0
    ledger.db.execute('DROP TRIGGER inject_failure')
    amend_policy(ledger, change, execute=True)
    for sql in ("UPDATE research_budget_amendments SET payload='{}'", 'DELETE FROM research_budget_amendments'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            ledger.db.execute(sql)


def test_sixty_credit_review_and_new_end_limits_still_apply(prepared):
    ledger, change = prepared
    amend_policy(ledger, change, execute=True)
    for n in range(3):
        add_job(ledger, f'followup-{n}', actual=2)
    policy = change['new_policy']
    assert guard(policy, None, ledger.jobs(), read_state(ledger.db), 8)['blocker'] == 'Free research review required at 60 credits'
    evidence = {'study_id': policy['study_id'], 'decision': 'continue', 'findings': 'Reviewed all billed attempts',
                'next_steps': 'One bounded final comparison', 'artifacts': [change['approval']]}
    record_review(ledger, policy, evidence, execute=True)
    assert guard(policy, None, ledger.jobs(), read_state(ledger.db), 8)['blocker'] is None
    assert guard(policy, None, ledger.jobs(), read_state(ledger.db), 27)['blocker'] == 'Shared study credit limit reached'
    add_job(ledger, 'final', actual=2)
    assert guard(policy, None, ledger.jobs(), read_state(ledger.db), 8)['blocker'] == 'Shared study run limit reached'


def test_local_only_review_is_not_cleared_by_more_budget(prepared):
    ledger, change = prepared
    ledger.db.execute("UPDATE research_budget_reviews SET payload=? WHERE review_id='40'",
                      (json.dumps({'milestone': 40, 'decision': 'local_only'}),))
    change['expected_snapshot'] = amendment_snapshot(ledger)
    amend_policy(ledger, change, execute=True)
    assert guard(change['new_policy'], None, ledger.jobs(), read_state(ledger.db), 8)['blocker'] == 'Latest research review chose local_only'


def test_two_connections_cannot_apply_the_same_old_snapshot_twice(prepared):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    ledger, change = prepared
    barrier = Barrier(2)
    def apply(number):
        connection = ExperimentLedger(change['ledger_path'])
        proposal = dict(change, amendment_id=f'concurrent-{number}')
        try:
            barrier.wait(timeout=10)
            try:
                amend_policy(connection, proposal, execute=True)
                return 'applied'
            except ValueError as exc:
                assert 'Stale' in str(exc)
                return 'stale'
        finally:
            connection.db.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(apply, range(2)))
    assert sorted(results) == ['applied', 'stale']
    assert ledger.db.execute('SELECT count(*) FROM research_budget_amendments').fetchone()[0] == 1
    assert amendment_snapshot(ledger)['usage'] == change['expected_snapshot']['usage']
