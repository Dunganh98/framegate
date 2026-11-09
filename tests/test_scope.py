"""Feature-scoped final gates: coverage + diff coverage and mutation on the feature's tests (its
spec IDs + the review tests); the whole suite runs once more without coverage (regression).
`[gates] coverage_scope / mutation_scope = "suite"` restores the whole-suite runs."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from collections.abc import Sequence

import pytest

from agentflow.adapters import dotnet as dn
from agentflow.adapters.base import TestScope
from agentflow.config import ConfigError, parse_config
from agentflow.gates import feature_scope, gate_report, scope_lines
from test_checks import adapter_for, framed, write  # noqa: F401  (fixture)
from test_dotnet import FakeDotnet, adapter, faked, tagged  # noqa: F401  (fixture)
from test_mutmut import fake_mutmut, run_fake  # noqa: F401  (fixture)


def test_config_and_describe(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    assert (cfg.coverage_scope, cfg.mutation_scope, cfg.regression_timeout) == (
        "feature",
        "feature",
        3600,
    )
    with pytest.raises(ConfigError, match="coverage_scope must be one of feature, suite"):
        parse_config(tmp_path, {"gates": {"coverage_scope": "all"}})
    assert TestScope().whole_suite and TestScope().describe() == "the whole suite"
    s = TestScope(["A-R-001", "A-E-001"], ["tests/review"])
    assert s.describe() == (
        "the feature's tests (tagged with the feature's 2 spec IDs or in tests/review/)"
    )
    assert TestScope(["A-R-001"]).describe() == (
        "the feature's tests (tagged with the feature's 1 spec ID)"
    )


# ---------- pytest plugin: --req-paths ----------
def test_plugin_req_paths(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    tagged_test = (
        "import pytest\n\n\n@pytest.mark.req('FT-R-001')\ndef test_a() -> None:\n    pass\n"
    )
    write(tmp_path, "tests/test_a.py", tagged_test)
    write(tmp_path, "tests/test_other.py", "def test_other() -> None:\n    pass\n")
    write(tmp_path, "tests/review/test_r.py", "def test_r() -> None:\n    pass\n")

    def run(*args: str) -> str:
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *args]
        return subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True).stdout

    assert "2 passed, 1 deselected" in run("--req", "FT-R-001", "--req-paths", "tests/review")
    assert "1 passed, 2 deselected" in run("--req-paths", "tests\\review\\")
    assert "1 passed, 2 deselected" in run("--req", "FT-R-001")
    assert "3 passed" in run()


# ---------- gate runner, Python adapter ----------
@pytest.fixture
def two_tests(framed: pathlib.Path) -> pathlib.Path:  # noqa: F811
    """The feature's test passes; an unrelated, untagged test fails."""
    write(framed, "src/pkg/a.py", "def f() -> int:\n    return 1\n")
    write(
        framed,
        "tests/test_a.py",
        "import pytest\n\nfrom pkg.a import f\n\n\n@pytest.mark.req('FT-R-001')\n"
        "def test_a() -> None:\n    assert f() == 1\n",
    )
    write(framed, "tests/test_other.py", "def test_other() -> None:\n    assert False\n")
    write(framed, "pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['src']\n")
    return framed


GATES = {"commands": {"lint": [], "format": [], "typecheck": []}, "mutation_tool": "none"}


def test_final_gates_feature_scope(two_tests: pathlib.Path) -> None:
    a = adapter_for(two_tests, {"gates": GATES, "protected": {"paths": []}})
    rep = gate_report(a, "f", "final")
    text = rep.report
    assert text.startswith("Test scope of the final checks:\n")
    assert "- regression: the whole suite, once, without coverage" in text
    assert (
        "- coverage + diff coverage: the feature's tests (tagged with the feature's 1 spec ID)"
        in text
    )
    assert "feature spec IDs: FT-R-001" in text
    assert "- mutation: the feature's tests" in text
    # the unrelated failing test fails the regression run, not the feature's coverage run
    assert "[FAIL] regression: whole suite, no coverage" in text
    assert "[PASS] coverage run (feature tests)" in text
    assert "[PASS] diff coverage >= 90% (feature tests)" in text
    assert "  Diff coverage 100% (1/1 changed lines covered)" in text, text


def test_final_gates_suite_scope(two_tests: pathlib.Path) -> None:
    gates = {**GATES, "coverage_scope": "suite", "mutation_scope": "suite"}
    a = adapter_for(two_tests, {"gates": gates, "protected": {"paths": []}})
    text = gate_report(a, "f", "final").report
    assert "regression: whole suite" not in text
    assert "- regression + coverage + diff coverage: the whole suite, with coverage" in text
    assert "- mutation: the whole suite" in text
    assert "[FAIL] full test suite + coverage" in text
    assert scope_lines(a, "f", skip_mutation=True)[-1].startswith("- regression + coverage")


