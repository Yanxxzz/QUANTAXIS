"""Offline source-tree smoke for the public Panda Alpha research harness.

Run with the interpreter that installed requirements-panda-alpha.txt. Optional
--clean-tree copies an explicit source allowlist into a new directory and runs
the same synthetic smoke there; it does not package market data or credentials.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PUBLIC_PATTERNS = (
    "panda_alpha/**/*.py", "tests/**/*.py", "scripts/*.py", "scripts/*.ps1",
    "docs/*.md", ".github/workflows/panda-alpha.yml", "requirements-panda-alpha.txt",
    "config/panda-alpha.example.json", "README*.md", "RESEARCH.md", "pyproject.toml",
)
PRIVATE_PARTS = {"research_runs", ".runtime", ".venv", "data", "_data_", ".git", "__pycache__"}


def public_files(root=ROOT):
    """Publication review list, independent of staging and private file globs."""
    root = Path(root)
    files = {path.relative_to(root).as_posix() for pattern in PUBLIC_PATTERNS
             for path in root.glob(pattern) if path.is_file()}
    if any(PRIVATE_PARTS.intersection(Path(item).parts) or item.endswith(".local.json") for item in files):
        raise ValueError("Private runtime, research data or local config entered public source list")
    return sorted(files)


def copy_public_tree(target, root=ROOT):
    """Only create a fresh directory; never replace or remove an existing tree."""
    target = Path(target).resolve()
    if target.exists():
        raise ValueError("Clean source destination must not already exist")
    files = public_files(root)
    target.mkdir(parents=True)
    for item in files:
        destination = target / item
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(root) / item, destination)
    return files


def _blocked_network(*args, **kwargs):
    raise AssertionError("Public synthetic smoke must remain offline")


def run_smoke(root=ROOT):
    root = Path(root).resolve()
    # CLI help deliberately uses a nonexistent config: argparse must exit before
    # reading any user configuration or creating research databases.
    help_output = io.StringIO()
    imports = []
    with ExitStack() as guards:
        guards.enter_context(patch.object(socket.socket, "connect", _blocked_network))
        guards.enter_context(patch.object(socket.socket, "connect_ex", _blocked_network))
        for module in sorted((root / "panda_alpha").glob("*.py")):
            if module.stem in {"__init__", "__main__"}:
                continue
            name = f"panda_alpha.{module.stem}"
            imported = importlib.import_module(name)
            if Path(imported.__file__).resolve().parent != root / "panda_alpha":
                raise AssertionError("Import resolved outside reviewed source tree")
            imports.append(name)
        from panda_alpha.cli import main as cli_main
        with redirect_stdout(help_output):
            try:
                cli_main(["--config", "public-smoke-config-must-not-exist.json", "--help"])
            except SystemExit as result:
                if result.code != 0:
                    raise AssertionError("CLI help failed") from result
        if "evaluate" not in help_output.getvalue() or "registry" not in help_output.getvalue():
            raise AssertionError("Public CLI help is incomplete")

        from tests.test_financial_statements import original_report
        from panda_alpha.annual_gross_profitability import select_annual_gross_profit_asof
        from panda_alpha.gross_profitability import select_gross_profit_asof
        _, fy = original_report()
        _, h1 = original_report("2025-06-30", "2025-08-27", "h125", assets="2200.00 2100.00")
        blocked = select_annual_gross_profit_asof([fy, h1], code="600026", decision_date="2025-08-28")
        if blocked["status"] != "later_report_selected_fy_asset_comparison_mismatch":
            raise AssertionError("Schema 2 original comparison did not reach annual guard")
        legacy = deepcopy(fy)
        legacy["schema_version"] = 1
        legacy.pop("field_contract_version", None)
        legacy.pop("schema_note", None)
        for field in legacy["field_evidence"].values():
            field.pop("field_contract", None)
            for key in ("comparative_stock_asof", "comparative_period_status",
                        "current_printed_quantum_yuan", "comparative_printed_quantum_yuan"):
                field.pop(key, None)
        legacy.pop("record_sha256", None)
        legacy["record_sha256"] = hashlib.sha256(json.dumps(legacy, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not select_gross_profit_asof([legacy], code="600026", decision_date="2025-04-02")["pit_usable"]:
            raise AssertionError("Schema 1 public source record is no longer readable")

        from tests.study_fixture import synthetic_study
        from panda_alpha.study import prepare_study, evaluate_study
        protocol, source, quotes, comparator = synthetic_study()
        receipt = {"scope": "public_synthetic_fixture", "financial_record_schema": [1, 2]}
        preparation = prepare_study(protocol, source, quotes, comparator, source_receipt=receipt)
        study = evaluate_study(preparation, protocol, source, quotes, comparator, source_receipt=receipt)
        if study["status"] != "evaluated_research" or len(study["runs"]) != 42:
            raise AssertionError("Shared two-cost all-group study did not finish")
        if len(study["cost_reviews"]) != 2 or study["quality"]["formal_admission"]["eligible"]:
            raise AssertionError("Synthetic study altered formal admission")
        json.dumps(study, allow_nan=False)
    return {"status": "PUBLIC_SOURCE_SMOKE_PASSED", "source_root": str(root),
            "python": sys.executable, "public_module_imports": imports,
            "cli_help": "passed_without_config", "financial_schemas_readable": [1, 2],
            "annual_asset_restatement_guard": "passed_from_original_text",
            "study_runs": len(study["runs"]), "cost_cases": len(study["cost_reviews"]),
            "official_runs": 0, "llm_calls": 0, "market_files_read": 0,
            "dependency_installation": "not_performed; reuse interpreter with requirements-panda-alpha.txt",
            "clean_tree_scope": "public source and synthetic fixtures; full repository pytest remains a separate CI step"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-tree", type=Path, help="Fresh destination for allowlisted source copy")
    parser.add_argument("--list-public", action="store_true", help="Print source publication review list only")
    args = parser.parse_args(argv)
    if args.list_public:
        result = {"public_files": public_files(), "private_runtime_and_data_included": False}
    elif args.clean_tree:
        files = copy_public_tree(args.clean_tree)
        target = args.clean_tree.resolve()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(target)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        child = subprocess.run([sys.executable, "-X", "utf8", "-B", str(target / "scripts/verify_public_research.py")],
                               cwd=target, env=environment, text=True, capture_output=True, check=True)
        result = json.loads(child.stdout)
        result["copied_public_files"] = files
        result["private_runtime_and_data_included"] = False
    else:
        result = run_smoke()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
