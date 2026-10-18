"""Test-only bridge surface; no actual authentication or website access."""
from stub_store import ROOT, call


def load_config(_):
    return {}


def login(_):
    return "fixture", "fixture", None


def run_factor(*args):
    state = call("start")
    if (ROOT / "lose-start-response").exists():
        raise RuntimeError("Synthetic lost Run acknowledgement")
    return "fixture-run-" + str(state["starts"]), None
