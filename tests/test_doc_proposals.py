"""No agent of the loop writes a documentation file: the spec and the agents propose, the
owner answers the important ones at the final gate, one docs step applies them afterwards."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from conftest import REQUIREMENTS, DemoRepo, git
from framegate import answered, doc_proposals
from framegate.config import load_config
from framegate.doc_proposals import Proposal
from framegate.hooks import protect_paths
from framegate.orchestrator import EXIT_GATE
from framegate.scaffold import package_text

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
README = "README.md: add() takes two ints"
CHANGELOG = "CHANGELOG.md: add() refuses bools"
DESIGN = "docs/design.md, Calc: today: 'any number' -> proposed: 'ints only' (built)"
PLAN = "docs/plan.md, Step 2: today: a method -> proposed: a function (it failed)"
CONTRA = "docs/guide.md, Input: today: 'floats are added' -> code now: add() refuses floats"
CONTRA_BODY = (
    'docs/guide.md, Input: the doc says: "floats are added". The code now does: add() refuses '
    "floats."
)
MINOR = "Doc updates that will be applied after approval"

DOCS = (
    "docs/guide.md",
    "doc/notes.rst",
    "packages/core/docs/intro.md",
    "README.md",
    "CHANGELOG.md",
    "HANDOVER-2026.md",
)
NOT_DOCS = (
    "docs/conf.py",
    "notes.md",
    "tests/README.md",
    "specs/demo/docs/more.md",
    "out/demo/docs/x.md",
    "CLAUDE.md",
    "docs/CLAUDE.md",
    ".claude/docs/x.md",
    ".github/README.md",
    "docs/code-map.md",  # the summary step writes it
)


def test_the_two_classes_of_documentation_file(demo_repo: DemoRepo) -> None:
    cfg = load_config(demo_repo.root)
    assert [rel for rel in DOCS + NOT_DOCS if doc_proposals.is_doc(cfg, rel)] == list(DOCS)
    assert [rel for rel in DOCS if doc_proposals.is_important(rel)] == list(DOCS[:3])
    assert [Proposal(x, "run").important for x in (README, CHANGELOG, DESIGN, PLAN)] == [
        False,
        False,
        True,
        True,
    ]
    assert Proposal("the design should mention bools", "run").important  # no file: the owner's
    assert doc_proposals.asked(f"[doc update] {DESIGN}{doc_proposals.ASK}") == DESIGN
    assert doc_proposals.asked("[structure report] x.py has 500 lines") == ""


def guard(root: pathlib.Path, rel: str) -> tuple[int, str]:
    data = {"cwd": str(root), "tool_name": "Write", "tool_input": {"file_path": str(root / rel)}}
    return protect_paths.evaluate(json.dumps(data))


def test_the_write_guard_allows_a_doc_in_the_docs_phase_only(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    monkeypatch.setenv("FRAMEGATE_AGENT", "implement")
    for phase in ("IMPLEMENT", "REVIEW", "TESTS", "SUMMARY", "RETRO"):
        (r.root / ".phase").write_text(phase, encoding="utf-8")
        code, said = guard(r.root, "docs/guide.md")
        assert code == 2 and said.startswith(f"[{phase}] Blocked write to docs/guide.md: docs")
        assert "Write the proposal to out/<feature>/doc_proposals.md" in said, phase
        assert "do not park" in said and doc_proposals.IMPORTANT_FORMAT in said
        code, said = guard(r.root, "README.md")
        assert code == 2 and doc_proposals.MINOR_FORMAT in said and "today:" not in said
        assert guard(r.root, "out/demo/doc_proposals.md")[0] == 0, phase
    (r.root / ".phase").write_text("SUMMARY", encoding="utf-8")
    assert guard(r.root, "docs/code-map.md")[0] == 0  # framegate's own file
    (r.root / ".phase").write_text("IMPLEMENT", encoding="utf-8")
    assert "-> code now: " in guard(r.root, "docs/guide.md")[1]
    assert guard(r.root, "src/demo/a.py")[0] == 0
    code, said = guard(r.root, "notes.md")  # no doc: the usual answer
    assert code == 2 and "doc_proposals" not in said and "park.md" in said
    (r.root / ".phase").write_text("SPEC", encoding="utf-8")
    code, said = guard(r.root, "README.md")
    assert code == 2 and "under `## Proposed doc updates` of the spec's requirements.md" in said
    # the docs step: documentation files and the out folder, nothing else
    (r.root / ".phase").write_text("DOCS", encoding="utf-8")
    for rel in ("docs/guide.md", "README.md", "CHANGELOG.md", "out/demo/docs_step.md"):
        assert guard(r.root, rel)[0] == 0, rel
    for rel in ("src/demo/a.py", "tests/test_demo.py", "notes.md", "specs/demo/plan.md"):
        assert guard(r.root, rel)[0] == 2, rel


def test_the_spec_section_and_the_run_file_make_one_list(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    assert doc_proposals.proposals(cfg, "demo") == []  # a spec without the section
    assert doc_proposals.brief_lines(cfg, "demo") == doc_proposals.facts_lines(cfg, "demo") == []
    assert doc_proposals.keep(cfg, "demo", [], []) == ""
    spec = r.root / "specs/demo/requirements.md"
    spec.write_text(
        REQUIREMENTS + f"\n## Proposed doc updates\n<!-- - x: y -->\n- {README}\n\n## Next\n- no\n",
        encoding="utf-8",
    )
    (r.root / "out/demo").mkdir(parents=True, exist_ok=True)
    (r.root / "out/demo/doc_proposals.md").write_text(
        f"- {DESIGN}\n- {README}\n* {PLAN.replace(' (', chr(10) + '  (')}\nNone.\n",
        encoding="utf-8",
    )
    found = doc_proposals.proposals(cfg, "demo")
    assert found == [Proposal(README, "spec"), Proposal(DESIGN, "run"), Proposal(PLAN, "run")]
    assert doc_proposals.brief_lines(cfg, "demo") == [
        f"{MINOR}: 1; nothing to answer",
        f"  - {README} (spec)",
    ]
    assert doc_proposals.to_apply(cfg, "demo", [PLAN]) == [found[0], found[2]]
    assert doc_proposals.keep(cfg, "demo", [README], [PLAN], "the docs agent failed")
    assert (
        (r.root / "specs/demo/doc_proposals.md")
        .read_text(encoding="utf-8")
        .endswith(
            f"- [x] {README} (spec): applied\n"
            f"- [ ] {DESIGN} (run): not applied (later)\n"
            f"- [ ] {PLAN} (run): not applied (the docs agent failed)\n"
        )
    )


def test_the_same_update_twice_is_one_and_framegates_own_files_are_none(
    demo_repo: DemoRepo,
) -> None:
    """Seen live: one RUNTIME_DESIGN.md change asked twice (spec and run), and the code map
    the summary step had already updated asked about."""
    r = demo_repo
    cfg = load_config(r.root)
    spec = r.root / "specs/demo/requirements.md"
    spec.write_text(REQUIREMENTS + f"\n## Proposed doc updates\n- {DESIGN}\n", encoding="utf-8")
    (r.root / "out/demo").mkdir(parents=True, exist_ok=True)
    (r.root / "out/demo/doc_proposals.md").write_text(
        '- docs/design.md, section Calc: today: any number -> proposed: "ints only" (spec)\n'
        "- docs/code-map.md, demo: today: - -> proposed: add() (new)\n"
        "- specs/demo/design.md: add() takes ints\n",
        encoding="utf-8",
    )
    assert doc_proposals.proposals(cfg, "demo") == [Proposal(DESIGN, "spec")]


def test_one_question_per_file_and_place(demo_repo: DemoRepo) -> None:
    """Seen live twice: one doc place asked twice at one gate, from the spec and from the
    run's `code now:` note, worded differently."""
    r = demo_repo
    cfg = load_config(r.root)
    plain = "docs/guide.md, Input: today: 'floats are added' -> proposed: ints only (the task)"
    contra = "docs/guide.md:12, section input: today: floats -> code now: add() refuses floats"
    other = "docs/guide.md, Output: today: x -> proposed: y"
    spec = r.root / "specs/demo/requirements.md"
    spec.write_text(REQUIREMENTS + f"\n## Proposed doc updates\n- {plain}\n", encoding="utf-8")
    (r.root / "out/demo").mkdir(parents=True, exist_ok=True)
    (r.root / "out/demo/doc_proposals.md").write_text(f"- {contra}\n- {other}\n", "utf-8")
    found = doc_proposals.proposals(cfg, "demo")
    assert found == [Proposal(contra, "run"), Proposal(other, "run")]
    asked = [
        answered.key(f"[doc update] {Proposal(x, 'spec').question()}") for x in (plain, contra)
    ]
    assert asked[0] == asked[1] == "doc docs/guide.md @ input"
    assert doc_proposals.key("docs/guide.md, line 40 Input: today: a") == asked[0][4:]


