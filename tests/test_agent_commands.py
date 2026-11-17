"""The commands an agent's task names are the project's own, and its tool rules allow them.

Seen live (da-argos): the brief named `ruff check orchestrator tests` and `ruff format` while
the project lints with `python -m ruff check` over five source folders and formats with black;
the reviewer and the triage agent ran the brief's command and were denied."""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest

from conftest import DemoRepo
from framegate import doctor
from framegate.adapters import get_adapter
from framegate.adapters.base import LanguageAdapter
from framegate.agent_commands import (
    FILES_ARG,
    command_prefix,
    command_rules,
    lint_line,
    rule_allows,
)
from framegate.config import load_config, parse_config
from framegate.orchestrator import BRIEF_PHASES, TASK_PHASE, Orchestrator
from framegate.setup_checks import BRIEF_RUN_PHASES, denied_brief_commands, renamed_names

#: da-argos: black as formatter, ruff through the interpreter, no type check, five roots
ARGOS: dict[str, Any] = {
    "paths": {"src": ["model", "data", "orchestrator", "utils", "platform/services"]},
    "python": {"pytest_args": ["-m", "not integration and not spark"]},
    "gates": {
        "commands": {
            "lint": ["python", "-m", "ruff", "check", "{src}", "{tests}"],
            "format": ["python", "-m", "black", "--check", "{src}", "{tests}"],
            "format_fix": ["python", "-m", "black", "{files}"],
            "typecheck": [],
        }
    },
}
GENERIC: dict[str, Any] = {
    "project": {"language": "generic"},
    "generic": {
        "tag_pattern": r"req\(([^)]*)\)",
        "test_all": ["go", "test", "./..."],
        "test_ids": ["go", "test", "-run", "{ids_regex}", "./..."],
        "format_fix": ["gofmt", "-w", "{files}"],
    },
    "gates": {"commands": {"lint": ["golangci-lint", "run", "{src}"]}},
}


def adapter(root: pathlib.Path, data: dict[str, Any]) -> LanguageAdapter:
    return get_adapter(parse_config(root, data))


def test_rules_and_matching() -> None:
    paths = ["src", "tests", "platform/services"]
    assert command_prefix(["python", "-m", "black", "--check", "src", "tests"], paths) == [
        "python",
        "-m",
        "black",
        "--check",
    ]
    assert command_prefix(["black", FILES_ARG], paths) == ["black"]
    assert command_prefix(["tool", "platform/services/x.py"], paths) == ["tool"]
    assert command_rules(["python", "-m", "mypy", "src"], paths) == ["Bash(python -m mypy *)"]
    # no file argument: as it stands, and with arguments
    assert command_rules(["lint-imports"], paths) == ["Bash(lint-imports)", "Bash(lint-imports *)"]
    assert command_rules([], paths) == []
    rules = ["Read", "Bash(python -m ruff check *)", "PowerShell(lint-imports)", "Bash(old:*)"]
    assert rule_allows(rules, "python -m ruff check model tests")
    assert rule_allows(rules, "python -m ruff check")
    assert not rule_allows(rules, "python -m ruff format model")
    assert not rule_allows(rules, "python -m ruff checkx")
    assert rule_allows(rules, "lint-imports") and not rule_allows(rules, "lint-imports --x")
    assert rule_allows(rules, "old thing") and rule_allows(["Bash"], "anything at all")
    assert lint_line([], None) == ""
    assert "not yours to run" in lint_line(["ruff check src"], ["Read"])
    assert lint_line(["a x", "b <files>"], ["Bash(b *)"]) == (
        "- Lint / format / types: `b <files>` (<files> = the files you changed)."
    )


def test_python_defaults(tmp_path: pathlib.Path) -> None:
    a = adapter(tmp_path, {})
    assert [" ".join(c) for c in a.agent_commands()] == [
        "python -m ruff check src tests",
        "python -m ruff format <files>",
        "python -m mypy src",
    ]
    assert a.lint_tools() == [
        "Bash(python -m ruff check *)",
        "Bash(python -m ruff format *)",
        "Bash(python -m mypy *)",
        "Bash(ruff check *)",
        "Bash(ruff format *)",
        "Bash(mypy *)",
    ]
    brief = a.conventions_brief()
    assert (
        "- Run tests: `python -m pytest -q` ; only some IDs: `python -m pytest -q --req " in brief
    )
    assert (
        "- Lint / format / types: `python -m ruff check src tests`, "
        "`python -m ruff format <files>`, `python -m mypy src` (<files> = the files you changed)."
    ) in brief
    assert "\n\n" not in brief and len(brief.encode()) < 1500


