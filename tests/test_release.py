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


def test_the_skills_bring_the_cli_to_the_plugin_version() -> None:
    """Claude Code updates the plugin only; the skills that start work install the CLI of the
    plugin's own version, and the session-start notice has a line for it."""
    version = framegate.__version__
    install = f'uv tool install --force "git+{framegate.SOURCE_URL}@v{version}"'
    for name in ("feature", "bugfix", "refactor"):
        skill = (ROOT / f"plugin/skills/{name}/SKILL.md").read_text(encoding="utf-8")
        assert f"This plugin is version {version}." in skill, name
        assert install in skill and "!`framegate --version`" in skill, name
        assert "Bash(uv tool install *)" in skill and "PowerShell(uv tool install *)" in skill
        # checked before the conventions are read: they are printed again after an update
        assert skill.index("## Version") < skill.index("## Project conventions"), name
    notes = json.loads((ROOT / "plugin/whats-new.json").read_text(encoding="utf-8"))
    assert len(notes.get(version, "")) > 20 and "\n" not in notes[version]
    hooks = json.loads((ROOT / "plugin/hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    (start,) = hooks["SessionStart"]
    assert start["matcher"] == "startup"
    assert start["hooks"][0]["args"] == ["${CLAUDE_PLUGIN_ROOT}/scripts/session-start.js"]


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