def test_a_doc_the_code_contradicts_asks_about_the_code() -> None:
    p = Proposal(CONTRA, "run")
    assert p.contradicts and not Proposal(DESIGN, "run").contradicts
    assert not Proposal("README.md: today: x -> code now: y", "run").contradicts  # minor
    assert p.question() == CONTRA_BODY + doc_proposals.CONTRA_ASK
    assert doc_proposals.asked(f"[doc update] {p.question()}") == p.body == CONTRA_BODY
    assert doc_proposals.key(CONTRA) == doc_proposals.key(CONTRA_BODY)
    q = p.question()
    verdicts = {
        "A": "approved",
        "approve the code; the docs step updates the doc after the task": "approved",
        "approve the code; leave the doc as it is (later)": "later",
        "Default: B": "later",
        "C: add() adds floats too": "rejected",
        "Same as item 2: reject, keep the doc": "rejected",
    }
    assert {a: doc_proposals.verdict(q, a) for a in verdicts} == verdicts
    plain = Proposal(DESIGN, "run").question()
    ordinary = {"A": "approved", "approve": "approved", "B": "later", "C": "later", "x": "later"}
    assert {a: doc_proposals.verdict(plain, a) for a in ordinary} == ordinary


def test_every_template_has_the_section_and_the_agents_are_told() -> None:
    for name in ("_template", "_template_bugfix", "_template_refactor"):
        text = package_text("templates", f"specs/{name}/requirements.md")
        assert "## Proposed doc updates\n" in text and "today: <what it says now" in text, name
        assert doc_proposals._bullets(doc_proposals._spec_section(text)) == [], name
        assert "becomes writable" not in package_text("templates", f"specs/{name}/plan.md")
    for role in ("implement", "review"):
        assert "{out}/{feature}/doc_proposals.md" in package_text("roles", f"{role}.md"), role
    assert "never park because a doc" in package_text("roles", "implement.md")
    for role in ("tests", "review", "test_review"):
        assert "wording of a doc, a docstring or a comment" in package_text("roles", f"{role}.md")
    assert "match the whole message for one input" in package_text("roles", "tests.md")
    assert "`[doc update]`" in package_text("roles", "summary.md")
    assert "Apply exactly those" in package_text("roles", "docs.md")


