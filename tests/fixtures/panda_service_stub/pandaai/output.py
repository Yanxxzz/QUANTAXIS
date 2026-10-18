from stub_store import ROOT, call


def get_factor_run_detail(*args):
    call("status")
    return {"status": 2 if (ROOT / "release-run").exists() else 1}
