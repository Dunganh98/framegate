"""`framegate setup --plan` / `--apply`: the setup as two commands (one table to confirm,
then the files, the check and what is left)."""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import pytest

from conftest import git, init_git
from framegate import cli, doctor, scaffold, setup_cmd, survey
from framegate.config import load_config

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PYPROJECT = """\
[project]
name = "app"
version = "0.1.0"

[tool.black]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["checks"]
"""
RULES = 'import sys\nif "--rules" in sys.argv:\n    print("R1")\n'


@pytest.fixture
def project(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A team's repo before any setup: packages in the root, black, a CI step with a rules
    script, a deploy folder. The tool install, the survey agent and doctor are stubbed."""
    root = tmp_path / "app"
    root.mkdir()
    init_git(root)
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    for name in ("core", "checks", "ci", "deploy"):
        (root / name).mkdir()
    (root / "core/__init__.py").write_text("X = 1\n", encoding="utf-8")
    (root / "checks/test_core.py").write_text("def test_x():\n    assert True\n", "utf-8")
    (root / "ci/check_rules.py").write_text(RULES, encoding="utf-8")
    (root / "ci/build.yml").write_text("- run: python ci/check_rules.py core\n", "utf-8")
    (root / "deploy/job.yml").write_text("x: 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    calls: list[Any] = []
    monkeypatch.setattr(scaffold, "install_tools", lambda *a, **k: calls.append(a) or 0)

    def mapped(cfg: Any, adapter: Any, facts: Any) -> dict[str, Any]:
        data = {"src": [{"path": "core", "evidence": "packages"}], "tests": {"path": "checks"}}
        survey.path(cfg).parent.mkdir(parents=True, exist_ok=True)
        survey.path(cfg).write_text(json.dumps({**data, "excluded": []}), encoding="utf-8")
        return data

    monkeypatch.setattr(survey, "survey", mapped)
    return root


def check(lines: list[str], code: int = 0) -> Any:
    def run(cfg: Any, echo: Any, **kw: Any) -> int:
        assert kw == {"only_brief": True, "tree": True}
        for line in lines:
            echo(line)
        return code

    return run


def test_set_key_keeps_the_comments_and_replaces_one_value() -> None:
    text = (
        '[project]\nlanguage = "python"   # kept\n\n[paths]\n# src = "src"   # one folder\n'
        '# tests = "tests"\n\n# [env]\n# python = "x"   # default\n#   more words\n\n'
        '[gates]\nunrunnable_tests = [\n  { test = "a/t.py", reason = "r" },\n]\nfinal = "human"\n'
    )
    out = setup_cmd.set_key(text, "paths", "src", setup_cmd.literal(["core", "lib"]))
    assert '[paths]\nsrc = ["core", "lib"]\n# tests = "tests"\n' in out
    out = setup_cmd.set_key(out, "env", "python", setup_cmd.literal(".venv/bin/python"))
    assert '\n[env]\npython = ".venv/bin/python"\n#   more words\n' in out
    out = setup_cmd.set_key(out, "gates.commands", "lint", "[]")
    assert out.endswith("\n[gates.commands]\nlint = []\n")
    red = setup_cmd.literal([{"test": "b/t.py::t", "reason": "needs a database"}])
    out = setup_cmd.set_key(out, "gates", "unrunnable_tests", red)
    data = tomllib.loads(out)
    assert data["gates"] == {
        "unrunnable_tests": [{"test": "b/t.py::t", "reason": "needs a database"}],
        "final": "human",
        "commands": {"lint": []},
    }
    assert data["paths"] == {"src": ["core", "lib"]} and "# kept" in out
    assert setup_cmd.literal(True) == "true"


def test_plan_prints_one_table_and_keeps_the_plan(
    project: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["setup", "--plan"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    at = lines.index("| # | What | Value | From | Answers |")
    rows = [x for x in lines[at + 2 :] if x.startswith("| ")]
    assert [r.split(" | ")[1] for r in rows] == list(setup_cmd.ROWS.values())
    assert lines[0] == "Survey: 1 source folder(s) mapped from the code."
    assert lines[1].startswith('**Please confirm the setup** - reply "ok", or the row')
    assert "| 7 | Source / test folders | core / checks | the survey, out/survey.json |" in out
    assert "| 6 | Types | off | the project names no such tool |" in out
    assert "| 10 | Locked deploy / CI paths | ci/, deploy/ |" in out
    assert "| 13 | Project rule checks (every step gate) | python ci/check_rules.py core " in out
    assert "(rules printed by `python ci/check_rules.py --rules`)" in out
    assert "| 14 | Mutation testing | on |" in out
    assert "**Machine files, ignored by framegate** - none tracked" in out
    assert "**Never approved without you**" in out and "schema / migration files" in out
    assert "Blocker: which interpreter does the project run in?" in out
    assert "`framegate setup --apply`" in lines[-1]
    assert not (project / "framegate.toml").exists()  # nothing is written before the ok

    plan = json.loads((project / "out" / setup_cmd.PLAN_FILE).read_text("utf-8"))
    by_key = {r["key"]: r for r in plan["rows"]}
    assert by_key["folders"]["edits"] == [["paths", "src", "core"], ["paths", "tests", "checks"]]
    assert by_key["typecheck"]["edits"] == [["gates.commands", "typecheck", []]]
    assert by_key["locked"]["edits"] == [["protected", "extra", ["ci/", "deploy/"]]]
    assert by_key["rules"]["edits"] == [
        ["checks", "rules", ["python ci/check_rules.py core"]],
        ["checks", "rules_text", "python ci/check_rules.py --rules"],
    ]
    assert ["gates.commands", "format_fix", ["python", "-m", "black", "{files}"]] in (
        by_key["format"]["edits"]
    )


def test_corrections_by_row_number_or_name() -> None:
    plan = {"language": "python", "rows": [{"key": k} for k in setup_cmd.ROWS]}
    fix = setup_cmd.correction
    assert fix(plan, "5", "python -m black --check app") == [
        ["gates.commands", "format", ["python", "-m", "black", "--check", "app"]]
    ]
    assert fix(plan, "6", "off") == [["gates.commands", "typecheck", []]]
    assert fix(plan, "7", "app, lib / checks") == [
        ["paths", "src", ["app", "lib"]],
        ["paths", "tests", "checks"],
    ]
    assert fix(plan, "9", "no") == [["git", "state_branch", ""]]
    assert fix(plan, "state_branch", "yes") == [["git", "state_branch", "framegate/{base}"]]
    assert fix(plan, "10", "none") == [["protected", "extra", []]]
    assert fix(plan, "3", '-m "not slow"') == [["python", "pytest_args", ["-m", "not slow"]]]
    assert fix(plan, "13", "make rules; make more") == [
        ["checks", "rules", ["make rules", "make more"]]
    ]
    assert fix(plan, "mutation", "off") == [["gates", "mutation_tool", "none"]]
    assert fix(plan, "12", "otto=../otto") == [["references", "otto", "../otto"]]
    assert fix(plan, "machine_files", "local.json") == [["git", "machine_files", ["local.json"]]]
    for key, value in (("9", "maybe"), ("99", "x"), ("mutation", "later")):
        with pytest.raises(setup_cmd.SetupError):
            fix(plan, key, value)


def test_apply_writes_the_confirmed_plan_and_says_what_is_left(
    project: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cli.main(["setup", "--apply"]) == 1
    assert "no setup plan here. Run `framegate setup --plan` first" in capsys.readouterr().out
    assert cli.main(["setup", "--plan"]) == 0
    capsys.readouterr()

    # a correction that names no option: refused, nothing written
    assert cli.main(["setup", "--apply", "--set", "9=perhaps"]) == 1
    assert "error: row 9: yes or no" in capsys.readouterr().out
    assert not (project / "framegate.toml").exists()

    monkeypatch.setattr(doctor, "run", check(["Verdict: ready", "Tree: green."]))
    args = ["--set", "2=.venv/Scripts/python.exe", "--set", "4=off", "--set", "base=develop"]
    assert cli.main(["setup", "--apply", *args]) == 0
    out = capsys.readouterr().out
    cfg = load_config(project)
    assert (cfg.src_roots, cfg.paths.tests) == (["core"], "checks")
    assert cfg.gate_commands["lint"] == [] and cfg.gate_commands["typecheck"] == []
    assert cfg.gate_commands["format"][:3] == ["python", "-m", "black"]
    assert cfg.format_fix_command == ["python", "-m", "black", "{files}"]
    assert cfg.python == ".venv/Scripts/python.exe" and cfg.git_base == "develop"
    assert cfg.protected_extra == ["ci/", "deploy/"]
    assert cfg.rule_commands == ["python ci/check_rules.py core"]
    assert cfg.rules_text_command == "python ci/check_rules.py --rules"
    assert cfg.final_gate == "human" and cfg.state_branch == ""
    assert (project / "specs/_template/plan.md").is_file()
    assert "Written: framegate.toml" in out and '[paths] src = "core"' in out
    assert "Setup check (all tests ran once):\nVerdict: ready\nTree: green." in out
    left = out[out.index("Left to do:") :]
    assert ".framegate/project.md: still the empty template" in left
    assert "docs/code-map.md: no code map yet" in left
    assert "permissions.allow in .claude/settings.local.json" in left
    assert "`framegate commit-setup`" in left and "framegate.toml" in left

    # again, with a red test the user agreed to list: the earlier corrections are kept
    red = ["PROBLEM: 1 test(s) fail on the untouched tree and are not listed:", "  x"]
    monkeypatch.setattr(doctor, "run", check(["Verdict: NOT READY", *red], 1))
    assert cli.main(["setup", "--apply", "--red", "checks/test_core.py::test_x=needs a db"]) == 1
    out = capsys.readouterr().out
    assert "The setup is NOT ready (lines above)" in out
    cfg = load_config(project)
    assert cfg.unrunnable_tests == {"checks/test_core.py::test_x": "needs a db"}
    assert cfg.gate_commands["lint"] == [] and cfg.git_base == "develop"
    text = (project / "framegate.toml").read_text("utf-8")
    assert text.count("\nunrunnable_tests = [") == 1

    monkeypatch.setattr(doctor, "run", check(["Verdict: ready", "MUTATION: the trial failed: x"]))
    assert cli.main(["setup", "--apply"]) == 1  # ready only once mutation is decided
    capsys.readouterr()
    monkeypatch.setattr(doctor, "run", check(["Verdict: ready"]))
    assert cli.main(["setup", "--apply", "--set", "14=off"]) == 0
    assert load_config(project).mutation_tool == "none"
    assert "nothing to change" not in capsys.readouterr().out.split("Setup check")[0]
    assert cli.main(["setup", "--apply"]) == 0
    assert "framegate.toml: nothing to change" in capsys.readouterr().out


def test_plan_with_a_config_shows_the_configured_values(
    project: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "run", check(["Verdict: ready"]))
    assert cli.main(["setup", "--plan"]) == 0
    assert cli.main(["setup", "--apply", "--set", "14=off"]) == 0
    capsys.readouterr()
    assert cli.main(["setup", "--plan"]) == 0
    out = capsys.readouterr().out
    assert "| 14 | Mutation testing | off | framegate.toml [gates] mutation_tool |" in out
    assert "| 13 | Project rule checks (every step gate) | python ci/check_rules.py core | " in out
    plan = json.loads((project / "out" / setup_cmd.PLAN_FILE).read_text("utf-8"))
    assert all(not r["edits"] for r in plan["rows"] if r["key"] != "locked")