def to_final_gate(r: DemoRepo, extra: str = "", **scenario: Any) -> None:
    r.add_toml(FAST)
    spec = r.root / "specs/demo/requirements.md"
    section = f"\n## Proposed doc updates\n- {README}\n" + (f"- {extra}\n" if extra else "")
    spec.write_text(REQUIREMENTS + section, encoding="utf-8")
    r.scenario(doc_proposals=True, **scenario)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final" and not r.progress().get("parked")


def docs_commit(r: DemoRepo) -> list[str]:
    log = git(r.root, "log", "--format=%H", "--grep", "^demo: docs$").split()
    return sorted(git(r.root, "show", "--name-only", "--format=", log[0]).split()) if log else []


def kept(r: DemoRepo) -> str:
    return (r.root / "specs/demo/doc_proposals.md").read_text(encoding="utf-8")


def test_important_proposals_are_gate_questions_and_the_docs_step_applies_the_approved(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    to_final_gate(r)
    assert not [c for c in r.calls() if c["phase"] == "DOCS"]
    asked = (r.root / "out/demo/open_questions.md").read_text(encoding="utf-8")
    later = " A: apply it after the task (the docs step). B: later (default).\n"
    assert asked == f"- [doc update] {DESIGN}{later}- [doc update] {PLAN}{later}"
    facts = (r.root / "out/demo/final_facts.md").read_text(encoding="utf-8")
    assert "## Gate items (numbered as the final gate shows them): 2" in facts
    listed = (
        f"## Proposed doc updates: 4\n\n{MINOR}: 2\n- {README} (spec)\n- {CHANGELOG} (run)\n\n"
        f"Yours to decide at the final gate (default: later): 2\n- {DESIGN} (run)\n- {PLAN} (run)\n"
    )
    assert listed in facts
    assert listed in (r.root / "out/demo/final_summary.md").read_text(encoding="utf-8")
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    brief = capsys.readouterr().out
    assert f"{MINOR}: 2; nothing to answer\n  - {README} (spec)\n  - {CHANGELOG} (run)\n" in brief
    assert f"1. [doc update] {DESIGN}" in brief and f"2. [doc update] {PLAN}" in brief
    assert not (r.root / "README.md").exists() and not (r.root / "docs").exists()

    # the same letters as the contradiction question: a bare "A" applies, a bare "B" does not
    (r.root / "out/demo/answers.md").write_text("1. A\n2. B\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.progress()["doc_approved"] == [DESIGN]
    # doc answers start no run: the gate is approved, the docs step applies them
    assert r.progress()["phase"] == "RETRO"
    assert not (r.root / "out/demo/open_questions.md").exists()
    assert r.af("run", "demo") == 0
    assert [c["phase"] for c in r.calls()][-2:] == ["DOCS", "RETRO"]
    task = next(c for c in r.calls() if c["phase"] == "DOCS")["prompt"]
    assert f"change nothing else:\n- {README}\n- {CHANGELOG}\n- {DESIGN}\nWhere" in task
    # one commit of its own with the three files; the proposal answered "later" is untouched
    assert docs_commit(r) == ["CHANGELOG.md", "README.md", "docs/design.md"]
    assert (r.root / "docs/design.md").read_text(encoding="utf-8") == DESIGN + "\n"
    assert not (r.root / "docs/plan.md").exists()
    assert not git(r.root, "status", "--porcelain", "--", "docs", "README.md", "CHANGELOG.md")
    assert kept(r).endswith(
        f"- [x] {README} (spec): applied\n- [x] {CHANGELOG} (run): applied\n"
        f"- [x] {DESIGN} (run): applied\n- [ ] {PLAN} (run): not applied (later)\n"
    )
    (ev,) = [e for e in r.events() if e["kind"] == "docs_step"]
    assert ev["applied"] == [README, CHANGELOG, DESIGN] and ev["why"] == ""
    later = "- D1 [docs] docs/plan.md: Step 2: today: a method -> proposed: a function (it failed)."
    assert later in (r.root / ".framegate/deferred.md").read_text(encoding="utf-8")


def test_without_an_answer_the_default_is_later_and_only_the_minor_ones_are_applied(
    demo_repo: DemoRepo,
) -> None:
    r = demo_repo
    to_final_gate(r)
    assert r.af("finish", "demo", "--no-retro") == 0
    assert docs_commit(r) == ["CHANGELOG.md", "README.md"]
    assert not (r.root / "docs").exists()
    assert f"- [ ] {DESIGN} (run): not applied (later)\n" in kept(r)
    assert f"- [x] {CHANGELOG} (run): applied\n" in kept(r)
    assert r.progress()["phase"] == "DONE" and not (r.root / ".phase").exists()


@pytest.mark.parametrize(
    ("scenario", "why"),
    [
        ({"docs_bad": True}, "the docs agent changed src/demo/__init__.py"),
        ({"max_turns": {"DOCS": 1}}, "the docs agent failed"),
    ],
)
def test_a_docs_step_that_fails_or_leaves_the_docs_is_taken_back_and_blocks_nothing(
    demo_repo: DemoRepo, scenario: dict[str, Any], why: str
) -> None:
    r = demo_repo
    to_final_gate(r, **scenario)
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    assert r.progress()["phase"] == "DONE" and r.progress()["docs_step"]["why"] == why
    assert docs_commit(r) == []
    assert not (r.root / "README.md").exists() and not (r.root / "CHANGELOG.md").exists()
    assert not git(r.root, "status", "--porcelain", "--", "src").strip()
    assert f"- [ ] {README} (spec): not applied ({why})\n" in kept(r)
    assert f"- [ ] {DESIGN} (run): not applied (later)\n" in kept(r)


def test_rejecting_the_code_a_doc_contradicts_starts_the_answer_run(demo_repo: DemoRepo) -> None:
    r = demo_repo
    to_final_gate(r, extra=CONTRA)
    asked = (r.root / "out/demo/open_questions.md").read_text(encoding="utf-8")
    assert asked.startswith(f"- [doc update] {CONTRA_BODY}{doc_proposals.CONTRA_ASK}\n")
    (r.root / "out/demo/answers.md").write_text(
        "1. C: add() adds floats too\n2. later\n3. later\n", encoding="utf-8"
    )
    assert r.af("answer", "demo") == 0
    prog = r.progress()
    assert prog["phase"] == "SPEC_UPDATE" and prog["doc_rejected"] == [CONTRA_BODY]
    assert not prog.get("quiet_answers") and prog["doc_approved"] == []
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final"
    assert not (r.root / "out/demo/open_questions.md").exists()  # answered: not asked again
    assert r.af("finish", "demo", "--no-retro") == 0
    assert f"- [ ] {CONTRA} (spec): not applied (you rejected the code)\n" in kept(r)
