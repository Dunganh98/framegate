"""A tree that is already red before the task, and parked work that cannot end differently.

Seen in three of three replay runs (real commits of three repositories, replayed on the code
before them): tests, lint or format failed on the untouched tree, every step gate blamed the
feature, and an approved fix that nobody could apply was redone on the same inputs.

- Lint, format, typecheck and imports findings count only in files the task changed
  (gates.lint_checks, red_tree.py).
- [gates] regression_baseline (on for a bug fix, or where framegate.toml sets it):
  `approve-spec` records the tests that fail, the gates leave them out (gates.py).
- Formatting debt in a file the task touches is fixed with the work and counted for the
  final summary.
- The frame check counts a suppression by its text, not by its line (frame_diff.py).
- `Fix owner: config`: framegate applies an approved framegate.toml change itself, unless it
  weakens a check (config_change.py); a redo whose gate inputs did not change is not run
  (redo.py).
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import DemoRepo, git, marker
from framegate import config_change, gates, red_tree, redo
from framegate.adapters import get_adapter
from framegate.adapters.base import CheckResult, lines_beyond
from framegate.checks import frame_diff
from framegate.config import load_config, parse_config
from framegate.orchestrator import EXIT_GATE
from framegate.report import final_facts
from framegate.specs import baseline_on

#: a "linter" for the demo repository: one line per source or test line that holds BAD
LINT = """\
import pathlib
import sys