def test_feature_scope_includes_review_dir(two_tests: pathlib.Path) -> None:
    a = adapter_for(two_tests)
    assert feature_scope(a, "f") == TestScope(["FT-R-001"], [])
    write(two_tests, "tests/review/test_r.py", "def test_r() -> None:\n    assert True\n")
    assert feature_scope(a, "f") == TestScope(["FT-R-001"], ["tests/review"])
    assert a.scope_args(feature_scope(a, "f")) == [
        "--req",
        "FT-R-001",
        "--req-paths",
        "tests/review",
    ]
    assert a.scope_args(None) == [] and a.scope_args(TestScope()) == []


# ---------- Python mutation: the scope reaches mutmut's pytest runs ----------
def test_mutmut_gets_the_scope(fake_mutmut: pathlib.Path) -> None:  # noqa: F811
    a = adapter_for(fake_mutmut)
    spec = a._mutmut_spec(["demo"], "p", "r", TestScope(["D-R-001"], ["tests/review"]))
    assert spec["pytest_addopts"] == "--req D-R-001 --req-paths tests/review"
    assert a._mutmut_spec(["demo"], "p", "r")["pytest_addopts"] == ""
    status, output, _ = run_fake(a, ["demo"], TestScope(["D-R-001"]))
    assert status == "PASS", output
    assert (fake_mutmut / "addopts.txt").read_text(encoding="utf-8") == "--req D-R-001"


# ---------- .NET ----------
TRX_PASS = (
    '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>'
    '<UnitTestResult testName="N.C.M" outcome="Passed" /></Results></TestRun>'
)


def test_dotnet_scope_filter_regression_and_coverage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "tests/T/T.csproj", "Microsoft.NET.Test.Sdk coverlet.collector")
    write(
        tmp_path,
        "tests/T/Review/R.cs",
        "namespace N { class R { [Fact] void T() { Assert.True(x); } } }",
    )
    fake = FakeDotnet(TRX_PASS)
    a = faked(tmp_path, fake, monkeypatch, final_filter="Category!=Gate")
    scope = TestScope(["A-R-001", "A-E-001"], ["tests/T/Review"])
    assert a.scope_filter(scope, "Category!=Gate") == (
        "(Req=A-R-001|Req=A-E-001|FullyQualifiedName~N.R.)&(Category!=Gate)"
    )
    assert a.scope_filter(None, "X=1") == "X=1" and a.scope_filter(TestScope(), "") == ""
    a.cfg.regression_timeout = 11
    res = a.regression_check().run()
    test_args = [c for c in fake.calls if c[0] == "test"][-1]
    assert res.status == "PASS" and a.regression_check().name.startswith("regression")
    assert test_args[test_args.index("--filter") + 1] == "Category!=Gate"
    assert "--collect" not in test_args
    checks = a.coverage_checks("t", scope)
    assert [c.name for c in checks] == [
        "coverage run (feature tests)",
        "diff coverage >= 90% (feature tests)",
    ]
    checks[0].run()
    test_args = [c for c in fake.calls if c[0] == "test"][-1]
    assert test_args[test_args.index("--filter") + 1].startswith("(Req=A-R-001|Req=A-E-001|")
    assert "--collect" in test_args
    assert [c.name for c in a.coverage_checks("t")][0] == "full test suite + coverage"


def test_dotnet_stryker_uses_the_scope(
    tagged: pathlib.Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tagged, "src/App/App.csproj", "<Project />")
    write(tagged, "tests/App.Tests/App.Tests.csproj", "Microsoft.NET.Test.Sdk App.csproj")
    write(
        tagged, "stryker-config.json", json.dumps({"stryker-config": {"test-case-filter": "X!=1"}})
    )
    configs: list[dict] = []

    def fake_run(
        cmd: Sequence[str], cwd: pathlib.Path, env: dict | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        cfg_file = pathlib.Path(cmd[cmd.index("-f") + 1])
        configs.append(json.loads(cfg_file.read_text(encoding="utf-8")))
        out_dir = pathlib.Path(cmd[cmd.index("-O") + 1])
        (out_dir / "reports").mkdir(parents=True)
        (out_dir / "reports" / "mutation-report.json").write_text('{"files": {}}', encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(dn, "run_command", fake_run)
    a = adapter(tagged, {"base_filter": "Category!=Slow"})
    monkeypatch.setattr(a, "stryker_command", lambda: ["dotnet-stryker"])
    a.mutation_check("f", "t", ["src/App/Calc.cs"], TestScope(["A-R-001"]))
    assert configs[-1]["stryker-config"]["test-case-filter"] == (
        "(X!=1)&((Req=A-R-001)&(Category!=Slow))"
    )
    a.mutation_check("f", "t", ["src/App/Calc.cs"])
    assert configs[-1]["stryker-config"]["test-case-filter"] == "(X!=1)&(Category!=Slow)"
