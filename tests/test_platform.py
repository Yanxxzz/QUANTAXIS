import pytest

from panda_alpha.platform import ExperimentLedger, beijing_day, budget_plan, dispatch, fingerprint, resume, settle_receipt


C = {"candidate_id": "P1", "formula": "RANK(close)", "direction": 1}
W = {"start": "2024-01-01", "end": "2024-06-30"}
B = {"total": 388, "gift": 10, "recharge": 378}


def config():
    return {"research": {"cycle": 5, "groups": 10}, "compute": {
        "daily_credit_limit": 10, "recharge_credit_limit": 0, "cost_ceiling": 2,
        "cost_ceiling_verified_at": "now", "cost_ceiling_source": "test backend contract",
        "server_enforced_cap": True, "billing_mode": "gift_only",
        "source_probe_fraction": 0.3, "exploration_fraction": 0.4, "validation_fraction": 0.3}}


class FakeClient:
    calls = 0
    started = 0
    remaining = B

    def balance(self): return self.remaining
    def create(self, *args): self.calls += 1; return "f1"
    def start(self, factor_id): self.started += 1; return "r1"
    def status(self, run_id): return 2
    def result(self, run_id): return {"test": True}


def test_gift_only_without_server_cap_is_pending():
    compute = config()["compute"]
    compute["server_enforced_cap"] = False
    assert not budget_plan([C], compute, B)["dispatch_ready"]


def test_resume_does_not_dispatch_twice(tmp_path):
    ledger, client = ExperimentLedger(tmp_path / "jobs.sqlite3"), FakeClient()
    one = dispatch(C, W, config(), ledger, client)
    two = dispatch(C, W, config(), ledger, client)
    assert one == two
    assert client.calls == client.started == 1
    assert resume(one["fingerprint"], ledger, client, tmp_path)["state"] == "BILLING_PENDING"


def test_ambiguous_dispatch_and_parallel_reservations_stay_locked(tmp_path):
    ledger = ExperimentLedger(tmp_path / "jobs.sqlite3")
    key = fingerprint(C, W, 5, 10)
    ledger.reserve(key, C, {}, "exploration", 2, 10, B)
    ledger.transition(key, "RESERVED", "DISPATCHING")
    with pytest.raises(ValueError, match="prior job"):
        ledger.reserve("second", C, {}, "exploration", 2, 10, B)


def test_formula_or_window_change_has_new_identity():
    assert fingerprint(C, W, 5, 10) != fingerprint(dict(C, formula="RANK(volume)"), W, 5, 10)
    assert fingerprint(C, W, 5, 10) != fingerprint(C, dict(W, end="2024-05-31"), 5, 10)


@pytest.mark.parametrize('value', [0, -1, float('nan'), '2'])
def test_invalid_ceiling_is_not_a_budget(value):
    compute = config()['compute']
    compute['cost_ceiling'] = value
    with pytest.raises(ValueError):
        budget_plan([C], compute, B)


