"""New users can find the public entry points and follow repository-local links."""
from pathlib import Path
import re

import pytest

from quantfactra.cli import main


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("args,label", [
    (["--help"], "quantfactra"),
    (["demo", "--help"], "quantfactra demo"),
    (["research", "--config", "must-not-exist.json", "--help"], "quantfactra research"),
    (["team", "--help"], "quantfactra team"),
])
def test_public_help_is_offline_and_does_not_need_config(args, label, capsys):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 0
    assert label in capsys.readouterr().out


@pytest.mark.parametrize("relative", ["README.md", "README_EN.md", "CONTRIBUTING.md", "docs/quickstart.md",
    "docs/backends.md", "docs/ai-integration.md", "docs/project-plan.md", "docs/open-source-review.md"])
def test_onboarding_document_local_links_exist(relative):
    path = ROOT / relative
    content = path.read_text(encoding="utf-8")
    urls = re.findall(r"\]\(([^)]+)\)|(?:src|href)=\"([^\"]+)\"", content)
    for markdown, attribute in urls:
        target = markdown or attribute
        if target.startswith(("https://", "http://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        assert (path.parent / target).exists(), f"{relative}: missing {target}"


def test_new_user_configuration_does_not_replace_historical_baseline():
    from panda_alpha.cli import get_config, registry_baseline
    fresh = get_config(ROOT / "config/quantfactra.example.json")
    historical = get_config(ROOT / "config/panda-alpha.example.json")
    assert registry_baseline(fresh) == 0
    assert registry_baseline(historical) == 415
    assert fresh["platform"]["backend"] == "pandaai"
    assert fresh["llm"]["enabled"] is False
    assert fresh["llm"]["api_key_env"] == "QUANTFACTRA_LLM_KEY"
