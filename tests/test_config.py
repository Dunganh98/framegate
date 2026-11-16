from __future__ import annotations

import pathlib

import pytest

from framegate.config import (
    DEFAULT_ID_PATTERN,
    ConfigError,
    drop_machine_files,
    find_root,
    is_machine_file,
    load_config,
    parse_config,
)

MACHINE = (
    *(".serena/project.yml", ".copilot/state.json", ".venv/Lib/site-packages/x/Dockerfile"),
    *(".aws/config", ".idea/workspace.xml", ".vscode/settings.json", ".cursor/rules/a.mdc"),
    *(".mypy_cache/3.12/a.json", ".config/dotnet-tools.json", ".husky/pre-commit"),
)
PROJECT = (
    *(".github/workflows/ci.yml", ".circleci/config.yml", ".azure-pipelines/build.yml"),
    *(".gitlab/ci/build.yml", ".azuredevops/pr.yml", ".buildkite/pipeline.yml"),
    *(".framegate/project.md", ".claude/settings.json", ".claude/settings.local.json"),
    ".devcontainer/devcontainer.json",
    # dot files at the root are project files
    *(".gitignore", ".dockerignore", ".gitlab-ci.yml", ".pre-commit-config.yaml"),
    *(".env.example", ".editorconfig", ".venv"),
    *("src/pkg/a.py", "src/.hidden/a.py", "tests/.data/x.yml", "local.settings.json"),
)


def test_machine_files_the_rule(tmp_path: pathlib.Path) -> None:
    """What lies in a top-level dot folder is tool state of this machine, except the deploy
    / CI folders, framegate's and Claude Code's own and the dev container; dot files at the
    root and dot folders deeper down are the project's."""
    cfg = parse_config(tmp_path, {})
    assert [p for p in MACHINE if not is_machine_file(cfg, p)] == []
    assert [p for p in PROJECT if is_machine_file(cfg, p)] == []
    assert is_machine_file(cfg, ".serena\\project.yml") and is_machine_file(cfg, "./.idea/x")
    assert drop_machine_files(cfg, [".serena/a", "src/a.py", ".gitignore"]) == [
        "src/a.py",
        ".gitignore",
    ]


def test_machine_files_follow_the_config(tmp_path: pathlib.Path) -> None:
    """A path the config names is project context also in a dot folder; `[git]
    machine_files` adds paths, `[git] project_folders` takes a dot folder back."""
    cfg = parse_config(
        tmp_path,
        {
            "paths": {"src": [".app/src", "lib"], "tests": ".app/tests", "specs": ".specs"},
            "protected": {"extra": [".deploy/", ".tools/ci.yml", "*.yml"]},
        },
    )
    project = (".app/src/a.py", ".app/tests/test_a.py", ".specs/f/plan.md", ".deploy/job.yml")
    assert [p for p in (*project, ".tools/ci.yml") if is_machine_file(cfg, p)] == []
    # only the named path, not the whole dot folder; a glob in `extra` takes nothing back
    assert is_machine_file(cfg, ".app/notes.md") and is_machine_file(cfg, ".tools/other.yml")
    assert is_machine_file(cfg, ".serena/project.yml")

    cfg = parse_config(
        tmp_path,
        {
            "git": {
                "machine_files": ["local.settings.json", "tools/cache/", "*.user", "**/.DS_Store"],
                "project_folders": [".config/", ".husky"],
            }
        },
    )
    assert cfg.machine_files == ["local.settings.json", "tools/cache/", "*.user", "**/.DS_Store"]
    assert cfg.project_folders == [".config/", ".husky/"]
    machine = ("local.settings.json", "tools/cache/a/b.bin", "src/App/App.csproj.user")
    machine = (*machine, "src/.DS_Store", ".DS_Store")
    assert [p for p in machine if not is_machine_file(cfg, p)] == []
    kept = ("local.settings.json.bak", "tools/cached.txt", ".config/x.json", ".husky/pre-commit")
    assert [p for p in kept if is_machine_file(cfg, p)] == []
    assert is_machine_file(cfg, ".vscode/settings.json")
    # the dotnet adapter's own setup file in a dot folder is a project file
    dotnet = parse_config(tmp_path, {"project": {"language": "dotnet"}})
    assert not is_machine_file(dotnet, ".config/dotnet-tools.json")
    assert is_machine_file(dotnet, ".config/other.json")