found = 0
for folder in ("src", "tests", "legacy"):
    for path in sorted(pathlib.Path(folder).rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "BAD" in line:
                print(f"{path.as_posix()}:{n}: X001 the word is not allowed")
                found += 1
print(f"Found {found} error(s).")
sys.exit(1 if found else 0)
"""
COMMANDS = '[gates.commands]\nlint = ["python", "tools/lint.py"]\nformat = []\ntypecheck = []\n'
RED_TEST = "def test_red_before_the_task() -> None:\n    assert 0\n"


def phases(r: DemoRepo) -> list[str]:
    return [c["phase"] for c in r.calls()]


# ---------- lint / format / typecheck: only the files the task changed count ----------
def test_a_finding_counts_only_in_a_changed_file(demo_repo: DemoRepo) -> None:
    r = demo_repo
    for name in ("old", "new"):
        (r.root / "src" / "demo" / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    adapter = get_adapter(load_config(r.root))
    changed = {"src/demo/new.py"}
    old = "F401 unused import\n --> src/demo/old.py:1:8\n  |\n1 | import os\n"
    res = red_tree.in_changed(adapter, changed, CheckResult("FAIL", old))
    assert res.status == "PASS" and "left out" in res.summary
    new = "src/demo/new.py:3: X001 c"
    res = red_tree.in_changed(adapter, changed, CheckResult("FAIL", f"{old}\n{new}\nFound 2."))
    assert res.status == "FAIL" and new in res.output
    assert "old.py" not in res.output and "F401" not in res.output
    crash = CheckResult("FAIL", "the tool crashed")  # names no file: stays a failure
    assert red_tree.in_changed(adapter, changed, crash) is crash


def test_findings_from_before_the_task_do_not_fail_its_gates(demo_repo: DemoRepo) -> None:
    """In every mode, without a record (seen live: every step parked, nothing built)."""
    r = demo_repo
    (r.root / "tools").mkdir()
    (r.root / "tools" / "lint.py").write_text(LINT, encoding="utf-8")
    (r.root / "legacy").mkdir()
    (r.root / "legacy" / "old.py").write_text("A = 1  # BAD\n", encoding="utf-8")
    r.add_toml(COMMANDS)
    r.defer_tests_gate()
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["completed_steps"] == [1] and not r.progress()["parked"]
    # a finding in a file the task changes fails, and only that file is shown
    (r.root / "src" / "demo" / "extra.py").write_text("D = 4  # BAD\n", encoding="utf-8")
    adapter = get_adapter(load_config(r.root))
    (lint,) = [c for c in gates.checks(adapter, "demo", "step", True) if c.name == "lint"]
    res = lint.run()
    assert res.status == "FAIL" and "src/demo/extra.py:1: X001" in res.output
    assert "legacy/old.py" not in res.output


def test_the_tests_of_the_task_are_never_on_the_list_of_old_failures(demo_repo: DemoRepo) -> None:
    """A record taken after the task's tests were written (the setting was turned on late):
    they are red by design and must not be left out of the regression check later."""
    r = demo_repo
    (r.root / "tests" / "test_legacy.py").write_text(RED_TEST, encoding="utf-8")
    (r.root / "tests" / "test_demo.py").write_text(
        'import pytest\n\nfrom demo import add\n\n\n@pytest.mark.req("DEMO-R-001")\n'
        "def test_add_returns_sum() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    r.set_toml("gates", "regression_baseline", "true")
    adapter = get_adapter(load_config(r.root))
    count, problem = gates.record_regression_baseline(adapter, "demo")
    assert (count, problem) == (1, "")
    path = r.root / "out" / "demo" / gates.BASELINE_FILE
    (name,) = json.loads(path.read_text(encoding="utf-8"))["failing"]
    assert name.endswith("test_red_before_the_task")


def test_the_record_is_on_for_a_bug_fix_only_unless_the_config_says(demo_repo: DemoRepo) -> None:
    """A feature and a refactor start on a green tree (setup sees to it); a bug fix may
    start on a red one. A setting in framegate.toml wins."""
    r = demo_repo
    cfg = load_config(r.root)
    assert parse_config(r.root, {}).regression_baseline is None  # the default: by mode
    cfg.regression_baseline = None
    assert not baseline_on(cfg, "demo")
    req = cfg.spec_dir("demo") / "requirements.md"
    req.write_text("Mode: bugfix\n" + req.read_text(encoding="utf-8"), encoding="utf-8")
    assert baseline_on(cfg, "demo")
    cfg.regression_baseline = False
    assert not baseline_on(cfg, "demo")
    req.write_text(req.read_text(encoding="utf-8").replace("bugfix", "refactor", 1), "utf-8")
    cfg.regression_baseline = None
    assert not baseline_on(cfg, "demo")
    cfg.regression_baseline = True
    assert baseline_on(cfg, "demo")


def test_formatting_debt_in_a_touched_file_is_fixed_with_the_work_and_counted(
    demo_repo: DemoRepo,
) -> None:
    """A file that was not formatted before the task is formatted whole when the task touches
    it, without a question; the lines the task did not change are counted for the summary."""
    r = demo_repo
    src = r.root / "src" / "demo" / "__init__.py"
    src.write_text(src.read_text(encoding="utf-8") + "\n\nOLD = [1,\n       2]\n", "utf-8")
    git(r.root, "add", "src")
    git(r.root, "commit", "-qm", "debt from before the task")
    assert r.af("approve-spec", "demo") == 0
    adapter = get_adapter(load_config(r.root))
    src.write_text(src.read_text(encoding="utf-8") + "\n\nNEW = [3,\n       4]\n", "utf-8")
    assert "formatted 1 changed file(s)" in adapter.format_fix("spec-approved/demo")
    text = src.read_text(encoding="utf-8")
    assert "OLD = [1, 2]" in text and "NEW = [3, 4]" in text
    assert adapter.format_beyond == {"src/demo/__init__.py": 2}  # the two OLD lines
    assert adapter.format_fix("spec-approved/demo") and adapter.format_beyond == {}

    assert lines_beyond("a\nb =  1\nc\n", "a\nb = 1\nc\n", {2}) == 0
    assert lines_beyond("a\nb =  1\nc\n", "a\nb = 1\nc\n", {3}) == 1
    events = [{"kind": "format_debt", "files": {"src/a.py": 2}}] * 2
    facts = final_facts("demo", [], {}, events, [], "", "")
    assert "## Formatted beyond the task's lines: 1 file(s)" in facts
    assert "- src/a.py: 4 line(s)" in facts
    assert "Formatted beyond" not in final_facts("demo", [], {}, [], [], "", "")


# ---------- the frame check: a suppression counts by its text ----------
def test_a_rewrapped_suppression_is_not_a_new_one() -> None:
    rx = re.compile(r"noqa|type:\s*ignore")
    moved = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -3 +3,2 @@\n"
        "-    except Exception as exc:  # noqa: BLE001 the caller logs it\n"
        "+    except (\n+        Exception\n+    ) as exc:  # noqa: BLE001\n"
    )
    assert frame_diff.new_suppressions(moved, rx) == []
    added = moved + "+x = f()  # noqa: E501\n"
    assert frame_diff.new_suppressions(added, rx) == ["x = f()  # noqa: E501"]
    # removed in one file, added in another: the other file has a new one
    two = moved + (
        "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n"
        "-y = g()\n+y = g()  # type: ignore\n"
    )
    assert frame_diff.new_suppressions(two, rx) == ["y = g()  # type: ignore"]


# ---------- a framegate.toml change during a task ----------
@pytest.mark.parametrize(
    ("key", "old", "new", "weakens"),
    [
        ("gates.regression_timeout", 3600, 7200, False),
        ("agents.max_attempts", 3, 5, False),
        ("gates.diff_coverage", 80, 90, False),
        ("gates.diff_coverage", None, 80, True),  # the default is 90
        ("gates.mutation_tool", None, "none", True),
        ("gates.regression_baseline", None, True, True),
        ("gates.commands.lint", ["ruff", "check"], ["ruff", "check", "--exclude", "x"], True),
        ("gates.commands.lint", ["ruff", "check"], ["python", "-m", "ruff", "check"], False),
        ("gates.commands.typecheck", None, [], True),  # switched off
        ("gates.unrunnable_tests", [], [{"test": "t", "reason": "r"}], True),
        ("gates.unrunnable_tests", [{"test": "t", "reason": "r"}], [], False),
        ("paths.tests", "tests", "tests/unit", True),  # on no list: weakening
    ],
)
def test_which_config_changes_weaken_a_check(
    key: str, old: object, new: object, weakens: bool
) -> None:
    assert config_change.weakens(key, old, new) is weakens


# ---------- parked work that no implementer run can finish ----------
def test_only_a_bare_approval_depends_on_the_fix_being_applied() -> None:
    assert redo.only_approves("")
    assert redo.only_approves("ok")
    assert redo.only_approves("Approved: apply the proposed fix as written.")
    assert redo.only_approves("Default: approve the proposed fix")
    assert not redo.only_approves("ok, redo anyway: I installed the package")
    assert not redo.only_approves("No: the implementer must read the file in chunks.")
    assert not redo.only_approves("Approved.\nAnd keep the old name as an alias.")


def to_parked_config_fix(r: DemoRepo, **scenario: object) -> None:
    r.add_toml(COMMANDS.replace('["python", "tools/lint.py"]', "[]"))
    r.defer_tests_gate()
    r.set_toml("agents", "diagnose_after", "2")
    r.scenario(implement_fail=2, diagnosis="config", spec_update="none", **scenario)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    (p,) = r.progress()["parked"]
    assert p["reason"] == "diagnosis: the fix needs a config change"
    assert p["owner"] == "config" and p["inputs"]
    assert "framegate changes framegate.toml as proposed" in p["question"]


def approve_the_fix(
    r: DemoRepo, answer: str = "Approved: apply the proposed fix as written."
) -> None:
    (r.root / "out/demo/answers.md").write_text(f"1. Q: step 1 - add\n   A: {answer}\n", "utf-8")
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE


def test_an_approved_fix_that_changed_nothing_is_not_redone(demo_repo: DemoRepo) -> None:
    """The approved fix was a framegate.toml change and nobody made it: the step is not
    redone (seen live: a full cycle of failed attempts and a second diagnosis, three times).
    It parks again at once and says so; "redo anyway" runs it."""
    r = demo_repo
    to_parked_config_fix(r)
    before = len(r.calls())
    approve_the_fix(r)
    assert phases(r)[before:] == ["SPEC_UPDATE", "TESTS"]  # no implementer run, no diagnosis
    (p,) = r.progress()["parked"]
    assert p["reason"] == "not redone: nothing changed since it parked"
    assert p["question"].startswith("Not redone: the fix was approved, but nothing it names")
    assert "Parked for: Diagnosis (out/demo/diagnoses/step-1-add.md)" in p["question"]
    assert p["owner"] == "config" and p["attempts"] == 0
    assert "framegate.toml is unchanged" in " ".join(r.progress()["spec_update_notes"])
    last = [e for e in r.events() if e["kind"] == "redo_result"][-1]
    assert last["passed"] is False and last["unchanged"] is True

    # a second bare approval changes nothing either; the question is not nested
    before = len(r.calls())
    approve_the_fix(r, "ok")
    assert "IMPLEMENT" not in phases(r)[before:]
    (p,) = r.progress()["parked"]
    assert p["question"].count("Not redone:") == 1

    before = len(r.calls())
    approve_the_fix(r, "redo anyway, I changed the environment")
    assert "IMPLEMENT" in phases(r)[before:]
    assert r.progress()["completed_steps"] == [1]


def test_an_approved_config_fix_is_applied_by_framegate(demo_repo: DemoRepo) -> None:
    """`Fix owner: config`, approved: the spec updater proposes the new framegate.toml in the
    out folder, framegate checks it, puts it in place (committed, tagged) and redoes the step
    with it; the frame check accepts the file as long as it is exactly that."""
    r = demo_repo
    to_parked_config_fix(r, config_update="\n[update]\ncheck = false\n")
    before = len(r.calls())
    approve_the_fix(r)
    update = next(c for c in r.calls()[before:] if c["phase"] == "SPEC_UPDATE")
    assert "write the whole new file to out/demo/config_update.toml" in update["prompt"]
    toml = (r.root / "framegate.toml").read_text(encoding="utf-8")
    assert toml.endswith("[update]\ncheck = false\n")
    assert marker(r.root, "config-update/demo")
    assert not (r.root / "out/demo/config_update.toml").exists()
    assert "framegate.toml updated from answers ANS-1" in git(r.root, "log", "--format=%s")
    assert git(r.root, "status", "--porcelain", "--", "framegate.toml") == ""
    (ev,) = [e for e in r.events() if e["kind"] == "config_update"]
    assert ev["applied"] is True and ev["changes"] == ["update.check: not set -> false"]
    facts = final_facts("demo", [], {}, r.events(), [], "", "")
    assert "## framegate.toml changed during the task" in facts
    assert "- update.check: not set -> false" in facts
    # the step was redone and is green: the frame check accepted the changed config
    assert "IMPLEMENT" in phases(r)[before:]
    assert r.progress()["completed_steps"] == [1] and not r.progress()["parked"]
    adapter = get_adapter(load_config(r.root))
    assert frame_diff.check(adapter, "demo").status == "PASS"
    # any further edit of the file is a frame violation again
    (r.root / "framegate.toml").write_text(toml + "# by hand\n", encoding="utf-8")
    res = frame_diff.check(adapter, "demo")
    assert res.status == "FAIL" and "PROTECTED PATH CHANGED: framegate.toml" in res.output


@pytest.mark.parametrize(
    ("proposal", "why"),
    [
        ("bad", "was not applied, the new file is not a valid config"),
        (
            "\n[frame]\nforbidden_patterns = []\n",
            "was not applied, it weakens a check (frame.forbidden_patterns: not set -> []). "
            "A task never does that: it belongs to the setup (/framegate:setup)",
        ),
    ],
    ids=["invalid", "weakening"],
)
def test_a_config_that_does_not_load_or_weakens_a_check_is_not_applied(
    demo_repo: DemoRepo, proposal: str, why: str
) -> None:
    r = demo_repo
    to_parked_config_fix(r, config_update=proposal)
    toml = (r.root / "framegate.toml").read_text(encoding="utf-8")
    before = len(r.calls())
    approve_the_fix(r)
    assert (r.root / "framegate.toml").read_text(encoding="utf-8") == toml
    assert not marker(r.root, "config-update/demo")
    notes = " ".join(r.progress()["spec_update_notes"])
    assert why in notes
    assert "IMPLEMENT" not in phases(r)[before:]  # nothing changed: not redone
    (p,) = r.progress()["parked"]
    assert p["reason"] == "not redone: nothing changed since it parked"
