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
    assert cfg.write_paths("TEST_REVIEW") == ["out/"]
    assert cfg.write_paths("TRIAGE") == ["tests/review/", "out/"]
    assert cfg.turn_cap("TEST_REVIEW") == 30 and cfg.turn_cap("TRIAGE") == 30
    assert cfg.frame_exceptions() == ["tests/review/"]
    assert cfg.tests_gate == "defer" and cfg.test_review_rounds == 2


def test_tests_gate_and_review_rounds(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {"gates": {"tests": "auto"}, "agents": {"test_review_rounds": 0}})
    assert cfg.tests_gate == "auto" and cfg.test_review_rounds == 0
    with pytest.raises(ConfigError, match="defer, human, auto"):
        parse_config(tmp_path, {"gates": {"tests": "skip"}})
    with pytest.raises(ConfigError, match="test_review_rounds must be >= 0"):
        parse_config(tmp_path, {"agents": {"test_review_rounds": -1}})


def test_models_effort_batches_format_fix_and_extra_checks(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    # defaults: the spec-following writers on Sonnet, the checkers and the retro on Opus
    assert {p: cfg.agent_model(p) for p in ("TESTS", "IMPLEMENT", "SPEC_UPDATE", "PR")} == {
        "TESTS": "sonnet",
        "IMPLEMENT": "sonnet",
        "SPEC_UPDATE": "sonnet",
        "PR": "sonnet",
    }
    assert {cfg.agent_model(p) for p in ("TEST_REVIEW", "REVIEW", "TRIAGE", "RETRO")} == {"opus"}
    assert cfg.agent_effort("IMPLEMENT") == ""
    assert (cfg.step_batch_ids, cfg.format_fix, cfg.extra_checks) == (15, True, {})
    assert (cfg.diagnose_after, cfg.knowledge) == (2, ".agentflow/project.md")
    assert cfg.agent_model("DIAGNOSIS") == "opus" and cfg.write_paths("DIAGNOSIS") == ["out/"]
    other = parse_config(
        tmp_path, {"agents": {"diagnose_after": 0}, "project": {"knowledge": "docs\\k.md"}}
    )
    assert (other.diagnose_after, other.knowledge) == (0, "docs/k.md")
    with pytest.raises(ConfigError, match="diagnose_after must be >= 0"):
        parse_config(tmp_path, {"agents": {"diagnose_after": -1}})
    cfg = parse_config(
        tmp_path,
        {
            "agents": {"model": "opus", "effort": "high", "step_batch_ids": 0},
            "phases": {"IMPLEMENT": {"model": "claude-sonnet-5-5", "effort": "medium"}},
            "gates": {
                "format_fix": False,
                "extra": {"readcheck": ["dotnet", "run", "--", "{out}"]},
                "extra_timeout": 600,
            },
        },
    )
    assert cfg.agent_model("TESTS") == "opus" and cfg.agent_effort("TESTS") == "high"
    assert cfg.agent_model("IMPLEMENT") == "claude-sonnet-5-5"
    assert cfg.agent_effort("IMPLEMENT") == "medium"
    assert parse_config(tmp_path, {"agents": {"model": ""}}).agent_model("TESTS") == ""
    assert (cfg.step_batch_ids, cfg.format_fix, cfg.extra_timeout) == (0, False, 600)
    assert cfg.extra_checks == {"readcheck": ["dotnet", "run", "--", "{out}"]}
    for bad, msg in [
        ({"agents": {"effort": "huge"}}, r"\[agents\] effort must be one of low"),
        ({"phases": {"PR": {"effort": "x"}}}, r"\[phases.PR\] effort must be one of"),
        ({"phases": {"PR": {"model": 3}}}, r"\[phases.PR\] model must be a string"),
        ({"agents": {"step_batch_ids": -1}}, "step_batch_ids must be >= 0"),
        ({"gates": {"extra": {"x": []}}}, r"\[gates.extra\] x must not be empty"),
        ({"gates": {"extra": {"x": "cmd"}}}, r"\[gates.extra\] x must be a list of strings"),
    ]:
        with pytest.raises(ConfigError, match=msg):
            parse_config(tmp_path, bad)


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
        ({"agents": {"max_budget_usd": -1}}, "max_budget_usd must be >= 0"),
        ({"agents": {"max_budget_usd": "2"}}, "must be int/float"),
        ({"phases": {"PR": {"max_budget_usd": -0.5}}}, "max_budget_usd must be a number >= 0"),
        ({"phases": {"PR": {"max_budget_usd": True}}}, "max_budget_usd must be a number >= 0"),
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


def test_budget_caps(tmp_path: pathlib.Path) -> None:
    """Defaults ~3x the live FCSim cost per run; [phases.<P>] wins over [agents]; 0 = no cap."""
    cfg = parse_config(tmp_path, {})
    caps = {p: cfg.budget_cap(p) for p in ("TESTS", "TEST_REVIEW", "IMPLEMENT", "RETRO")}
    assert caps == {"TESTS": 2.0, "TEST_REVIEW": 1.5, "IMPLEMENT": 1.5, "RETRO": 3.0}
    cfg = parse_config(
        tmp_path,
        {"agents": {"max_budget_usd": 4}, "phases": {"PR": {"max_budget_usd": 0.75}}},
    )
    assert cfg.budget_cap("PR") == 0.75 and cfg.budget_cap("TESTS") == 4.0
    cfg = parse_config(
        tmp_path, {"agents": {"max_budget_usd": 0}, "phases": {"RETRO": {"max_budget_usd": 1}}}
    )
    assert cfg.budget_cap("TESTS") is None and cfg.budget_cap("RETRO") == 1.0
