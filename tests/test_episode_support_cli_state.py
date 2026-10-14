"""Bookkeeping regression only: no actual research, source or registry calls."""
import json
import os

import pytest

from scripts import research_limit_episode_support as cli


@pytest.mark.parametrize("stage", ["completed", "frozen", "cutoff"])
def test_refused_invocation_preserves_existing_state(tmp_path, monkeypatch, stage):
    state = {"status": "FIXED_PAST_EPISODE_VALUE_AND_SUPPORT_DIAGNOSTIC_COMPLETE"
             if stage == "completed" else "FROZEN_REGISTERED_NO_REAL_EPISODE_VALUES_YET",
             "process": None, "original": "preserve"}
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    (tmp_path / "protocol.json").write_text("{}")
    if stage == "completed":
        (tmp_path / "summary.json").write_text("{}")
    original = state_path.read_bytes()
    monkeypatch.setattr(cli, "OUT", tmp_path)
    def refuse():
        raise RuntimeError("Refuse duplicate or cutoff; no source")
    monkeypatch.setattr(cli, "run", refuse)
    with pytest.raises(RuntimeError, match="Refuse"):
        cli.main()
    assert state_path.read_bytes() == original


def test_only_owned_running_unit_records_source_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "OUT", tmp_path)
    state_path = tmp_path / "state.json"
    def failed_run():
        state_path.write_text(json.dumps({"status": "RUNNING_NEW_REGISTERED_HISTORICAL_EPISODE_SUPPORT",
                                          "process": os.getpid()}))
        raise ValueError("Exact original source position pending")
    monkeypatch.setattr(cli, "run", failed_run)
    with pytest.raises(ValueError, match="Exact original"):
        cli.main()
    current = json.loads(state_path.read_text())
    assert current["status"] == "SOURCE_OR_EXECUTION_PENDING_NOT_ECONOMIC_FAILURE"
    assert current["process"] is None
