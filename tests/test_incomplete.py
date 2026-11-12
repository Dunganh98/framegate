"""Cut-short test runs: a killed / timed-out / aborted run, or a missing report or coverage
file, is reported as `ERROR: ... incomplete (<reason>)`, never as a coverage or mutation figure."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Sequence

import pytest

from framegate.adapters import dotnet as dn
from framegate.adapters.base import TIMEOUT_CODE, incomplete_reason, incomplete_result
from framegate.config import ConfigError, parse_config
from test_checks import adapter_for, framed, write  # noqa: F401  (fixture)
from test_dotnet import TRX, FakeDotnet, adapter, faked, tagged  # noqa: F401  (fixture)

PASSED_TRX = TRX.split('<UnitTestResult testName="N.CalcTests.Theory')[0] + "</Results></TestRun>"
ABORTED = (
    "Passed!  - Failed: 0, Passed: 217, Total: 217, Duration: 29 m 24 s\n"
    "The active test run was aborted. Reason: Test host process crashed\nTest Run Aborted.\n"
)


def test_incomplete_reason() -> None:
    assert incomplete_reason(TIMEOUT_CODE, "", 1800) == "timed out after 1800 s"
    assert incomplete_reason(TIMEOUT_CODE, "") == "timed out"
    assert incomplete_reason(143, "") == "killed (SIGTERM, exit 143)"
    assert incomplete_reason(3221225786, "") == "killed (Ctrl+C, exit 3221225786)"
    assert incomplete_reason(-9, "") == "killed by signal 9"
    assert incomplete_reason(1, ABORTED) == "test host crashed, run aborted"
    assert incomplete_reason(0, "Passed! 3 tests") == ""
    assert incomplete_reason(1, "Failed! 1 test") == ""
    res = incomplete_result("timed out after 5 s", "a\nb", "coverage run")
    assert (
        res.status == "FAIL"
        and res.summary == "ERROR: coverage run incomplete (timed out after 5 s)"
    )
    assert res.output.endswith("\na\nb")


def test_gate_timeouts_config(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    assert (cfg.coverage_timeout, cfg.mutation_timeout) == (1800, 3600)
    cfg = parse_config(tmp_path, {"gates": {"coverage_timeout": 60, "mutation_timeout": 90}})
    assert (cfg.coverage_timeout, cfg.mutation_timeout) == (60, 90)
    with pytest.raises(ConfigError, match="coverage_timeout must be >= 1"):
        parse_config(tmp_path, {"gates": {"coverage_timeout": 0}})


# ---------- .NET ----------
class CoverageFake(FakeDotnet):
    """FakeDotnet that also writes a Cobertura file when coverage is collected (unless told
    not to) and records the timeouts."""

    def __init__(self, *a: object, cobertura: bool = True, **k: object) -> None:
        super().__init__(*a, **k)  # type: ignore[arg-type]
        self.cobertura = cobertura
        self.timeouts: list[int] = []

    def __call__(self, args: Sequence[str], timeout: int) -> tuple[int, str]:
        self.timeouts.append(timeout)
        code, out = super().__call__(args, timeout)
        if args[0] == "test" and "--collect" in args and self.cobertura:
            d = pathlib.Path(args[args.index("--results-directory") + 1]) / "guid"
            d.mkdir(parents=True, exist_ok=True)
            (d / "coverage.cobertura.xml").write_text("<coverage/>", encoding="utf-8")
        return code, out


def coverage_adapter(
    tmp_path: pathlib.Path, fake: FakeDotnet, monkeypatch: pytest.MonkeyPatch
) -> dn.DotnetAdapter:
    write(tmp_path, "tests/T/T.csproj", "Microsoft.NET.Test.Sdk coverlet.collector")
    a = faked(tmp_path, fake, monkeypatch)
    a.cfg.coverage_timeout = 77
    return a


def run_checks(a: dn.DotnetAdapter) -> list[tuple[str, str, str]]:
    return [(c.name, *(lambda r: (r.status, r.output))(c.run())) for c in a.coverage_checks("t")]


def test_dotnet_aborted_coverage_run_is_an_error_not_0_percent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = CoverageFake(PASSED_TRX, code=1, out=ABORTED)
    a = coverage_adapter(tmp_path, fake, monkeypatch)
    (suite, s_status, s_out), (diff, d_status, d_out) = run_checks(a)
    assert s_status == "FAIL" and s_out.startswith(
        "ERROR: test run incomplete (test host crashed, run aborted)"
    )
    assert d_status == "FAIL" and d_out.startswith(
        "ERROR: coverage run incomplete (test host crashed, run aborted; no diff coverage"
    )
    assert "0%" not in d_out and 77 in fake.timeouts


def test_dotnet_timeout_and_missing_coverage_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = coverage_adapter(tmp_path, CoverageFake(PASSED_TRX, code=TIMEOUT_CODE), monkeypatch)
    checks = run_checks(a)
    assert checks[0][2].startswith("ERROR: test run incomplete (timed out after 77 s)")
    assert "timed out after 77 s; no diff coverage computed" in checks[1][2]
    a = coverage_adapter(tmp_path, CoverageFake(PASSED_TRX, cobertura=False), monkeypatch)
    checks = run_checks(a)
    assert checks[0][2].startswith("ERROR: test run incomplete (coverage file missing)")
    assert checks[1][1] == "FAIL"
    run = faked(tmp_path, FakeDotnet(None, code=1), monkeypatch).run_tests()
    assert run.status == "error" and run.incomplete == "no test report (exit 1)"


def test_dotnet_mutation_incomplete(tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    write(tagged, "src/App/App.csproj", "<Project />")
    write(
        tagged,
        "tests/App.Tests/App.Tests.csproj",
        r'Microsoft.NET.Test.Sdk <ProjectReference Include="..\..\src\App\App.csproj" />',
    )
    result = {"code": TIMEOUT_CODE, "timeouts": []}

    def fake_run(
        cmd: Sequence[str], cwd: pathlib.Path, env: dict | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        result["timeouts"].append(timeout)  # type: ignore[attr-defined]
        return int(result["code"]), "[INF] Initial test run started."  # type: ignore[call-overload]

    monkeypatch.setattr(dn, "run_command", fake_run)
    a = adapter(tagged, gates={"mutation_timeout": 42})
    monkeypatch.setattr(a, "stryker_command", lambda: ["dotnet-stryker"])
    res = a.mutation_check("f", "t", ["src/App/Calc.cs"])
    assert res.status == "FAIL"
    assert res.summary == "ERROR: mutation run (App.csproj) incomplete (timed out after 42 s)"
    assert result["timeouts"] == [42]
    result["code"] = 0  # finished, but no report
    res = a.mutation_check("f", "t", ["src/App/Calc.cs"])
    assert "incomplete (no mutation report)" in res.output
    a = adapter(tagged, {"mutation_timeout": 5}, gates={"mutation_timeout": 42})
    monkeypatch.setattr(a, "stryker_command", lambda: ["dotnet-stryker"])
    a.mutation_check("f", "t", ["src/App/Calc.cs"])
    assert result["timeouts"][-1] == 5  # [dotnet] mutation_timeout wins when set


# ---------- Python ----------
def test_python_coverage_timeout(framed: pathlib.Path) -> None:  # noqa: F811
    write(
        framed,
        "tests/test_slow.py",
        "import time\n\n\ndef test_slow() -> None:\n    time.sleep(30)\n    assert True\n",
    )
    a = adapter_for(framed, {"gates": {"coverage_timeout": 3}})
    suite, diff = (c.run() for c in a.coverage_checks("tests-approved/f"))
    assert suite.status == "FAIL" and suite.output.startswith(
        "ERROR: test run incomplete (timed out after 3 s)"
    )
    assert diff.status == "FAIL" and "no diff coverage computed" in diff.output


def test_python_mutation_incomplete(
    framed: pathlib.Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from framegate.adapters import python as py

    a = adapter_for(framed, {"gates": {"mutation_runner": "native", "mutation_timeout": 10}})
    result_file = framed / "out" / ".mutmut" / "result.json"
    result_file.parent.mkdir(parents=True)
    seen: list[float | None] = []

    def fake_run(
        cmd: Sequence[str], cwd: pathlib.Path, env: dict | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        seen.append(timeout)
        result_file.write_text(
            json.dumps({"ok": True, "run_code": TIMEOUT_CODE, "run_output": "TIMEOUT"}),
            encoding="utf-8",
        )
        return 0, ""

    monkeypatch.setattr(py, "run_command", fake_run)
    res = a._mutmut_finish("f", ["pkg.a"], ["runner"], "native")
    assert res.status == "FAIL" and res.summary == (
        "ERROR: mutation run (native) incomplete (timed out after 10 s)"
    )
    assert seen == [10 + 1800]

    def killed(*args: object, **kw: object) -> tuple[int, str]:
        return TIMEOUT_CODE, "TIMEOUT: runner"

    result_file.unlink()
    monkeypatch.setattr(py, "run_command", killed)
    res = a._mutmut_finish("f", ["pkg.a"], ["runner"], "WSL")
    assert "mutation run (WSL) incomplete (timed out after 1810 s)" in res.output
