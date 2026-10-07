from pathlib import Path
import json
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.verify_public_research import ROOT, copy_public_tree, public_files


class PublicResearchSourceTests(unittest.TestCase):
    def test_new_modules_tests_and_public_contract_are_in_source_list(self):
        files = public_files()
        for item in ("panda_alpha/financial_statements.py", "panda_alpha/study.py",
                     "panda_alpha/registry.py", "tests/study_fixture.py",
                     "tests/test_financial_statements.py", "docs/panda_alpha_native_contract.md"):
            self.assertIn(item, files)
        self.assertFalse(any(item.startswith(("research_runs/", ".runtime/", ".venv/", "data/")) for item in files))
        self.assertNotIn("config/panda-alpha.local.json", files)

    def test_ci_tracks_public_contract_and_uses_the_single_dependency_entry(self):
        text = (ROOT / ".github/workflows/panda-alpha.yml").read_text(encoding="utf-8")
        paths = [ast.literal_eval(line.split("paths:", 1)[1].strip())
                 for line in text.splitlines() if "paths:" in line]
        self.assertEqual(2, len(paths))
        self.assertEqual(paths[0], paths[1])
        for item in ("panda_alpha/**", "tests/**", "scripts/**", "README*.md", "config/**", "docs/**", "examples/**"):
            self.assertIn(item, paths[0])
        self.assertEqual(1, text.count("python -m pip install -r requirements-panda-alpha.txt"))
        self.assertEqual(1, text.count("pip install"))
        self.assertIn("scripts/verify_public_research.py", text)

    def test_allowlist_copy_has_no_private_files_and_never_replaces_existing_tree(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as parent:
            base = Path(parent)
            source = base / "source"
            for item in ("panda_alpha/fixture.py", "tests/test_fixture.py", "docs/public.md",
                         "config/panda-alpha.example.json", "config/panda-alpha.local.json",
                         "research_runs/market_data.json", ".runtime/token.json", "data/private.json"):
                target = source / item
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic", encoding="utf-8")
            clean = base / "clean"
            copied = copy_public_tree(clean, source)
            self.assertEqual(4, len(copied))
            self.assertFalse((clean / "config/panda-alpha.local.json").exists())
            self.assertFalse((clean / "research_runs").exists())
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                copy_public_tree(clean, source)


@unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell not installed")
class PortableWrapperTests(unittest.TestCase):
    def run_wrapper(self, args, expected):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        environment = os.environ.copy()
        # Ensure PATH fallback uses the same installed-dependency interpreter as
        # the test process. No caller-specific drive is embedded in the wrapper.
        environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
        invocation = "& $env:PANDA_TEST_WRAPPER " + " ".join(args)
        command = "\n".join((
            "$ErrorActionPreference = 'Stop'",
            "$PSNativeCommandUseErrorActionPreference = $true",
            "$before = (Get-Location).Path",
            "$env:PYTHONPATH = 'public-wrapper-sentinel'",
            "$env:PYTHONUTF8 = '0'",
            "$env:PYTHONIOENCODING = 'ascii'",
            invocation,
            "$code = $LASTEXITCODE",
            "if ((Get-Location).Path -ne $before) { throw 'Working directory was not restored' }",
            "if ($env:PYTHONPATH -ne 'public-wrapper-sentinel' -or $env:PYTHONUTF8 -ne '0' -or $env:PYTHONIOENCODING -ne 'ascii') { throw 'Python environment was not restored' }",
            "$summary = @{ code=$code; cwdRestored=$true; environmentRestored=$true } | ConvertTo-Json -Compress",
            "Write-Output ('WRAPPER_TEST:' + $summary)",
        ))
        environment["PANDA_TEST_WRAPPER"] = str(ROOT / "scripts/Invoke-PandaResearch.ps1")
        environment["PANDA_TEST_PYTHON"] = sys.executable
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                                    cwd=directory, env=environment, text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        summary = next(line for line in result.stdout.splitlines() if line.startswith("WRAPPER_TEST:"))
        data = json.loads(summary[len("WRAPPER_TEST:"):])
        self.assertEqual(expected, data["code"])
        return result

    def test_default_path_python_cli_help_needs_no_local_config(self):
        result = self.run_wrapper(["-Config", "public-config-must-not-exist.json", "--help"], 0)
        self.assertIn("evaluate", result.stdout)

    def test_explicit_python_preserves_failed_cli_exit_and_environment(self):
        result = self.run_wrapper(["-Python", "$env:PANDA_TEST_PYTHON", "-Config",
                                   "public-config-must-not-exist.json", "public-command-does-not-exist"], 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
