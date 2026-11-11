""".NET adapter, fast unit tests: C# scanner, traceability + smells, VSTest filters, TRX/JUnit
parsing, run_tests status mapping (dotnet faked), gate commands, Cobertura/Stryker parsing, config
keys, init. The real-toolchain flow is in test_dotnet_e2e.py (slow)."""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Sequence

import pytest

from agentflow import cli
from agentflow.adapters import csharp, detect_language, get_adapter
from agentflow.adapters import dotnet as dn
from agentflow.adapters.base import TestCase, parse_junit
from agentflow.adapters.csharp import has_assertion, method_bodies, scan, strip
from agentflow.adapters.dotnet import DotnetAdapter
from agentflow.checks import frame_diff
from agentflow.config import ConfigError, load_config, parse_config
from conftest import git, init_git


def write(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def adapter(root: pathlib.Path, dotnet: dict | None = None, **sections: dict) -> DotnetAdapter:
    data: dict = {"project": {"language": "dotnet"}, **sections}
    if dotnet is not None:
        data["dotnet"] = dotnet
    a = get_adapter(parse_config(root, data))
    assert isinstance(a, DotnetAdapter)
    return a


# ---------- C# scanner ----------
def test_strip_comments_and_literals() -> None:
    src = (
        'var a = "// not a comment"; // comment [Trait("Req", "X-R-001")]\n'
        '/* block [Fact] */ var b = @"say ""hi"" // x";\n'
        'var c = \'"\'; var d = $"{(x ? "[" : "]")} {{ }}";\n'
        '#region hidden [Fact]\nvar e = """raw " text""";\n'
    )
    code, mask = strip(src)
    assert len(code) == len(mask) == len(src)
    assert "comment" not in code.split("\n")[0].split(";", 1)[1]
    assert '"// not a comment"' in code and "block" not in code and "hidden" not in code
    assert "not a comment" not in mask and "say" not in mask and "raw" not in mask
    assert "[" not in mask.split("\n")[2] and "Fact" not in mask
    assert mask.count("\n") == src.count("\n")


TEST_FILE = """\
using Xunit;

namespace Shop.Tests
{
    [Trait("Req", "SHOP-R-002")]
    public class CartTests
    {
        [Fact]
        [Trait("Req", "SHOP-R-001")]
        public void Adds() { Assert.Equal(1, Cart.Count()); }

        [Theory, InlineData(1), InlineData(2)]
        [Trait("Req", "SHOP-E-001"), Trait("Category", "Slow")]
        public async Task Removes(int n) => await Check(n);

        // [Fact] [Trait("Req", "SHOP-R-999")] commented out
        private Task Check(int n)
        {
            AssertHelper.Valid(n);
            return Task.CompletedTask;
        }

        public class Nested
        {
            [Xunit.FactAttribute]
            public void Inner() { Assert.True(Cart.Ok); }
        }
    }
}
"""


def test_scan_structure() -> None:
    f = scan(TEST_FILE)
    assert [(c.name, c.full_name) for c in f.classes] == [
        ("CartTests", "Shop.Tests.CartTests"),
        ("Nested", "Shop.Tests.CartTests+Nested"),
    ]
    tests = {m.qualified: m for m in f.tests}
    assert set(tests) == {"CartTests.Adds", "CartTests.Removes", "Nested.Inner"}
    removes = tests["CartTests.Removes"]
    assert [a.name for a in removes.attrs] == [
        "Theory",
        "InlineData",
        "InlineData",
        "Trait",
        "Trait",
    ]
    assert removes.attrs[3].string_args() == ["Req", "SHOP-E-001"]
    assert removes.attrs[1].string_args() is None  # InlineData(1): not a string literal
    assert "Check" in removes.body
    assert [c.name for c in tests["Nested.Inner"].class_chain()] == ["Nested", "CartTests"]
    assert tests["CartTests.Adds"].line == 10


def test_scan_file_scoped_namespace_records_and_generics() -> None:
    f = scan(
        "namespace A.B;\n"
        "public record Point(int X, int Y);\n"
        "public sealed class T<TItem> where TItem : class\n{\n"
        "    [Fact] public void G<TX>() where TX : new() { Assert.NotNull(new TX()); }\n"
        "    [Property] public bool P(int x) => x == x;\n"
        "    [Obsolete] public void NotATest() { }\n"
        "    [JsonProperty] public int Field { get; set; }\n"
        "}\n"
    )
    assert [c.full_name for c in f.classes] == ["A.B.T"]
    assert [m.name for m in f.tests] == ["G", "P"]
    assert [m.name for m in f.methods] == ["G", "P", "NotATest"]


def test_helper_assertions_are_followed() -> None:
    _, mask = strip(TEST_FILE + "static class AssertHelper { public static void Valid(int n) { } }")
    index = method_bodies(mask)
    assert {"Check", "Valid"} <= set(index)
    assert has_assertion("await Check(n);", index)
    assert not has_assertion("Cart.Count();", index)
    assert has_assertion("x.Should().Be(1);", {}) and has_assertion("Assert.Throws<E>(f);", {})
    assert not has_assertion(
        "Helper();", {"Helper": ["Deeper();"], "Deeper": ["Assert.True(x);"]}, 1
    )
    assert has_assertion("Helper();", {"Helper": ["Deeper();"], "Deeper": ["Assert.True(x);"]}, 2)


# ---------- traceability + smells ----------
def test_collect_req_tags(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "tests/Shop.Tests/CartTests.cs", TEST_FILE)
    write(tmp_path, "tests/Shop.Tests/obj/Gen.cs", '[Fact][Trait("Req", "SHOP-R-777")] void X() {}')
    write(tmp_path, "src/Shop/Cart.cs", '// [Trait("Req", "SHOP-R-888")]')
    tags = adapter(tmp_path).collect_req_tags()
    f = "tests/Shop.Tests/CartTests.cs"
    assert tags == {
        "SHOP-R-001": [f"{f}::CartTests.Adds"],
        "SHOP-R-002": [f"{f}::CartTests.Adds", f"{f}::CartTests.Removes", f"{f}::Nested.Inner"],
        "SHOP-E-001": [f"{f}::CartTests.Removes"],
    }


SMELLY = """\
namespace S
{
    public class SmellTests
    {
        [Fact] public void NoAssert() { var x = Calc.Add(1, 2); }
        [Fact(Skip = "later")] public void Skipped() { Assert.Equal(1, 1); }
        [Theory(Skip="x")] [InlineData(1)] public void SkippedTheory(int a) { Assert.Equal(a, a); }
        [Fact] public void Early() { return; Assert.Equal(1, 2); }
        [Fact] public void Trivial() { Assert.True( true ); }
        [Fact] public void Inline() { Assert.Skip("no"); Assert.Equal(1, 1); }
        [Fact] public void ViaHelper() => Helper(3);
        [Fact] public void Fine() { Assert.Equal(3, Calc.Add(1, 2)); }
        [Fact] public void Vacuous()
        {
            foreach (var r in Run(3).Records.Where(x => x.Lap)) { Assert.True(r.Missed); }
        }
        [Fact] public void VacuousAll()
        {
            Assert.True(Run(3).Records.Where(x => x.Lap).All(x => x.Missed));
        }
        [Fact] public void Guarded()
        {
            var picked = Run(3).Records.Where(x => x.Lap).ToList();
            Assert.NotEmpty(picked);
            foreach (var r in picked.Where(x => x.Lap)) { Assert.True(r.Missed); }
        }
        private static void Helper(int n) { Assert.InRange(n, 0, 9); }
    }
}
"""


def test_smells(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "tests/S.Tests/SmellTests.cs", SMELLY)
    where = "tests/S.Tests/SmellTests.cs::SmellTests"
    assert adapter(tmp_path).test_smells() == [
        f"NO ASSERTION: {where}.NoAssert",
        f"SKIPPED (Skip =): {where}.Skipped",
        f"SKIPPED (Skip =): {where}.SkippedTheory",
        f"EARLY RETURN (test body starts with return;): {where}.Early",
        f"TRIVIAL ASSERTION (Assert.True(true)): {where}.Trivial",
        f"INLINE SKIP: {where}.Inline",
        "NOTE: LOOP OVER PICKED CASES WITHOUT A VISIBLE NON-EMPTY CHECK (zero cases would pass; "
        f"the test reviewer decides): {where}.Vacuous",
        "NOTE: LOOP OVER PICKED CASES WITHOUT A VISIBLE NON-EMPTY CHECK (zero cases would pass; "
        f"the test reviewer decides): {where}.VacuousAll",
    ]


def test_format_and_lint_findings_only_on_changed_lines(tmp_path: pathlib.Path) -> None:
    """dotnet format diagnostics on lines the feature did not change are ignored (no churn in
    old code, no loop on them); the format fix keeps only its changes on changed lines."""
    from agentflow.adapters.base import merge_scoped

    init_git(tmp_path)
    write(tmp_path, "src/A/Old.cs", "class Old\n{\n  int a;\n  int b;\n}\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "tag", "base")
    write(tmp_path, "src/A/Old.cs", "class Old\n{\n  int a;\n  int b;\n  int  c;\n}\n")
    write(tmp_path, "src/A/New.cs", "class New{}\n")
    a = adapter(tmp_path)
    out = (
        f"{tmp_path / 'src/A/Old.cs'}(3,3): error WHITESPACE: Fix whitespace. [x.csproj]\n"
        "src/A/Old.cs(5,6): error WHITESPACE: Fix whitespace. [x.csproj]\n"
        "src/A/New.cs(1,10): warning IDE0055: Fix formatting. [x.csproj]\n"
        "Build summary\n"
    )
    scoped, kept, ignored = a.scope_diagnostics(out, "base")
    assert (kept, ignored) == (2, 1)
    assert "Old.cs(3,3)" not in scoped and "src/A/Old.cs(5,6)" in scoped
    assert "src/A/New.cs(1,10)" in scoped and "Build summary" in scoped
    assert a.scope_diagnostics(out, None) == (out, 0, 0)
    # the formatter re-indented every member; only the changed line keeps the change
    before = "class Old\n{\n  int a;\n  int b;\n  int  c;\n}\n"
    after = "class Old\n{\n    int a;\n    int b;\n    int c;\n}\n"
    assert merge_scoped(before, after, {5}) == "class Old\n{\n  int a;\n  int b;\n    int c;\n}\n"
    assert merge_scoped(before, after, set()) == before


def test_test_classes_for_review_paths(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "tests/Shop.Tests/Review/ReviewTests.cs", TEST_FILE.replace("CartTests", "R"))
    write(tmp_path, "tests/Shop.Tests/Review/Empty.cs", "namespace X { class Y { } }")
    a = adapter(tmp_path)
    assert a.test_classes(["tests/Shop.Tests/Review"]) == ["Shop.Tests.R", "Shop.Tests.R+Nested"]
    assert a.test_classes(["tests/Shop.Tests/Review/Empty.cs", "nope"]) == []


# ---------- filters / commands ----------
def test_filters() -> None:
    assert dn.combine_filters() == ""
    assert dn.combine_filters("", "A=1") == "A=1"
    assert dn.combine_filters("Req=X|Req=Y", "", "Category!=Slow&Category!=Gate") == (
        "(Req=X|Req=Y)&(Category!=Slow&Category!=Gate)"
    )
    assert dn.req_filter(["A-R-001", "A-E-001"]) == "Req=A-R-001|Req=A-E-001"
    assert dn.class_filter(["N.C", "N.O+I"]) == "FullyQualifiedName~N.C.|FullyQualifiedName~N.O+I."
    assert dn.class_filter(["N.C(x)"]) == r"FullyQualifiedName~N.C\(x\)."


def test_test_command_and_targets(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "App.slnx", "<Solution />")
    a = adapter(tmp_path, {"base_filter": "Category!=Slow&Category!=Gate"})
    assert a.test_targets() == ["App.slnx"] and a.build_target() == "App.slnx"
    assert (
        a.test_command()
        == 'dotnet test App.slnx -c Release --filter "Category!=Slow&Category!=Gate"'
    )
    assert a.test_command(["A-R-001", "A-E-001"]) == (
        'dotnet test App.slnx -c Release --filter "(Req=A-R-001|Req=A-E-001)'
        '&(Category!=Slow&Category!=Gate)"'
    )
    b = adapter(tmp_path, {"test_projects": ["tests/T/T.csproj"], "configuration": "Debug"})
    assert (
        b.test_command(["A-R-001"])
        == 'dotnet test tests/T/T.csproj -c Debug --filter "Req=A-R-001"'
    )
    write(tmp_path, "Other.sln", "")
    with pytest.raises(ConfigError, match="several solution files"):
        adapter(tmp_path).solution  # noqa: B018 - property access raises
    assert adapter(tmp_path, {"solution": "App.slnx"}).solution == "App.slnx"


def test_detected_test_projects(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "src/App/App.csproj", "<Project />")
    write(
        tmp_path,
        "tests/App.Tests/App.Tests.csproj",
        '<PackageReference Include="Microsoft.NET.Test.Sdk" />',
    )
    write(tmp_path, "tests/Helpers/Helpers.csproj", "<Project />")
    write(tmp_path, "tests/App.Tests/bin/Copy.csproj", "Microsoft.NET.Test.Sdk")
    a = adapter(tmp_path)
    assert a.detected_test_projects() == ["tests/App.Tests/App.Tests.csproj"]
    assert a.test_targets() == ["tests/App.Tests/App.Tests.csproj"]  # no solution
    assert not a.references("coverlet.collector") and not a.uses_junit_logger()
    write(
        tmp_path,
        "tests/App.Tests/App.Tests.csproj",
        "Microsoft.NET.Test.Sdk JunitXml.TestLogger coverlet.collector FsCheck",
    )
    assert a.references("coverlet.collector") and a.uses_junit_logger()
    assert "FsCheck `[Property]`" in a.conventions_brief()


# ---------- reports ----------
TRX = """<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>
<UnitTestResult testName="N.CalcTests.Adds" outcome="Passed" />
<UnitTestResult testName="N.CalcTests.Theory(a: 1, b: &quot;x.y&quot;)" outcome="Failed">
<Output><ErrorInfo><Message>System.NotImplementedException : not implemented</Message>
<StackTrace>at N.Calc.Add()</StackTrace></ErrorInfo></Output></UnitTestResult>
<UnitTestResult testName="N.CalcTests.Skipped" outcome="NotExecuted" />
<UnitTestResult testName="N.CalcTests.Hung" outcome="Timeout" />
</Results></TestRun>
"""


def test_parse_trx_and_junit_roundtrip(tmp_path: pathlib.Path) -> None:
    (tmp_path / "r.trx").write_text(TRX, encoding="utf-8")
    cases = dn.parse_trx(tmp_path / "r.trx")
    assert cases == [
        TestCase("N.CalcTests::Adds", "passed", ""),
        TestCase(
            'N.CalcTests::Theory(a: 1, b: "x.y")',
            "failed",
            "System.NotImplementedException : not implemented\nat N.Calc.Add()",
        ),
        TestCase("N.CalcTests::Skipped", "skipped", ""),
        TestCase("N.CalcTests::Hung", "error", ""),
    ]
    dn.write_junit(cases, tmp_path / "out" / "j.xml")
    assert [(c.name, c.outcome) for c in parse_junit(tmp_path / "out" / "j.xml")] == [
        (c.name, c.outcome) for c in cases
    ]
    assert "NotImplementedException" in parse_junit(tmp_path / "out" / "j.xml")[1].message
    assert dn.run_summary(cases) == "1 passed, 1 failed, 1 error, 1 skipped"


def test_stub_failure_problem(tmp_path: pathlib.Path) -> None:
    a = adapter(tmp_path)
    assert (
        a.stub_failure_problem(TestCase("t", "failed", "x\n---- System.NotImplementedException"))
        is None
    )
    assert a.stub_failure_problem(TestCase("t", "failed", "Assert.Equal() Failure\nmore")) == (
        "expected a NotImplementedException from a stub, got: Assert.Equal() Failure"
    )
    assert "no message" in str(a.stub_failure_problem(TestCase("t", "failed", "")))


BUILD_OUT = r"""
  Demo -> C:\r\src\Demo\bin\Demo.dll
C:\r\src\Demo\Calc.cs(10,13): warning CS0219: The variable 'x' is unused [C:\r\src\D\D.csproj]
C:\r\tests\T\CalcTests.cs(12,30): error CS0117: 'Calc' has no 'Ad' [C:\r\tests\T\T.csproj]
Build FAILED.
C:\r\tests\T\CalcTests.cs(12,30): error CS0117: 'Calc' has no 'Ad' [C:\r\tests\T\T.csproj]
"""


def test_diagnostics() -> None:
    errors = dn.diagnostics(BUILD_OUT, "error")
    assert errors == [
        (
            r"C:\r\tests\T\CalcTests.cs",
            12,
            r"C:\r\tests\T\CalcTests.cs(12,30): error CS0117: 'Calc' has no 'Ad'",
        )
    ]
    assert [w[1] for w in dn.diagnostics(BUILD_OUT, "warning")] == [10]


def test_normalize_cobertura(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "repo/src/Demo/Calc.cs", "class C {}")
    root = tmp_path / "repo"
    anchor = str(root.resolve().anchor)
    rel_from_anchor = str((root / "src" / "Demo" / "Calc.cs").resolve())[len(anchor) :]
    xml = (
        f"<coverage><sources><source>{anchor}</source></sources><packages><package><classes>"
        f'<class name="Demo.Calc" filename="{rel_from_anchor}"><lines>'
        '<line number="1" hits="1"/></lines></class>'
        '<class name="Gone" filename="nowhere/X.cs"/>'
        "</classes></package></packages></coverage>"
    )
    write(tmp_path, "raw.xml", xml)
    assert dn.normalize_cobertura(tmp_path / "raw.xml", tmp_path / "n" / "c.xml", root) == 1
    text = (tmp_path / "n" / "c.xml").read_text(encoding="utf-8")
    assert 'filename="src/Demo/Calc.cs"' in text and "<sources>" not in text
    assert 'filename="nowhere/X.cs"' in text


def test_stryker_results(tmp_path: pathlib.Path) -> None:
    calc = str((tmp_path / "src" / "Demo" / "Calc.cs").resolve())
    other = str((tmp_path / "src" / "Demo" / "Other.cs").resolve())

    def mutant(status: str, line: int) -> dict:
        loc = {"start": {"line": line, "column": 5}}
        return {
            "status": status,
            "mutatorName": "Arithmetic",
            "replacement": "a - b",
            "location": loc,
        }

    report = {
        "files": {
            calc: {
                "mutants": [
                    mutant(s, i)
                    for i, s in enumerate(
                        ["Killed", "Timeout", "Survived", "NoCoverage", "Ignored", "CompileError"],
                        1,
                    )
                ]
            },
            other: {"mutants": [mutant("Survived", 1)]},
        }
    }
    detected, undetected, survivors = dn.stryker_results(report, tmp_path, ["src/Demo/Calc.cs"])
    assert (detected, undetected) == (2, 2)
    assert survivors == [
        "- `src/Demo/Calc.cs:3:5` Arithmetic -> `a - b` (Survived)",
        "- `src/Demo/Calc.cs:4:5` Arithmetic -> `a - b` (NoCoverage)",
    ]


# ---------- run_tests status mapping (dotnet faked) ----------
class FakeDotnet:
    """Replaces DotnetAdapter._exec: records argv, writes a TRX into --results-directory."""

    def __init__(self, trx: str | None, code: int = 0, build_code: int = 0, out: str = "") -> None:
        self.trx, self.code, self.build_code, self.out = trx, code, build_code, out
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], timeout: int) -> tuple[int, str]:
        self.calls.append(list(args))
        if args[0] == "build":
            return self.build_code, BUILD_OUT if self.build_code else "Build succeeded."
        if self.trx is not None:
            d = pathlib.Path(args[args.index("--results-directory") + 1])
            (d / "run.trx").write_text(self.trx, encoding="utf-8")
        return self.code, self.out


