"""Deterministic CLI protocol fixture. Never a real model or research evidence."""
import json
from pathlib import Path
import sys
import time

argv = sys.argv[1:]
output = Path(argv[argv.index("--output-last-message") + 1])
folder = output.parent
payload = json.loads(sys.stdin.read().split("\n", 1)[1])
p = payload["packet"]
mode = next((x["text"] for x in payload["context"] if x["path"].endswith("mode.txt")), "ok").strip()
thread = argv[argv.index("resume") + 1] if "resume" in argv else "fixture-" + p["role"]
if mode == "auth_stderr":
    print("Not logged in. Please log in.", file=sys.stderr, flush=True)
    sys.exit(1)
print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
time.sleep(.1)
if mode == "quota":
    print(json.dumps({"type": "turn.failed", "error": {"message": "Usage limit reached"}}), flush=True)
    sys.exit(1)
if mode == "timeout" or (mode == "retry" and folder.name == "1"):
    if mode == "retry":
        print('{"type":"partial', flush=True)
        sys.exit(1)
    time.sleep(60)
if p["action"] == "plan_round":
    out = {"decision": "research", "reason": "Offline protocol fixture", "assignments": [
        {"owner": r, "brief": "No invented evidence"} for r in ("market", "fundamental")]}
elif p["action"] == "propose":
    out = {"decision": "no_candidate", "reason": "No evidence in this fixture"}
elif p["action"] == "freeze":
    out = {"decision": "park", "reason": "Fixture only, no website execution"}
else:
    out = {"claim": "Fixture evidence only", "test": "No scientific conclusion", "artifacts": [],
           "decision": "approve" if p["action"] == "review" else "test"}
output.write_text(json.dumps({"work_id": p["work_id"], "output_json": json.dumps(out)}), encoding="utf-8")
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}), flush=True)