@pytest.mark.parametrize(
    ("section", "needle"),
    [
        ({"machine_files": "x"}, "[git] machine_files must be list, got str"),
        ({"machine_files": [1]}, "[git] machine_files must be a list of strings"),
        ({"machine_files": [""]}, "must be a path inside the repository"),
        ({"machine_files": ["/abs/x"]}, "must be a path inside the repository"),
        ({"machine_files": ["../x"]}, "must be a path inside the repository"),
        ({"machine_files": ["C:/x"]}, "must be a path inside the repository"),
        ({"machine_files": ["src/"]}, "would cover src, which the workflow needs"),
        ({"machine_files": ["*.toml"]}, "would cover framegate.toml"),
        ({"machine_files": [".framegate/"]}, "would cover .framegate"),
        ({"machine_files": ["**/project.md"]}, "would cover .framegate/project.md"),
        ({"project_folders": ["config/"]}, "must be a top-level folder whose name starts with"),
        ({"project_folders": [".a/b"]}, "must be a top-level folder whose name starts with"),
        ({"project_folders": [".git"]}, "must be a top-level folder whose name starts with"),
    ],
)
def test_machine_file_settings_are_validated(
    tmp_path: pathlib.Path, section: dict[str, object], needle: str
) -> None:
    with pytest.raises(ConfigError) as e:
        parse_config(tmp_path, {"git": section})
    assert needle in str(e.value)


def test_the_template_names_the_machine_file_settings() -> None:
    import framegate

    template = pathlib.Path(framegate.__file__).parent / "templates" / "framegate.toml"
    text = template.read_text(encoding="utf-8")
    assert '# machine_files = ["local.settings.json", "tools/cache/"]' in text
    assert '# project_folders = [".config/"]' in text


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
    caps = {p: cfg.turn_cap(p) for p in ("TESTS", "IMPLEMENT", "REVIEW", "SUMMARY", "RETRO")}
    assert caps == {"TESTS": 50, "IMPLEMENT": 80, "REVIEW": 40, "SUMMARY": 30, "RETRO": 30}
    assert cfg.diff_coverage == 90 and cfg.mutation_score == 80
    assert cfg.id_pattern == DEFAULT_ID_PATTERN
    assert cfg.write_paths("TESTS") == ["tests/", "out/"]
    assert cfg.write_paths("IMPLEMENT") == ["src/", "out/"]
    assert cfg.write_paths("REVIEW") == ["tests/review/", "out/"]
    assert cfg.write_paths("SPEC") == ["specs/", "src/"]
    assert cfg.write_paths("SUMMARY") == ["out/", "docs/code-map.md"]
    assert cfg.write_paths("TEST_REVIEW") == ["out/"]
    assert cfg.write_paths("TRIAGE") == ["tests/review/", "out/"]
    assert cfg.turn_cap("TEST_REVIEW") == 30 and cfg.turn_cap("TRIAGE") == 30
    assert cfg.frame_exceptions() == ["tests/review/"]
    assert cfg.tests_gate == "defer" and cfg.test_review_rounds == 2
    assert cfg.code_map == "docs/code-map.md" and cfg.max_file_lines == 400


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
    assert {p: cfg.agent_model(p) for p in ("TESTS", "IMPLEMENT", "SPEC_UPDATE", "SUMMARY")} == {
        "TESTS": "sonnet",
        "IMPLEMENT": "sonnet",
        "SPEC_UPDATE": "sonnet",
        "SUMMARY": "sonnet",
    }
    assert {cfg.agent_model(p) for p in ("TEST_REVIEW", "REVIEW", "TRIAGE", "RETRO")} == {"opus"}
    assert cfg.agent_effort("IMPLEMENT") == ""
    assert (cfg.step_batch_ids, cfg.format_fix, cfg.extra_checks) == (15, True, {})
    assert (cfg.diagnose_after, cfg.knowledge) == (2, ".framegate/project.md")
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
        ({"phases": {"SUMMARY": {"effort": "x"}}}, r"\[phases.SUMMARY\] effort must be one of"),
        ({"phases": {"SUMMARY": {"model": 3}}}, r"\[phases.SUMMARY\] model must be a string"),
        ({"agents": {"step_batch_ids": -1}}, "step_batch_ids must be >= 0"),
        ({"gates": {"extra": {"x": []}}}, r"\[gates.extra\] x must not be empty"),
        ({"gates": {"extra": {"x": "cmd"}}}, r"\[gates.extra\] x must be a list of strings"),
    ]:
        with pytest.raises(ConfigError, match=msg):
            parse_config(tmp_path, bad)


