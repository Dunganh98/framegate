"""The pull request's description (description.py): written by framegate itself from the
briefs of the run, kept with the spec, short enough for every host. No git, no agent here;
how `publish` and the pipeline task use it: tests/test_publish.py, tests/test_pipeline.py."""

from __future__ import annotations

import pathlib

from conftest import PLAN, PLAN2, REQUIREMENTS
from framegate import __version__, description, hosts
from framegate.config import Config, parse_config
from framegate.state import log_event

SPEC_BRIEF = """\
**Task** — add() sums two whole numbers and refuses anything else.
It is the first building block of the demo package.

**Plan**
1. Build add() with its type check (src/demo/__init__.py)

**How it is proven**
- 2 + 3 gives 5
- a text argument is refused with "ints only"

**Not proven**
- how it behaves with very large numbers

**Decisions I made for you**
- True and False count as numbers: rules out a separate bool check.

**Questions**
None.
"""
FINAL_BRIEF = """\
## Steps
1. Build add() with its type check: as planned, 1 failed attempt: the check came after the
   sum, moved before it (src/demo/__init__.py)

## Struggles
- None.

## Decisions I made
- [2] bool is refused too: the spec says ints only (out/demo/steps.md)
- The error text is "ints only" everywhere

## Proven
- 2 + 3 gives 5
- a text argument is refused with "ints only" (see out/demo/test_summary.md)

## Not proven
- None.
"""
LIST = """\
# Pipeline follow-up: demo

- [x] .github/workflows/ci.yml: run tests/test_demo.py (CI does not select it) (reviewer)
- [ ] Dockerfile: COPY the demo folder (the image lacks it) (from the spec)
- [ ] compose.yml: mount the folder (not needed: the image holds it)
- [ ] OUTSIDE [CI variable group demo-dev]: add DEMO_TOKEN (the new job reads it)
"""
EXPECTED = f"""\
## What

add() sums two whole numbers and refuses anything else.

It is the first building block of the demo package.

## To do by hand outside the repository: 1

- [ ] CI variable group demo-dev: add DEMO_TOKEN (the new job reads it)

## Changes

1. Build add() with its type check: as planned, 1 failed attempt: the check came after the \
sum, moved before it (src/demo/__init__.py)

## How it is proven

- 2 + 3 gives 5
- a text argument is refused with "ints only"
Checks: all final checks passed; diff coverage PASS: 100% of 6 changed lines; mutation PASS: \
9 of 9 killed

## Decisions

- bool is refused too: the spec says ints only
- The error text is "ints only" everywhere

## Deploy / CI changes (the second commit of this pull request)

- .github/workflows/ci.yml: run tests/test_demo.py (CI does not select it)
- not done yet, a separate step after this pull request: Dockerfile: COPY the demo folder \
(the image lacks it)

Built with framegate {__version__}
"""


