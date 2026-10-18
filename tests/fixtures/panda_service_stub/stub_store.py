"""Disk-backed fake remote account. Copied into pytest tmp_path; never real data."""
import json
from pathlib import Path
import socket

ROOT = Path(__file__).resolve().parent


def no_network(*args, **kwargs):
    raise AssertionError("The service acceptance fixture forbids network")


socket.socket.connect = socket.socket.connect_ex = no_network


def call(operation):
    path = ROOT / "state.json"
    state = json.loads(path.read_text()) if path.exists() else {"calls": [], "creates": 0, "starts": 0}
    state["calls"].append(operation)
    if operation in {"create", "start"}:
        state[operation + "s"] += 1
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)
    return state
