"""End-to-end on a real .NET solution (slow; `uv run pytest -m slow`): spec -> approve-spec -> run
(TESTS -> approve -> IMPLEMENT -> REVIEW -> PR -> RETRO -> DONE) with a fake claude writing C#,
real `dotnet build` / `dotnet test` / `dotnet format`, coverlet + diff-cover and (if installed)
Stryker.NET. Plus the red-stub failure modes on the same fixture."""

from __future__ import annotations

import pathlib
import shutil

import pytest

from conftest import FAKE_CLAUDE, DemoRepo, git, init_git
from fake_claude import CS_TESTS, cs_impl
from framegate.adapters.dotnet import DotnetAdapter
from framegate.checks import red_stubs
from framegate.config import load_config

pytestmark = pytest.mark.slow

SLNX = """<Solution>
  <Project Path="src/Demo/Demo.csproj" />
  <Project Path="tests/Demo.Tests/Demo.Tests.csproj" />
</Solution>
"""

SRC_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.1</TargetFramework>
    <LangVersion>9.0</LangVersion>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
"""

# Same package versions as the first user project (FCSim), so restore works from the cache.
TEST_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="coverlet.collector" Version="6.0.4" />
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.14.1" />
    <PackageReference Include="xunit" Version="2.9.3" />
    <PackageReference Include="xunit.runner.visualstudio" Version="3.1.4" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="..\\..\\src\\Demo\\Demo.csproj" />
  </ItemGroup>
</Project>
"""

REQUIREMENTS = """\
# Demo — Requirements

Feature code: DEMO

## Requirements
| ID | Requirement | Acceptance criterion |
|---|---|---|
| DEMO-R-001 | Calc.Add returns the sum of two ints | given 2, 3 when Add then 5 |

## Edge cases
| ID | Situation | Expected behavior |
|---|---|---|
| DEMO-E-001 | the sum overflows int | throws OverflowException |
"""

DESIGN = (
    "# Demo — Design\n\n## Public interfaces\nCalc.Add(int a, int b) (DEMO-R-001, DEMO-E-001)\n"
)

PLAN = """\
# Demo — Implementation plan

## Step 1: add
Covers: DEMO-R-001, DEMO-E-001
Build Calc.Add.
"""

STUB = cs_impl("            throw new NotImplementedException();")


def dotnet_available() -> bool:
    adapter_probe = shutil.which("dotnet") or any(
        p.is_file()
        for p in (
            pathlib.Path(r"C:\Program Files\dotnet\dotnet.exe"),
            pathlib.Path.home() / ".dotnet" / "dotnet",
            pathlib.Path("/usr/share/dotnet/dotnet"),
        )
    )
    return bool(adapter_probe)


needs_dotnet = pytest.mark.skipif(not dotnet_available(), reason="dotnet SDK not found")


