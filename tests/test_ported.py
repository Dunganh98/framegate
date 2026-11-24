"""What the first real port showed (da-argos `runner-skeleton`, ported from da-otto): tests that
are green against the stubs by design, stubs written from the reference, fidelity at every step
gate, kept quirks, the reference follow-up, an idle plan step, the carried-tests report."""

from __future__ import annotations

import pathlib

import pytest

from conftest import REQUIREMENTS, DemoRepo, git, init_git
from framegate import doctor, followup, ported, references
from framegate.adapters import get_adapter, python_port
from framegate.checks import red_stubs, spec_lint
from framegate.config import load_config, parse_config
from framegate.orchestrator import EXIT_GATE, IDLE_PROGRESS, Orchestrator
from framegate.scaffold import package_text

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"

OTTO_CODE = '''\
"""Demo feature (OTTO-123, see ADR 7)."""


def add(a: int, b: int) -> int:
    """Add two integers the otto way."""
    if not isinstance(a, int) or not isinstance(b, int):
        raise TypeError("ints only")
    return a + b
'''
OTTO_STUB = '''\
"""Demo feature (OTTO-123, see ADR 7)."""


def add(a: int, b: int) -> int:
    """Add two integers the otto way."""
    raise NotImplementedError
'''
OTTO_TESTS = """\
from demo import add


def test_add_returns_sum() -> None:
    assert add(2, 3) == 5


def test_add_rejects_non_int() -> None:
    assert add


class TestOtto:
    def test_otto_only(self) -> None:
        assert add(1, 1) == 2
"""
PORT = """
## Ported from
Reference: otto
Commit: {commit}

| Source (path in the reference) | Target (path in this repository) | Kind (code / tests) |
|---|---|---|
| demo/__init__.py | src/demo/__init__.py | code |
| demo/data.json | src/demo/data.json | code |
| tests/test_demo.py | tests/test_demo.py | tests |

What may differ:
- tests carry `@pytest.mark.req` tags

Kept quirks:
- demo/__init__.py: add(): a bool counts as an int
- None.

## Reference follow-up
- demo/__init__.py: say "ints only" for None too (fixed in this port)

## Green against stubs
- `tests/test_demo.py::test_reference_constant`: reads the function's name, which the stub has
"""


def make_otto(r: DemoRepo) -> pathlib.Path:
    otto = r.root.parent / "da-otto"
    (otto / "demo").mkdir(parents=True)
    (otto / "tests").mkdir()
    init_git(otto)
    (otto / "demo" / "__init__.py").write_text(OTTO_CODE, encoding="utf-8")
    (otto / "demo" / "data.json").write_text('{"a": 1}\n', encoding="utf-8")
    (otto / "tests" / "test_demo.py").write_text(OTTO_TESTS, encoding="utf-8")
    git(otto, "add", "-A")
    git(otto, "commit", "-q", "-m", "otto")
    r.add_toml('[references]\notto = "../da-otto"\n')
    return otto


def use(r: DemoRepo, text: str) -> None:
    (r.root / "specs/demo/requirements.md").write_text(REQUIREMENTS + text, encoding="utf-8")


