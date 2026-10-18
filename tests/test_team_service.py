"""Service restart tests use real processes and the offline role protocol fixture."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from panda_alpha.team.contracts import digest
from panda_alpha.team.runtime_io import owned, read, terminate, write
from panda_alpha.team.service import enable_service, load_config, prepare_service, run_service, stop_service
from test_team_runtime import REPO, setup, until


def deployment(tmp_path, mode="ok", **kwargs):
    args = setup(tmp_path, mode, deadline=120, turn_timeout=20)
    result = prepare_service(*args[:-1], args[-1], tmp_path / "service", "PandaAlpha-Test", **kwargs)
    return args, Path(result["config"])


def launch_service(config):
    log = (config.parent / "launcher.log").open("ab")
    try:
        # An unrelated working directory reproduces the Task Scheduler environment.
        return subprocess.Popen([sys.executable, str(REPO / "scripts/team_service.py"), "--config", str(config)],
            cwd=config.parent, stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        log.close()


def cleanup(args, child):
    if child.poll() is None:
        # Stop the relauncher first; terminating children first races its restart loop.
        child.kill()
    child.wait(timeout=10)
    root = args[-1]
    for path in (root / "components").glob("*/process.json"):
        terminate(read(path).get("process"))
    for path in (root / "workers").glob("*/work/*/state.json"):
        terminate(read(path)["attempts"][-1].get("process"))


def running_attempt(root):
    state = read(root / "supervisor.json", {})
    if not state.get("components"):
        return None
    for name, component in state["components"].items():
        receipt = read(root / "components" / name / "process.json", {})
        if (not component["launches"] or receipt.get("launch") != component["launches"][-1]["id"]
                or not owned(receipt.get("process"))):
            return None
    return next((p for p in (root / "workers").glob("*/work/*/state.json")
                 if read(p)["attempts"][-1]["status"] == "running"
                 and owned(read(p)["attempts"][-1].get("process"))), None)


def test_prepare_is_offline_and_refuses_rebinding(tmp_path):
    args, config = deployment(tmp_path, "hold")
    assert not (args[-1] / "supervisor.json").exists()
    assert load_config(config)["manifest_hash"] == digest(read(args[-1] / "manifest.json"))
    with pytest.raises(ValueError, match="different bindings"):
        prepare_service(*args[:-1], args[-1], config.parent, "PandaAlpha-Different")
    with pytest.raises(ValueError, match="Service name"):
        prepare_service(*args[:-1], args[-1], config.parent, "unsafe name")


def test_graceful_stop_restarts_original_work_with_stale_stop_ignored(tmp_path):
    args, config = deployment(tmp_path, "hold")
    child = launch_service(config)
    try:
        pending = until(lambda: running_attempt(args[-1]))
        original_hash = read(pending)["packet_hash"]
        assert stop_service(config)["status"] == "stop_requested"
        child.wait(timeout=15)
        state = read(config.parent / "service-state.json")
        assert state["status"] == "paused" and state["reason"] == "service_stop"
        assert run_service(config)["reason"] == "operator_stop"
        assert len(read(config.parent / "service-state.json")["starts"]) == 1
        enable_service(config, True)
        (tmp_path / "release-model").touch()
        child = launch_service(config)
        child.wait(timeout=55)
        state = read(config.parent / "service-state.json")
        assert state["status"] == "complete" and state["reason"] == "round_limit"
        assert len(state["starts"]) == 2
        assert read(pending)["packet_hash"] == original_hash
        assert all(len(read(p)["attempts"]) <= 2 for p in (args[-1] / "workers").glob("*/work/*/state.json"))
    finally:
        cleanup(args, child)


def test_hard_service_restart_adopts_live_workers_without_second_model_attempt(tmp_path):
    args, config = deployment(tmp_path, "hold")
    child = launch_service(config)
    try:
        pending = until(lambda: running_attempt(args[-1]))
        original = read(pending)["attempts"][0]["process"]
        child.kill()
        child.wait(timeout=5)
        child = launch_service(config)
        until(lambda: len(read(config.parent / "service-state.json")["starts"]) == 2)
        (tmp_path / "release-model").touch()
        child.wait(timeout=55)
        state = read(config.parent / "service-state.json")
        assert state["status"] == "complete", state
        assert state["starts"][0]["recovery"] == "process_restart"
        assert read(pending)["attempts"][0]["process"] == original
        assert len(read(pending)["attempts"]) == 1
    finally:
        cleanup(args, child)


def test_all_processes_lost_resume_from_disk_with_original_bounds(tmp_path):
    args, config = deployment(tmp_path, "hold")
    child = launch_service(config)
    try:
        pending = until(lambda: running_attempt(args[-1]))
        role = pending.parents[2].name
        session_file = args[-1] / "workers" / role / "session.json"
        session = until(lambda: read(session_file))
        manifest = read(args[-1] / "manifest.json")
        cleanup(args, child)  # A cold process tree, not a claim that the machine was rebooted.
        (tmp_path / "release-model").touch()
        child = launch_service(config)
        child.wait(timeout=55)
        state = read(config.parent / "service-state.json")
        assert state["status"] == "complete", state
        assert read(session_file) == session
        assert read(args[-1] / "manifest.json") == manifest
        attempts = read(pending)["attempts"]
        assert len(attempts) <= 2
        if len(attempts) == 2:
            assert attempts[1]["resume_thread"] == session["thread_id"]
        import sqlite3
        with sqlite3.connect(args[0]) as db:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        cleanup(args, child)


def test_service_restart_limit_and_deadline_survive_new_process(tmp_path):
    args, config = deployment(tmp_path, max_starts=1)
    event = threading.Event()
    event.set()
    assert run_service(config, event)["status"] == "paused"
    assert run_service(config)["reason"] == "service_start_limit"
    assert len(read(config.parent / "service-state.json")["starts"]) == 1
    # Deadline is checked before starting any child, including on a later boot.
    expired = tmp_path / "expired"
    expired.mkdir()
    old_args = setup(expired, deadline=-1)
    result = prepare_service(*old_args[:-1], old_args[-1], expired / "service", "PandaAlpha-Expired")
    assert run_service(result["config"])["reason"] == "deadline"
    assert not (old_args[-1] / "supervisor.json").exists()


def test_login_plan_quotes_paths_without_installing_task(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows login task adapter")
    folder = tmp_path / "含 空格 $data"
    folder.mkdir()
    _, config = deployment(folder)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(REPO / "scripts/team-service.ps1"),
        "-Action", "Plan", "-Config", str(config)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["LogonType"] == "Interactive" and plan["RunLevel"] == "Limited"
    assert plan["WebsiteExecution"] is False and plan["RestartCount"] == 3
    assert '-Action Run -Config "' + str(config) + '"' in plan["Arguments"]
