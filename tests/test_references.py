"""Reference folders ([references], the spec's "References" section, --add-dir, pins) and
ports (the spec's "Ported from" section, task notes, the fidelity report)."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import REQUIREMENTS, DemoRepo, git, init_git
from framegate import doctor, references
from framegate.checks import spec_lint
from framegate.config import ConfigError, load_config, parse_config
from framegate.hooks import protect_paths
from framegate.orchestrator import EXIT_GATE
from framegate.scaffold import spec_brief

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"

#: the demo feature's code and tests as the reference project has them
OTTO_CODE = '''\
"""Demo feature."""


def add(a: int, b: int) -> int:
    """Add two integers the otto way."""
    if not isinstance(a, int) or not isinstance(b, int):
        raise TypeError("ints only")
    return a + b
'''
OTTO_TESTS = "from demo import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"

REFERENCES_SECTION = """
## References
| Name | Files / folders that matter | What for |
|---|---|---|
| `otto` | demo/ | the add() this feature follows |
"""
PORT_SECTION = """
## Ported from
Reference: otto
Commit: {commit}

| Source (path in the reference) | Target (path in this repository) | Kind (code / tests) |
|---|---|---|
| demo/__init__.py | src/demo/__init__.py | code |
| tests/test_demo.py | tests/test_demo.py | tests |
| demo/gone.py | src/demo/gone.py | code |