def test_zero_charge_receipt_unlocks_failed_run(tmp_path):
    import hashlib
    ledger, client = ExperimentLedger(tmp_path / 'jobs.sqlite3'), FakeClient()
    job = dispatch(C, W, config(), ledger, client)
    artifact = tmp_path / 'receipt.json'
    artifact.write_text('{"run_id":"r1","charge":0}')
    receipt = {'actual': 0, 'run_id': 'r1', 'final_status': 'FAILED', 'verified_by': 'test',
               'official_receipt_path': str(artifact), 'official_receipt_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}
    assert settle_receipt(job['fingerprint'], ledger, receipt)['state'] == 'SETTLED'


@pytest.mark.parametrize("marker", [
    {"provenance": "historical_memory_requires_revalidation"},
    {"source": "historical_requires_axis_revalidation"},
    {"data_requirements": ["quantaxis_field_mapping_pending"]},
    {"data_requirements": ["requires_source:TURNOVER"]},
    {"data_requirements": ["revalidate_legacy_formula_and_operator_semantics"]},
    {"data_requirements": ["freeze_direction_requires_evidence"]},
])
def test_unresolved_historical_candidate_cannot_reach_account_or_dispatch(tmp_path, marker):
    class NoAccountClient:
        def balance(self):
            raise AssertionError("Unresolved candidates must not access account or dispatch")
    ledger = ExperimentLedger(tmp_path / "blocked.sqlite3")
    with pytest.raises(ValueError, match="Resolve legacy"):
        dispatch(dict(C, **marker), W, config(), ledger, NoAccountClient())
    assert ledger.jobs() == []


def authorization(key, amount_field, amount=10, batch_id="confirmed-batch"):
    return {"day": beijing_day(), "batch_id": batch_id, "fingerprints": [key], amount_field: amount}


class NoAccountClient:
    def balance(self):
        raise AssertionError("Out-of-scope authorization must fail before account calls")


@pytest.mark.parametrize("change", ["formula", "direction", "window", "cycle", "groups"])
def test_recharge_authorization_cannot_be_reused_for_another_definition_or_window(tmp_path, change):
    cfg = config()
    cfg["compute"]["recharge_credit_limit"] = 5
    cfg["compute"]["recharge_batch_authorization"] = authorization(fingerprint(C, W, 5, 10), "max_recharge_credits", 5)
    candidate, window = dict(C), dict(W)
    if change == "formula": candidate["formula"] = "RANK(volume)"
    elif change == "direction": candidate["direction"] = 0
    elif change == "window": window["end"] = "2024-05-31"
    elif change == "cycle": cfg["research"]["cycle"] = 3
    elif change == "groups": cfg["research"]["groups"] = 5
    ledger = ExperimentLedger(tmp_path / "scope.sqlite3")
    with pytest.raises(ValueError, match="outside"):
        dispatch(candidate, window, cfg, ledger, NoAccountClient())
    assert ledger.jobs() == []


def test_recharge_requires_fingerprint_list_and_sufficient_confirmed_amount(tmp_path):
    cfg = config()
    cfg["compute"]["recharge_credit_limit"] = 5
    ledger = ExperimentLedger(tmp_path / "limits.sqlite3")
    for receipt in ({"day": beijing_day(), "batch_id": "old-broad-permission"},
                    authorization(fingerprint(C, W, 5, 10), "max_recharge_credits", 4)):
        cfg["compute"]["recharge_batch_authorization"] = receipt
        with pytest.raises(ValueError):
            dispatch(C, W, cfg, ledger, NoAccountClient())
    assert ledger.jobs() == []


def test_estimated_billing_boolean_alone_is_never_authorization(tmp_path):
    cfg = config()
    cfg["compute"].update(server_enforced_cap=False, allow_estimated_billing=True)
    assert not budget_plan([C], cfg["compute"], B)["dispatch_ready"]
    with pytest.raises(ValueError, match="scoped batch"):
        dispatch(C, W, cfg, ExperimentLedger(tmp_path / "risk.sqlite3"), NoAccountClient())


def test_estimated_billing_risk_is_bound_to_fingerprint_amount_and_explicit_acceptance(tmp_path):
    cfg = config()
    cfg["compute"].update(server_enforced_cap=False, allow_estimated_billing=True)
    key = fingerprint(C, W, 5, 10)
    good = dict(authorization(key, "max_total_credits", 10), accept_estimated_charge_risk=True)
    ledger = ExperimentLedger(tmp_path / "risk-scope.sqlite3")
    for receipt in (dict(good, fingerprints=[fingerprint(dict(C, formula="RANK(volume)"), W, 5, 10)]),
                    dict(good, max_total_credits=9), dict(good, accept_estimated_charge_risk=False)):
        cfg["compute"]["estimated_billing_batch_authorization"] = receipt
        with pytest.raises(ValueError):
            dispatch(C, W, cfg, ledger, NoAccountClient())
    cfg["compute"]["estimated_billing_batch_authorization"] = good
    assert budget_plan([C], cfg["compute"], B)["dispatch_ready"]
    client = FakeClient()
    job = dispatch(C, W, cfg, ledger, client)
    assert job["run_id"] == "r1"
    assert client.calls == client.started == 1


def test_recharge_and_estimated_risk_must_be_the_same_confirmed_batch(tmp_path):
    cfg = config()
    cfg["compute"].update(server_enforced_cap=False, allow_estimated_billing=True, recharge_credit_limit=5)
    key = fingerprint(C, W, 5, 10)
    cfg["compute"]["recharge_batch_authorization"] = authorization(key, "max_recharge_credits", 5, "batch-A")
    cfg["compute"]["estimated_billing_batch_authorization"] = dict(
        authorization(key, "max_total_credits", 10, "batch-B"), accept_estimated_charge_risk=True)
    ledger = ExperimentLedger(tmp_path / "combined.sqlite3")
    with pytest.raises(ValueError, match="same confirmed batch"):
        dispatch(C, W, cfg, ledger, NoAccountClient())
    assert ledger.jobs() == []
