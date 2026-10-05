"""Small subprocess bridge to the installed official pandaai-cli library.

No credentials are accepted as arguments or written to the research directory.
Dispatch and polling are separate so a Run ID is durably recorded before polling.
"""
import json
import sys


def main():
    site_packages, action, object_id = sys.argv[1:4]
    sys.path.insert(0, site_packages)
    from pandaai import load_config, login, run_factor
    from pandaai.output import get_factor_run_detail

    config = load_config(None)
    token, uid, _ = login(config)
    if action == "start":
        run_id, error = run_factor(config, token, uid, object_id)
        output = {"run_id": run_id, "success": bool(run_id), "error": error}
    elif action == "status":
        detail = get_factor_run_detail(config, token, uid, object_id)
        output = {"success": bool(detail), "status": detail.get("status") if detail else None}
    else:
        raise ValueError("Unknown bridge action")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