What may differ:
- the docstring of add() names the spec IDs
- tests carry `@pytest.mark.req` tags
"""


def make_otto(r: DemoRepo) -> pathlib.Path:
    """A sibling git repository `otto` next to the demo repo, declared as a reference."""
    otto = r.root.parent / "da otto"  # a space: the path is one argument everywhere
    (otto / "demo").mkdir(parents=True)
    (otto / "tests").mkdir()
    init_git(otto)
    (otto / "demo" / "__init__.py").write_text(OTTO_CODE, encoding="utf-8")
    (otto / "tests" / "test_demo.py").write_text(OTTO_TESTS, encoding="utf-8")
    git(otto, "add", "-A")
    git(otto, "commit", "-q", "-m", "otto")
    r.add_toml(
        '[references]\notto = { path = "../da otto", note = "the project this one ports" }\n'
        'core = "../da-core"\n'
    )
    return otto


def use(r: DemoRepo, *sections: str) -> None:
    path = r.root / "specs" / "demo" / "requirements.md"
    path.write_text(REQUIREMENTS + "".join(sections), encoding="utf-8")


def head(repo: pathlib.Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


# ---------- config ----------
def test_references_config_forms(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(
        tmp_path / "repo",
        {"references": {"otto": {"path": "../da-otto", "note": " the  runner "}, "core": "/x/c"}},
    )
    assert cfg.references["otto"].note == "the runner"
    assert cfg.reference_dir("otto") == tmp_path / "da-otto"
    assert cfg.reference_dir("core") == pathlib.Path("/x/c").resolve()
    assert parse_config(tmp_path, {}).references == {}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"otto": 3}, "must be a path or"),
        ({"otto": {"note": "x"}}, "needs a path"),
        ({"otto": {"path": "../o", "url": "x"}}, "must be a path or"),
        ({"otto": {"path": "../o", "note": 1}}, "note must be a string"),
        ({"1 bad": "../o"}, "a name is letters"),
    ],
)
def test_references_config_errors(
    tmp_path: pathlib.Path, body: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(tmp_path, {"references": body})


# ---------- the spec's sections ----------
def test_parse_port() -> None:
    port = references.parse_port(REQUIREMENTS + PORT_SECTION.format(commit="1a2b3c4d5e"))
    assert port is not None and port.reference == "otto" and port.commit == "1a2b3c4d5e"
    assert [(f.source, f.target, f.kind) for f in port.files] == [
        ("demo/__init__.py", "src/demo/__init__.py", "code"),
        ("tests/test_demo.py", "tests/test_demo.py", "tests"),
        ("demo/gone.py", "src/demo/gone.py", "code"),
    ]
    assert port.differ == [
        "the docstring of add() names the spec IDs",
        "tests carry `@pytest.mark.req` tags",
    ]


def test_normal_feature_is_no_port() -> None:
    template = (
        pathlib.Path(references.__file__).parent / "templates/specs/_template/requirements.md"
    ).read_text(encoding="utf-8")
    assert "## References" in template and "## Ported from" in template
    assert references.parse_port(template) is None  # the empty template section
    assert references.parse_port(REQUIREMENTS) is None
    assert references.parse_port(REQUIREMENTS + "\n## Ported from\nNone.\n\n## Next\nx\n") is None
    # an empty `Reference:` line never swallows the next line
    assert references.parse_port("## Ported from\nReference:\nCommit: abcdef12\n") is None


def test_named_and_used_references(demo_repo: DemoRepo) -> None:
    r = demo_repo
    make_otto(r)
    cfg = load_config(r.root)
    assert references.used(cfg, "demo") == []  # declared, but the spec names none
    use(r, REFERENCES_SECTION.replace("| `otto` |", "| otto |\n| ghost | x | y |\n| None. |"))
    assert references.named(cfg, "demo") == ["otto", "ghost"]
    assert references.used(cfg, "demo") == ["otto"]
    assert spec_lint.problems(cfg, "demo") == [
        "requirements.md: reference 'ghost' is not declared in framegate.toml [references] "
        "(declared: otto, core)"
    ]
    # a port's reference counts without a row of its own; a port needs a file pair
    use(r, "\n## Ported from\nReference: otto\n")
    assert references.used(cfg, "demo") == ["otto"]
    assert "no source -> target row" in spec_lint.problems(cfg, "demo")[0]
    assert r.af("approve-spec", "demo") == 1


# ---------- doctor, spec-brief, the write guard ----------
def test_doctor_and_spec_brief_list_references(demo_repo: DemoRepo) -> None:
    r = demo_repo
    otto = make_otto(r)
    cfg = load_config(r.root)
    lines: list[str] = []
    doctor.run(cfg, echo=lines.append)
    text = "\n".join(lines)
    assert "[OK] reference 'otto'" in text and f"at {head(otto)[:10]}" in text
    assert "[WARN] reference 'core'" in text and "is not on this machine" in text
    brief = spec_brief(cfg, "demo")
    assert f"otto: {otto.as_posix()} at {head(otto)[:10]} (the project this one ports)" in brief
    assert "core: " in brief and "(not on this machine)" in brief
    assert "'Ported from'" in brief and "only those are given to the agents" in brief
    # without [references] the brief says nothing about them
    (r.root / "framegate.toml").write_text(
        (r.root / "framegate.toml").read_text(encoding="utf-8").split("[references]")[0],
        encoding="utf-8",
    )
    assert "References" not in spec_brief(load_config(r.root), "demo")


def test_write_guard_blocks_reference_paths(demo_repo: DemoRepo) -> None:
    r = demo_repo
    otto = make_otto(r)
    (r.root / ".phase").write_text("IMPLEMENT", encoding="utf-8")
    for tool, key in (("Write", "file_path"), ("Edit", "file_path")):
        data = {
            "tool_name": tool,
            "cwd": str(r.root),
            "tool_input": {key: str(otto / "demo" / "__init__.py")},
        }
        code, message = protect_paths.evaluate(json.dumps(data))
        assert code == 2 and "outside the repository" in message
    data["tool_input"] = {"file_path": "../da otto/new.py"}
    assert protect_paths.evaluate(json.dumps(data))[0] == 2


def test_deny_rule_forms() -> None:
    assert references._deny_rule(pathlib.PureWindowsPath("C:/Users/a b/otto")) == (
        "Edit(//c/Users/a b/otto/**)"
    )
    assert references._deny_rule(pathlib.PurePosixPath("/home/x/otto")) == (
        "Edit(//home/x/otto/**)"
    )


# ---------- a run with a reference ----------
def test_agents_get_the_feature_references(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """approve-spec pins the used reference; every agent run gets it as --add-dir with edits
    denied and named in its orientation; a new commit there is a warning, not a stop."""
    r = demo_repo
    r.add_toml(FAST)
    otto = make_otto(r)
    use(r, REFERENCES_SECTION)
    pinned = head(otto)
    assert r.af("approve-spec", "demo") == 0
    out = capsys.readouterr().out
    assert f"- otto: {otto.as_posix()} at {pinned[:10]}" in out
    pin = r.progress()["references"]
    assert [(p["name"], p["commit"], p["dirty"], p["missing"]) for p in pin] == [
        ("otto", pinned, False, False)
    ]
    assert [e["references"] for e in r.events() if e["kind"] == "references_pinned"] == [pin]

    assert r.af("run", "demo") == EXIT_GATE
    assert not [e for e in r.events() if e["kind"] == "reference_changed"]
    for call in r.calls():
        argv = call["argv"]
        at = argv.index("--add-dir")
        assert argv[at + 1 : at + 4] == [
            str(otto),
            "--disallowedTools",
            references._deny_rule(otto),
        ]
        assert "da-core" not in " ".join(argv)  # declared, not used by this spec
        assert "- References (other projects, READ-ONLY" in call["prompt"]
        assert (
            f"    otto: {otto.as_posix()} (the project this one ports); pinned at {pinned[:10]}"
            in call["prompt"]
        )
        assert "This feature is a PORT" not in call["prompt"]

    # the reference gets a new commit: the next run says so and goes on
    (otto / "demo" / "more.py").write_text("X = 1\n", encoding="utf-8")
    git(otto, "add", "-A")
    git(otto, "commit", "-q", "-m", "more")
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    moved = f"reference 'otto' moved since the spec approval: {pinned[:10]} -> {head(otto)[:10]}"
    assert [e["warnings"] for e in r.events() if e["kind"] == "reference_changed"] == [[moved]]
    log = (r.root / "out" / "demo" / "progress.log").read_text(encoding="utf-8")
    assert f"WARNING: {moved}" in log
    assert r.progress()["waiting"]["gate"] == "final"
    # no port: no fidelity report
    assert "Port fidelity" not in (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")


def test_pin_warnings_dirty_missing_unpinned(demo_repo: DemoRepo) -> None:
    r = demo_repo
    otto = make_otto(r)
    use(r, REFERENCES_SECTION)
    cfg = load_config(r.root)
    pins = references.pin(cfg, "demo")
    assert references.pin_warnings(cfg, "demo", pins) == []
    assert "was not pinned" in references.pin_warnings(cfg, "demo", None)[0]
    (otto / "scratch.txt").write_text("x", encoding="utf-8")
    assert references.pin_warnings(cfg, "demo", pins) == [
        f"reference 'otto' has uncommitted changes (pinned at {head(otto)[:10]})"
    ]
    assert references.pin(cfg, "demo")[0]["dirty"] is True
    otto.rename(otto.with_name("moved away"))
    assert "is not on this machine" in references.pin_warnings(cfg, "demo", pins)[0]
    assert references.agent_args(cfg, "demo") == []
    assert "not on this machine" in "\n".join(references.orientation_lines(cfg, "demo", pins))


# ---------- a port ----------
def test_port_tasks_and_fidelity_report(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """A filled "Ported from": the test writer, test reviewer, implementer and reviewer get
    the port text; the final gates report each target file against its reference file."""
    r = demo_repo
    r.add_toml(FAST)
    otto = make_otto(r)
    use(r, PORT_SECTION.format(commit="0000000aaa"))
    assert r.af("approve-spec", "demo") == 0
    out = capsys.readouterr().out
    assert "names commit 0000000aaa" in out and "written against another state" in out
    # after the pin: the report reads the pinned commit, not this later edit
    (otto / "demo" / "__init__.py").write_text(OTTO_CODE + "\nLATER = 1\n", encoding="utf-8")
    git(otto, "commit", "-q", "-am", "later")

    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    prompts: dict[str, str] = {}
    for call in r.calls():
        prompts.setdefault(call["phase"], call["prompt"])
    place = f"reference 'otto' at {otto.as_posix()}"
    for phase in ("TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW"):
        assert (
            f"This feature is a PORT (specs/demo/requirements.md, 'Ported from') from {place}"
            in prompts[phase]
        ), phase
        assert "What may differ from the reference: the docstring of add()" in prompts[phase]
    assert (
        "Carry over the reference's tests, do not write your own: tests/test_demo.py -> "
        "tests/test_demo.py." in prompts["TESTS"]
    )
    assert "## New tests (not in the reference)" in prompts["TESTS"]
    assert "rewritten or weakened" in prompts["TEST_REVIEW"]
    assert "Copy, do not rewrite: demo/__init__.py -> src/demo/__init__.py" in prompts["IMPLEMENT"]
    assert "comments and docstrings word for word" in prompts["IMPLEMENT"]
    assert "`## Structure findings`" in prompts["REVIEW"]
    assert "This feature is a PORT" not in prompts["PR"]

    gates = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert "Port fidelity against reference 'otto' (not blocking" in gates
    code_line = next(x for x in gates.splitlines() if x.startswith("- [code] demo/__init__.py"))
    # the fake implementer wrote its own add(): the report shows how far it is from otto's
    assert (
        "-> src/demo/__init__.py: " in code_line and f"reference at {pinned(r)[:10]}" in code_line
    )
    assert " of 8 lines" in code_line and "identical" not in code_line
    assert "diff: out/demo/fidelity/src__demo____init__.py.diff" in code_line
    assert "- [tests] tests/test_demo.py -> tests/test_demo.py: " in gates
    assert "- [code] demo/gone.py -> src/demo/gone.py: not found in the reference" in gates
    diff = (r.root / "out/demo/fidelity/src__demo____init__.py.diff").read_text(encoding="utf-8")
    assert diff.startswith(f"--- otto/demo/__init__.py (at {pinned(r)[:10]})\n+++ src/demo/")
    assert '-    """Add two integers the otto way."""' in diff and "LATER" not in diff
    last = [e for e in r.events() if e["kind"] == "final_gates"][-1]
    assert last["passed"] and last["fidelity"]["reference"] == "otto"
    assert r.af("gate-summary", "demo") == 0
    summary = capsys.readouterr().out
    assert "port fidelity against 'otto' (not blocking): 3 file(s), 0 identical; " in summary
    assert "; 1 not compared (per file: final_gates.md; diffs: fidelity/)" in summary


def pinned(r: DemoRepo) -> str:
    return str(r.progress()["references"][0]["commit"])


def test_fidelity_counts_identical_and_skipped(demo_repo: DemoRepo) -> None:
    r = demo_repo
    otto = make_otto(r)
    use(r, PORT_SECTION.format(commit=head(otto)[:10]))
    cfg = load_config(r.root)
    assert references.fidelity(load_config(r.root), "other", None) == {}
    (r.root / "src/demo/__init__.py").write_text(OTTO_CODE, encoding="utf-8")
    (r.root / "tests/test_demo.py").write_text(
        OTTO_TESTS.replace("== 5", "== 5  # same").replace("from demo", "import pytest\nfrom demo")
        + "\n\ndef test_new() -> None:\n    pass\n",
        encoding="utf-8",
    )
    rep = references.fidelity(cfg, "demo", references.pin(cfg, "demo"))
    code, tests, gone = rep["files"]
    assert (code["changed"], code["added"], code["removed"], code["lines"]) == (0, 0, 0, 8)
    assert "diff" not in code
    assert (tests["changed"], tests["added"], tests["removed"]) == (1, 5, 0)
    assert gone["note"] == "not found in the reference"
    lines = references.fidelity_lines(rep)
    assert "identical (8 lines, reference at " in lines[1]
    assert "1 changed, 5 added, 0 removed of 5 lines" in lines[2]
    assert "3 file(s), 1 identical; 1 changed, 5 added, 0 removed line(s); 1 not compared" in (
        references.fidelity_summary(rep)
    )
    # a target that does not exist yet; a reference that is not a git repository
    (r.root / "tests/test_demo.py").unlink()
    rep = references.fidelity(cfg, "demo", None)
    assert rep["files"][1]["note"] == "target file missing"
    assert rep["files"][0]["origin"] == "as it is now"
    # the reference is not on this machine
    otto.rename(otto.with_name("moved away"))
    rep = references.fidelity(cfg, "demo", None)
    assert references.fidelity_lines(rep) == [
        "Port fidelity (not blocking): SKIPPED, reference 'otto' is not on this machine "
        f"({otto.as_posix()})"
    ]
    assert references.fidelity_summary(rep).startswith("port fidelity (not blocking): SKIPPED")


# ---------- a port keeps the reference's file layout; its later test runs ----------
def test_port_targets_are_not_asked_about_their_size(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """With a limit of 3 lines the stub file is over it. A normal feature is told so in the
    implementer task, gets GREW at the step gate and a question at the final gate; a port's
    target file gets none of the three, and the structure report still lists its size."""
    from framegate.adapters import get_adapter
    from framegate.checks import structure

    shape = structure.Structure(
        [structure.FileSize("src/a.py", 900), structure.FileSize("src/b.py", 500, before=450)],
        limit=400,
        ported=frozenset({"src/a.py"}),
    )
    assert [f.path for f in shape.grown] == ["src/b.py"]
    assert shape.step_lines() == [
        "Structure (not blocking; changed files over 400 lines):",
        "  src/b.py: 450 -> 500  GREW: put new code in its own file",
    ]
    assert (
        "  src/a.py: 900 lines (new)  OVER 400 (ported: the reference's file layout is kept)"
        in shape.lines()
    )
    assert shape.as_dict()["ported"] == ["src/a.py"]

    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("gates", "max_file_lines", "3")
    make_otto(r)
    cfg = load_config(r.root)
    target = "src/demo/__init__.py"
    assert references.port_targets(cfg, "demo") == set()
    assert structure.oversized(get_adapter(cfg))[0][0][0] == target
    use(r, PORT_SECTION.format(commit="0000000aaa"))
    assert references.port_targets(cfg, "demo") == {
        target,
        "tests/test_demo.py",
        "src/demo/gone.py",
    }
    assert structure.oversized(get_adapter(cfg), skip={target}) == ([], 0)

    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    task = next(c["prompt"] for c in r.calls() if c["phase"] == "IMPLEMENT")
    assert "Source files already over" not in task
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert gates and all(not e.get("grown") for e in gates)
    assert "GREW" not in (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    final = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert f"  {target}: " in final
    assert "OVER 3 (ported: the reference's file layout is kept)" in final
    last = [e for e in r.events() if e["kind"] == "final_gates"][-1]["structure"]
    assert last["grown"] == [] and last["ported"] == [target] and last["over"] == [target]
    oq = r.root / "out/demo/open_questions.md"
    assert not oq.exists() or "Split it" not in oq.read_text(encoding="utf-8")
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    assert "Split it into one file per concept" not in capsys.readouterr().out


def test_port_notes_for_the_later_test_runs(demo_repo: DemoRepo) -> None:
    """Answers at the final gate (TESTS_AMEND) and strengthening after weak final gates
    (TESTS_STRENGTHEN) stay with the reference's tests too."""
    from framegate.orchestrator import Orchestrator

    r = demo_repo
    make_otto(r)
    orch = Orchestrator(load_config(r.root))
    plain = orch.task_text("TESTS_STRENGTHEN", "demo", failed="diff coverage")
    assert "This feature is a PORT" not in plain
    use(r, PORT_SECTION.format(commit="0000000aaa"))
    amend = orch.task_text("TESTS_AMEND", "demo", ref="ANS-1")
    strengthen = orch.task_text("TESTS_STRENGTHEN", "demo", failed="diff coverage")
    carried = "carried over (tests/test_demo.py -> tests/test_demo.py)"
    for task in (amend, strengthen):
        assert "This feature is a PORT (specs/demo/requirements.md, 'Ported from')" in task
        assert f"The tests are the reference's tests, {carried}" in task
        assert "## New tests (not in the reference)" in task
    assert "the human answered questions at the final gate" in amend
    assert "carry over the reference test that covers it if one exists" in amend
    assert "Never rewrite, rename or drop a carried-over test" in amend
    assert "found the tests too weak" in strengthen
    assert "only where no reference test exists for it" in strengthen
    assert "first look in the reference's test files".lower() in strengthen.lower()
