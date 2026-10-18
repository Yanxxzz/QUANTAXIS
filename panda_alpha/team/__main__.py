"""One maintained entry point for the four-role research workflow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from .contracts import ROLES


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Offline synthetic end-to-end rehearsal; no account access")
    demo.add_argument("--output", type=Path, required=True)
    rehearsal = commands.add_parser("host-demo", help="Three bounded offline rounds including restart and same-task evidence review")
    rehearsal.add_argument("--output", type=Path, required=True)
    service = commands.add_parser("host-serve", help="Supervise four real Codex workers; default has no website execution")
    service.add_argument("--ledger", type=Path, required=True)
    service.add_argument("--registry", type=Path, required=True)
    service.add_argument("--session", required=True)
    service.add_argument("--workers", type=Path, required=True)
    service.add_argument("--output", type=Path, required=True)
    service.add_argument("--execute", action="store_true")
    service.add_argument("--config", type=Path)
    retry = commands.add_parser("host-retry", help="Audit a repaired model login/quota issue without resetting limits")
    retry.add_argument("--output", type=Path, required=True)
    retry.add_argument("--reason", required=True)
    deployment = commands.add_parser("service-prepare", help="Freeze a chat deployment; default offline, no installation or execution")
    deployment.add_argument("--ledger", type=Path, required=True)
    deployment.add_argument("--registry", type=Path, required=True)
    deployment.add_argument("--session", required=True)
    deployment.add_argument("--workers", type=Path, required=True)
    deployment.add_argument("--runtime", type=Path, required=True)
    deployment.add_argument("--output", type=Path, required=True)
    deployment.add_argument("--name", required=True)
    deployment.add_argument("--max-starts", type=int, default=10)
    deployment.add_argument("--execute", action="store_true")
    deployment.add_argument("--config", type=Path)
    operator = commands.add_parser("host-control", help="Explicit chat start/stop or read-only live status; no OS scheduler")
    operator.add_argument("--deployment", type=Path, required=True)
    operator.add_argument("--action", choices=("start", "stop", "status"), required=True)
    operator.add_argument("--execute", action="store_true", help="Explicitly start the already frozen website deployment")
    amend = commands.add_parser("amend-budget", help="Preview an audited same-study cap increase; no account access")
    amend.add_argument("--ledger", type=Path, required=True)
    amend.add_argument("--input", type=Path, help="Amendment JSON; omit to read the current policy/usage snapshot")
    amend.add_argument("--execute", action="store_true", help="Apply an explicitly authorized amendment; does not activate a grant")
    source = commands.add_parser("source-submit", help="Snapshot explicit source text and queue same-task re-review; no network")
    source.add_argument("--ledger", type=Path, required=True)
    source.add_argument("--task", required=True)
    source.add_argument("--input", type=Path, required=True)
    readiness = commands.add_parser("readiness", help="Read source blockers, intake queue, original budget and host bounds")
    readiness.add_argument("--ledger", type=Path, required=True)
    capture = commands.add_parser("source-capture", help="One explicit public official guide GET; no login or account APIs")
    capture.add_argument("--url", required=True)
    capture.add_argument("--output", type=Path, required=True)
    for name in ("host-init", "host-next", "host-respond", "host-run", "conclude", "recover-conclusions", "reopen-evidence", "evidence"):
        p = commands.add_parser(name)
        p.add_argument("--ledger", type=Path, required=True)
        if name not in {"reopen-evidence", "evidence"}:
            p.add_argument("--registry", type=Path, required=True)
        if name.startswith("host-"):
            p.add_argument("--session", required=True)
        if name in {"host-init", "host-respond", "conclude", "reopen-evidence"}:
            p.add_argument("--input", type=Path, required=True)
        if name in {"conclude", "reopen-evidence", "evidence"}:
            p.add_argument("--task", required=True)
        if name in {"conclude", "reopen-evidence"}:
            p.add_argument("--version", type=int, required=True)
        if name in {"host-run", "evidence"}:
            p.add_argument("--output", type=Path, required=True)
        if name == "evidence":
            p.add_argument("--input", type=Path, help="Optional JSON list of extra cached artifacts from this owned Run")
        if name == "host-run":
            transport = p.add_mutually_exclusive_group(required=True)
            transport.add_argument("--commands", type=Path, help="Explicit trusted argv per role; no default model/provider")
            transport.add_argument("--mailbox", type=Path, help="File bridge to already-running real role agents")
            p.add_argument("--watch", action="store_true", help="Wait for role/Run replies until the immutable session deadline")
            p.add_argument("--interval", type=float, default=5, help="Seconds between waits, from 1 to 60")
            p.add_argument("--config", type=Path)
            p.add_argument("--execute", action="store_true", help="Enable existing execution gateway; current grant still mandatory")
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
    if args.command == "host-control":
        from .operator import control
        result = control(args.deployment, args.action, execute=args.execute)
    elif args.command == "source-capture":
        from .public_docs import capture
        result = capture(args.url, args.output)
    elif args.command == "service-prepare":
        if args.execute != (args.config is not None):
            parser.error("Website execution requires both --execute and --config; omit both for offline research")
        from .service import prepare_service
        result = prepare_service(args.ledger, args.registry, args.session, args.workers, args.runtime,
                                 args.output, args.name, args.max_starts, execution_config=args.config)
    elif args.command == "demo":
        from .demo import run_demo
        result = run_demo(args.output)
    elif args.command == "host-demo":
        from .rehearsal import run_rehearsal
        result = run_rehearsal(args.output)
    elif args.command == "host-serve":
        if args.execute != (args.config is not None):
            parser.error("Website execution requires both --execute and --config; omit both for offline research")
        from .runtime import serve
        result = serve(args.ledger, args.registry, args.session, args.workers, args.output, args.config)
    elif args.command == "host-retry":
        from .runtime import retry_attention
        result = retry_attention(args.output, args.reason)
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
        c = Coordinator(args.ledger, read_only=args.command in {"status", "packet", "plan", "readiness"})
        try:
            if args.command == "readiness":
                from .sources import readiness
                result = readiness(c)
            elif args.command == "source-submit":
                from .sources import submit
                receipt = submit(c.board, args.task, read(args.input))
                result = {k: receipt[k] for k in ("event_id", "status", "reason", "artifact", "review")}
            elif args.command in {"init", "status", "plan"}:
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
            elif args.command == "reopen-evidence":
                result = c.board.reopen_evidence(args.task, args.version, read(args.input))
            elif args.command == "evidence":
                result = c.evidence(args.task, args.output, read(args.input) if args.input else [])
            elif args.command.startswith("host-") or args.command in {"conclude", "recover-conclusions"}:
                from ..registry import ResearchRegistry
                from .host import Host, CommandAgents, MailboxAgents, ExistingExecutor
                if not args.registry.is_file():
                    parser.error("Scientific registry must already exist; do not reset history")
                registry = ResearchRegistry(args.registry)
                try:
                    if args.command == "conclude":
                        result = c.conclude(args.task, args.version, read(args.input), registry)
                    elif args.command == "recover-conclusions":
                        result = c.recover_conclusions(registry)
                    else:
                        host = Host(c, registry, args.session, read(args.input) if args.command == "host-init" else None)
                        if args.command == "host-init":
                            result = host.state()
                        elif args.command == "host-next":
                            result = host.next()
                        elif args.command == "host-respond":
                            response = read(args.input)
                            result = host.respond(response["work_id"], response)
                        else:
                            executor = None
                            if args.execute:
                                if not args.config:
                                    parser.error("--execute requires --config and an existing current grant")
                                from ..platform import PandaClient
                                config = read(args.config)
                                executor = ExistingExecutor(c, registry, PandaClient(config.get("platform", {})), config, args.output / "results")
                            if not 1 <= args.interval <= 60:
                                parser.error("--interval must be from 1 to 60 seconds")
                            agents = MailboxAgents(args.mailbox) if args.mailbox else CommandAgents(read(args.commands), args.output / "role-replies")
                            while True:
                                result = host.run(agents, executor)
                                if not args.watch or result.get("reason") not in {"role_response", "original_run"}:
                                    break
                                time.sleep(args.interval)
                finally:
                    registry.close()
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
