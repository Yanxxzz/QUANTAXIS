"""The neutral CLI must work without credentials or a local config file."""
import subprocess
import sys
from pathlib import Path

import pytest

from quant_research.cli import main


@pytest.mark.parametrize("arguments, expected", [
    (["--help"], "quant-research"),
    (["research", "--config", "must-not-exist.json", "--help"], "quant-research research"),
    (["team", "--help"], "quant-research team"),
])
def test_help_does_not_read_configuration(arguments, expected, capsys):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 0
    assert expected in capsys.readouterr().out


def test_module_entry_point_from_checkout():
    result = subprocess.run([sys.executable, "-m", "quant_research", "team", "--help"],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=True)
    assert "host-demo" in result.stdout
    assert "quant-research team" in result.stdout


def test_unknown_workflow_is_rejected(capsys):
    with pytest.raises(SystemExit) as error:
        main(["missing-workflow"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