def write(root: pathlib.Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def project(root: pathlib.Path, requirements: str = REQUIREMENTS, plan: str = PLAN) -> Config:
    cfg = parse_config(root, {})
    write(root, "specs/demo/requirements.md", requirements)
    write(root, "specs/demo/plan.md", plan)
    return cfg


def run_files(root: pathlib.Path, cfg: Config) -> None:
    write(root, "out/demo/spec_brief.md", SPEC_BRIEF)
    write(root, "out/demo/final_brief.md", FINAL_BRIEF)
    write(root, "out/demo/final_summary.md", "# Final summary\n\n## Summary\nInternal.\n")
    write(root, "specs/demo/pipeline_followup.md", LIST)
    log_event(
        cfg,
        "demo",
        "final_gates",
        passed=True,
        failed=[],
        results={
            "diff coverage": "PASS: 100% of 6 changed lines",
            "mutation": "PASS: 9 of 9 killed",
            "regression": "PASS: 40 passed",
            "structure": "SKIPPED: off",
        },
    )
    log_event(cfg, "demo", "agent_run", cost=12.5, seconds=600)


def test_the_description_of_a_finished_task(tmp_path: pathlib.Path) -> None:
    cfg = project(tmp_path)
    run_files(tmp_path, cfg)
    written = description.refresh(cfg, "demo")
    assert (written.rel, written.changed, written.note) == (
        "specs/demo/pr_description.md",
        True,
        "",
    )
    text = (tmp_path / written.rel).read_text(encoding="utf-8")
    assert text == EXPECTED
    # for a reviewer: no cost figure, no path into the out folder, no gate-item number
    assert "$" not in text and "12.5" not in text and "out/" not in text and "[2]" not in text
    assert "Struggles" not in text and "Questions" not in text and "regression" not in text
    assert not description.refresh(cfg, "demo").changed  # again: the same text


def test_it_survives_the_cleanup_and_follows_the_list(tmp_path: pathlib.Path) -> None:
    cfg = project(tmp_path)
    run_files(tmp_path, cfg)
    description.refresh(cfg, "demo")
    for name in ("spec_brief.md", "final_brief.md", "final_summary.md"):
        (tmp_path / "out/demo" / name).unlink()  # framegate cleanup demo
    assert not description.has_run_files(cfg, "demo")
    again = description.refresh(cfg, "demo")
    assert not again.changed and not again.note  # nothing lost, nothing to say
    # the pipeline task did the second item, the user did the outside one: only those two
    # sections change, the rest keeps the text of the run
    listed = tmp_path / "specs/demo/pipeline_followup.md"
    text = listed.read_text(encoding="utf-8")
    listed.write_text(text.replace("- [ ] Dockerfile", "- [x] Dockerfile"), encoding="utf-8")
    text = listed.read_text(encoding="utf-8")
    listed.write_text(text.replace("- [ ] OUTSIDE", "- [x] OUTSIDE"), encoding="utf-8")
    assert description.refresh(cfg, "demo").changed
    text = (tmp_path / "specs/demo/pr_description.md").read_text(encoding="utf-8")
    assert "To do by hand" not in text and "not done yet" not in text
    assert "- Dockerfile: COPY the demo folder (the image lacks it)\n" in text
    assert "1. Build add() with its type check: as planned, 1 failed attempt" in text
    assert "Checks: all final checks passed" in text
    assert text.endswith(f"Built with framegate {__version__}\n")
    # the deploy / CI change went out as a pull request of its own: the title says so
    text, _ = description.compose(cfg, "demo", own=True)
    assert "## Deploy / CI changes (a pull request of their own)\n" in text


def test_a_task_finished_before_there_was_a_description(tmp_path: pathlib.Path) -> None:
    """No description, the run files gone: built from the spec, and said in one line."""
    requirements = REQUIREMENTS.replace(
        "## Requirements",
        "## Context\n<!-- why -->\nThe demo package has no way\nto add numbers (plan §2).\n\n"
        "## Goals\n- add two whole numbers\n\n## Requirements",
    )
    cfg = project(tmp_path, requirements, PLAN2)
    write(tmp_path, "specs/demo/pipeline_followup.md", LIST)
    written = description.refresh(cfg, "demo")
    assert written.note == (
        "The description is short: the run files of demo are gone (cleaned up before it was "
        "written), so it is built from the spec."
    )
    text = (tmp_path / written.rel).read_text(encoding="utf-8")
    # a wrapped paragraph of the spec is one paragraph here (seen live: one per line, and cut
    # in the middle of a sentence)
    what = "## What\n\nThe demo package has no way to add numbers (plan §2).\n\nadd two whole"
    assert what in text
    long = "A sentence of the task. " * 40
    assert description._sentences(long).endswith("task.")
    assert len(description._sentences(long)) <= description.WHAT_MAX
    assert "## Changes\n\n1. add\n2. neg\n" in text
    assert "## To do by hand outside the repository: 1\n" in text
    assert "## How it is proven" not in text and "why" not in text
    assert description.refresh(cfg, "demo").note == ""  # said once: the file is there now


def test_bugfix_and_refactor_say_what_in_their_own_words(tmp_path: pathlib.Path) -> None:
    for mode, brief, wanted in (
        (
            "bugfix",
            "**Bug** — add(2, 3) gives -1\n**Right result** — 5, as the docs say\n**Plan**\n1. x\n",
            "## What\n\n**Bug:** add(2, 3) gives -1\n\n**Right result:** 5, as the docs say\n",
        ),
        (
            "refactor",
            "**Goal**\nOne file per concept.\n\n**What moves**\n- add() to its own file\n",
            "## What\n\n**Goal:** One file per concept.\n\n**What moves:** add() to its own file\n",
        ),
    ):
        root = tmp_path / mode
        cfg = project(
            root, REQUIREMENTS.replace("Feature code: DEMO", f"Mode: {mode}\nFeature code: DEMO")
        )
        write(root, "out/demo/spec_brief.md", brief)
        text, note = description.compose(cfg, "demo")
        assert wanted in text and not note, mode


def test_too_long_for_the_host_it_gets_shorter_by_whole_lines(tmp_path: pathlib.Path) -> None:
    cfg = project(tmp_path)
    run_files(tmp_path, cfg)
    many = "".join(f"- decision {i}: " + "words " * 60 + "\n" for i in range(30))
    steps = "".join(f"{i}. step {i}: as planned (src/demo/f{i}.py)\n" for i in range(1, 16))
    write(
        tmp_path,
        "out/demo/final_brief.md",
        f"## Steps\n{steps}\n## Decisions I made\n{many}\n## Proven\n{many}\n## Not proven\n{many}",
    )
    text, _ = description.compose(cfg, "demo")
    assert len(text) <= hosts.AZURE_DESCRIPTION_MAX == description.LIMIT
    assert text.rstrip().endswith(f"Built with framegate {__version__} (shortened)")
    assert text.count("(shortened)") == 1
    # every step and the whole checklist are kept; the numbers line too
    assert all(f"{i}. step {i}: as planned" in text for i in range(1, 16))
    assert "- [ ] CI variable group demo-dev: add DEMO_TOKEN" in text
    assert "Checks: all final checks passed" in text
    # no section stops in the middle of a line: a clipped line ends with "..."
    for line in text.splitlines():
        assert len(line) <= description.LINE_MAX
    assert 0 < text.count("- decision ") < 90
    # a text that fits says nothing
    assert "(shortened)" not in EXPECTED


def test_the_text_of_a_pipeline_change_of_its_own(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    write(tmp_path, "specs/ci/pipeline_followup.md", LIST.replace("demo", "ci", 1))
    written = description.refresh_pipeline(cfg, "ci")
    assert written.rel == "specs/ci/pipeline_pr_description.md"
    text = (tmp_path / written.rel).read_text(encoding="utf-8")
    assert text.startswith("## To do by hand outside the repository: 1\n\n- [ ] CI variable")
    assert "## Deploy / CI changes\n\n- .github/workflows/ci.yml: run tests/test_demo.py" in text
    # the brief the user approved replaces the list; too long, it is cut at a line end
    write(tmp_path, "out/ci/pipeline_brief.md", "**What changed**\n" + "- a line of it\n" * 600)
    text = description.compose_pipeline(cfg, "ci")
    assert len(text) <= description.LIMIT and text.startswith("## To do by hand")
    assert text.endswith(f"- a line of it\n\nBuilt with framegate {__version__} (shortened)\n")
