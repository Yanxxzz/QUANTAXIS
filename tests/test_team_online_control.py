"""Real chat/service/bridge processes with a local fake website and fake roles.

An official-shaped grant in an isolated ledger exercises the production gateway;
it is not user approval, a real account, native evidence or an economic result.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from panda_alpha.registry import ResearchRegistry
from panda_alpha.team.budget import activate
from panda_alpha.team.coordinator import Coordinator
from panda_alpha.team.demo import artifact, freeze_synthetic, synthetic_config, synthetic_grant, synthetic_request
from panda_alpha.team.host import Host
from panda_alpha.team.operator import control, status
from panda_alpha.team.runtime_io import read, terminate, write
from panda_alpha.team.service import prepare_service, run_service
from test_team_operator import cleanup
from test_team_runtime import REPO, setup, until


def online_deployment(tmp_path, *, blocked=None):
    args = list(setup(tmp_path, "hold", deadline=120, turn_timeout=60))
    args[2] = "online-fixture"
    proof = artifact(tmp_path / "proof.txt")
    sdk = tmp_path / "fake-sdk"
    shutil.copytree(REPO / "tests/fixtures/panda_service_stub", sdk)
    config = synthetic_config()
    config["platform"] = {"python": sys.executable, "site_packages": str(sdk)}
    c, registry = Coordinator(args[0]), ResearchRegistry(args[1])
    try:
        if blocked in {"source", "pit"}:
            # Use another definition; requests already submitted by setup are immutable.
            request = synthetic_request("fundamental" if blocked == "pit" else "market", proof)
            request.update(task_id="source-pending")
            request["candidate"].update(formula="RANK(close / (volume + 1))" if blocked == "source"
                else "RANK(operating_cash_flow / (total_assets + 1))",
                data_requirements=["requires_source:financial_report_pit" if blocked == "pit" else "requires_source:native_clock"])
        else:
            request = synthetic_request("market", proof)
        task = freeze_synthetic(c.board, request)
        if blocked != "grant":
            grant = synthetic_grant(proof, config)
            grant["environment"] = "official"  # Fake transport, production permission checks.
            if blocked == "expired":
                grant["valid_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            activate(c.board, grant)
        Host(c, registry, args[2], {"objective": "Synthetic online lifecycle; no network or scientific claim",
            "deadline": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
            "max_rounds": 1, "max_actions": 20, "seed_tasks": [task["task_id"]]})
    finally:
        c.close()
        registry.close()
    path = tmp_path / "execution.json"
    write(path, config)
    result = prepare_service(*args[:-1], args[-1], tmp_path / "service", "PandaAlpha-OnlineTest", execution_config=path)
    return args, Path(result["config"]), sdk, task


def jobs(ledger):
    c = Coordinator(ledger, read_only=True)
    try:
        return c.ledger.jobs()
    finally:
        c.close()


def test_online_prepare_status_and_ordinary_start_never_launch(tmp_path, monkeypatch):
    args, config, sdk, _ = online_deployment(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected process"))
    snapshot = status(config)
    assert snapshot["website_execution"] and snapshot["unsettled_jobs"] == []
    assert control(config, "start")["reason"] == "explicit_online_start_required"
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not (config.parent / "manual-launch.json").exists()
    assert not (config.parent / "desired-state.json").exists()
    with pytest.raises(ValueError, match="explicit chat start"):
        run_service(config)  # A scheduler or direct service entry cannot arm online work.
    assert not (sdk / "state.json").exists() and jobs(args[0]) == []
    with pytest.raises(ValueError, match="Runtime binding changed"):
        prepare_service(*args[:-1], args[-1], config.parent, "PandaAlpha-OnlineTest")


@pytest.mark.parametrize("interruption", ["stop", "all_processes_lost"])
def test_online_chat_resume_reuses_original_run_after_pause_or_crash(tmp_path, interruption):
    args, config, sdk, task = online_deployment(tmp_path)
    root = args[-1]
    manifest = read(root / "manifest.json")
    # Changing the external file must not change frozen execution settings on start/recovery.
    write(tmp_path / "execution.json", {"changed": True})
    try:
        assert control(config, "start", execute=True)["status"] == "running"
        until(lambda: jobs(args[0]) and jobs(args[0])[0]["state"] == "RUNNING")
        until(lambda: "status" in read(sdk / "state.json")["calls"])
        original = jobs(args[0])[0]
        assert control(config, "start", execute=True)["starts"] == 1
        if interruption == "stop":
            snapshot = control(config, "stop")
            assert snapshot["status"] == "paused" and not snapshot["service_alive"]
            assert not any(snapshot["components"].values()) and snapshot["live_model_processes"] == 0
        else:
            terminate(read(config.parent / "service-state.json")["starts"][-1]["process"])
            assert status(config)["status"] == "interrupted"
        before = read(sdk / "state.json")
        snapshot = status(config)
        assert snapshot["unsettled_jobs"][0]["run_id"] == original["run_id"]
        assert snapshot["remote_status"] == "not_queried" and read(sdk / "state.json") == before
        # Existing Run settlement is permitted while new dispatch is paused.
        c = Coordinator(args[0])
        c.board.pause("Synthetic operator pause; do not renew grant")
        c.close()
        (sdk / "release-run").touch()
        assert control(config, "start", execute=True)["status"] == "running"
        until(lambda: jobs(args[0])[0]["state"] == "SETTLED", timeout=30)
        control(config, "stop")
        final = jobs(args[0])[0]
        assert final["run_id"] == original["run_id"] and final["fingerprint"] == task["fingerprint"]
        assert final["actual"] == 2 and len(jobs(args[0])) == 1
        state = read(sdk / "state.json")
        assert state["creates"] == state["starts"] == 1
        assert status(config)["starts"] == 2 and status(config)["unsettled_jobs"] == []
        assert read(root / "manifest.json") == manifest
        registry = ResearchRegistry(args[1])
        assert registry.new_tested == 1
        registry.close()
    finally:
        cleanup(config, root)


@pytest.mark.parametrize("blocker", ["grant", "expired", "source", "pit"])
def test_online_chat_cannot_bypass_source_or_grant(tmp_path, blocker):
    args, config, sdk, _ = online_deployment(tmp_path, blocked=blocker)
    try:
        control(config, "start", execute=True)
        result = until(lambda: read(args[-1] / "host-status.json"))
        assert result["reason"] == "execution_blocked", result
        assert jobs(args[0]) == [] and not (sdk / "state.json").exists()
    finally:
        cleanup(config, args[-1])


def test_lost_remote_acknowledgement_is_never_reissued(tmp_path):
    args, config, sdk, _ = online_deployment(tmp_path)
    (sdk / "lose-start-response").touch()
    try:
        control(config, "start", execute=True)
        until(lambda: status(config)["status"] == "complete")
        pending = jobs(args[0])[0]
        assert pending["state"] == "DISPATCHING" and pending["run_id"] is None
        assert not control(config, "start", execute=True)["can_start"]
        assert read(sdk / "state.json")["starts"] == 1
        assert status(config)["unsettled_jobs"][0]["state"] == "DISPATCHING"
    finally:
        cleanup(config, args[-1])


def test_scheduler_cannot_install_an_online_deployment(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows task adapter")
    _, config, _, _ = online_deployment(tmp_path)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(REPO / "scripts/team-service.ps1"),
        "-Action", "Plan", "-Config", str(config)], capture_output=True, timeout=15)
    assert result.returncode != 0 and b"explicit host-control" in result.stderr


def test_cli_prepare_requires_paired_online_flags(tmp_path):
    args = setup(tmp_path)
    command = [sys.executable, "-m", "panda_alpha.team", "service-prepare", "--ledger", str(args[0]),
        "--registry", str(args[1]), "--session", args[2], "--workers", str(args[3]), "--runtime", str(args[4]),
        "--output", str(tmp_path / "service"), "--name", "PandaAlpha-Test"]
    result = subprocess.run(command + ["--execute"], capture_output=True, cwd=REPO, timeout=10)
    assert result.returncode != 0 and b"both --execute and --config" in result.stderr
    assert not (tmp_path / "service").exists()
