"""The documentation files a plan step names are writable for the implementer in that step."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import PLAN2, DemoRepo, git
from framegate.adapters import get_adapter
from framegate.checks import plan_files
from framegate.config import load_config
from framegate.hooks import protect_paths
from framegate.orchestrator import EXIT_GATE

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
DOCS_STEP2 = PLAN2 + "Describe neg() in `docs/guide.md` and add a line to CHANGELOG.md.\n"


def write_plan(r: DemoRepo, text: str) -> None:
    (r.root / "specs/demo/plan.md").write_text(text, encoding="utf-8")


# ---------- the rule ----------
DOCS = ("docs/guide.md", "doc/notes.md", "packages/core/docs/intro.md", "README.md", "CHANGELOG.md")
NOT_DOCS = (
    "docs/usage.rst",  # Markdown only
    "docs/conf.py",
    "notes.md",  # no docs folder, no README / CHANGELOG
    "tests/README.md",
    "tests/docs/cases.md",
    "specs/demo/docs/more.md",
    "out/demo/docs/x.md",
    "CLAUDE.md",
    "docs/CLAUDE.md",
    ".claude/docs/x.md",
    ".github/README.md",
    "docs/code-map.md",  # the summary step writes it
    "docs/Dockerfile.md",  # a deploy / CI name
    "docs/locked/x.md",  # [protected] extra
)


def test_what_counts_as_a_documentation_file(demo_repo: DemoRepo) -> None:
    r = demo_repo
    toml = r.root / "framegate.toml"
    text = toml.read_text(encoding="utf-8")
    assert "[protected]\n" in text
    text = text.replace("[protected]\n", '[protected]\nextra = ["docs/locked/"]\n')
    toml.write_text(text, encoding="utf-8")
    adapter = get_adapter(load_config(r.root))
    assert [rel for rel in DOCS + NOT_DOCS if plan_files.is_doc_file(adapter, rel)] == list(DOCS)


def test_only_the_docs_a_step_names_become_writable_and_the_approval_freezes_them(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.two_steps()
    write_plan(
        r,
        DOCS_STEP2 + "Read tests/test_demo.py, pyproject.toml and .github/workflows/ci.yml; "
        "see https://x.org/docs/a.md and ../other/docs/b.md.\n## Notes\nNot a step: docs/no.md\n",
    )
    capsys.readouterr()
    assert r.af("spec-check", "demo") == 0
    assert (
        "Documentation files the plan makes writable (frozen with the spec): "
        "step 2: docs/guide.md, CHANGELOG.md\n" in capsys.readouterr().out
    )
    adapter = get_adapter(load_config(r.root))
    assert plan_files.approved_docs(adapter, "demo", [1, 2]) == []  # not approved yet
    assert r.af("approve-spec", "demo") == 0
    assert plan_files.approved_docs(adapter, "demo", [1]) == []
    # a later edit of plan.md (a spec update, a stray write) makes nothing new writable
    write_plan(r, DOCS_STEP2 + "Also docs/more.md.\n")
    git(r.root, "commit", "-q", "-am", "plan changed after the approval")
    assert plan_files.approved_docs(adapter, "demo", [2]) == ["docs/guide.md", "CHANGELOG.md"]


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
