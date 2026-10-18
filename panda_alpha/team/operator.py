"""Chat-operated start/stop/status around the existing bounded service entry."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from .contracts import digest
from .runtime_io import lease, owned, read, write
from .service import enable_service, load_config, stop_service


def status(path):
    """Read durable state and live birth-time receipts without starting anything."""
    path = Path(path).resolve()
    config = load_config(path)
    root = Path(config["runtime"])
    manifest = read(root / "manifest.json")
    if digest(manifest) != config["manifest_hash"] or manifest["binding"]["execution_config"] is not None:
        raise ValueError("Runtime manifest changed or website executor enabled")
    state = read(path.parent / "service-state.json", {})
    if state.get("config_hash") not in {None, digest(config)}:
        raise ValueError("Service configuration changed")
    runtime = read(root / "supervisor.json", {})
    launch = read(path.parent / "manual-launch.json", {})
    if launch and launch.get("config_hash") != digest(config):
        raise ValueError("Manual launch configuration changed")
    starts = state.get("starts", [])
    enabled = read(path.parent / "desired-state.json", {"enabled": True})["enabled"]
    service_alive = bool(starts and owned(starts[-1].get("process")))
    launcher_alive = bool(owned(launch.get("process")))
    supervisor_alive = bool(owned(runtime.get("process")))
    outside_deployment = supervisor_alive and not service_alive and not launcher_alive
    components = {name: bool(owned(read(root / "components" / name / "process.json", {}).get("process")))
                  for name in ("lead", "market", "fundamental", "skeptic", "coordinator")}
    model_processes = sum(bool(owned(attempt.get("process")))
                          for p in (root / "workers").glob("*/work/*/state.json")
                          for attempt in read(p).get("attempts", []))
    binding = manifest["binding"]
    with sqlite3.connect(Path(binding["ledger"]).as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT round,actions,stop_reason FROM team_host_sessions WHERE session_id=?",
                         (binding["session"],)).fetchone()
    if row is None:
        raise ValueError("Original host session is missing")
    blocked = None
    if runtime.get("status") in {"complete", "attention"}:
        blocked = (runtime["status"], runtime.get("result", {}).get("reason"))
    elif row[2]:
        blocked = ("complete", row[2])
    elif time.time() >= manifest["deadline_epoch"]:
        blocked = ("complete", "deadline")
    elif len(starts) >= config["max_starts"]:
        blocked = ("attention", "service_start_limit")
    if outside_deployment:
        current, reason = "attention", "runtime_outside_deployment"
    elif service_alive or launcher_alive:
        current, reason = ("running", "service_running") if enabled else ("stopping", "operator_stop")
    elif launch.get("status") == "launching":
        current, reason = (("starting", "awaiting_launcher_receipt") if time.time() - launch["time"] < 10
                           else ("attention", "ambiguous_manual_launch"))
    elif blocked:
        current, reason = blocked
    elif not enabled:
        current = "stopping" if any(components.values()) or model_processes else "paused"
        reason = "operator_stop"
    elif starts or any(components.values()) or model_processes:
        current, reason = "interrupted", "service_process_exited"
    else:
        current, reason = "ready", "prepared"
    return {"status": current, "reason": reason, "name": config["name"], "session": binding["session"],
            "runtime": str(root), "enabled": enabled, "service_alive": service_alive or launcher_alive,
            "supervisor_alive": supervisor_alive,
            "components": components, "live_model_processes": model_processes,
            "can_start": blocked is None and current in {"ready", "paused", "interrupted"},
            "starts": len(starts), "max_starts": config["max_starts"],
            "round": row[0], "actions": row[1], "bounds": binding["spec"],
            "website_execution": False}


def control(path, action, wait_seconds=10):
    """Explicit operation only; no scheduler, new session, grant or reset."""
    if action not in {"start", "stop", "status"}:
        raise ValueError("Expected start, stop or status")
    if not 0 <= wait_seconds <= 30:
        raise ValueError("wait_seconds must be between 0 and 30")
    if action == "status":
        return status(path)
    path = Path(path).resolve()
    child = None
    with lease(path.parent / "operator.lock"):
        snapshot = status(path)
        if action == "stop":
            if snapshot["reason"] == "runtime_outside_deployment":
                return snapshot
            stop_service(path)
        else:
            if not snapshot["can_start"]:
                return snapshot
            config = load_config(path)
            # The child acknowledges itself before consuming work. An interrupted
            # parent cannot turn a missing Popen receipt into a second model run.
            launch = {"id": uuid.uuid4().hex, "config_hash": digest(config),
                      "status": "launching", "time": time.time()}
            write(path.parent / "manual-launch.json", launch)
            enable_service(path, True)
            try:
                child = subprocess.Popen([config["python"], str(Path(config["repo"]) / "scripts/team_service.py"),
                    "--config", str(path), "--launch", launch["id"]], cwd=config["repo"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    start_new_session=not hasattr(subprocess, "CREATE_NO_WINDOW"))
            except OSError:
                write(path.parent / "manual-launch.json", dict(launch, status="spawn_failed"))
                enable_service(path, False)
                raise
    until = time.monotonic() + wait_seconds
    while True:
        if child is not None:
            child.poll()
        snapshot = status(path)
        active = snapshot["service_alive"] or any(snapshot["components"].values()) or snapshot["live_model_processes"]
        waiting = snapshot["status"] == "starting" if action == "start" else active
        if not waiting or time.monotonic() >= until:
            return snapshot
        time.sleep(.05)