def write(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_solution(root: pathlib.Path) -> None:
    write(root, "Demo.slnx", SLNX)
    write(root, "src/Demo/Demo.csproj", SRC_CSPROJ)
    write(root, "tests/Demo.Tests/Demo.Tests.csproj", TEST_CSPROJ)
    write(root, "src/Demo/Calc.cs", STUB)


@pytest.fixture
def dotnet_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> DemoRepo:
    root = tmp_path / "repo"
    root.mkdir()
    init_git(root)
    make_solution(root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("FRAMEGATE_CLAUDE", str(FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", str(tmp_path / "scenario.json"))
    repo = DemoRepo(root)
    repo.scenario(language="dotnet")
    assert repo.af("init") == 0  # detects dotnet from Demo.slnx
    toml = root / "framegate.toml"
    # this flow stops at the tests gate ("human"; the default "defer" goes on to the final gate)
    text = (
        toml.read_text(encoding="utf-8")
        .replace('# review_tests = "tests/review"', 'review_tests = "tests/Demo.Tests/Review"')
        .replace("[gates]\n", '[gates]\ntests = "human"\n', 1)
    )
    toml.write_text(text + 'base_filter = "Category!=Slow"\n', encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    assert repo.af("spec", "demo") == 0
    spec = root / "specs" / "demo"
    (spec / "requirements.md").write_text(REQUIREMENTS, encoding="utf-8")
    (spec / "design.md").write_text(DESIGN, encoding="utf-8")
    (spec / "plan.md").write_text(PLAN, encoding="utf-8")
    return repo


@needs_dotnet
def test_dotnet_full_flow(dotnet_repo: DemoRepo) -> None:
    r = dotnet_repo
    assert git(r.root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feat/demo"
    r.scenario(language="dotnet", review_finding=True)
    gi = (r.root / ".gitignore").read_text(encoding="utf-8")
    assert "bin/" in gi and "obj/" in gi and "out/" in gi
    assert r.af("approve-spec", "demo") == 0

    # TESTS -> red against stubs (NotImplementedException), traceability, smells
    assert r.af("run", "demo") == 10
    assert r.progress()["waiting"]["gate"] == "tests"
    gate = (r.root / "out/demo/test_checks.md").read_text(encoding="utf-8")
    assert "Red against stubs: PASS" in gate, gate
    assert "Traceability: PASS" in gate and "Test smells: PASS" in gate, gate
    assert "2 tests for feature demo." in gate
    tests_gate = (r.root / "out/demo/tests_gate.md").read_text(encoding="utf-8")
    assert "| DEMO-E-001 | tests/Demo.Tests/CalcTests.cs::" in tests_gate, tests_gate
    tests_call = r.calls()[0]
    tools = tests_call["argv"][tests_call["argv"].index("--allowedTools") + 1].split(",")
    assert "Bash(dotnet test *)" in tools and "Bash(dotnet format *)" in tools
    assert '[Trait("Req", "XXX-R-001")]' in tests_call["prompt"]
    assert "NotImplementedException" in tests_call["prompt"]
    assert r.af("approve", "demo") == 0

    # IMPLEMENT (step gate: format, style, build, tests) -> REVIEW finding -> TRIAGE (valid)
    # -> fix -> final gates -> PR -> final gate
    assert r.af("run", "demo") == 10
    prog = r.progress()
    assert prog["phase"] == "FINAL" and prog["waiting"]["gate"] == "final", prog
    impl_call = r.calls()[2]
    assert (
        'dotnet test Demo.slnx -c Release --filter "(Req=DEMO-R-001|Req=DEMO-E-001)'
        '&(Category!=Slow)"' in impl_call["prompt"]
    )
    triage = [e for e in r.events() if e["kind"] == "triage"][0]["findings"]
    assert [f["verdict"] for f in triage] == ["VALID"], triage
    assert r.af("approve", "demo") == 0

    # RETRO -> DONE
    assert r.af("run", "demo") == 0
    assert r.progress()["phase"] == "DONE"
    final = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert "ALL GATES PASSED" in final, final
    for check in (
        "[PASS] lint",
        "[PASS] format",
        "[PASS] typecheck",
        "[PASS] frame diff",
        "[PASS] regression: whole suite, no coverage",
        "[PASS] coverage run (feature tests)",
        "[PASS] diff coverage >= 90% (feature tests)",
        "  Diff coverage 100% (",
        "- coverage + diff coverage: the feature's tests (tagged with the feature's 2 spec IDs "
        "or in tests/Demo.Tests/Review/)",
        "[PASS] traceability",
        "[PASS] test smells",
    ):
        assert check in final, (check, final)
    if DotnetAdapter(load_config(r.root)).stryker_command():
        assert "[PASS] mutation score (feature tests)" in final, final
        survivors = (r.root / "out/demo/mutation_survivors.md").read_text(encoding="utf-8")
        assert survivors.startswith("# Mutation survivors")
    else:
        assert "[SKIPPED] mutation score" in final
    assert [c["phase"] for c in r.calls()] == [
        "TESTS",
        "TEST_REVIEW",
        "IMPLEMENT",
        "REVIEW",
        "TRIAGE",
        "IMPLEMENT",
        "PR",
        "RETRO",
    ]
    tracked = git(r.root, "ls-files").splitlines()
    assert not [f for f in tracked if "/bin/" in f or "/obj/" in f or f.startswith("out/")]
    assert "tests/Demo.Tests/Review/ReviewCalcTests.cs" in tracked


@needs_dotnet
def test_dotnet_red_stub_failure_modes(dotnet_repo: DemoRepo) -> None:
    r = dotnet_repo
    adapter = DotnetAdapter(load_config(r.root))

    # a test that fails for another reason than the stub's NotImplementedException
    other = CS_TESTS.replace(
        "Assert.Equal(5, Calc.Add(2, 3));", 'throw new InvalidOperationException("setup");'
    )
    write(r.root, "tests/Demo.Tests/CalcTests.cs", other)
    res = red_stubs.check(adapter, "demo")
    assert res.status == "FAIL"
    assert (
        "FAILS FOR ANOTHER REASON THAN THE STUB: Demo.Tests.CalcTests::Add_ReturnsSum — "
        "expected a NotImplementedException from a stub, got: "
        "System.InvalidOperationException : setup" in res.output
    ), res.output
    assert "Add_Overflow_Throws" not in res.output
    assert (r.root / "out/demo/red_stubs.xml").is_file()

    # a compile error: no report, a clear BUILD FAILED line with the error
    write(
        r.root, "tests/Demo.Tests/CalcTests.cs", CS_TESTS.replace("Calc.Add(2, 3)", "Calc.Ad(2, 3)")
    )
    res = red_stubs.check(adapter, "demo")
    assert res.status == "FAIL"
    assert "BUILD FAILED (compile errors):" in res.output, res.output
    assert "tests/Demo.Tests/CalcTests.cs" in res.output.replace("\\", "/")
    assert "CS0117" in res.output
    assert not (r.root / "out/demo/red_stubs.xml").exists()

    # selection by IDs that match nothing -> no_tests; review folder without tests -> no_tests
    write(r.root, "tests/Demo.Tests/CalcTests.cs", CS_TESTS)
    assert adapter.run_tests(req_ids=["DEMO-R-999"]).status == "no_tests"
    assert adapter.run_tests(paths=["tests/Demo.Tests/Review"]).status == "no_tests"
    run = adapter.run_tests(paths=["tests/Demo.Tests"])
    assert run.status == "failed" and run.cases is not None and len(run.cases) == 2
    assert [c.outcome for c in run.cases] == ["failed", "failed"]