def faked(
    tmp_path: pathlib.Path, fake: FakeDotnet, monkeypatch: pytest.MonkeyPatch, **kw: object
) -> DotnetAdapter:
    write(tmp_path, "App.slnx", "<Solution />")
    a = adapter(tmp_path, {"base_filter": "Category!=Slow", **kw})
    monkeypatch.setattr(a, "_exec", fake)
    return a


def test_run_tests_failed_with_junit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDotnet(TRX, code=1)
    a = faked(tmp_path, fake, monkeypatch)
    run = a.run_tests(req_ids=["A-R-001"], junit=tmp_path / "out" / "f" / "red.xml")
    assert run.status == "failed" and run.cases is not None and len(run.cases) == 4
    assert (tmp_path / "out" / "f" / "red.xml").is_file()
    assert fake.calls[0] == ["build", "App.slnx", "-c", "Release"]
    test_args = fake.calls[1]
    assert test_args[:5] == ["test", "App.slnx", "-c", "Release", "--no-build"]
    assert test_args[test_args.index("--logger") + 1] == "trx"
    assert test_args[test_args.index("--filter") + 1] == "(Req=A-R-001)&(Category!=Slow)"
    assert "ids run: 1 passed, 1 failed, 1 error, 1 skipped" in run.output


def test_run_tests_passed_no_tests_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passed = TRX.split('<UnitTestResult testName="N.CalcTests.Theory')[0] + "</Results></TestRun>"
    assert faked(tmp_path, FakeDotnet(passed), monkeypatch).run_tests().status == "passed"
    empty = '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"/>'
    run = faked(tmp_path, FakeDotnet(empty), monkeypatch).run_tests(req_ids=["X-R-001"])
    assert run.status == "no_tests" and run.cases is None
    run = faked(tmp_path, FakeDotnet(None, build_code=1), monkeypatch).run_tests()
    assert run.status == "error" and run.cases is None
    assert "BUILD FAILED (compile errors):" in run.output and "CS0117" in run.output
    run = faked(tmp_path, FakeDotnet(None, code=1), monkeypatch).run_tests()
    assert run.status == "error" and run.cases is None  # crashed host, no report
    run = faked(tmp_path, FakeDotnet(passed, code=124, out="TIMEOUT"), monkeypatch).run_tests()
    assert run.status == "error" and run.returncode == 124