def test_pytest_through_the_project_interpreter_is_allowed(tmp_path: pathlib.Path) -> None:
    a = adapter(tmp_path, {"env": {"python": ".venv/Scripts/python.exe"}})
    tools = a.allowed_tools("TESTS")
    for typed in (
        ".venv\\Scripts\\python.exe -m pytest tests/test_x.py",
        ".\\.venv\\Scripts\\python.exe -m pytest -q",
        "./.venv/Scripts/python.exe -m pytest",
        "python -m pytest -q",
    ):
        assert rule_allows(tools, typed), typed
    assert "PowerShell(.venv\\Scripts\\python.exe -m pytest *)" in tools
    assert not rule_allows(tools, ".venv\\Scripts\\python.exe -c 1")
    assert adapter(tmp_path, {"env": {"python": "python"}}).test_tools() == [
        "Bash(python -m pytest *)",
        "Bash(pytest *)",
    ]


def test_the_projects_own_commands_are_named_and_allowed(tmp_path: pathlib.Path) -> None:
    """black where the project formats with black, every source folder, no disabled command;
    nothing of the adapter's defaults is left in the brief or in the rules."""
    a = adapter(tmp_path, ARGOS)
    roots = "model data orchestrator utils platform/services tests"
    assert [" ".join(c) for c in a.agent_commands()] == [
        f"python -m ruff check {roots}",
        "python -m black <files>",
    ]
    assert a.lint_tools() == [
        "Bash(python -m ruff check *)",
        "Bash(python -m black *)",
        "Bash(ruff check *)",
        "Bash(black *)",
    ]
    brief = a.conventions_brief(a.allowed_tools("IMPLEMENT"))
    assert f"`python -m ruff check {roots}`, `python -m black <files>`" in brief
    assert "ruff format" not in brief and "mypy" not in brief
    # the project's pytest options are part of the test command the brief names
    assert '`python -m pytest -q -m "not integration and not spark"`' in brief
    tools = a.allowed_tools("IMPLEMENT")
    assert not [t for t in tools if "ruff format" in t or "mypy" in t]
    # a project that only checks the format (no fix command): the check is what agents run
    data = {"gates": {"commands": {"format": ["python", "-m", "black", "--check", "{src}"]}}}
    b = adapter(tmp_path, data)
    assert ["python", "-m", "black", "--check", "src"] in b.agent_commands()
    assert "Bash(python -m black --check *)" in b.lint_tools()
    # every lint command off: no line, no rule
    off = {"gates": {"commands": {"lint": [], "format": [], "typecheck": []}}}
    c = adapter(tmp_path, off)
    assert c.agent_commands() == [] and c.lint_tools() == []
    assert "Lint" not in c.conventions_brief()


def test_a_run_without_lint_tools_is_not_told_to_lint(tmp_path: pathlib.Path) -> None:
    """The reviewer and the triage agent have no lint rules: their brief says so instead of
    naming a command that is denied."""
    a = adapter(tmp_path, ARGOS)
    for phase in ("REVIEW", "TRIAGE", "TEST_REVIEW", "DIAGNOSIS"):
        brief = a.conventions_brief(a.allowed_tools(phase))
        assert "ruff" not in brief and "black" not in brief, phase
        assert "- Lint / format / types: not yours to run" in brief
        assert "`python -m pytest -q" in brief


def dotnet_adapter(root: pathlib.Path, data: dict[str, Any]) -> LanguageAdapter:
    (root / "App.sln").write_text("", encoding="utf-8")
    return adapter(root, {"project": {"language": "dotnet"}, **data})


def test_dotnet_and_generic(tmp_path: pathlib.Path) -> None:
    d = dotnet_adapter(tmp_path, {})
    assert [" ".join(c) for c in d.agent_commands()] == [
        "dotnet format whitespace App.sln --include <files>",
        "dotnet format style App.sln --include <files>",
    ]
    assert d.lint_tools() == ["Bash(dotnet format *)"]
    brief = d.conventions_brief(d.allowed_tools("IMPLEMENT"))
    assert "- Build `dotnet build App.sln -c Release` (no new warnings)." in brief
    assert "`dotnet format whitespace App.sln --include <files>`" in brief
    assert "dotnet format" not in d.conventions_brief(d.allowed_tools("REVIEW")).replace(
        "no `dotnet add`", ""
    )
    # the project's own lint and type commands replace the defaults, also in the rules
    own = {"commands": {"lint": ["dotnet", "roslynator", "analyze", "App.sln"], "typecheck": []}}
    e = dotnet_adapter(tmp_path, {"gates": own})
    assert [" ".join(c) for c in e.agent_commands()] == [
        "dotnet roslynator analyze App.sln",
        "dotnet format whitespace App.sln --include <files>",
    ]
    assert e.lint_tools() == ["Bash(dotnet format *)", "Bash(dotnet roslynator analyze *)"]

    g = adapter(tmp_path, GENERIC)
    assert [" ".join(c) for c in g.agent_commands()] == [
        "golangci-lint run src",
        "gofmt -w <files>",
    ]
    assert g.lint_tools() == ["Bash(golangci-lint *)", "Bash(gofmt *)"]
    assert "`golangci-lint run src`, `gofmt -w <files>`" in g.conventions_brief()


