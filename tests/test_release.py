"""One version for the CLI and the plugin, installable from the release tag: the plugin comes from
the marketplace in this repository, and its setup skill installs the CLI of the same version."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

import framegate
from framegate.adapters import get_adapter
from framegate.adapters import python as python_adapter
from framegate.config import parse_config

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_one_version_everywhere() -> None:
    version = framegate.__version__
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(version)}"$', pyproject, re.M)
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == version
    skill = (ROOT / "plugin/skills/setup/SKILL.md").read_text(encoding="utf-8")
    assert f'uv tool install "git+{framegate.SOURCE_URL}@v{version}"' in skill
    assert "Bash(uv tool install *)" in skill and "PowerShell(uv tool install *)" in skill


def test_marketplace_serves_the_plugin_from_this_repository() -> None:
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == "framegate")
    assert (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").is_file()
    owner_repo = framegate.SOURCE_URL.removeprefix("https://github.com/")
    assert f"claude plugin marketplace add {owner_repo}" in (ROOT / "README.md").read_text("utf-8")


def test_project_gets_this_version_from_the_tag_without_a_checkout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed CLI (no source tree) puts its own version into the project environment
    from the release tag, never a same-named package from an index."""
    assert framegate.release_requirement() == (
        f"framegate @ git+{framegate.SOURCE_URL}@v{framegate.__version__}"
    )
    adapter = get_adapter(parse_config(tmp_path, {"project": {"language": "python"}}))
    monkeypatch.setattr(python_adapter, "framegate_source", lambda: None)
    own = adapter.tool_requirements()[0]  # type: ignore[attr-defined]
    assert own.module == "framegate" and own.pip == (framegate.release_requirement(),)
