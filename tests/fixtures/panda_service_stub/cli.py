"""Fake pandaai-cli JSON protocol, isolated from user configuration."""
import json
import sys
from stub_store import call


def main():
    command = sys.argv[sys.argv.index("--json") + 1]
    if command == "balance":
        state = call("balance")
        remaining = 100 - 2 * state["starts"]
        result = {"balance": {"computingPower": remaining, "giftBalance": remaining, "rechargeBalance": 0}}
    elif command == "factor_create":
        state = call("create")
        result = {"factor_id": "fixture-factor-" + str(state["creates"])}
    elif command == "factor_result":
        call("result")
        result = {"synthetic": True, "economic_evidence": False}
    else:
        raise AssertionError("Unexpected fake CLI command: " + command)
    print(json.dumps(dict(result, success=True)))
