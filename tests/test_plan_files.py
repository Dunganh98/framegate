"""The files a plan step names (checks/plan_files.py): a documentation file becomes writable
for the implementer in that step, anything else it cannot write fails the spec check.

Seen live (a replay pilot): a step said "update docs/xyz.md", the write guard blocked the
implementer and the step parked."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import PLAN, PLAN2, DemoRepo, git
from framegate.adapters import get_adapter
from framegate.checks import plan_files
from framegate.config import load_config
from framegate.hooks import protect_paths
from framegate.orchestrator import EXIT_GATE

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
DOCS_STEP2 = PLAN2 + "Describe neg() in `docs/guide.md` and add a line to CHANGELOG.md.\n"


def write_plan(r: DemoRepo, text: str) -> None:
    (r.root / "specs/demo/plan.md").write_text(text, encoding="utf-8")


def classify(r: DemoRepo, text: str) -> tuple[dict[int, list[str]], list[str]]:
    return plan_files.classify(get_adapter(load_config(r.root)), text)


# ---------- the rule ----------
@pytest.mark.parametrize(
    ("rel", "doc"),
    [
        ("docs/guide.md", True),
        ("docs/api/usage.rst", True),
        ("doc/notes.rst", True),
        ("README.txt", True),
        ("doc/notes.txt", False),  # text files there are too often something else
        ("packages/core/docs/intro.md", True),
        ("README.md", True),
        ("CHANGELOG.md", True),
        ("packages/core/README.rst", True),
        ("docs/guide.html", False),  # not markdown / text
        ("docs/conf.py", False),
        ("notes.md", False),  # no docs folder, no README / CHANGELOG
        ("tests/README.md", False),
        ("tests/docs/cases.md", False),
        ("specs/demo/docs/more.md", False),
        ("out/demo/docs/x.md", False),
        ("CLAUDE.md", False),
        ("docs/CLAUDE.md", False),
        ("docs/AGENTS.md", False),
        (".claude/docs/x.md", False),
        (".github/README.md", False),
        (".framegate/docs/x.md", False),
        ("docs/code-map.md", False),  # the summary step writes it
        ("docs/requirements.txt", False),  # a dependency list
        ("docs/Dockerfile.md", False),  # a deploy / CI name
        ("docs/locked/x.md", False),  # [protected] extra
    ],
)
def test_what_counts_as_a_documentation_file(demo_repo: DemoRepo, rel: str, doc: bool) -> None:
    r = demo_repo
    toml = r.root / "framegate.toml"
    text = toml.read_text(encoding="utf-8")
    assert "[protected]\n" in text
    text = text.replace("[protected]\n", '[protected]\nextra = ["docs/locked/"]\n')
    toml.write_text(text, encoding="utf-8")
    assert plan_files.is_doc_file(get_adapter(load_config(r.root)), rel) is doc


def test_the_files_a_step_names() -> None:
    body = (
        "## Step 1: x\nCovers: A-R-001\n"
        "Update `docs/guide.md`, .\\docs\\win.md and README.md. See https://x.org/a/b.md, "
        "e.g. version 1.2 of Parser.parse; ../outside/docs/x.md is not ours.\n"
        "Follow tests/conftest.py (read only) and `docs/style.md` (read-only: the rules).\n"
    )
    names = plan_files.named_files(body)
    assert names == ["docs/guide.md", "docs/win.md", "README.md", "e.g", "Parser.parse"]


def test_a_section_after_the_steps_is_not_part_of_the_last_step() -> None:
    text = PLAN + "\n## To do by hand\n- scripts/release.py: bump the version (not a source)\n"
    bodies = plan_files.step_bodies(text)
    assert list(bodies) == [1] and "release.py" not in bodies[1]
    hidden = PLAN + "<!-- was: update docs/old.md -->\n"
    assert "docs/old.md" not in plan_files.step_bodies(hidden)[1]


def test_docs_become_writable_and_the_rest_is_a_finding(demo_repo: DemoRepo) -> None:
    r = demo_repo
    (r.root / "tests/conftest.py").write_text("", encoding="utf-8")
    (r.root / "scripts").mkdir()
    plan = (
        PLAN + "Write src/demo/helpers.py. Update docs/guide.md and README.md.\n"
        "Add a case to tests/test_demo.py, a job to .github/workflows/ci.yml, a script "
        "scripts/build.py and a dependency to pyproject.toml. Update docs/code-map.md.\n"
        "As design.md and specs/demo/design.md say, e.g. like Parser.parse in v1.2; "
        "the fixtures in tests/conftest.py (read only) stay.\n"
    )
    docs, problems = classify(r, plan)
    assert docs == {1: ["docs/guide.md", "README.md"]}
    named = [p.split("names ")[1].split(",")[0] for p in problems]
    assert named == [
        "tests/test_demo.py",
        ".github/workflows/ci.yml",
        "scripts/build.py",
        "pyproject.toml",
        "docs/code-map.md",
    ]
    assert all(p.startswith("plan.md, Step 1: ") and "(read only)" in p for p in problems)
    assert "the test writer writes the tests" in problems[0]
    assert '"Pipeline follow-up"' in problems[1]
    assert '"## To do by hand"' in problems[2]
    assert "in the spec phase" in problems[3]


def test_a_readme_without_its_file_name_is_a_finding(demo_repo: DemoRepo) -> None:
    docs, problems = classify(demo_repo, PLAN + "Update the README and the CHANGELOG.md.\n")
    assert docs == {1: ["CHANGELOG.md"]}
    assert len(problems) == 1 and "names README without its file name" in problems[0]
    assert classify(demo_repo, PLAN + "As the README (read only) says.\n") == ({}, [])


def test_the_demo_plan_names_nothing(demo_repo: DemoRepo) -> None:
    assert classify(demo_repo, PLAN2) == ({}, [])


# ---------- spec-check and the approval ----------
def test_spec_check_lists_the_files_and_the_approval_refuses(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.two_steps()
    write_plan(r, DOCS_STEP2 + "Add a test to tests/test_demo.py.\n")
    capsys.readouterr()
    assert r.af("spec-check", "demo") == 1
    out = capsys.readouterr().out
    assert "- plan.md, Step 2: names tests/test_demo.py, which the implementer cannot" in out
    assert (
        "Documentation files the plan makes writable (frozen with the spec): "
        "step 2: docs/guide.md, CHANGELOG.md" in out
    )
    assert r.af("approve-spec", "demo") == 1
    write_plan(r, DOCS_STEP2)
    assert r.af("spec-check", "demo") == 0
    assert "Spec check: OK" in capsys.readouterr().out
    assert r.af("approve-spec", "demo") == 0


def test_the_approval_freezes_the_list(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.two_steps()
    write_plan(r, DOCS_STEP2)
    adapter = get_adapter(load_config(r.root))
    assert plan_files.approved_docs(adapter, "demo", [1, 2]) == []  # not approved yet
    assert r.af("approve-spec", "demo") == 0
    assert plan_files.approved_docs(adapter, "demo", [1]) == []
    assert plan_files.approved_docs(adapter, "demo", [2]) == ["docs/guide.md", "CHANGELOG.md"]
    # a later edit of plan.md (a spec update, a stray write) makes nothing new writable
    write_plan(r, DOCS_STEP2 + "Also docs/more.md.\n")
    git(r.root, "commit", "-q", "-am", "plan changed after the approval")
    assert plan_files.planned_docs(adapter, "demo")[2][-1] == "docs/more.md"
    assert plan_files.approved_docs(adapter, "demo", [2]) == ["docs/guide.md", "CHANGELOG.md"]


# ---------- what the chat is told ----------
ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["feature", "bugfix", "refactor"])
def test_the_skills_ask_before_a_spec_from_the_name_alone(name: str) -> None:
    """Seen live (3 of 3 pilot runs): started with a name only, the chat wrote the spec from
    the name. It asks first, in the dialogue's first paragraph, and says how plan.md names
    files."""
    skill = (ROOT / f"plugin/skills/{name}/SKILL.md").read_text(encoding="utf-8")
    part1 = skill[skill.index("## Part 1:") :]
    assert part1.index("First: do you know") < part1.index("\n1. ")
    first = " ".join(part1.split("\n\n")[1].split())  # the paragraph before step 1
    assert first.startswith("First: do you know what is wanted?")
    for words in (
        "gave only the name `$ARGUMENTS`",
        "ask for it in one short question",
        "wait for the answer",
        "write no spec file",
        "go on without asking",
    ):
        assert words in first, (name, words)
    rest = " ".join(part1.split())
    assert "Files in a plan step:" in rest and '"## To do by hand"' in rest
    assert '"Documentation files the plan makes writable"' in rest


def test_the_spec_brief_and_the_templates_say_it_too(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert demo_repo.af("spec-brief", "demo") == 0
    out = capsys.readouterr().out
    assert "Only a name so far: ask for it in one short question" in out
    assert "A step names only files its implementer can write: src/" in out
    for mode in ("_template", "_template_bugfix", "_template_refactor"):
        plan = ROOT / "src/framegate/templates/specs" / mode / "plan.md"
        text = plan.read_text(encoding="utf-8")
        assert "Files a step names: only what its implementer can write." in text, mode
        assert '"## To do by hand"' in text and '"(read only)"' in text, mode
        # the template itself names no file: its own spec check is clean
        assert classify(demo_repo, text) == ({}, [])


# ---------- the write guard ----------
def guard(root: pathlib.Path, rel: str) -> int:
    data = {"cwd": str(root), "tool_name": "Write", "tool_input": {"file_path": str(root / rel)}}
    return protect_paths.evaluate(json.dumps(data))[0]


def test_the_write_guard_allows_the_steps_docs_only(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    (r.root / ".phase").write_text("IMPLEMENT", encoding="utf-8")
    monkeypatch.setenv("FRAMEGATE_AGENT", "implement")
    assert guard(r.root, "docs/guide.md") == 2  # no list: as before
    listed = ["docs/guide.md", "tests/README.md", "CLAUDE.md", ".github/README.md", "x/a.py"]
    monkeypatch.setenv("FRAMEGATE_STEP_DOCS", json.dumps(listed))
    assert guard(r.root, "docs/guide.md") == 0
    assert guard(r.root, "src/demo/a.py") == 0
    assert guard(r.root, "docs/other.md") == 2  # not named by the step
    assert guard(r.root, "README.md") == 2
    # a list that holds more than documentation files opens nothing: the rule is checked again
    for rel in listed[1:]:
        assert guard(r.root, rel) == 2, rel
    # only the implementer's phase, and only an agent of the run
    for phase in ("REVIEW", "DIAGNOSIS", "TESTS", "SUMMARY"):
        (r.root / ".phase").write_text(phase, encoding="utf-8")
        assert guard(r.root, "docs/guide.md") == 2, phase
    (r.root / ".phase").write_text("IMPLEMENT", encoding="utf-8")
    monkeypatch.delenv("FRAMEGATE_AGENT")
    assert guard(r.root, "docs/guide.md") == 2
    monkeypatch.setenv("FRAMEGATE_AGENT", "implement")
    monkeypatch.setenv("FRAMEGATE_STEP_DOCS", "not json")
    assert guard(r.root, "docs/guide.md") == 2


# ---------- the run ----------
def test_a_step_writes_its_docs_and_they_are_committed(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.defer_tests_gate()
    write_plan(r, DOCS_STEP2)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and r.progress()["waiting"]["gate"] == "final"
    assert r.progress()["completed_steps"] == [1, 2] and not r.progress()["parked"]
    runs = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert [c["step_docs"] for c in runs] == [None, '["docs/guide.md", "CHANGELOG.md"]']
    assert "Documentation files of this step" not in runs[0]["prompt"]
    assert (
        "you may write these, and no other file outside the source folders): "
        "docs/guide.md, CHANGELOG.md." in runs[1]["prompt"]
    )
    # no other agent of the run got the list
    assert {c["step_docs"] for c in r.calls() if c["phase"] != "IMPLEMENT"} == {None}
    # committed with the step's code, and the frame check passed with them
    sha = git(r.root, "log", "--format=%H", "--grep", "demo: step 2 - neg").split()[0]
    changed = git(r.root, "show", "--name-only", "--format=", sha).split()
    assert {"docs/guide.md", "CHANGELOG.md", "src/demo/__init__.py"} <= set(changed)
    assert not git(r.root, "status", "--porcelain", "--", "docs", "CHANGELOG.md").strip()
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert gates and all(e["passed"] for e in gates)


def test_a_parked_step_takes_its_docs_back(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.defer_tests_gate()
    (r.root / "CHANGELOG.md").write_text("# Changes\n", encoding="utf-8")
    git(r.root, "add", "CHANGELOG.md")
    git(r.root, "commit", "-q", "-m", "changelog")
    write_plan(r, DOCS_STEP2)
    r.scenario(park_steps=[2])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and r.progress()["waiting"]["gate"] == "final"
    parked = r.progress()["parked"]
    assert [p["label"] for p in parked] == ["step 2 - neg"]
    # the new file is gone, the changed one is as committed; both are in the kept diff
    assert not (r.root / "docs/guide.md").exists()
    assert (r.root / "CHANGELOG.md").read_text(encoding="utf-8") == "# Changes\n"
    diff = (r.root / parked[0]["diff"]).read_text(encoding="utf-8")
    assert "docs/guide.md" in diff and "CHANGELOG.md" in diff
