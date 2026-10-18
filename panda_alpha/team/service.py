"""OS service lifecycle around the existing bounded, durable role supervisor."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import signal
import sys
import threading
import time
import uuid

import psutil

from .contracts import digest
from .runtime import prepare, serve
from .runtime_io import identity, lease, owned, read, write

REPO = Path(__file__).resolve().parents[2]


def prepare_service(ledger, registry, session, workers, runtime, output, name, max_starts=10):
    """Freeze a deployment without installing anything or starting a model."""
    if not re.fullmatch(r"PandaAlpha-[A-Za-z0-9-]{1,48}", name):
        raise ValueError("Service name must be PandaAlpha- followed by 1-48 letters, digits or hyphens")
    if type(max_starts) is not int or not 1 <= max_starts <= 100:
        raise ValueError("max_starts must be an integer from 1 to 100")
    root = Path(runtime).resolve()
    destination = Path(output).resolve()
    with lease(root / "supervisor.lock"), lease(str(Path(ledger).resolve()) + ".host-service.lock"):
        root, manifest = prepare(ledger, registry, session, workers, root)
    if manifest["binding"]["execution_config"] is not None:
        raise ValueError("Service deployment currently permits offline/source review only")
    value = {"schema": 1, "name": name, "runtime": str(root), "workers": str(Path(workers).resolve()),
             "manifest_hash": digest(manifest), "python": str(Path(sys.executable).resolve()),
             "repo": str(REPO), "max_starts": max_starts}
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "service.json"
    with lease(destination / "service.lock"):
        previous = read(path)
        if previous is not None and previous != value:
            raise ValueError("Deployment already exists with different bindings; do not reset its history")
        write(path, value)
    return {"config": str(path), "runtime": str(root), "name": name, "installed": False,
            "website_execution": False, "deadline": manifest["binding"]["spec"]["deadline"]}


def load_config(path):
    config = read(path)
    fields = {"schema", "name", "runtime", "workers", "manifest_hash", "python", "repo", "max_starts"}
    if not isinstance(config, dict) or set(config) != fields or config["schema"] != 1:
        raise ValueError("Invalid service deployment")
    if not re.fullmatch(r"PandaAlpha-[A-Za-z0-9-]{1,48}", config["name"]):
        raise ValueError("Invalid service name")
    if type(config["max_starts"]) is not int or not 1 <= config["max_starts"] <= 100:
        raise ValueError("Invalid service start limit")
    if Path(config["repo"]).resolve() != REPO or Path(config["python"]).resolve() != Path(sys.executable).resolve():
        raise ValueError("Use the original service checkout and interpreter")
    return config


class StopRequest:
    def __init__(self, folder, start, event):
        self.path, self.start, self.event = folder / "stop.json", start, event

    def is_set(self):
        return (self.event.is_set() or read(self.path, {}).get("start") == self.start
                or not read(self.path.parent / "desired-state.json", {"enabled": True})["enabled"])


def enable_service(path, enabled):
    path = Path(path).resolve()
    load_config(path)
    action = {"enabled": enabled, "time": time.time(), "reason": "operator_start" if enabled else "operator_stop"}
    write(path.parent / "operator-control" / (uuid.uuid4().hex + ".json"), action)
    write(path.parent / "desired-state.json", action)


def stop_service(path):
    path = Path(path).resolve()
    config = load_config(path)
    state = read(path.parent / "service-state.json", {})
    if state.get("config_hash") not in {None, digest(config)}:
        raise ValueError("Service configuration changed")
    enable_service(path, False)
    starts = state.get("starts", [])
    if not starts or not owned(starts[-1].get("process")):
        return {"status": "stopped"}
    write(path.parent / "stop.json", {"start": starts[-1]["id"], "time": time.time()})
    return {"status": "stop_requested", "start": starts[-1]["id"]}


def run_service(path, stop_event=None, launch_id=None):
    """Task Scheduler and foreground recovery tests share exactly this entry point."""
    path = Path(path).resolve()
    config = load_config(path)
    root = Path(config["runtime"])
    stop_event = stop_event or threading.Event()
    with lease(path.parent / "service.lock"):
        state_path = path.parent / "service-state.json"
        state = read(state_path, {"config_hash": digest(config), "starts": []})
        if state["config_hash"] != digest(config):
            raise ValueError("Service bindings changed; preserved history cannot be rebound")
        manifest = read(root / "manifest.json")
        if digest(manifest) != config["manifest_hash"] or manifest["binding"]["execution_config"] is not None:
            raise ValueError("Runtime manifest changed or website executor enabled")
        if launch_id is not None:
            launch_path = path.parent / "manual-launch.json"
            launch = read(launch_path, {})
            if launch.get("id") != launch_id or launch.get("config_hash") != digest(config):
                raise ValueError("Manual launch identity changed")
            write(launch_path, dict(launch, status="acknowledged", process=identity()))
        state["last_check"] = {"time": time.time(), "boot_time": psutil.boot_time(), "process": identity()}
        if not read(path.parent / "desired-state.json", {"enabled": True})["enabled"]:
            state.update(status="paused", reason="operator_stop")
            write(state_path, state)
            return state
        runtime_state = read(root / "supervisor.json", {})
        if runtime_state.get("status") in {"complete", "attention"}:
            state.update(status=runtime_state["status"], reason=runtime_state.get("result", {}).get("reason"))
            write(state_path, state)
            return state
        # A boot/start event never creates a session, extends its deadline or clears attention.
        if time.time() >= manifest["deadline_epoch"]:
            state.update(status="complete", reason="deadline")
            write(state_path, state)
            return state
        if len(state["starts"]) >= config["max_starts"]:
            state.update(status="attention", reason="service_start_limit")
            write(state_path, state)
            return state
        boot = psutil.boot_time()
        if state["starts"] and state["starts"][-1]["status"] == "running":
            previous = state["starts"][-1]
            previous.update(status="interrupted", recovered_at=time.time(),
                            # psutil's Windows boot epoch can jitter by a second between processes.
                            recovery="boot_time_changed" if abs(previous["boot_time"] - boot) > 5 else "process_restart")
        start = {"id": uuid.uuid4().hex, "time": time.time(), "boot_time": boot,
                 "process": identity(), "status": "running"}
        state["starts"].append(start)
        state.update(status="running", reason="service_started")
        write(state_path, state)
        binding = manifest["binding"]
        try:
            result = serve(binding["ledger"], binding["registry"], binding["session"], config["workers"],
                           root, stop_event=StopRequest(path.parent, start["id"], stop_event))
            state.update(status=result["status"], reason=result.get("result", {}).get("reason"))
            start.update(status=state["status"], ended=time.time())
            return state
        except Exception as exc:
            # Only stable categories in shared status; traceback belongs to a private service log.
            state.update(status="attention", reason="service_error", error_type=type(exc).__name__)
            start.update(status="attention", ended=time.time())
            raise
        finally:
            write(state_path, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stop", action="store_true", help="Request a graceful stop of this exact service instance")
    parser.add_argument("--enable", action="store_true", help="Explicitly allow the stopped service to resume")
    parser.add_argument("--launch", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.config = args.config.resolve()
    if args.stop:
        stop_service(args.config)
        return
    if args.enable:
        enable_service(args.config, True)
        return
    event = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: event.set())
    load_config(args.config)
    # No console, credentials or private model responses in service-manager output.
    with (args.config.parent / "service.log").open("a", encoding="utf-8") as log:
        from contextlib import redirect_stderr, redirect_stdout
        with redirect_stderr(log), redirect_stdout(log):
            try:
                run_service(args.config, event, args.launch)
            except Exception:
                import traceback
                traceback.print_exc()
                raise SystemExit(1)


if __name__ == "__main__":
    main()