def test_full_config(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAMEGATE_CLAUDE", raising=False)
    (tmp_path / "framegate.toml").write_text(
        """
[project]
language = "python"
[paths]
src = "lib/"
out = "build/framegate"
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
write = ["lib", "build/framegate"]
tools = ["Read"]
[roles]
extra_dir = ".framegate/roles"
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.loaded_from_file
    assert cfg.paths.src == "lib" and cfg.paths.out == "build/framegate"
    assert cfg.out_dir == tmp_path / "build/framegate"
    assert cfg.claude == "my-claude" and cfg.max_attempts == 5
    assert cfg.diff_coverage == 75.5 and cfg.mutation_tool == "none"
    assert cfg.gate_commands == {"lint": ["ruff", "check", "{src}"], "typecheck": []}
    assert cfg.protected_extra == ["config/"]
    assert cfg.write_paths("IMPLEMENT") == ["lib/", "build/framegate/"]
    assert cfg.write_paths("TESTS") == ["tests/", "build/framegate/"]
    assert cfg.phase_tools == {"IMPLEMENT": ["Read"]}
    assert cfg.frame_exceptions() == ["tests/adversarial/"]
    assert cfg.roles_extra_dir == ".framegate/roles"


def test_builtin_deploy_ci_paths_and_the_opt_out(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    assert cfg.deploy_ci_paths() == [
        *(".github/", ".circleci/", ".azure-pipelines/"),
        *("**/Dockerfile", "**/Dockerfile.*", "**/*.dockerfile", "**/.dockerignore"),
        *("**/docker-compose*.yml", "**/docker-compose*.yaml"),
        *("**/compose*.yml", "**/compose*.yaml", "**/Jenkinsfile"),
        *("**/bitbucket-pipelines.yml", "**/azure-pipelines*.yml"),
        *("**/.gitlab-ci*.yml", "**/.travis.yml"),
    ]
    off = ["dockerfile", "**/compose*.yml", ".circleci", "Dockerfile"]
    cfg = parse_config(tmp_path, {"protected": {"builtin_off": off}})
    assert cfg.protected_builtin_off == ["Dockerfile", "compose*.yml", ".circleci/"]
    left = cfg.deploy_ci_paths()
    assert not {"**/Dockerfile", "**/compose*.yml", ".circleci/"} & set(left)
    assert {"**/Dockerfile.*", "**/compose*.yaml", ".github/"} <= set(left)
    for bad in (["deploy/"], ["src/app/Dockerfile"], ["*.yml"]):
        with pytest.raises(ConfigError, match="is not a built-in deploy / CI name"):
            parse_config(tmp_path, {"protected": {"builtin_off": bad}})
    with pytest.raises(ConfigError, match="builtin_off must be list"):
        parse_config(tmp_path, {"protected": {"builtin_off": "Dockerfile"}})


def test_env_overrides_claude(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAMEGATE_CLAUDE", "/opt/fake-claude")
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
        ({"phases": {"SUMMARY": {"max_budget_usd": -0.5}}}, "max_budget_usd must be a number >= 0"),
        ({"phases": {"SUMMARY": {"max_budget_usd": True}}}, "max_budget_usd must be a number >= 0"),
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
    with pytest.raises(ConfigError, match="framegate init"):
        load_config(tmp_path)
    assert not load_config(tmp_path, require=False).loaded_from_file
    (tmp_path / "framegate.toml").write_text("[paths\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_find_root_walks_up(tmp_path: pathlib.Path) -> None:
    (tmp_path / "framegate.toml").write_text("", encoding="utf-8")
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
    assert (
        parse_config(tmp_path, {"phases": {"SUMMARY": {"max_turns": 5}}}).turn_cap("SUMMARY") == 5
    )


def test_budget_caps(tmp_path: pathlib.Path) -> None:
    """Defaults ~3x the live FCSim cost per run; [phases.<P>] wins over [agents]; 0 = no cap."""
    cfg = parse_config(tmp_path, {})
    caps = {p: cfg.budget_cap(p) for p in ("TESTS", "TEST_REVIEW", "IMPLEMENT", "RETRO")}
    assert caps == {"TESTS": 2.0, "TEST_REVIEW": 1.5, "IMPLEMENT": 1.5, "RETRO": 3.0}
    cfg = parse_config(
        tmp_path,
        {"agents": {"max_budget_usd": 4}, "phases": {"SUMMARY": {"max_budget_usd": 0.75}}},
    )
    assert cfg.budget_cap("SUMMARY") == 0.75 and cfg.budget_cap("TESTS") == 4.0
    cfg = parse_config(
        tmp_path, {"agents": {"max_budget_usd": 0}, "phases": {"RETRO": {"max_budget_usd": 1}}}
    )
    assert cfg.budget_cap("TESTS") is None and cfg.budget_cap("RETRO") == 1.0
