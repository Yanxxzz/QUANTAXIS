"""One maintained entry point for the four-role research workflow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import ROLES


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Offline synthetic end-to-end rehearsal; no account access")
    demo.add_argument("--output", type=Path, required=True)
    amend = commands.add_parser("amend-budget", help="Preview an audited same-study cap increase; no account access")
    amend.add_argument("--ledger", type=Path, required=True)
    amend.add_argument("--input", type=Path, help="Amendment JSON; omit to read the current policy/usage snapshot")
    amend.add_argument("--execute", action="store_true", help="Apply an explicitly authorized amendment; does not activate a grant")
    for name in ("init", "status", "packet", "submit", "message", "freeze", "decide", "plan", "activate", "dispatch", "resume", "pause"):
        p = commands.add_parser(name)
        p.add_argument("--ledger", type=Path, required=True)
        if name == "packet":
            p.add_argument("--role", choices=ROLES, required=True)
        if name == "submit":
            p.add_argument("--request", type=Path, required=True)
        if name in {"message", "freeze", "decide"}:
            p.add_argument("--input", type=Path, required=True)
        if name in {"freeze", "decide", "dispatch", "resume"}:
            p.add_argument("--task", required=True)
        if name in {"freeze", "decide"}:
            p.add_argument("--version", type=int, required=True)
        if name == "activate":
            p.add_argument("--grant", type=Path, required=True)
            p.add_argument("--confirm-paid-pilot", action="store_true", required=True)
        if name in {"dispatch", "resume"}:
            p.add_argument("--config", type=Path, required=True)
        if name == "dispatch":
            p.add_argument("--registry", type=Path, required=True)
            p.add_argument("--execute", action="store_true", required=True)
        if name == "resume":
            p.add_argument("--output", type=Path, required=True)
        if name == "pause":
            p.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.command == "demo":
        from .demo import run_demo
        result = run_demo(args.output)
    elif args.command != "init" and not args.ledger.is_file():
        parser.error("Ledger does not exist; use init to create an offline board")
    elif args.command == "amend-budget":
        if args.execute and args.input is None:
            parser.error("--execute requires an amendment --input")
        from ..platform import ExperimentLedger
        from ..research_budget import amend_policy, amendment_snapshot
        ledger = ExperimentLedger(args.ledger, read_only=not args.execute)
        try:
            result = (amend_policy(ledger, read(args.input), execute=args.execute)
                      if args.input else amendment_snapshot(ledger))
        finally:
            ledger.db.close()
    else:
        from .coordinator import Coordinator
        c = Coordinator(args.ledger, read_only=args.command in {"status", "packet", "plan"})
        try:
            if args.command in {"init", "status", "plan"}:
                result = c.plan()
            elif args.command == "packet":
                result = c.board.packet(args.role)
            elif args.command == "submit":
                result = c.board.submit(read(args.request))
            elif args.command == "message":
                result = c.board.message(read(args.input))
            elif args.command in {"freeze", "decide"}:
                result = getattr(c.board, args.command)(args.task, args.version, read(args.input))
            elif args.command == "activate":
                from .budget import activate
                result = activate(c.board, read(args.grant))
            elif args.command == "pause":
                c.board.pause(args.reason)
                result = c.board.mode()
            else:
                from ..platform import PandaClient
                config = read(args.config)
                client = PandaClient(config.get("platform", {}))
                if args.command == "dispatch":
                    from ..registry import ResearchRegistry
                    registry = ResearchRegistry(args.registry, historical=config["research"].get("history_denominator", 0))
                    try:
                        result = c.dispatch(args.task, config, client, registry, execute=args.execute)
                    finally:
                        registry.close()
                else:
                    result = c.resume(args.task, client, args.output)
        finally:
            c.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
