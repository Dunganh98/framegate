"""Core extension points used by the .NET adapter: run_command timeouts, JUnit failure messages,
glob protected paths, stub_failure_problem in the red-stub check, adapter config sections and the
init hooks."""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Sequence

import pytest

from agentflow.adapters.base import TestCase, TestRun, parse_junit, run_command
from agentflow.adapters.python import PythonAdapter
from agentflow.checks import red_stubs
from agentflow.checks.frame_diff import path_matches
from agentflow.config import ConfigError, parse_config


def test_run_command_timeout_and_missing_tool(tmp_path: pathlib.Path) -> None:
    code, out = run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=1
    )
    assert code == 124 and "TIMEOUT" in out and "within 1 s" in out
    code, out = run_command([sys.executable, "-c", "print('hi')"], tmp_path, timeout=30)
    assert (code, out) == (0, "hi")
    assert run_command(["no-such-tool-xyz"], tmp_path)[0] == 127


def test_parse_junit_messages(tmp_path: pathlib.Path) -> None:
    xml = tmp_path / "r.xml"
    xml.write_text(
        '<testsuites><testsuite name="s">'
        '<testcase classname="A" name="ok"/>'
        '<testcase classname="A" name="bad"><failure message="boom">trace</failure></testcase>'
        '<testcase classname="A" name="err"><error message="E"/></testcase>'
        '<testcase name="sk"><skipped message="why"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert parse_junit(xml) == [
        TestCase("A::ok", "passed", ""),
        TestCase("A::bad", "failed", "boom\ntrace"),
        TestCase("A::err", "error", "E"),
        TestCase("sk", "skipped", "why"),
    ]


@pytest.mark.parametrize(
    ("path", "hit"),
    [
        ("src/App/App.csproj", True),
        ("App.CSPROJ", True),
        ("tests/x/Directory.Build.props", True),
        ("global.json", True),
        ("src/global.json", False),
        ("src/App/Program.cs", False),
        ("tests/review/x.cs", False),
    ],
)
def test_path_matches_prefix_and_glob(path: str, hit: bool) -> None:
    entries = ["*.csproj", "*Directory.Build.props", "global.json", "docs/"]
    assert path_matches(path, entries) is hit


def test_path_matches_empty() -> None:
    assert not path_matches("anything", [])


class _StubAdapter(PythonAdapter):
    def red_stub_run(self, req_ids: Sequence[str], report: pathlib.Path) -> TestRun:
        return TestRun(
            "failed",
            1,
            "",
            [TestCase("T::a", "failed", "NotImplementedError"), TestCase("T::b", "failed", "Bad")],
        )

    def stub_failure_problem(self, case: TestCase) -> str | None:
        return None if "NotImplemented" in case.message else "no stub exception"


def test_red_stubs_reports_failures_for_another_reason(tmp_path: pathlib.Path) -> None:
    (tmp_path / "specs" / "f").mkdir(parents=True)
    (tmp_path / "specs" / "f" / "requirements.md").write_text("FT-R-001", encoding="utf-8")
    res = red_stubs.check(_StubAdapter(parse_config(tmp_path, {})), "f")
    assert res.status == "FAIL"
    assert "FAILS FOR ANOTHER REASON THAN THE STUB: T::b — no stub exception" in res.output
    assert "T::a" not in res.output


def test_language_section_and_bools(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(
        tmp_path,
        {
            "project": {"language": "dotnet"},
            "dotnet": {"build_before_test": False, "test_projects": ["a.csproj"]},
        },
    )
    assert cfg.language_options == {
        "dotnet": {"build_before_test": False, "test_projects": ["a.csproj"]}
    }
    with pytest.raises(ConfigError, match="must be bool"):
        parse_config(tmp_path, {"dotnet": {"build_before_test": "yes"}})
    with pytest.raises(ConfigError, match="list of strings"):
        parse_config(tmp_path, {"dotnet": {"test_projects": [1]}})
    with pytest.raises(ConfigError, match="unknown key 'bogus'"):
        parse_config(tmp_path, {"dotnet": {"bogus": 1}})


def test_with_powershell_adds_twins_after_each_bash_rule() -> None:
    from agentflow.adapters.base import with_powershell

    rules = ["Read", "Bash(dotnet test *)", "Write", "Bash(git diff *)", "PowerShell(git diff *)"]
    assert with_powershell(rules) == [
        "Read",
        "Bash(dotnet test *)",
        "PowerShell(dotnet test *)",
        "Write",
        "Bash(git diff *)",
        "PowerShell(git diff *)",
    ]
