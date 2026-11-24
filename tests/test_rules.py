"""The project's CI rules as gate checks (`[checks] rules`), their generated task block and
the setup's proposal."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import DemoRepo, git
from framegate import rules
from framegate.adapters import get_adapter
from framegate.adapters.base import CheckResult
from framegate.config import ConfigError, load_config, parse_config
from framegate.orchestrator import EXIT_GATE

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
#: fails on a file that holds the word FORBIDDEN; --rules prints the rule; --legacy always fails
SCRIPT = """\
import pathlib
import sys

if "--rules" in sys.argv:
    print("R1: no file holds the forbidden word")
    raise SystemExit(0)
if "--legacy" in sys.argv:
    print("legacy: 3 violations in generated code")
    raise SystemExit(1)
bad = 0
for arg in sys.argv[1:]:
    for path in sorted(pathlib.Path(arg).rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "FORBIDDEN" in line:
                print(f"{path.as_posix()}:{n}: R1 forbidden word")
                bad += 1
raise SystemExit(1 if bad else 0)
"""
CHECKS = (
    '[checks]\nrules = ["python ci/check_rules.py src", "python ci/check_rules.py --legacy"]\n'
    'rules_text = "python ci/check_rules.py --rules"\n'
)


def with_rules(r: DemoRepo) -> None:
    (r.root / "ci").mkdir()
    (r.root / "ci/check_rules.py").write_text(SCRIPT, encoding="utf-8")
    r.add_toml(FAST + CHECKS)


def test_config_and_check_names(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {"checks": {"rules": [" make rules "], "rules_text": "x --rules"}})
    assert cfg.rule_commands == ["make rules"] and cfg.rules_text_command == "x --rules"
    assert parse_config(tmp_path, {}).rule_commands == []
    with pytest.raises(ConfigError, match=r"\[checks\] rules: a command must not be empty"):
        parse_config(tmp_path, {"checks": {"rules": [""]}})
    assert (
        rules.name("python ci/check_rules.py src") == "project rule: python ci/check_rules.py src"
    )
    assert len(rules.name("x " * 80)) < 80


def test_lines_from_before_the_task_are_not_the_tasks() -> None:
    red = CheckResult("FAIL", "legacy: 3 violations\n\nnew: 1 violation\n")
    kept = rules.without_before(red, ["legacy: 3 violations"])
    assert kept.status == "FAIL" and "legacy" not in kept.output.split("(")[0]
    assert "new: 1 violation" in kept.output and "1 line(s)" in kept.summary
    same = rules.without_before(red, ["legacy: 3 violations", "new: 1 violation"])
    assert same.status == "PASS" and same.summary.startswith(rules.BEFORE_NOTE)
    assert rules.without_before(red, []) is red
    crashed = CheckResult("FAIL", "legacy: 3 violations", tool_failure=True)
    assert rules.without_before(crashed, ["legacy: 3 violations"]) is crashed


def test_a_rule_runs_at_the_gates_and_debt_is_not_blamed_on_the_task(demo_repo: DemoRepo) -> None:
    r = demo_repo
    with_rules(r)
    # debt from before the task: a finding in a file the task never touches
    (r.root / "src/demo/old.py").write_text("X = 'FORBIDDEN'\n", encoding="utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "old debt")
    r.defer_tests_gate()
    assert r.af("approve-spec", "demo") == 0
    recorded = json.loads((r.root / "out/demo/rules_baseline.json").read_text("utf-8"))
    assert recorded["python ci/check_rules.py --legacy"] == [
        "legacy: 3 violations in generated code"
    ]
    assert recorded["python ci/check_rules.py src"] == ["src/demo/old.py:1: R1 forbidden word"]
    assert r.af("run", "demo") == EXIT_GATE
    assert "Final gates PASSED" in r.progress()["waiting"]["message"]

    names = {e["name"] for e in r.events() if e["kind"] == "check"}
    assert {
        "project rule: python ci/check_rules.py src",
        "project rule: python ci/check_rules.py --legacy",
    } <= names
    step = [e for e in r.events() if e["kind"] == "check" and e.get("scope") == "step"]
    assert [e["status"] for e in step if e["name"].startswith("project rule")] == ["PASS", "PASS"]
    report = (r.root / "out/demo/final_gates.md").read_text("utf-8")
    assert "[PASS] project rule: python ci/check_rules.py --legacy" in report
    assert "printed the same before the task (left out): 1 line(s)" in report

    # the implementer's task: the commands and the printed rules, generated
    task = next(c["prompt"] for c in r.calls() if c["phase"] == "IMPLEMENT")
    assert "## Project rules (generated" in task
    assert "`python ci/check_rules.py src`, `python ci/check_rules.py --legacy`" in task
    assert "R1: no file holds the forbidden word" in task
    assert "## Project rules" not in next(c["prompt"] for c in r.calls() if c["phase"] == "TESTS")

    # a finding on a line the task wrote fails the check, with the tool's own line
    adapter = get_adapter(load_config(r.root))
    path = r.root / "src/demo/__init__.py"
    path.write_text(path.read_text("utf-8") + "\nY = 'FORBIDDEN'\n", encoding="utf-8")
    first, legacy = (c.run() for c in rules.checks(adapter, "demo"))
    assert first.status == "FAIL" and legacy.status == "PASS"
    assert "src/demo/__init__.py:" in first.output and "old.py:1" not in first.output.split("(")[0]
    # without a feature (doctor): the raw run, debt included
    raw = rules.checks(adapter, "")[0].run()
    assert raw.status == "FAIL" and "src/demo/old.py:1: R1 forbidden word" in raw.output


def test_files_placeholder_and_a_command_that_cannot_start(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    cfg.rule_commands = ["python ci/check_rules.py {files}", "no-such-rule-tool-xyz src"]
    adapter = get_adapter(cfg)
    listed, missing = (c.run() for c in rules.checks(adapter, ""))
    assert listed.status == "PASS" and listed.summary == "no changed file to check"
    assert missing.status == "FAIL" and missing.tool_failure


def test_the_task_block_names_the_lint_selection(demo_repo: DemoRepo) -> None:
    r = demo_repo
    adapter = get_adapter(load_config(r.root))
    assert rules.lint_selection(adapter) == "" and rules.text_block(adapter) == ""
    with open(r.root / "pyproject.toml", "a", encoding="utf-8") as fh:
        fh.write('\n[tool.ruff]\nline-length = 100\n[tool.ruff.lint]\nselect = ["E", "F"]\n')
        fh.write('ignore = ["E501"]\n')
    assert rules.lint_selection(adapter) == (
        "ruff (pyproject.toml): select E, F; ignore E501; line length 100"
    )
    block = rules.text_block(adapter)
    assert block.splitlines()[0].startswith("## Project rules (generated")
    assert "- Lint rules: ruff (pyproject.toml): select E, F" in block and "~~~" not in block
    off = load_config(r.root)
    off.gate_commands["lint"] = []
    assert rules.text_block(get_adapter(off)) == ""


def test_setup_proposes_a_rules_script_and_its_ci_step(tmp_path: pathlib.Path) -> None:
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci/check_rules.py").write_text(SCRIPT, encoding="utf-8")
    (tmp_path / "ci/build.yml").write_text(
        "steps:\n  - run: python ci/check_rules.py ci/allowlist.txt src tests\n", "utf-8"
    )
    files = ["ci/check_rules.py", "ci/build.yml", "src/app/rules.py", "scripts/deploy.sh"]
    found = rules.detect(tmp_path, files, ["ci/build.yml"])
    assert found["rules"] == ["python ci/check_rules.py ci/allowlist.txt src tests"]
    assert found["rules_text"] == "python ci/check_rules.py --rules"
    assert found["why"] == "ci/build.yml calls ci/check_rules.py"
    alone = rules.detect(tmp_path, files)
    assert alone["rules"] == ["python ci/check_rules.py"] and "check them" in alone["why"]
    assert rules.detect(tmp_path, ["src/app/rules.py", "scripts/deploy.sh"]) == {}
