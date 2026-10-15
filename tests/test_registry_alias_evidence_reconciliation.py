"""Materialization evidence cannot silently become a registered identity alias."""
import pytest

from panda_alpha.registry import ResearchRegistry, strategy_definition, trial_key


@pytest.fixture
def registry(tmp_path):
    value = ResearchRegistry(tmp_path / "research.sqlite3", historical=415)
    try:
        yield value
    finally:
        value.close()


def malformed(registry):
    registry.append_event("identity_alias", "source-literal-proof", {
        "materialized_code_sha256": "1" * 64,
        "new_contexts": 0,
    }, event_id="synthetic-materialization-alias")
    return registry.events()[-1]


def reconcile(registry, target, **overrides):
    correction = {
        "event_id": target["event_id"],
        "event_hash": target["event_hash"],
        "replacement_kind": "execution_repair",
        "reason": "source_literal_materialization_not_identity_alias",
        **overrides,
    }
    registry.append_event("reconciliation", "synthetic-kind-correction", {
        "alias_event_kind_corrections": [correction],
    })


def test_malformed_alias_without_reconciliation_is_rejected(registry):
    malformed(registry)
    with pytest.raises(ValueError, match="lacks trial_key"):
        registry.aliases()
    assert registry.total == 415


def test_exact_reconciliation_preserves_count_and_normal_alias(registry):
    candidate = {"candidate_id": "normal", "direction": 1, "formula": "close"}
    window = {"start": "2024-01-02", "end": "2026-09-18"}
    registry.record(candidate, window, 5, 10)
    key = trial_key(strategy_definition(candidate, window, 5, 10))
    registry.append_event("identity_alias", "normal-friendly-name", {"trial_key": key})
    before = registry.total
    target = malformed(registry)
    reconcile(registry, target)
    assert registry.aliases()["normal-friendly-name"] == key
    assert registry.aliases()[key] == key
    assert "source-literal-proof" not in registry.aliases()
    assert registry.total == before == 416
    assert registry.new_tested == 1
    assert registry.events()[-2] == target
    registry.verify_integrity()


@pytest.mark.parametrize("overrides", [
    {"event_hash": "0" * 64},
    {"replacement_kind": "evaluation"},
    {"event_id": "another-event-id"},
])
def test_inexact_reconciliation_does_not_authorize_ignoring_alias(registry, overrides):
    target = malformed(registry)
    reconcile(registry, target, **overrides)
    with pytest.raises(ValueError, match="does not exactly bind"):
        registry.aliases()
    assert registry.total == 415