@pytest.mark.parametrize("name", ["python", "argos", "dotnet", "dotnet-own", "generic"])
def test_every_command_a_brief_names_is_allowed(tmp_path: pathlib.Path, name: str) -> None:
    """The invariant, per adapter and per phase that gets the brief: every command the brief
    names (its test commands, its lint line) is allowed by that phase's tool rules; and every
    backticked command line in the brief's text is one of them."""
    own = {"gates": {"commands": {"lint": ["dotnet", "roslynator", "analyze", "App.sln"]}}}
    a = {
        "python": lambda: adapter(tmp_path, {}),
        "argos": lambda: adapter(tmp_path, ARGOS),
        "dotnet": lambda: dotnet_adapter(tmp_path, {}),
        "dotnet-own": lambda: dotnet_adapter(tmp_path, own),
        "generic": lambda: adapter(tmp_path, GENERIC),
    }[name]()
    assert {TASK_PHASE.get(k, k) for k in BRIEF_PHASES} == set(BRIEF_RUN_PHASES)
    heads = {c.split()[0] for c in a.brief_commands()}
    for phase in BRIEF_RUN_PHASES:
        allowed = a.allowed_tools(phase)
        named = a.brief_commands(allowed)
        assert named[:2] == [a.test_command(), a.test_command(["ID1", "ID2"])]
        for cmd in named:
            assert rule_allows(allowed, cmd), (phase, cmd)
        # the text: every `...` span that starts like a command is one the rules allow
        # (the dotnet brief names two forbidden commands on purpose: "no `dotnet add`")
        text = a.conventions_brief(allowed)
        spans = [s for s in re.findall(r"`([^`\n]+)`", text) if s.split()[0] in heads]
        spans = [s for s in spans if s not in ("dotnet add", "dotnet new")]
        assert spans, (phase, text)
        for span in spans:
            assert rule_allows(allowed, span), (phase, span)
    assert denied_brief_commands(a.cfg, a) == []


def test_the_task_gets_the_brief_of_its_phase(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(
        "[gates.commands]\nlint = ['python', '-m', 'ruff', 'check', '{src}']\n"
        "format = ['python', '-m', 'black', '--check', '{src}']\n"
        "format_fix = ['python', '-m', 'black', '{files}']\ntypecheck = []\n"
    )
    cfg = load_config(r.root)
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lambda s: None)
    fix = orch.task_text("FIX", "demo", check="x", note="n")
    assert "`python -m ruff check src`, `python -m black <files>`" in fix
    review = orch.task_text("REVIEW", "demo")
    assert "Lint / format / types: not yours to run" in review and "black" not in review
    assert "Bash(python -m black *)" in orch.allowed_tools("IMPLEMENT")
    assert "Bash(python -m black *)" not in orch.allowed_tools("REVIEW")


def test_doctor_names_a_command_the_rules_deny_and_renamed_names(demo_repo: DemoRepo) -> None:
    r = demo_repo
    lines: list[str] = []
    cfg = load_config(r.root)
    assert doctor.config_notes(cfg, get_adapter(cfg)) == []
    assert renamed_names(cfg) == []
    # a tools list that forgets the test command; a section and a role file under old names
    (r.root / ".framegate/roles").mkdir(parents=True)
    (r.root / ".framegate/roles/pr.md").write_text("Mention the ticket.\n", encoding="utf-8")
    r.add_toml(
        "[phases.TEST_REVIEW]\ntools = ['Read', 'Write']\n[phases.PR]\nmax_turns = 12\n"
        "[roles]\nextra_dir = '.framegate/roles'\n"
    )
    cfg = load_config(r.root)
    assert cfg.turn_cap("SUMMARY") == 12
    assert denied_brief_commands(cfg, get_adapter(cfg))[0] == "TEST_REVIEW: `python -m pytest -q`"
    assert renamed_names(cfg) == [
        "[phases.PR] is now [phases.SUMMARY]",
        ".framegate/roles/pr.md is now .framegate/roles/summary.md",
    ]
    doctor.run(cfg, echo=lines.append)
    out = "\n".join(lines)
    assert "[WARN] config: renamed names (still read)" in out
    assert "[WARN] agents: commands their task names but their tool rules deny" in out
    assert "- WARN config: renamed names (still read): [phases.PR] is now" in out
    # the role file under its old name still reaches the summary agent
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lambda s: None)
    text = orch.role_text("summary", "demo")
    assert "Mention the ticket." in text and "A comment or docstring is no evidence" in text
