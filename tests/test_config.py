from __future__ import annotations

import pathlib

import pytest

from agentflow.config import (
    DEFAULT_ID_PATTERN,
    ConfigError,
    find_root,
    load_config,
    parse_config,
)


def test_defaults_match_the_guide(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    assert cfg.language == "python"
    assert (cfg.paths.src, cfg.paths.tests, cfg.paths.specs, cfg.paths.out) == (
        "src",
        "tests",
        "specs",
        "out",
    )
    assert cfg.max_attempts == 3 and cfg.max_turns is None
    caps = {p: cfg.turn_cap(p) for p in ("TESTS", "IMPLEMENT", "REVIEW", "PR", "RETRO")}
    assert caps == {"TESTS": 50, "IMPLEMENT": 80, "REVIEW": 40, "PR": 30, "RETRO": 30}
    assert cfg.diff_coverage == 90 and cfg.mutation_score == 80
    assert cfg.id_pattern == DEFAULT_ID_PATTERN
    assert cfg.write_paths("TESTS") == ["tests/", "out/"]
    assert cfg.write_paths("IMPLEMENT") == ["src/", "out/"]
    assert cfg.write_paths("REVIEW") == ["tests/review/", "out/"]
    assert cfg.write_paths("SPEC") == ["specs/", "src/"]
    assert cfg.write_paths("PR") == ["out/"]
    assert cfg.frame_exceptions() == ["tests/review/"]


def test_full_config(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTFLOW_CLAUDE", raising=False)
    (tmp_path / "agentflow.toml").write_text(
        """
[project]
language = "python"
[paths]
src = "lib/"
out = "build/agentflow"
review_tests = "tests/adversarial"
[agents]
claude = "my-claude"
max_attempts = 5
[gates]
diff_coverage = 75.5
mutation_tool = "none"
[gates.commands]
lint = ["ruff", "check", "{src}"]
typecheck = []
[protected]
extra = ["config/"]
[phases.IMPLEMENT]
write = ["lib", "build/agentflow"]
tools = ["Read"]
[roles]
extra_dir = ".agentflow/roles"
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.loaded_from_file
    assert cfg.paths.src == "lib" and cfg.paths.out == "build/agentflow"
    assert cfg.out_dir == tmp_path / "build/agentflow"
    assert cfg.claude == "my-claude" and cfg.max_attempts == 5
    assert cfg.diff_coverage == 75.5 and cfg.mutation_tool == "none"
    assert cfg.gate_commands == {"lint": ["ruff", "check", "{src}"], "typecheck": []}
    assert cfg.protected_extra == ["config/"]
    assert cfg.write_paths("IMPLEMENT") == ["lib/", "build/agentflow/"]
    assert cfg.write_paths("TESTS") == ["tests/", "build/agentflow/"]
    assert cfg.phase_tools == {"IMPLEMENT": ["Read"]}
    assert cfg.frame_exceptions() == ["tests/adversarial/"]
    assert cfg.roles_extra_dir == ".agentflow/roles"


def test_env_overrides_claude(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTFLOW_CLAUDE", "/opt/fake-claude")
    assert parse_config(tmp_path, {"agents": {"claude": "x"}}).claude == "/opt/fake-claude"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"bogus": {}}, "unknown section"),
        ({"paths": {"source": "x"}}, "unknown key 'source'"),
        ({"agents": {"max_attempts": "3"}}, "must be int"),
        ({"agents": {"max_attempts": True}}, "must be int"),
        ({"agents": {"max_turns": 0}}, ">= 1"),
        ({"phases": {"RETRO": {"max_turns": 0}}}, "max_turns must be an int >= 1"),
        ({"phases": {"RETRO": {"max_turns": "9"}}}, "max_turns must be an int >= 1"),
        ({"gates": {"commands": {"build": ["x"]}}}, "unknown key 'build'"),
        ({"gates": {"commands": {"lint": "ruff"}}}, "list of strings"),
        ({"phases": {"DEPLOY": {"write": []}}}, "unknown phase"),
        ({"phases": {"TESTS": {"read": []}}}, "unknown key 'read'"),
        ({"paths": "src"}, "must be a table"),
    ],
)
def test_invalid_config(tmp_path: pathlib.Path, data: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(tmp_path, data)


def test_missing_and_broken_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ConfigError, match="agentflow init"):
        load_config(tmp_path)
    assert not load_config(tmp_path, require=False).loaded_from_file
    (tmp_path / "agentflow.toml").write_text("[paths\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_find_root_walks_up(tmp_path: pathlib.Path) -> None:
    (tmp_path / "agentflow.toml").write_text("", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_root(deep) == tmp_path.resolve()
    other = tmp_path.parent / f"{tmp_path.name}-no-config"
    other.mkdir()
    assert find_root(other) is None


def test_turn_caps(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(
        tmp_path, {"agents": {"max_turns": 60}, "phases": {"RETRO": {"max_turns": 12}}}
    )
    assert cfg.turn_cap("RETRO") == 12  # phase wins
    assert cfg.turn_cap("TESTS") == 60 and cfg.turn_cap("IMPLEMENT") == 60  # [agents] for all
    assert parse_config(tmp_path, {"phases": {"PR": {"max_turns": 5}}}).turn_cap("PR") == 5
