"""Manual chat control uses the real process supervisor and offline role fixtures."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from panda_alpha.team.contracts import digest
from panda_alpha.team.operator import control, status
from panda_alpha.team.runtime_io import owned, read, terminate, write
from panda_alpha.team.service import prepare_service
from test_team_runtime import REPO, setup, until
from test_team_service import deployment, running_attempt


def cleanup(config, root):
    control(config, "stop")
    for start in read(config.parent / "service-state.json", {}).get("starts", []):
        terminate(start.get("process"))
    for path in (root / "components").glob("*/process.json"):
        terminate(read(path).get("process"))
    for path in (root / "workers").glob("*/work/*/state.json"):
        for attempt in read(path).get("attempts", []):
            terminate(attempt.get("process"))


def test_status_is_read_only_and_missing_deployment_is_not_created(tmp_path, monkeypatch):
    args, config = deployment(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Status started a process"))
    result = control(config, "status")
    assert result["status"] == "ready" and result["can_start"]
    assert result["website_execution"] is False and result["actions"] == 0
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    missing = tmp_path / "absent" / "service.json"
    with pytest.raises(ValueError, match="Invalid service"):
        control(missing, "status")
    assert not missing.parent.exists()


def test_manual_start_duplicate_stop_resume_and_finished_session(tmp_path):
    # The package entry must preserve literal spaces, Unicode and shell-like characters.
    tmp_path = tmp_path / "含 空格 $data"
    tmp_path.mkdir()
    args, config = deployment(tmp_path, "hold")
    root = args[-1]
    manifest = read(root / "manifest.json")
    try:
        assert control(config, "start")["status"] == "running"
        pending = until(lambda: running_attempt(root))
        packet_hash = read(pending)["packet_hash"]
        original_launch = read(config.parent / "manual-launch.json")
        assert control(config, "start")["starts"] == 1
        assert read(config.parent / "manual-launch.json") == original_launch
        stopped = control(config, "stop")
        assert stopped["status"] == "paused" and not stopped["service_alive"]
        assert not any(stopped["components"].values()) and stopped["live_model_processes"] == 0
        (tmp_path / "release-model").touch()
        assert control(config, "start")["status"] == "running"
        until(lambda: status(config)["status"] == "complete", timeout=55)
        completed = control(config, "start")
        assert completed["status"] == "complete" and not completed["can_start"]
        assert completed["starts"] == 2 and completed["round"] == 3
        assert read(root / "manifest.json") == manifest and read(pending)["packet_hash"] == packet_hash
        assert not any(completed["components"].values()) and completed["live_model_processes"] == 0
    finally:
        cleanup(config, root)


def test_manual_parent_loss_recovers_existing_model_attempt(tmp_path):
    args, config = deployment(tmp_path, "hold")
    root = args[-1]
    try:
        control(config, "start")
        pending = until(lambda: running_attempt(root))
        original_attempt = read(pending)["attempts"][0]["process"]
        parent = owned(read(config.parent / "service-state.json")["starts"][-1]["process"])
        parent.kill()
        parent.wait(timeout=10)
        assert status(config)["status"] == "interrupted"
        control(config, "start")
        (tmp_path / "release-model").touch()
        until(lambda: status(config)["status"] == "complete", timeout=55)
        assert read(pending)["attempts"][0]["process"] == original_attempt
        assert len(read(pending)["attempts"]) == 1
    finally:
        cleanup(config, root)


@pytest.mark.parametrize("blocker", ["deadline", "attention", "limit", "ambiguous"])
def test_control_does_not_bypass_terminal_bounds_or_unknown_launch(tmp_path, monkeypatch, blocker):
    args = setup(tmp_path, deadline=-1 if blocker == "deadline" else 120)
    prepared = prepare_service(*args[:-1], args[-1], tmp_path / "service", "PandaAlpha-Control", max_starts=1)
    config = Path(prepared["config"])
    if blocker == "attention":
        write(args[-1] / "supervisor.json", {"status": "attention", "result": {"reason": "model_quota"}})
    elif blocker == "limit":
        write(config.parent / "service-state.json", {"config_hash": digest(read(config)), "starts": [{"status": "paused"}]})
    elif blocker == "ambiguous":
        write(config.parent / "manual-launch.json", {"config_hash": digest(read(config)), "status": "launching", "time": 0})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Blocked session started a process"))
    result = control(config, "start")
    assert not result["can_start"] and result["status"] in {"complete", "attention"}
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not (config.parent / "desired-state.json").exists()


def test_cli_status_has_no_scheduler_dependency(tmp_path):
    _, config = deployment(tmp_path)
    # The maintained module entry is launched with an explicit source checkout.
    result = subprocess.run([sys.executable, "-m", "panda_alpha.team", "host-control", "--deployment", str(config),
                             "--action", "status"], cwd=REPO, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "ready"


def test_live_legacy_host_is_not_mistaken_for_a_dead_service(tmp_path):
    from test_team_runtime import launch, cleanup as cleanup_runtime
    args, config = deployment(tmp_path, "hold")
    child = launch(args)
    try:
        until(lambda: running_attempt(args[-1]))
        for action in ("status", "start", "stop"):
            result = control(config, action)
            assert result["reason"] == "runtime_outside_deployment"
            assert result["supervisor_alive"] and not result["service_alive"]
            assert not result["can_start"] and result["starts"] == 0
        assert not (config.parent / "manual-launch.json").exists()
    finally:
        cleanup_runtime(args, child)