def test_run_tests_without_prebuild_and_review_paths(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDotnet(None, code=1, out=BUILD_OUT)
    a = faked(tmp_path, fake, monkeypatch, build_before_test=False)
    run = a.run_tests()
    assert run.status == "error" and "BUILD FAILED" in run.output
    assert [c[0] for c in fake.calls] == ["test"] and "--no-build" not in fake.calls[0]
    assert a.run_tests(paths=["tests/review"]).status == "no_tests"
    write(
        tmp_path,
        "tests/T/Review/R.cs",
        "namespace N { class R { [Fact] void T() { Assert.True(x); } } }",
    )
    a.run_tests(paths=["tests/T/Review"])
    assert fake.calls[-1][fake.calls[-1].index("--filter") + 1] == (
        "(FullyQualifiedName~N.R.)&(Category!=Slow)"
    )


def test_junit_logger_when_referenced(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "tests/T/T.csproj", "Microsoft.NET.Test.Sdk JunitXml.TestLogger")
    junit = '<testsuites><testsuite><testcase classname="N.C" name="M"/></testsuite></testsuites>'

    def fake(args: Sequence[str], timeout: int) -> tuple[int, str]:
        if args[0] == "test":
            logger = args[args.index("--logger") + 1]
            assert logger.startswith("junit;LogFilePath=")
            path = pathlib.Path(
                logger.split("LogFilePath=")[1].split(";")[0].replace("{assembly}", "T")
            )
            path.write_text(junit, encoding="utf-8")
        return 0, ""

    a = adapter(tmp_path)
    monkeypatch.setattr(a, "_exec", fake)
    run = a.run_tests()
    assert run.status == "passed" and run.cases == [TestCase("N.C::M", "passed", "")]


# ---------- gates ----------
@pytest.fixture
def tagged(tmp_path: pathlib.Path) -> pathlib.Path:
    init_git(tmp_path)
    write(tmp_path, ".gitignore", "out/\nbin/\nobj/\n")
    write(tmp_path, "App.slnx", "<Solution />")
    write(tmp_path, "src/App/Calc.cs", "class Calc { }\n")
    write(tmp_path, "src/App/Same.cs", "class Same { }\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "tag", "tests-approved/f")
    return tmp_path


def test_gate_commands_use_changed_files(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_run(
        cmd: Sequence[str], cwd: pathlib.Path, env: dict | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        seen.append(list(cmd))
        return 0, "ok"

    monkeypatch.setattr(dn, "run_command", fake_run)
    a = adapter(tagged, {"executable": "dotnet"})
    names = [c.name for c in a.lint_checks("tests-approved/f")]
    assert names == ["lint", "format", "typecheck"]
    assert [c.run().output for c in a.lint_checks("tests-approved/f")[:2]] == [
        "No changed C# files."
    ] * 2
    assert not seen
    write(tagged, "src/App/Calc.cs", "class Calc { int X; }\n")
    write(tagged, "src/App/New.cs", "class New { }\n")
    write(tagged, "docs/readme.md", "x")
    for c in a.lint_checks("tests-approved/f"):
        assert c.run().status == "PASS"
    assert seen[0] == [
        "dotnet",
        "format",
        "style",
        "App.slnx",
        "--verify-no-changes",
        "--include",
        "src/App/Calc.cs",
        "src/App/New.cs",
    ]
    assert seen[1][2] == "whitespace"
    assert seen[2] == ["dotnet", "build", "App.slnx", "-c", "Release", "--no-incremental"]
    seen.clear()
    a.lint_checks(None)[0].run()  # no base ref: the src and tests folders
    assert seen[0][-2:] == ["src", "tests"]


def test_gate_command_override(tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        dn,
        "run_command",
        lambda cmd, cwd, env=None, timeout=None: seen.append(list(cmd)) or (1, "bad"),
    )
    a = adapter(tagged, gates={"commands": {"lint": [], "typecheck": ["mybuild", "{src}"]}})
    checks = a.lint_checks("tests-approved/f")
    assert [c.name for c in checks] == ["format", "typecheck"]
    assert checks[1].run().status == "FAIL" and seen[-1] == ["mybuild", "src"]


@pytest.mark.parametrize(
    ("mode", "status"), [("changed", "FAIL"), ("any", "FAIL"), ("ignore", "PASS")]
)
def test_typecheck_warnings(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch, mode: str, status: str
) -> None:
    write(tagged, "src/App/Calc.cs", "class Calc { int X; }\n")
    calc = (tagged / "src" / "App" / "Calc.cs").resolve()
    out = (
        f"{calc}(1,18): warning CS0169: The field 'Calc.X' is never used [{tagged / 'App.csproj'}]"
    )
    monkeypatch.setattr(dn, "run_command", lambda cmd, cwd, env=None, timeout=None: (0, out))
    a = adapter(tagged, {"warnings": mode})
    res = [c for c in a.lint_checks("tests-approved/f") if c.name == "typecheck"][0].run()
    assert res.status == status
    if mode == "changed":
        assert "NEW WARNINGS in changed files:" in res.output and "CS0169" in res.output
        same = (tagged / "src" / "App" / "Same.cs").resolve()
        unchanged = out.replace(str(calc), str(same))
        monkeypatch.setattr(
            dn, "run_command", lambda cmd, cwd, env=None, timeout=None: (0, unchanged)
        )
        assert [c for c in a.lint_checks("tests-approved/f") if c.name == "typecheck"][
            0
        ].run().status == "PASS"
    if mode == "ignore":
        assert "--no-incremental" not in a.default_gate_commands()["typecheck"]


def test_frame_patterns_and_protected_paths(tagged: pathlib.Path) -> None:
    a = adapter(tagged)
    rx = frame_diff.forbidden_regex(a)
    assert rx is not None
    for bad in (
        "#pragma warning disable CS0168",
        "  # pragma warning disable",
        '[SuppressMessage("x", "y")]',
        "[ExcludeFromCodeCoverage]",
        '[Fact(Skip = "later")]',
        "catch (Exception) { }",
        "catch (System.Exception)",
        "} catch { }",
        "catch (IOException e) { }",
    ):
        assert rx.search(bad), bad
    for ok in (
        "var x = y!;",
        "catch (IOException e) { Log(e); }",
        "#pragma warning restore",
        "SkipWhile(x)",
    ):
        assert not rx.search(ok), ok
    protected = frame_diff.protected_paths(a)
    for path in (
        "src/App/App.csproj",
        "App.slnx",
        "x.sln",
        "src/Directory.Build.props",
        "Directory.Packages.props",
        "global.json",
        "NuGet.config",
        ".editorconfig",
        "src/.editorconfig",
        ".config/dotnet-tools.json",
        "tests/App.Tests/x.cs",
    ):
        assert frame_diff.path_matches(path, protected), path
    for path in ("src/App/Calc.cs", "src/App/global.json.cs", "docs/x.md"):
        assert not frame_diff.path_matches(path, protected), path
    write(tagged, "src/App/App.csproj", "<Project />")
    write(tagged, "src/App/Calc.cs", "class Calc {\n#pragma warning disable CS0169\n int X; }\n")
    res = frame_diff.check(a, "f")
    assert "PROTECTED PATH CHANGED: src/App/App.csproj" in res.output
    assert "SUPPRESSION ADDED: #pragma warning disable CS0169" in res.output


# ---------- mutation ----------
def test_mutation_skips(tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = adapter(tagged, gates={"mutation_tool": "none"})
    assert a.mutation_check("f", "t", ["src/App/Calc.cs"]).status == "SKIPPED"
    a = adapter(tagged, gates={"mutation_tool": "mutmut"})
    assert "not supported" in a.mutation_check("f", "t", ["src/App/Calc.cs"]).output
    a = adapter(tagged)
    assert a.mutation_check("f", "t", ["src/App/x.py"]).status == "PASS"
    monkeypatch.setattr(a, "stryker_command", lambda: None)
    res = a.mutation_check("f", "t", ["src/App/Calc.cs"])
    assert res.status == "SKIPPED" and "dotnet tool install -g dotnet-stryker" in res.output
    monkeypatch.setattr(a, "stryker_command", lambda: ["dotnet-stryker"])
    assert "no test project" in a.mutation_check("f", "t", ["src/App/Calc.cs"]).output


def test_mutation_runs_stryker_per_project(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tagged, "src/App/App.csproj", "<Project />")
    write(tagged, "src/App/Sub/Deep.cs", "class D { }")
    write(
        tagged,
        "tests/App.Tests/App.Tests.csproj",
        'Microsoft.NET.Test.Sdk <ProjectReference Include="..\\..\\src\\App\\App.csproj" />',
    )
    write(tagged, "stryker-config.json", json.dumps({"stryker-config": {"concurrency": 2}}))
    other = b"\xef\xbb\xbfclass Other {\r\n}\r\n"  # untracked, unchanged: BOM + CRLF kept
    (tagged / "src/App/Other.cs").write_bytes(other)
    seen: list[list[str]] = []
    trees: list[pathlib.Path] = []

    def fake_run(
        cmd: Sequence[str], cwd: pathlib.Path, env: dict | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        seen.append(list(cmd))
        # Stryker runs in a temporary worktree with the uncommitted files, where only the
        # changed files can be mutated: the others start with `// Stryker disable all`
        assert cwd != tagged and (cwd / ".git").is_file()
        trees.append(cwd)
        app = cwd / "src/App"
        for rel in ("Calc.cs", "Sub/Deep.cs"):  # the changed files: as in the working tree
            mine = (tagged / "src/App" / rel).read_bytes()
            mutated = f"**/{rel}" in cmd
            assert (app / rel).read_bytes().endswith(mine)
            assert (app / rel).read_bytes().startswith(b"// Stryker") != mutated
        same = (tagged / "src/App/Same.cs").read_bytes()
        eol = b"\r\n" if b"\r\n" in same else b"\n"
        assert (app / "Same.cs").read_bytes() == b"// Stryker disable all" + eol + same
        assert (app / "Other.cs").read_bytes() == (
            b"\xef\xbb\xbf// Stryker disable all\r\nclass Other {\r\n}\r\n"
        )
        assert cmd[cmd.index("-tp") + 1] == str(cwd / "tests/App.Tests/App.Tests.csproj")
        out_dir = pathlib.Path(cmd[cmd.index("-O") + 1])
        (out_dir / "reports").mkdir(parents=True)
        files = {
            str((cwd / "src/App/Calc.cs").resolve()): {
                "mutants": [
                    {"status": "Killed"},
                    {"status": "Survived", "location": {"start": {"line": 1, "column": 1}}},
                ]
            },
            str((cwd / "src/App/Sub/Deep.cs").resolve()): {"mutants": [{"status": "Killed"}]},
        }
        (out_dir / "reports" / "mutation-report.json").write_text(
            json.dumps({"files": files}), encoding="utf-8"
        )
        return 0, "done"

    monkeypatch.setattr(dn, "run_command", fake_run)
    a = adapter(tagged, {"base_filter": "Category!=Slow"}, gates={"mutation_score": 60})
    monkeypatch.setattr(a, "stryker_command", lambda: ["dotnet-stryker"])
    res = a.mutation_check("f", "tests-approved/f", ["src/App/Calc.cs", "src/App/Sub/Deep.cs"])
    assert res.status == "PASS" and res.output.startswith("Mutation score 66.7% (2/3 killed)"), (
        res.output
    )
    cmd = seen[0]
    assert cmd[cmd.index("--project") + 1] == "App.csproj"
    assert [cmd[i + 1] for i, x in enumerate(cmd) if x == "--mutate"] == [
        "**/Calc.cs",
        "**/Sub/Deep.cs",
    ]
    assert cmd[cmd.index("-tp") + 1].endswith("App.Tests.csproj")
    cfg = json.loads(pathlib.Path(cmd[cmd.index("-f") + 1]).read_text(encoding="utf-8"))
    assert cfg == {"stryker-config": {"concurrency": 2, "test-case-filter": "Category!=Slow"}}
    survivors = (tagged / "out/f/mutation_survivors.md").read_text(encoding="utf-8")
    assert "`src/App/Calc.cs:1:1`" in survivors
    assert "--mutation-level" not in cmd  # default: Stryker's own (Standard)
    # the worktree is gone, the repository's own files are untouched
    assert not trees[0].exists()
    assert len(git(tagged, "worktree", "list").splitlines()) == 1
    assert (tagged / "src/App/Other.cs").read_bytes() == other
    assert b"Stryker" not in (tagged / "src/App/Same.cs").read_bytes()
    a2 = adapter(tagged, {"mutation_level": "Basic"}, gates={"mutation_score": 80})
    monkeypatch.setattr(a2, "stryker_command", lambda: ["dotnet-stryker"])
    assert a2.mutation_check("f", "x", ["src/App/Calc.cs"]).status == "FAIL"
    assert seen[-1][seen[-1].index("--mutation-level") + 1] == "Basic"


def _projects(root: pathlib.Path, tests_ref: Sequence[str]) -> None:
    """Core, Lib (-> Core) and Runner (-> Core) under src, a test project referencing
    `tests_ref` (project names under src)."""
    write(root, "src/Core/Core.csproj", "<Project />")
    write(root, "src/Core/Engine.cs", "class Engine { }\n")
    for name in ("Lib", "Runner"):
        write(
            root,
            f"src/{name}/{name}.csproj",
            r'<Project><ItemGroup><ProjectReference Include="..\Core\Core.csproj" />'
            "</ItemGroup></Project>",
        )
        write(root, f"src/{name}/{name}Code.cs", f"class {name}Code {{ }}\n")
    refs = "".join(rf'<ProjectReference Include="..\..\src\{n}\{n}.csproj" />' for n in tests_ref)
    # the name in a comment is not a reference
    write(
        root,
        "tests/Demo.Tests/Demo.Tests.csproj",
        f"<Project><!-- Runner.csproj: tested by hand --> Microsoft.NET.Test.Sdk {refs}</Project>",
    )


def _fake_stryker(
    monkeypatch: pytest.MonkeyPatch,
    mutants: dict[str, list[str]],
    fail: Sequence[str] = (),
    fail_once: Sequence[str] = (),
) -> list[list[str]]:
    """run_command faking Stryker: `mutants` = repo-relative file -> mutant statuses; a run
    with --project in `fail` exits 1 without a report (`fail_once`: only its first run).
    Returns the commands seen."""
    seen: list[list[str]] = []
    once = list(fail_once)

    def fake_run(
        cmd: Sequence[str], cwd: pathlib.Path, env: dict | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        seen.append(list(cmd))
        proj = cmd[cmd.index("--project") + 1]
        if proj in fail:
            return 1, "Failed to analyze project builds. Stryker cannot continue."
        if proj in once:
            once.remove(proj)
            return 1, "simulated build failed. Use '--diag' option to have the build log."
        out_dir = pathlib.Path(cmd[cmd.index("-O") + 1])
        (out_dir / "reports").mkdir(parents=True)
        loc = {"start": {"line": 1, "column": 1}}
        files = {
            str((cwd / f).resolve()): {"mutants": [{"status": s, "location": loc} for s in sts]}
            for f, sts in mutants.items()
        }
        (out_dir / "reports" / "mutation-report.json").write_text(
            json.dumps({"files": files}), encoding="utf-8"
        )
        return 0, "done"

    monkeypatch.setattr(dn, "run_command", fake_run)
    return seen


def _stryker_adapter(root: pathlib.Path, score: float = 60) -> DotnetAdapter:
    a = adapter(root, gates={"mutation_score": score})
    a.stryker_command = lambda: ["dotnet-stryker"]  # type: ignore[method-assign]
    return a


def test_mutation_leaves_out_a_project_no_test_project_references(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _projects(tagged, ["Core"])
    seen = _fake_stryker(monkeypatch, {"src/Core/Engine.cs": ["Killed", "Killed", "Survived"]})
    changed = ["src/Core/Engine.cs", "src/Runner/RunnerCode.cs"]
    res = _stryker_adapter(tagged).mutation_check("f", "tests-approved/f", changed)
    assert [c[c.index("--project") + 1] for c in seen] == ["Core.csproj"]
    first = res.output.splitlines()[0]
    assert res.status == "PASS" and first == res.summary, res.output
    assert first.startswith("Mutation score 66.7% (2/3 killed) on Core.csproj, threshold 60%")
    assert first.endswith("Runner.csproj not mutated: no test project references it")
    line = "not mutated (no test project references it): Runner.csproj: src/Runner/RunnerCode.cs"
    assert line in res.output.splitlines() and "Core.csproj: 2 detected, 1 undetected" in res.output
    survivors = (tagged / "out/f/mutation_survivors.md").read_text(encoding="utf-8")
    assert "`src/Core/Engine.cs:1:1`" in survivors and line in survivors
    # the score comes from Core's report alone
    res = _stryker_adapter(tagged, score=80).mutation_check("f", "tests-approved/f", changed)
    assert res.status == "FAIL" and "not mutated" in res.summary


def test_mutation_skipped_when_no_changed_project_is_referenced(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _projects(tagged, ["Core"])
    seen = _fake_stryker(monkeypatch, {})
    res = _stryker_adapter(tagged).mutation_check(
        "f", "tests-approved/f", ["src/Runner/RunnerCode.cs"]
    )
    assert res.status == "SKIPPED" and not seen
    lines = res.output.splitlines()
    assert lines[0] == (
        "not mutated (no test project references it): Runner.csproj: src/Runner/RunnerCode.cs"
    )
    assert lines[-1] == "SKIPPED: nothing to mutate, no test project references Runner.csproj"


def test_mutation_follows_project_references_transitively(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _projects(tagged, ["Lib"])  # Tests -> Lib -> Core
    assert dn.referenced_projects(tagged / "tests/Demo.Tests/Demo.Tests.csproj") == {
        (tagged / "src/Lib/Lib.csproj").resolve(),
        (tagged / "src/Core/Core.csproj").resolve(),
    }
    seen = _fake_stryker(monkeypatch, {"src/Core/Engine.cs": ["Killed"]})
    res = _stryker_adapter(tagged).mutation_check("f", "tests-approved/f", ["src/Core/Engine.cs"])
    assert res.status == "PASS" and "not mutated" not in res.output, res.output
    cmd = seen[0]
    assert cmd[cmd.index("--project") + 1] == "Core.csproj"
    assert cmd[cmd.index("-tp") + 1].endswith("Demo.Tests.csproj")


def test_mutation_failure_keeps_the_finished_projects(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _projects(tagged, ["Core", "Lib"])
    seen = _fake_stryker(
        monkeypatch, {"src/Lib/LibCode.cs": ["Killed", "Survived"]}, ["Core.csproj"]
    )
    changed = ["src/Core/Engine.cs", "src/Lib/LibCode.cs", "src/Runner/RunnerCode.cs"]
    res = _stryker_adapter(tagged).mutation_check("f", "tests-approved/f", changed)
    # a failed Stryker run is started once more before it counts
    assert [c[c.index("--project") + 1] for c in seen] == ["Core.csproj"] * 2 + ["Lib.csproj"]
    assert "--diag" not in seen[1]  # only when Stryker's output offers it
    assert res.status == "FAIL", res.output
    assert res.output.startswith(
        "Stryker failed twice for Core.csproj (exit 1; log: out/f/stryker/Core.log):\n"
        "Failed to analyze"
    )
    assert "Finished: Lib.csproj: 1 detected, 1 undetected" in res.output
    assert "not mutated (no test project references it): Runner.csproj" in res.output
    assert res.summary == (
        "Stryker failed for Core.csproj; finished: Lib.csproj: 1 detected, 1 undetected"
    )
    survivors = (tagged / "out/f/mutation_survivors.md").read_text(encoding="utf-8")
    assert "`src/Lib/LibCode.cs:1:1`" in survivors


def test_mutation_second_try_after_a_stryker_failure(
    tagged: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen live: Stryker's own build failed once and worked on the next run, nothing changed."""
    _projects(tagged, ["Core"])
    seen = _fake_stryker(
        monkeypatch, {"src/Core/Engine.cs": ["Killed", "Killed"]}, fail_once=["Core.csproj"]
    )
    res = _stryker_adapter(tagged).mutation_check("f", "tests-approved/f", ["src/Core/Engine.cs"])
    assert len(seen) == 2 and seen[1] == [*seen[0], "--diag"]
    assert res.status == "PASS", res.output
    assert "Core.csproj: 2 detected, 0 undetected (second try: Stryker failed" in res.output
    first = (tagged / "out/f/stryker/Core.first.log").read_text(encoding="utf-8")
    assert "simulated build failed" in first


def test_diff_hunks() -> None:
    diff = (
        "diff --git a/x.cs b/x.cs\n@@ -3 +3 @@\n-a\n+b\n@@ -10,0 +11,4 @@\n+c\n+d\n+e\n+f\n"
        "@@ -20,2 +24,0 @@\n-g\n-h\n"
    )
    assert dn.diff_hunks(diff) == [(3, 3), (11, 14), (24, 25)]
    assert dn.diff_hunks("") == []


SCOPED_SRC = """\
namespace Demo
{
    public static class Engine
    {
        public static int Hot(int x) => x * 2;

        public static string Text() => @"line one
line two";

        public static bool Changed(int x)
        {
            var y = x + 1;
            return y > 3
                && y != 7;
        }

        public static int Other(int x) => x - 1;
    }
}
"""


def test_scope_mutations_frames_the_changed_statements() -> None:
    """Only the changed hunk is mutated: disable on top, restore before the hunk's first
    statement, disable after its last one; never inside a literal or mid-expression."""
    assert csharp.enclosed_lines(SCOPED_SRC) == {8}  # inside the verbatim string
    text, orig = dn.scope_mutations(SCOPED_SRC, [(14, 14)])  # `&& y != 7;` changed
    lines = text.splitlines()
    assert lines[0] == "// Stryker disable all"
    restore = lines.index("// Stryker restore all")
    # widened up to the statement start (`return y > 3` begins after `var y = x + 1;`)
    assert lines[restore + 1].strip() == "return y > 3"
    assert lines[restore + 3] == "// Stryker disable all"  # after `&& y != 7;`
    assert lines[restore + 4].strip() == "}"
    assert len(lines) == len(SCOPED_SRC.splitlines()) + 3
    assert [orig[i] for i in range(len(lines)) if orig[i] == 0] == [0, 0, 0]
    assert orig[restore + 1] == 13 and orig[-1] == 19
    # a hunk inside the literal moves the restore up to the statement before the string
    text, _ = dn.scope_mutations(SCOPED_SRC, [(8, 8)])
    assert '@"line one\nline two";' in text.replace("\r\n", "\n")
    lines = text.splitlines()
    assert lines[lines.index("// Stryker restore all") + 1].strip().startswith("public static str")
    # adjacent hunks are one region; CRLF and a BOM are kept
    crlf = "﻿" + SCOPED_SRC.replace("\n", "\r\n")
    text, _ = dn.scope_mutations(crlf, [(12, 12), (13, 14)])
    assert text.startswith("﻿// Stryker disable all\r\n")
    assert text.count("// Stryker restore all") == 1 and "\n" not in text.replace("\r\n", "")


def test_stryker_survivor_lines_map_back(tmp_path: pathlib.Path) -> None:
    report = {
        "files": {
            str(tmp_path / "src/A.cs"): {
                "mutants": [
                    {"status": "Survived", "location": {"start": {"line": 3, "column": 5}}},
                    {"status": "Killed"},
                ]
            }
        }
    }
    d, u, s = dn.stryker_results(report, tmp_path, ["src/A.cs"], {"src/a.cs": [0, 1, 7]})
    assert (d, u) == (1, 1) and s[0].startswith("- `src/A.cs:7:5`")


def test_format_fix_commands(tagged: pathlib.Path) -> None:
    """The gates fix with the rules the format (whitespace) and lint (style) checks verify."""
    a = adapter(tagged)
    cmds = a.format_fix_commands(["src/App/Calc.cs"])
    assert [c[1:3] for c in cmds] == [["format", "whitespace"], ["format", "style"]]
    assert cmds[0][-3:] == ["App.slnx", "--include", "src/App/Calc.cs"]
    a = adapter(tagged, gates={"commands": {"lint": []}})
    assert [c[2] for c in a.format_fix_commands(["src/App/Calc.cs"])] == ["whitespace"]
    a = adapter(tagged, gates={"commands": {"format": ["my-formatter"]}})
    assert a.format_fix("tests-approved/f") == ""  # the project's own formatter: not touched


# ---------- config, agents, init ----------
def test_settings_defaults_and_validation(tmp_path: pathlib.Path) -> None:
    s = adapter(tmp_path).settings
    assert (s.configuration, s.base_filter, s.build_before_test, s.warnings) == (
        "Release",
        "",
        True,
        "changed",
    )
    with pytest.raises(ConfigError, match="warnings must be one of"):
        adapter(tmp_path, {"warnings": "loud"})
    with pytest.raises(ConfigError, match="test_timeout must be >= 1"):
        adapter(tmp_path, {"test_timeout": 0})
    with pytest.raises(ConfigError, match="mutation_level must be one of"):
        adapter(tmp_path, {"mutation_level": "fast"})
    keys = set(re.findall(r"^# (\w+) =", dn.CONFIG_TEMPLATE, re.M))
    assert keys == set(dn.DotnetSettings.__dataclass_fields__)


def test_agent_tools_and_brief(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "App.sln", "")
    a = adapter(
        tmp_path, {"base_filter": "Category!=Slow"}, paths={"review_tests": "tests/A.Tests/Review"}
    )
    assert a.allowed_tools("IMPLEMENT")[-6:] == [
        "Bash(dotnet test *)",
        "PowerShell(dotnet test *)",
        "Bash(dotnet build *)",
        "PowerShell(dotnet build *)",
        "Bash(dotnet format *)",
        "PowerShell(dotnet format *)",
    ]
    assert not [t for t in a.allowed_tools("TESTS") if "dotnet add" in t or "dotnet new" in t]
    brief = a.conventions_brief()
    assert len(brief.encode()) < 1600
    for text in (
        '[Trait("Req", "XXX-R-001")]',
        "throw new NotImplementedException();",
        "[MemberData]",
        '--filter "(Req=ID1|Req=ID2)&(Category!=Slow)"',
        "tests/A.Tests/Review/",
        "#pragma warning disable",
    ):
        assert text in brief, text


def test_detect_init_and_ci(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert not DotnetAdapter.detect(tmp_path)
    write(tmp_path, "App.slnx", "<Solution />")
    write(tmp_path, "tests/App.Tests/App.Tests.csproj", "Microsoft.NET.Test.Sdk")
    assert DotnetAdapter.detect(tmp_path) and detect_language(tmp_path) == "dotnet"
    write(tmp_path, ".gitignore", "bin/\n")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    toml = (tmp_path / "agentflow.toml").read_text(encoding="utf-8")
    assert 'language = "dotnet"' in toml and "[dotnet]" in toml and '# base_filter = ""' in toml
    assert load_config(tmp_path).language == "dotnet"  # the template parses
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert gi == ["bin/", "out/", ".phase", ".reqs", "obj/", "TestResults/"]
    assert "actions/setup-dotnet@v4" in out and 'dotnet-version: "10.0.x"' in out
    assert 'review_tests = "tests/App.Tests/Review"' in out
    write(tmp_path, "global.json", "{}")
    assert "global-json-file: global.json" in adapter(tmp_path).ci_setup()
