"""Run factor research and team workflows through a platform-neutral entry point."""
from __future__ import annotations

import argparse
import sys


def main(argv=None, *, prog="quant-research"):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="One isolated offline adapter run and a local HTML receipt")
    demo.add_argument("--output", required=True)
    commands.add_parser("research", help="Data, planning, local evaluation and remote experiments")
    commands.add_parser("team", help="Offline demos and bounded collaborative research")
    if not arguments or arguments[0] not in {"research", "team"}:
        parsed = parser.parse_args(arguments)
        if parsed.command == "demo":
            import json
            from quantfactra.demo import run_demo
            print(json.dumps(run_demo(parsed.output), ensure_ascii=False, indent=2, allow_nan=False))
        return
    command = arguments[0]
    if command == "team":
        from panda_alpha.team.__main__ import main as run
    else:
        from panda_alpha.cli import main as run
    options = {"prog": f"{prog} {command}"}
    if prog == "quantfactra" and command == "research":
        options["default_config"] = "config/quantfactra.local.json"
    return run(arguments[1:], **options)