def head(repo: pathlib.Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


# ---------- 2. stubs from the reference ----------
SOURCE = '''\
"""Module doc (PLAN-9)."""
from __future__ import annotations

import os
from typing import overload

LIMIT = 3  # a constant the tests read
PATTERN = make_pattern("x")


def make_pattern(text: str) -> str:
    """Runs at import."""
    return helper(text) + "$"


def helper(text: str) -> str:
    return text.strip()


def register(fn):
    return fn


class Runner:
    """A runner."""

    retries: int = LIMIT

    def __init__(self, name: str) -> None:
        self.name = name  # set
        self.count = 0

    @property
    def label(self) -> str:
        """The label."""
        # a comment of the body
        return f"{self.name}:{self.count}"

    @overload
    def get(self, key: int) -> int: ...

    def get(self, key):
        def inner() -> int:
            return key

        return inner()

    def nothing(self) -> None:
        """Only a docstring."""

    async def wait(self) -> None:
        pass


@register
def run(argv: list[str] | None = None) -> int:
    for _ in range(LIMIT):
        os.getcwd()
    return 0  # exit code


def short(): return 1


if __name__ == "__main__":
    raise SystemExit(run())
'''
STUB = '''\
"""Module doc (PLAN-9)."""
from __future__ import annotations

import os
from typing import overload

LIMIT = 3  # a constant the tests read
PATTERN = make_pattern("x")


def make_pattern(text: str) -> str:
    """Runs at import."""
    return helper(text) + "$"


def helper(text: str) -> str:
    return text.strip()


def register(fn):
    return fn


class Runner:
    """A runner."""

    retries: int = LIMIT

    def __init__(self, name: str) -> None:
        raise NotImplementedError

    @property
    def label(self) -> str:
        """The label."""
        # a comment of the body
        raise NotImplementedError

    @overload
    def get(self, key: int) -> int: ...

    def get(self, key):
        raise NotImplementedError

    def nothing(self) -> None:
        """Only a docstring."""
        raise NotImplementedError

    async def wait(self) -> None:
        pass


@register
def run(argv: list[str] | None = None) -> int:
    raise NotImplementedError


def short(): raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(run())
'''


def test_python_stub_keeps_the_text_and_replaces_the_bodies() -> None:
    stub, notes = python_port.stub_source(SOURCE.encode("utf-8"))
    assert stub.decode("utf-8") == STUB
    assert notes[0] == "6 function bodies replaced"
    # called at module level, by such a function, or used as a decorator: left whole
    assert (
        "kept whole, they run when the module is imported: helper, make_pattern, register"
        in (notes[1])
    )
    assert "'Green against stubs'" in notes[1]
    assert notes[2].startswith("2 left as they are")
    # line endings and a byte order mark stay as they are; a second run changes nothing
    crlf = b"\xef\xbb\xbf" + SOURCE.replace("\n", "\r\n").encode("utf-8")
    again, _ = python_port.stub_source(crlf)
    assert again == b"\xef\xbb\xbf" + STUB.replace("\n", "\r\n").encode("utf-8")
    assert python_port.stub_source(STUB.encode("utf-8"))[0].decode("utf-8") == STUB
    with pytest.raises(SyntaxError):
        python_port.stub_source(b"def broken(:\n")


def test_stub_imports_that_will_not_resolve(tmp_path: pathlib.Path) -> None:
    (tmp_path / "runner").mkdir()
    (tmp_path / "runner" / "common.py").write_text("X = 1\n", encoding="utf-8")
    text = (
        b"import os\nimport yaml\nimport ghostlib.sub\nfrom platform.runner.common import X\n"
        b"from runner.common import X\nfrom runner.gone import Y\nfrom . import common, cli\n"
        b"from .common import X\nfrom ..shared import Z\n"
    )
    ported_files = {"platform/runner/common.py": "runner/common.py"}
    found = python_port.import_problems(
        tmp_path, [tmp_path], "runner/controller.py", text, ported_files, {"yaml"}
    )
    assert [x.split(": ", 1)[0] for x in found] == [
        "runner/controller.py:3",
        "runner/controller.py:4",
        "runner/controller.py:6",
        "runner/controller.py:7",
        "runner/controller.py:9",
    ]
    assert "`ghostlib.sub`: not found in this repository or its environment" in found[0]
    assert "the reference's module is ported here as runner/common.py" in found[1]
    assert "`runner.gone`: no such module in this repository's `runner`" in found[2]
    assert "`.cli`: no such module next to the target" in found[3]
    assert "`..shared`" in found[4]
    # the interpreter could not be asked: an unknown package is still named
    unknown = python_port.import_problems(tmp_path, [tmp_path], "a.py", b"import yaml\n", {}, None)
    assert unknown and "or its environment" not in unknown[0]


def test_port_stubs_command(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    """The stub of a ported code file is the reference's text with the bodies replaced; a
    data file is copied; a file that is there is kept unless --force; imports are checked."""
    r = demo_repo
    assert r.af("port-stubs", "demo") == 0
    assert "has no filled 'Ported from' section: nothing to do" in capsys.readouterr().out
    otto = make_otto(r)
    use(r, PORT.format(commit=head(otto)[:10]))
    assert r.af("port-stubs", "demo") == 0
    out = capsys.readouterr().out
    assert "KEPT: src/demo/__init__.py exists and is not the generated stub (--force" in out
    assert "WROTE: src/demo/data.json <- otto/demo/data.json" in out and "copied as it is" in out
    assert "port-stubs: 1 written, 0 unchanged, 1 kept, 0 problem(s); 0 import(s)" in out
    # the reference moves on: the stub is the text at the spec's commit
    old = head(otto)[:10]
    (otto / "demo" / "__init__.py").write_text(OTTO_CODE + "\nLATER = 1\n", encoding="utf-8")
    git(otto, "commit", "-q", "-am", "later")
    assert r.af("port-stubs", "demo", "--force") == 0
    out = capsys.readouterr().out
    assert f"WROTE: src/demo/__init__.py <- otto/demo/__init__.py (reference at {old}): " in out
    assert "1 function bodies replaced" in out and "UNCHANGED: src/demo/data.json" in out
    assert (r.root / "src/demo/__init__.py").read_text(encoding="utf-8") == OTTO_STUB
    assert (r.root / "src/demo/data.json").read_bytes() == (
        otto / "demo" / "data.json"
    ).read_bytes()
    assert r.af("port-stubs", "demo") == 0
    assert "port-stubs: 0 written, 2 unchanged, 0 kept" in capsys.readouterr().out
    # a stub whose import this repository cannot resolve; a source row that is not there
    (otto / "demo" / "more.py").write_text("from otto_pkg.x import y\n", encoding="utf-8")
    git(otto, "add", "-A")
    git(otto, "commit", "-q", "-m", "more")
    use(
        r,
        PORT.format(commit=head(otto)[:10]).replace(
            "| demo/data.json | src/demo/data.json | code |",
            "| demo/more.py | src/demo/more.py | code |\n| demo/no.py | src/demo/no.py | code |",
        ),
    )
    assert r.af("port-stubs", "demo") == 1
    out = capsys.readouterr().out
    assert "IMPORT: src/demo/more.py:1: `otto_pkg.x`: not found in this repository" in out
    assert "PROBLEM: src/demo/no.py <- otto/demo/no.py: not found in the reference" in out
    assert "Fix each IMPORT line in its stub" in out
    # after the approval the stubs are frozen; a reference that is not here
    (r.root / "src/demo/more.py").unlink()
    use(r, PORT.format(commit=head(otto)[:10]))
    assert r.af("approve-spec", "demo") == 0
    capsys.readouterr()
    assert r.af("port-stubs", "demo") == 1
    assert "PROBLEM: the spec is approved" in capsys.readouterr().out
    otto.rename(otto.with_name("moved away"))
    assert r.af("port-stubs", "demo", "--force") == 1
    assert "its folder is not on this machine" in capsys.readouterr().out


def test_port_stubs_says_when_the_language_has_none(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = parse_config(
        tmp_path, {"project": {"language": "dotnet"}, "references": {"otto": "../otto"}}
    )
    spec = tmp_path / "specs" / "demo"
    spec.mkdir(parents=True)
    (spec / "requirements.md").write_text(PORT.format(commit="abcdef1234"), encoding="utf-8")
    assert ported.port_stubs(cfg, get_adapter(cfg), "demo") == 0
    out = capsys.readouterr().out
    assert out.startswith("NOT SUPPORTED: no stub generator for language 'dotnet'")
    rep = ported.carried(cfg, get_adapter(cfg), "demo", None)
    assert rep["skipped"] == "reference 'otto' cannot be read on this machine"


def test_the_conventions_and_the_templates_name_the_port_rules() -> None:
    adapter = get_adapter(parse_config(pathlib.Path("."), {}))
    assert "written in the SPEC phase by `framegate port-stubs`" in adapter.conventions_brief()
    template = package_text("templates", "specs/_template/requirements.md")
    for text in (
        "Kept quirks:",
        "## Reference follow-up",
        "## Green against stubs",
        "`framegate port-stubs <feature>`",
        "stay the reference's, word for word",
    ):
        assert text in template, text
    port = references.parse_port(template)
    assert port is None and red_stubs.parse_green(template) == []
    assert red_stubs.green_problems(template) == []
    for name in ("_template", "_template_bugfix", "_template_refactor"):
        text = package_text("templates", f"specs/{name}/requirements.md")
        assert "## Reference follow-up" in text, name
        assert followup.parse_spec(text, followup.REFERENCE) == [], name
        assert "Only deploy / CI files" in text, name
    plan = package_text("templates", "specs/_template/plan.md")
    assert "One step per `code` target file, in import order" in plan
    skill = (
        pathlib.Path(__file__).resolve().parents[1] / "plugin/skills/feature/SKILL.md"
    ).read_text(encoding="utf-8")
    for text in (
        "framegate port-stubs $ARGUMENTS",
        '"Kept quirks"',
        '"Green against stubs"',
        '"Reference follow-up"',
        "one step per `code` target file",
        "`NOT SUPPORTED`",
    ):
        assert text in skill, text


def test_doctor_names_a_missing_label_line_of_the_template_copy(demo_repo: DemoRepo) -> None:
    r = demo_repo
    tpl = r.root / "specs" / "_template" / "requirements.md"
    text = tpl.read_text(encoding="utf-8")
    assert doctor.outdated_templates(load_config(r.root)) == {}
    tpl.write_text(
        text.replace("\nKept quirks:\n-\n", "\n").replace("## Green against stubs", "## Other"),
        encoding="utf-8",
    )
    assert doctor.outdated_templates(load_config(r.root)) == {
        "requirements.md": ["Green against stubs", 'line "Kept quirks:" (in Ported from)']
    }


# ---------- 4. kept quirks, 5. reference follow-up: the spec and the tasks ----------
def test_kept_quirks_and_the_reference_followup_in_the_tasks(demo_repo: DemoRepo) -> None:
    r = demo_repo
    make_otto(r)
    use(r, PORT.format(commit="abcdef1234"))
    port = references.parse_port(REQUIREMENTS + PORT.format(commit="abcdef1234"))
    assert port is not None
    assert port.differ == ["tests carry `@pytest.mark.req` tags"]
    assert port.quirks == ["demo/__init__.py: add(): a bool counts as an int"]
    orch = Orchestrator(load_config(r.root))
    review = orch.task_text("REVIEW", "demo")
    triage = orch.task_text("TRIAGE", "demo", findings="- test_x")
    implement = orch.task_text(
        "IMPLEMENT", "demo", n="1", title="add", ids="DEMO-R-001", check="x", note="n.md"
    )
    line = "`- REFERENCE FOLLOW-UP [<reference file>]: <what the reference should change"
    quirk = "demo/__init__.py: add(): a bool counts as an int"
    assert f"lists them under 'Kept quirks'): {quirk}. Write no finding and no test" in review
    assert "stays a finding when the spec promises otherwise" in review
    assert f"AND add one line to review.md, {line}" in review
    assert f"the port keeps on purpose): {quirk}. A finding about one of them is INVALID" in triage
    assert "'The reference does the same' is no reason to dismiss a finding." in triage
    assert f"AND add one line to triage.md, {line}" in triage
    assert f"Quirks of the reference that stay as they are, on purpose: {quirk}." in implement
    assert f"add one line to your step note, {line}" in implement
    assert "every hunk must match a 'What may differ' bullet" in implement
    # the pipeline line is for deploy / CI files only
    assert "is not a pipeline follow-up" in implement
    assert "'Green against stubs'" in orch.task_text("TESTS", "demo")
    assert "'Reference tests carried over'" in orch.task_text(
        "TEST_REVIEW", "demo", round="1", rounds="2", note=""
    )
    # a feature that only reads a reference is told where a needed change of it is noted
    use(r, "\n## References\n| Name | Files | What for |\n|---|---|---|\n| otto | demo/ | x |\n")
    plain = orch.task_text("REVIEW", "demo")
    assert "This feature is a PORT" not in plain
    assert f"add one line to review.md: {line}" in plain
    assert "REFERENCE FOLLOW-UP" not in orch.task_text("TESTS", "demo")
    use(r, "")
    assert "REFERENCE FOLLOW-UP" not in orch.task_text("REVIEW", "demo")


def test_reference_followup_lines_and_lists(demo_repo: DemoRepo) -> None:
    r = demo_repo
    make_otto(r)
    use(r, PORT.format(commit="abcdef1234") + "\n## Pipeline follow-up\n- ci/x.yml: add it\n")
    cfg = load_config(r.root)
    ref = followup.REFERENCE
    notes = (
        "- REFERENCE FOLLOW-UP [`demo/__init__.py`]: refuse bools (shared bug)\n"
        "* reference followup [tests/t.py] add the test\n- REFERENCE [x]: not this\n"
        "- PIPELINE FOLLOW-UP [ci/x.yml]: add it\n"
    )
    assert followup.parse_run(notes, ref) == [
        "demo/__init__.py: refuse bools (shared bug)",
        "tests/t.py: add the test",
    ]
    assert followup.parse_run(notes) == ["ci/x.yml: add it"]
    out = r.root / "out" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "steps.md").write_text(notes, encoding="utf-8")
    (out / "triage.md").write_text("- REFERENCE FOLLOW-UP [a.py]: from triage\n", "utf-8")
    assert [it.line() for it in followup.items(cfg, "demo", ref)] == [
        'demo/__init__.py: say "ints only" for None too (fixed in this port) (from the spec)',
        "demo/__init__.py: refuse bools (shared bug) (implementer)",
        "tests/t.py: add the test (implementer)",
        "a.py: from triage (triage)",
    ]
    assert [it.line() for it in followup.items(cfg, "demo")] == ["ci/x.yml: add it (from the spec)"]
    brief = followup.all_brief_lines(cfg, "demo")
    assert brief[0].startswith("Pipeline follow-up (a separate step after this feature;")
    assert "Reference follow-up (for otto; not part of this feature): 4; nothing to answer" in brief
    facts = "\n".join(followup.facts_lines(cfg, "demo"))
    assert "## Reference follow-up (for otto; not part of this feature): 4" in facts
    assert facts.count("Not a gate item") == 2
    assert (
        (out / "reference_followup.md")
        .read_text(encoding="utf-8")
        .startswith("# Reference follow-up: demo\n\nWhat the reference should change")
    )
    assert (out / "pipeline_followup.md").is_file()


# ---------- 1. green against stubs by design ----------
def test_green_list_shape_and_matching(demo_repo: DemoRepo) -> None:
    text = (
        "## Green against stubs\n<!-- - not: this -->\n"
        "- `tests/test_demo.py::test_scan`: a scan of the source\n"
        "- TestRunner::test_names — reads names only\n- test_plain - no code runs\n"
        "- the scan test passes anyway\n- test_alone\n\n## Next\n- test_other: no\n"
    )
    green = red_stubs.parse_green(text)
    assert [(g.name, g.reason) for g in green] == [
        ("tests/test_demo.py::test_scan", "a scan of the source"),
        ("TestRunner::test_names", "reads names only"),
        ("test_plain", "no code runs"),
    ]
    assert green[0].matches("tests.test_demo::test_scan")
    assert green[0].matches("tests/test_demo.py::test_scan")
    assert not green[0].matches("tests.test_other::test_scan")
    assert green[1].matches("tests.test_x.TestRunner::test_names[1]")
    assert not green[1].matches("tests.test_x.TestOther::test_names")
    assert green[2].matches("anything.at.all::test_plain") and not green[2].matches("test_plain2")
    problems = red_stubs.green_problems(text)
    assert (
        len(problems) == 2 and '"the scan test passes anyway" is not `- <test name' in (problems[0])
    )
    for empty in ("None.", "", "-", "<none>"):
        assert red_stubs.parse_green(f"## Green against stubs\n{empty}\n") == []
        assert red_stubs.green_problems(f"## Green against stubs\n{empty}\n") == []
    r = demo_repo
    use(r, "\n## Green against stubs\n- this is no test line\n")
    assert "'Green against stubs'" in spec_lint.problems(load_config(r.root), "demo")[0]
    assert r.af("spec-check", "demo") == 1


TESTS = """\
import pytest

from demo import add


@pytest.mark.req("DEMO-R-001")
def test_add_returns_sum() -> None:
    assert add(2, 3) == 5


@pytest.mark.req("DEMO-E-001")
def test_add_rejects_non_int() -> None:
    with pytest.raises(TypeError, match="ints only"):
        add("1", 2)


@pytest.mark.req("DEMO-R-001")
def test_reference_constant() -> None:
    assert add.__name__ == "add"
"""
SCAN = """

@pytest.mark.req("DEMO-E-001")
def test_scan() -> None:
    assert "analytic" not in add.__doc__
"""


def test_red_check_accepts_exactly_the_listed_tests(demo_repo: DemoRepo) -> None:
    r = demo_repo
    adapter = get_adapter(load_config(r.root))
    tests = r.root / "tests" / "test_demo.py"
    tests.write_text(TESTS, encoding="utf-8")
    res = red_stubs.check(adapter, "demo")
    assert res.status == "FAIL"
    assert "PASSES AGAINST STUB (weak or wrong test): " in res.output
    assert "test_reference_constant" in res.output

    green = "\n## Green against stubs\n- test_reference_constant: reads the name only\n"
    use(r, green + "- tests/test_demo.py::test_gone: was planned\n")
    res = red_stubs.check(adapter, "demo")
    assert res.status == "PASS", res.output
    assert "passes by design (listed in the spec's 'Green against stubs'): " in res.output
    assert "test_reference_constant — reads the name only" in res.output
    assert "PASSES AGAINST STUB" not in res.output
    assert "lists tests/test_demo.py::test_gone, which is not among the feature's tests" in (
        res.output
    )

    # a listed test that is red; another test that passes; an ID only green tests prove
    raises = 'with pytest.raises(TypeError, match="ints only"):\n        add("1", 2)'
    tests.write_text(
        TESTS.replace(raises, "assert add is not None").replace(
            "test_add_rejects_non_int", "test_scan_too"
        )
        + SCAN,
        encoding="utf-8",
    )
    use(
        r,
        green + "- test_add_returns_sum: listed by mistake\n- test_scan: a scan\n"
        "- test_scan_too: a scan\n",
    )
    res = red_stubs.check(adapter, "demo")
    assert res.status == "FAIL"
    assert "LISTED AS GREEN BY DESIGN BUT RED" in res.output
    assert "test_add_returns_sum" in res.output.split("LISTED AS GREEN BY DESIGN BUT RED")[1]
    assert (
        "ONLY TESTS THAT PASS BY DESIGN PROVE DEMO-E-001 (tests/test_demo.py::test_scan_too, "
        "tests/test_demo.py::test_scan): add a test for it that fails against the stubs"
    ) in res.output


def test_a_spec_id_in_the_green_list_stands_for_its_tagged_tests(demo_repo: DemoRepo) -> None:
    """Seen live: "lists RLF-I-002, which is not among the feature's tests"."""
    r = demo_repo
    adapter = get_adapter(load_config(r.root))
    keep = '\n\n@pytest.mark.req("DEMO-I-001")\nclass TestKept:\n    def test_name(self) -> None:\n'
    keep += '        assert add.__name__ == "add"\n'
    tests = r.root / "tests" / "test_demo.py"
    tests.write_text(TESTS.split('@pytest.mark.req("DEMO-R-001")\ndef test_ref')[0] + keep, "utf-8")
    use(r, "\n## Must not change\n| DEMO-I-001 | the name |\n\n## Green against stubs\n")
    assert "PASSES AGAINST STUB (weak or wrong test): " in red_stubs.check(adapter, "demo").output
    use(r, "\n## Green against stubs\n- DEMO-I-001: holds today\n- DEMO-I-009: no test\n")
    res = red_stubs.check(adapter, "demo")
    assert res.status == "PASS", res.output
    assert "passes by design" in res.output and "TestKept" in res.output
    assert "ONLY TESTS THAT PASS BY DESIGN" not in res.output
    assert "lists DEMO-I-009, which is not among the feature's tests" in res.output
    assert "lists DEMO-I-001" not in res.output


# ---------- 7. which reference tests were carried over ----------
def test_carried_tests_report(demo_repo: DemoRepo) -> None:
    r = demo_repo
    otto = make_otto(r)
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    assert python_port.test_names(OTTO_TESTS.encode("utf-8")) == [
        "test_add_returns_sum",
        "test_add_rejects_non_int",
        "TestOtto::test_otto_only",
    ]
    assert ported.carried(cfg, adapter, "demo", None) == {}  # no port
    assert ported.brief_line(cfg, adapter, "demo", None) == ""
    use(r, PORT.format(commit=head(otto)[:10]))
    rep = ported.carried(cfg, adapter, "demo", None)  # no target file yet
    assert ported.carried_counts(rep) == (0, 3)
    (r.root / "tests/test_demo.py").write_text(TESTS, encoding="utf-8")
    rep = ported.carried(cfg, adapter, "demo", references.pin(cfg, "demo"))
    assert rep["files"] == [
        {
            "source": "tests/test_demo.py",
            "target": "tests/test_demo.py",
            "total": 3,
            "missing": ["TestOtto::test_otto_only"],
        }
    ]
    lines = ported.carried_lines(rep)
    assert lines[0] == (
        "2 of 3 test function(s) of reference 'otto' are in the target files (by name)."
    )
    assert lines[1:3] == [
        "- tests/test_demo.py -> tests/test_demo.py: 2 of 3 carried over",
        "    NOT CARRIED OVER: TestOtto::test_otto_only",
    ]
    assert "'## Reference tests not carried over'" in lines[3]
    assert ported.checks_section(rep).startswith(
        "### Reference tests carried over (note, not blocking): 2 of 3\n~~~\n2 of 3 test"
    )
    assert ported.brief_line(cfg, adapter, "demo", None) == (
        "reference tests carried: 2 of 3; not carried: see out/demo/test_checks.md, "
        "'Reference tests carried over'"
    )
    # all carried: nothing to look up; a test file the reference does not have
    (r.root / "tests/test_demo.py").write_text(OTTO_TESTS, encoding="utf-8")
    assert ported.brief_line(cfg, adapter, "demo", None) == "reference tests carried: 3 of 3"
    use(r, PORT.format(commit="abcdef1234").replace("| tests/test_demo.py |", "| tests/no.py |"))
    rep = ported.carried(cfg, adapter, "demo", None)
    assert rep["files"][0]["note"] == "not found in the reference"
    assert ported.carried_lines(rep)[1].endswith("not found in the reference")


# ---------- the whole run of a port ----------
def test_a_port_run_end_to_end(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    """Stubs from the reference, a carried test that is green by design, the carried-tests
    report, fidelity at the step gate and in the next run's task, kept quirks, and the
    reference follow-up from the spec, the implementer, the reviewer and the triage up to the
    final brief and the spec folder."""
    r = demo_repo
    r.add_toml(FAST)
    otto = make_otto(r)
    use(r, PORT.format(commit=head(otto)[:10]))
    assert r.af("port-stubs", "demo", "--force") == 0
    assert (r.root / "src/demo/__init__.py").read_text(encoding="utf-8") == OTTO_STUB
    r.scenario(green_test=True, ref_followup=True, review_finding=True)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE

    checks = (r.root / "out/demo/test_checks.md").read_text(encoding="utf-8")
    assert "### Red against stubs: PASS" in checks
    assert "passes by design (listed in the spec's 'Green against stubs'): " in checks
    assert "test_reference_constant — reads the function's name, which the stub has" in checks
    assert "### Reference tests carried over (note, not blocking): 2 of 3" in checks
    assert "    NOT CARRIED OVER: TestOtto::test_otto_only" in checks
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "Red against stubs PASS" in log and "; reference tests carried: 2 of 3" in log

    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    # the fake implementer wrote its own docstring: the step gate says how far that is
    step = next(e for e in r.events() if e["kind"] == "gate" and e["scope"] == "step")
    (row,) = step["fidelity"]["files"]
    assert row["target"] == "src/demo/__init__.py" and row["changed"] >= 1
    assert row["diff"] == "out/demo/fidelity/src__demo____init__.py.diff"
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "green (attempt 1), committed; 1 ported file(s) differ from the reference" in log
    # the run after it (the review fixes) is told, with the rule
    fix = [c["prompt"] for c in r.calls() if c["phase"] == "IMPLEMENT"][-1]
    assert "make all currently failing tests pass" in fix
    assert "Ported files as the steps before this run left them" in fix
    assert "- [code] demo/__init__.py -> src/demo/__init__.py: " in fix
    assert references.STEP_FIDELITY_RULE in fix
    review = next(c["prompt"] for c in r.calls() if c["phase"] == "REVIEW")
    assert "a bool counts as an int. Write no finding and no test about them." in review

    wanted = [
        'demo/__init__.py: say "ints only" for None too (fixed in this port) (from the spec)',
        "demo/__init__.py: refuse bools (fixed here; the reference accepts them) (implementer)",
        "demo/__init__.py: add() has no check for None (the reference crashes the same way) "
        "(reviewer)",
        "tests/test_demo.py: add the bool test (valid here, missing there) (triage)",
    ]
    head_line = "Reference follow-up (for otto; not part of this feature)"
    facts = (r.root / "out/demo/final_facts.md").read_text(encoding="utf-8")
    assert f"## {head_line}: 4" in facts and all(f"- {x}" in facts for x in wanted)
    assert "Pipeline follow-up" not in facts
    summary = (r.root / "out/demo/final_summary.md").read_text(encoding="utf-8")
    assert f"## {head_line} (generated by framegate): 4" in summary
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    brief = capsys.readouterr().out
    assert (f"{head_line}: 4; nothing to answer\n" + "".join(f"  - {x}\n" for x in wanted)) in brief
    assert "  reference tests carried: 2 of 3; not carried: see out/demo/test_checks.md" in brief
    assert "port fidelity against 'otto' (not blocking): 3 file(s), 1 identical" in brief

    assert r.af("finish", "demo", "--no-retro") == 0
    kept = r.root / "specs/demo/reference_followup.md"
    assert all(f"- [ ] {x}" in kept.read_text(encoding="utf-8") for x in wanted)
    assert not (r.root / "specs/demo/pipeline_followup.md").exists()
    (ev,) = [e for e in r.events() if e["kind"] == "reference_followup"]
    assert ev["items"] == wanted


# ---------- 6. a plan step whose run changes nothing ----------
def test_an_idle_step_runs_only_its_own_tests(demo_repo: DemoRepo) -> None:
    """Step 1 builds step 2's code too (a port lands whole files). Step 2's run changes
    nothing: its gate runs the step's tests and reuses the rest; nothing is committed."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.scenario(whole_files=True)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final" and not r.progress().get("parked")
    one, two = [e for e in r.events() if e["kind"] == "gate"][:2]
    assert (one["scope"], one["passed"]) == ("step", True)
    assert (two["scope"], two["passed"], two["step"]) == ("idle", True, "step 2 - neg")
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert f"[step 2 - neg] {IDLE_PROGRESS}" in log
    assert "[step 2 - neg] green (attempt 1), nothing to commit" in log
    assert "[step 1 - add] green (attempt 1), committed" in log
    assert r.progress()["completed_steps"] == [1, 2]
    assert len([c for c in r.calls() if c["phase"] == "IMPLEMENT"]) == 2
