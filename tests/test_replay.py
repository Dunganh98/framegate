"""`framegate replay`: the candidates file, the sandbox, the score, the scorecard, the caps,
the scheduler.
No test calls claude or the network."""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from framegate import cli
from framegate.replay import chat, guard, run, sandbox, score
from framegate.replay import limit as limits
from framegate.replay.candidates import Candidate, CandidatesError, parse
from framegate.replay.sandbox import Layout, ReplayError, build, read_json, write_json

EXAMPLE = """\
# Candidates

## lock-free-at-once
- repo: ../da-otto
- commit: fd83df7
- kind: bugfix
- tests: tests/test_controller.py, tests/conftest.py
- install: uv pip install --python {py} -r requirements.txt
- install: uv pip install --python {py} deltalake
- probes: tests/test_pin.py
- extra: tests/test_late.py
- max-usd: 10

### Task
A released lock must be free at once.

```
## not a candidate
### Facts
```

### Facts
- F1 [lease time, expiry]: The lease time stays 60 s,
  also for a released lock.
- F2 [other holders]: A lock another run holds is never released.

## contract-key
- repo: https://example.org/da-core.git
- commit: a0b0d3eed2a62c66a44dbabe5d1d006f4422be1b
- kind: feature
"""


def test_candidates_file_is_parsed() -> None:
    first, second = parse(EXAMPLE)

    assert (first.name, first.repo, first.commit, first.kind) == (
        "lock-free-at-once",
        "../da-otto",
        "fd83df7",
        "bugfix",
    )
    assert first.tests == ["tests/test_controller.py", "tests/conftest.py"]
    assert len(first.install) == 2 and first.max_usd == 10 and first.max_hours is None
    assert first.task.startswith("A released lock") and first.task.endswith("### Facts\n```")
    assert (first.probes, first.extra) == (["tests/test_pin.py"], ["tests/test_late.py"])
    assert [f.line() for f in first.facts] == [
        "- F1 [lease time, expiry]: The lease time stays 60 s, also for a released lock.",
        "- F2 [other holders]: A lock another run holds is never released.",
    ]
    assert (second.name, second.task, second.facts, second.probes) == ("contract-key", "", [], [])
    assert second.extra == []


def test_candidates_file_errors_name_the_line() -> None:
    bad = (
        "## one\n- repo: r\n- comit: abc1234\n- kind: fix\nstray\n"
        "## Two Words\n- repo: r\n\n"
        "## one\n- repo: r\n- commit: abc1234\n- kind: feature\n"
        "## facts\n- repo: r\n- commit: abc1234\n- kind: feature\n"
        "### Facts\n- F1 [a]: one\n- F1 [b]: two\n- no id\n"
    )

    with pytest.raises(CandidatesError) as e:
        parse(bad)

    assert str(e.value).splitlines() == [
        "line 3: unknown field 'comit' (known: repo, commit, kind, tests, pythonpath, "
        "probes, extra, python, install, setup, max-usd, max-hours)",
        "line 5: expected `- field: value`, `### Task` or `### Facts`, got 'stray'",
        "line 1: candidate 'one' has no `- commit:`",
        "line 4: kind 'fix' is not one of feature, bugfix, refactor",
        "line 6: 'Two Words' is not a candidate name (a-z, 0-9, '-'); "
        "a heading inside a task needs ### or deeper",
        "line 9: candidate 'one' is there twice",
        "line 19: fact F1 is there twice",
        "line 20: expected `- F1 [topic words]: the fact`, got '- no id'",
    ]


def test_replay_command_refuses_a_bad_file(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "c.md"
    path.write_text("## a\n- repo: r\n- commit: abc1234\n- kind: feature\n- max-usd: 0\n", "utf-8")

    assert cli.main(["replay", str(path)]) == 2
    assert "c.md: line 5: max-usd '0' is not a number above 0" in capsys.readouterr().err


# ---------- the sandbox ----------
CALC = "def add(a, b):\n    return a + b\n"
SUB = "\n\ndef sub(a, b):\n    return a - b\n"
TEST_ADD = "import calc\n\n\ndef test_add():\n    assert calc.add(2, 3) == 5\n"
TEST_SUB = "\n\ndef test_sub():\n    assert calc.sub(5, 3) == 2\n"
TEST_MORE = "\n\ndef test_add_zero():\n    assert calc.add(0, 0) == 0\n"


def git(repo: pathlib.Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """parent: add(). target: sub() and its test. later: one more test of add()."""
    root = tmp_path_factory.mktemp("repo")
    git(root, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    shas = {}
    for name, code, tests in (
        ("parent", CALC, TEST_ADD),
        ("target", CALC + SUB, TEST_ADD + TEST_SUB),
        ("later", CALC + SUB, TEST_ADD + TEST_SUB + TEST_MORE),
    ):
        (root / "src" / "calc.py").write_text(code, encoding="utf-8")
        (root / "tests" / "test_calc.py").write_text(tests, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", f"{name}: the message")
        shas[name] = git(root, "rev-parse", "HEAD")
    return {"root": root, **shas}


def this_python(c: Candidate, sandbox: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(sys.executable)


@pytest.fixture(scope="module")
def built(repo: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> Layout:
    lay = Layout(tmp_path_factory.mktemp("out"), "sub")
    build(
        Candidate("sub", str(repo["root"]), repo["target"][:8], "feature", setup='say "no"'),
        lay,
        repo["root"],
        this_python,
    )
    return lay


def test_sandbox_holds_the_parent_and_hides_the_answer(repo: dict[str, Any], built: Layout) -> None:
    sb = built.sandbox

    assert git(sb, "rev-parse", "HEAD") == repo["parent"]
    assert git(sb, "rev-list", "--all") == repo["parent"] and git(sb, "remote") == ""
    for sha in (repo["target"], repo["later"]):
        seen = subprocess.run(["git", "-C", str(sb), "cat-file", "-e", sha], capture_output=True)
        assert seen.returncode != 0
    assert "test_sub" not in (sb / "tests" / "test_calc.py").read_text("utf-8")
    assert "test_sub" in (built.answer / "hidden" / "tests" / "test_calc.py").read_text("utf-8")
    assert (built.driver / "task.md").read_text("utf-8") == "target: the message\n"
    assert (built.driver / "setup.txt").read_text("utf-8") == '/framegate:setup say "no"\n'
    rules = read_json(sb / ".claude" / "settings.local.json")["permissions"]
    deny = rules["deny"]
    assert any(rule.endswith("/answers/**)") for rule in deny) and "Bash(git push *)" in deny
    assert rules["allow"] == ["Bash(framegate *)", "PowerShell(framegate *)"]  # the setup's
    ex = read_json(built.answer / "exercise.json")  # the tests that prove the task
    assert ex["proving"] == ["tests.test_calc::test_sub"]
    assert ex["base_pass"] == ["tests.test_calc::test_add"]


def test_rmtree_removes_paths_over_260_characters(tmp_path: pathlib.Path) -> None:
    deep = tmp_path / "venv" / ("d" * 120) / ("e" * 120) / ("f" * 40)
    prefix = "\\\\?\\" if os.name == "nt" else ""
    os.makedirs(prefix + str(deep))
    open(prefix + str(deep / "x.h"), "w").close()

    sandbox.rmtree(tmp_path / "venv")

    assert not (tmp_path / "venv").exists()


def test_candidate_whose_tests_pass_on_the_parent_is_dropped(
    repo: dict[str, Any], tmp_path: pathlib.Path
) -> None:
    c = Candidate("later", str(repo["root"]), repo["later"], "feature")

    with pytest.raises(ReplayError, match="no test fails on the parent and passes on the commit"):
        build(c, Layout(tmp_path, "later"), repo["root"], this_python)


PIN = "import calc\n\n\ndef test_add_keeps_floats():\n    assert calc.add(0.5, 0.5) == 1.0\n"


def pinned_candidate(repo: dict[str, Any]) -> Candidate:
    c = Candidate("sub", str(repo["root"]), repo["target"], "feature", task="Add sub.")
    c.facts, c.probes = parse(EXAMPLE)[0].facts, ["tests/test_pin.py"]
    return c


@pytest.fixture(scope="module")
def pinned(repo: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> Layout:
    """A candidate with two facts and one probe (in probes/sub/ next to the candidates file)."""
    base = tmp_path_factory.mktemp("pinned")
    (base / "probes" / "sub" / "tests").mkdir(parents=True)
    (base / "probes" / "sub" / "tests" / "test_pin.py").write_text(PIN, encoding="utf-8")
    lay = Layout(base / "out", "sub")
    build(pinned_candidate(repo), lay, repo["root"], this_python, base / "probes" / "sub")
    return lay


def test_facts_go_to_the_driver_and_probes_stay_outside_the_sandbox(pinned: Layout) -> None:
    facts = (pinned.driver / "facts.md").read_text("utf-8").splitlines()
    deny = read_json(pinned.sandbox / ".claude" / "settings.local.json")["permissions"]["deny"]

    assert facts[1] == "- F2 [other holders]: A lock another run holds is never released."
    assert (pinned.driver / "task.md").read_text("utf-8") == "Add sub.\n"
    assert not list(pinned.sandbox.rglob("facts.md")) + list(pinned.sandbox.rglob("test_pin.py"))
    assert any(rule.endswith("/probes/**)") for rule in deny)
    assert any(rule.endswith("/runs/*/driver/**)") for rule in deny)
    ex = read_json(pinned.answer / "exercise.json")
    assert ex["probes"] == ["tests.test_pin::test_add_keeps_floats"]


def test_a_probe_that_is_missing_or_fails_before_the_task_drops_the_candidate(
    repo: dict[str, Any], tmp_path: pathlib.Path
) -> None:
    folder = tmp_path / "probes" / "sub"
    with pytest.raises(ReplayError, match="probe file tests/test_pin.py is not in"):
        build(pinned_candidate(repo), Layout(tmp_path, "sub"), repo["root"], this_python, folder)
    (folder / "tests").mkdir(parents=True)
    (folder / "tests" / "test_pin.py").write_text(PIN.replace("1.0", "2.0"), encoding="utf-8")

    with pytest.raises(ReplayError, match="a probe does not pass on the parent.*keeps_floats"):
        build(pinned_candidate(repo), Layout(tmp_path, "sub"), repo["root"], this_python, folder)


def test_a_failed_probe_is_broke_a_rule_and_the_scorecard_counts_facts_and_lines(
    pinned: Layout,
) -> None:
    probes = pinned.out.parent / "probes" / "sub"
    files = ["tests/test_pin.py"]
    calc = pinned.sandbox / "src" / "calc.py"
    calc.write_text(CALC + SUB, encoding="utf-8")
    write_json(pinned.driver / "facts_used.json", ["F2", "F9"])
    kept = score.score(pinned, files, probes)
    calc.write_text(CALC.replace("a + b", "int(a) + int(b)") + SUB, encoding="utf-8")
    (pinned.driver / "facts_used.json").unlink()
    (pinned.driver / "log.md").write_text("fact F1 given for: lease?\n", encoding="utf-8")
    broke = score.score(pinned, files, probes)
    unprobed = score.score(pinned)

    assert (kept["verdict"], kept["probes_passed"], kept["probes"]) == ("good", 1, 1)
    assert (kept["facts"], kept["facts_used"]) == (2, ["F2"])
    assert (kept["src_lines"], kept["commit_src_lines"]) == (4, 4)
    assert (broke["verdict"], broke["passed"], broke["probes_passed"]) == ("broke a rule", 1, 0)
    assert broke["probes_failed"] == ["tests.test_pin::test_add_keeps_floats"]
    assert (broke["facts_used"], broke["src_lines"]) == (["F1"], 6)
    assert unprobed["verdict"] == "good" and "probes" not in unprobed
    assert not list(pinned.sandbox.rglob("test_pin.py"))
    assert score._cells({**scored("x"), **broke})[4:6] == ["-", "0/1"]


def test_probes_that_did_not_run_are_a_failed_score(
    pinned: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_tests(py: Any, tree: Any, roots: Any, files: Any, xml: pathlib.Path) -> Any:
        return ({}, "no tests ran") if xml.name == "probes.xml" else ({"a": "pass"}, "1 passed")

    monkeypatch.setattr(score, "run_tests", run_tests)

    row = score.score(pinned, ["tests/test_pin.py"], pinned.out.parent / "probes" / "sub")

    assert (row["verdict"], row["result_tail"]) == ("score failed", "probes: no tests ran")


# ---------- the score ----------
def test_score_runs_the_hidden_tests_on_the_result(built: Layout) -> None:
    before = score.score(built)
    (built.sandbox / "src" / "calc.py").write_text(CALC + SUB, encoding="utf-8")
    after = score.score(built)

    # no framegate.toml in the sandbox: the run ended in the setup, which is not "bad"
    assert (before["verdict"], before["stage"], before["passed"], before["failed"]) == (
        "not run",
        "SETUP",
        0,
        ["tests.test_calc::test_sub"],
    )
    assert (after["verdict"], after["passed"], after["regressions"]) == ("good", 1, [])


def test_verdict_skipped_is_not_proven_and_a_broken_old_test_is_bad() -> None:
    skipped = score.verdict(["a", "b"], ["old"], {"a": "pass", "b": "skip", "old": "pass"})
    missing = score.verdict(["a"], [], {"other": "pass"})
    regression = score.verdict(["a"], ["old"], {"a": "pass", "old": "fail"})

    assert (skipped["verdict"], skipped["skipped"]) == ("not proven", ["b"])
    assert (missing["verdict"], missing["failed"]) == ("bad", ["a"])
    assert (regression["verdict"], regression["regressions"]) == ("bad", ["old"])


def test_a_probe_that_did_not_run_fails_and_a_skipped_one_does_not_pass() -> None:
    assert score.probed(["p", "q"], {"p": "pass", "q": "skip"}) == {
        "probes": 2,
        "probes_passed": 1,
        "probes_failed": [],
    }
    assert score.probed([], {"p": "fail"})["probes_failed"] == ["p"]
    assert score.probed(["p"], {"other": "pass"})["probes_failed"] == ["p"]


def event(kind: str, **data: Any) -> dict[str, Any]:
    return {"kind": kind, "feature": "f", "ts": "2026-11-16T10:00:00", **data}


def test_flow_counts_questions_late_bugs_and_send_backs() -> None:
    valid = {"test": "t1", "verdict": "VALID"}
    events = [
        event("questions", questions="- one\n- two"),
        event("test_checks", sent_back=True),
        event("test_review", sent_back=True),
        event("test_review", sent_back=False),
        event("gate", attempt=1, passed=False),
        event("gate", attempt=2, passed=True),
        event("triage", findings=[valid, {"test": "t2", "verdict": "DISMISSED"}]),
        event("triage", findings=[valid]),
        event("final_gates", passed=False),
        event("final_gates", passed=True),
        {**event("final_gates", passed=False), "feature": "other"},
    ]

    assert score.flow(events, "f") == {"questions": 2, "late_bugs": 2, "send_backs": 3}
    assert score.flow([], "f") == {"questions": 0, "late_bugs": 0, "send_backs": 0}


def test_source_lines_leave_out_tests_documents_config_and_framegates_files() -> None:
    numstat = (
        "3\t1\tsrc/calc.py\n10\t0\ttests/test_calc.py\n5\t0\tdocs/calc.md\n"
        "7\t0\tspecs/sub/plan.md\n2\t0\tframegate.toml\n4\t0\t.framegate/map.json\n"
        "-\t-\tsrc/logo.png\n0\t2\tpyproject.toml\ngarbage\n"
        "9\t0\t.serena/project.yml\n1\t0\t.gitignore\n3\t0\tdeploy/job.yml\n"
    )

    patch = (
        "diff --git a/src/calc.py b/src/calc.py\n--- a/src/calc.py\n+++ b/src/calc.py\n"
        "@@ -1,2 +1,3 @@\n def add(a, b):\n-    return a+b\n+    return a + b\n+\n"
        "diff --git a/tests/test_calc.py b/tests/test_calc.py\n@@ -1 +1,2 @@\n+import calc\n"
    )

    assert score.source_lines(numstat) == 4
    assert score.patch_lines(patch) == 3


LATE = "import calc\n\n\ndef test_sub_below_zero():\n    assert calc.sub(3, 5) == -2\n"


def test_extra_tests_fail_before_pass_after_and_count_like_hidden_tests(
    repo: dict[str, Any], tmp_path: pathlib.Path
) -> None:
    """An after-only test of the candidate's author (extra/<name>/ next to the candidates
    file), and the candidates file hidden from the chat and the driver."""
    base = tmp_path / "cands"
    extra, file = base / "extra" / "sub", base / "candidates.md"
    (extra / "tests").mkdir(parents=True)
    late = extra / "tests" / "test_late.py"
    late.write_text(LATE, encoding="utf-8")
    file.write_text("# Candidates\n", encoding="utf-8")
    c = Candidate("sub", str(repo["root"]), repo["target"], "feature", extra=["tests/test_late.py"])
    lay = Layout(base / "out", "sub")
    name = "tests.test_late::test_sub_below_zero"

    def make() -> None:
        hide = sandbox.author_paths(base, file, lay.out)
        build(c, lay, repo["root"], this_python, None, extra, hide)

    make()
    calc = lay.sandbox / "src" / "calc.py"
    calc.write_text(CALC + SUB, encoding="utf-8")
    good = score.score(lay, (), None, c.extra, extra)
    calc.write_text(CALC + SUB.replace("a - b", "abs(a - b)"), encoding="utf-8")
    wrong = score.score(lay, (), None, c.extra, extra)

    assert read_json(lay.answer / "exercise.json")["extra"] == [name]
    for folder in (lay.sandbox, lay.driver):
        deny = read_json(folder / ".claude" / "settings.local.json")["permissions"]["deny"]
        for end in ("/cands/candidates.md)", "/cands/extra/**)", "/cands/probes/**)"):
            assert any(rule.endswith(end) for rule in deny), end
    # the runs are elsewhere: the whole folder of the candidates file
    assert sandbox.author_paths(base, file, tmp_path / "elsewhere") == [base]
    assert (good["verdict"], good["extra_passed"], good["extra"]) == ("good", 1, 1)
    # the hidden tests pass, the extra one fails ("not run": this sandbox has no engine)
    assert (wrong["verdict"], wrong["passed"], wrong["extra_failed"]) == ("not run", 1, [name])
    assert score.with_extra(good, [name], {name: "fail"}, "DONE")["verdict"] == "bad"
    assert score.with_extra(good, [name], {name: "skip"}, "DONE")["verdict"] == "not proven"
    assert score._cells({**scored("x"), **wrong})[4] == "0/1"
    assert not list(lay.sandbox.rglob("test_late.py"))

    late.write_text(PIN, encoding="utf-8")  # passes on the parent too
    with pytest.raises(ReplayError, match="an extra test does not fail on the parent.*floats"):
        make()
    late.write_text(LATE.replace("-2", "2"), encoding="utf-8")
    with pytest.raises(ReplayError, match="an extra test does not pass on the commit.*below"):
        make()
    late.unlink()
    with pytest.raises(ReplayError, match="extra test file tests/test_late.py is not in"):
        make()


def test_run_lines_start_at_the_task_without_setup_files_and_format_commits(
    tmp_path: pathlib.Path,
) -> None:
    sb = tmp_path / "sandbox"
    (sb / "src").mkdir(parents=True)
    git(sb, "init", "-q", "-b", "main")

    def commit(message: str, files: dict[str, str]) -> str:
        for rel, text in files.items():
            (sb / rel).parent.mkdir(parents=True, exist_ok=True)
            (sb / rel).write_text(text, encoding="utf-8")
        git(sb, "add", "-A")
        git(sb, "commit", "-q", "-m", message)
        return git(sb, "rev-parse", "HEAD")

    parent = commit("parent", {"src/calc.py": "def add(a,b):\n    return a+b\n"})
    commit("framegate: setup", {"pyproject.toml": "[project]\n", ".serena/project.yml": "x\n"})
    formatted = commit("f: format src/calc.py", {"src/calc.py": CALC})
    commit("f: step 1 - sub", {"src/calc.py": CALC + SUB, "tests/test_calc.py": "x\n"})

    assert score.run_lines(sb, "f", parent) == 4  # sub() only: from the last setup commit
    assert score.run_lines(sb, "g", parent) == 8  # not g's format commit
    git(sb, "update-ref", "refs/framegate/task-base/start/g", formatted)
    assert score.run_lines(sb, "g", parent) == 4  # the engine's formatted start commit
    assert score.run_lines(tmp_path, "f", parent) is None


def test_the_note_names_another_mode_and_an_approval_with_failed_checks() -> None:
    red = [
        event("final_gates", passed=False, failed=["lint", "format"]),
        event("human_gate", gate="tests", approved=True),
        event("human_gate", gate="final", approved=True),
    ]
    green = [event("final_gates", passed=True), event("human_gate", gate="final", approved=True)]
    row = scored("a", note="cost cap", mode="feature", approved_red=["lint"])

    assert score.approved_red(red) == ["lint", "format"]
    assert score.approved_red(red[:2]) == [] and score.approved_red([*red, *green]) == []
    assert score._cells(row)[-1] == (
        "cost cap; ran as feature, not bugfix; approved with failed checks: lint"
    )
    assert score._cells(scored("a", mode="bugfix", approved_red=[]))[-1] == ""
    # the engine ran and left no source change
    assert score._cells(scored("a", note="ended at FINAL", src_lines=0))[-1] == (
        "ended at FINAL; nothing built"
    )
    assert score._cells(scored("a", src_lines=0, stage="SPEC"))[-1] == ""
    assert score._cells(scored("a", src_lines=3))[-1] == ""


def test_spec_questions_are_the_options_asked_before_the_spec_approval(
    tmp_path: pathlib.Path,
) -> None:
    lay = Layout(tmp_path, "f")
    lay.driver.mkdir(parents=True)
    asked = (
        "1. Empty?\n   A: refused (default) / B: stored\n2. Order?\n   - **A:** kept\n   - B: no"
    )
    turns = [
        ("setup", "1. Commit?\n   A: yes (default) / B: no"),
        ("task", asked),
        ("task", "**Task** - as approved. A: nothing here"),
        ("task", "1. Parked step?\n   A: leave it (default) / B: redo"),
    ]
    for n, (session, result) in enumerate(turns, 1):
        write_json(lay.driver / f"turn-{n:02d}.json", {"session_id": session, "result": result})

    assert score.spec_questions(lay) == {"spec_questions": 3}  # no approval yet: every task turn
    assert score.spec_questions(Layout(tmp_path, "none")) == {"spec_questions": None}

    # approved after the third turn: the gate's question does not count
    (lay.sandbox / "out").mkdir(parents=True)
    (lay.sandbox / "framegate.toml").write_text("", encoding="utf-8")
    stamp = datetime.datetime(2026, 11, 16, 10, 0, 0)
    for n, minutes in enumerate((-30, -20, -10, 10), 1):
        at = (stamp + datetime.timedelta(minutes=minutes)).timestamp()
        os.utime(lay.driver / f"turn-{n:02d}.json", (at, at))
    line = json.dumps({**event("spec_approved"), "ts": stamp.isoformat()})
    (lay.sandbox / "out" / "events.jsonl").write_text(line + "\n", encoding="utf-8")

    assert score.spec_questions(lay) == {"spec_questions": 2}


def test_a_run_without_scorecard_data_shows_dashes(tmp_path: pathlib.Path) -> None:
    lay = Layout(tmp_path, "empty")
    (lay.driver).mkdir(parents=True)
    (lay.driver / "facts.md").write_text("- F1 [a]: one\n", encoding="utf-8")

    card = score.scorecard(lay)
    cells = score._details({**scored("empty"), **card})

    assert card == {
        "questions": None,
        "late_bugs": None,
        "send_backs": None,
        "mode": None,
        "approved_red": None,
        "facts": 1,
        "facts_used": None,
        "spec_questions": None,
        "src_lines": None,
        "commit_src_lines": None,
    }
    assert cells[5:] == ["-/1", "-", "-", "-", "-", "- / -"]
    # a row of an older framegate: no scorecard keys at all
    assert score._details(scored("old"))[5:] == ["-", "-", "-", "-", "-", "- / -"]
    assert score._cells(scored("old"))[4:6] == ["-", "-"]


def test_a_scoring_run_without_a_result_is_score_failed_not_bad(
    built: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    died = subprocess.CompletedProcess([], 137, "", "")  # killed: no output, no junit file
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **kw: died)
    killed = score.score(built)
    monkeypatch.setattr(score, "run_tests", lambda *a: (_ for _ in ()).throw(OSError("no memory")))
    unstarted = score.score(built)

    assert (killed["verdict"], killed["failed"], killed["regressions"]) == ("score failed", [], [])
    assert killed["result_tail"] == "no output (exit 137)"
    assert (unstarted["verdict"], unstarted["result_tail"]) == (
        "score failed",
        "OSError: no memory",
    )
    assert score.verdict(["a"], ["old"], {})["verdict"] == "score failed"


def test_a_cut_off_junit_file_is_no_result(tmp_path: pathlib.Path) -> None:
    def half(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        xml = next(a for a in cmd if a.startswith("--junitxml=")).split("=", 1)[1]
        pathlib.Path(xml).write_text("<testsuites><testsuite><testcase", "utf-8")
        return subprocess.CompletedProcess(cmd, 1, "", "")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sandbox.subprocess, "run", half)
        found, _tail = sandbox.run_tests(
            pathlib.Path(sys.executable), tmp_path, ["."], ["t.py"], tmp_path / "r.xml"
        )

    assert found == {}


def test_usage_limit_wait_ends_at_the_next_event_or_now() -> None:
    events = [
        {"ts": "2026-11-17T10:00:00", "kind": "usage_limit"},
        {"ts": "2026-11-17T11:00:00", "kind": "usage_limit_over"},
        {"ts": "2026-11-17T11:30:00", "kind": "usage_limit"},
    ]

    assert score.waited_seconds(events, datetime.datetime(2026, 11, 17, 11, 40)) == 3600 + 600


def test_chat_cost_is_the_last_total_of_each_session(tmp_path: pathlib.Path) -> None:
    for n, (session, cost) in enumerate([("s1", 1.0), ("s1", 2.5), ("s2", 0.5)], 1):
        write_json(tmp_path / f"turn-{n:02d}.json", {"session_id": session, "total_cost_usd": cost})

    assert score.chat_cost(tmp_path) == 3.0


def scored(name: str, **over: Any) -> dict[str, Any]:
    row = {
        "name": name,
        "kind": "bugfix",
        "status": "scored",
        "verdict": "good",
        "hidden": 2,
        "passed": 2,
        "failed": [],
        "skipped": [],
        "regressions": [],
        "stage": "DONE",
        "engine_usd": 6.38,
        "chat_usd": 9.4,
        "driver_usd": 1.25,
        "final_rounds": 1,
        "redos": 0,
        "waited_s": 600,
        "wall_s": 4200,
        "note": "",
    }
    return {**row, **over}


def test_results_table_and_collected_retros(tmp_path: pathlib.Path) -> None:
    card = {"probes": 3, "probes_passed": 2, "facts": 4, "facts_used": ["F1"], "questions": 5}
    card |= {"late_bugs": 1, "send_backs": 2, "src_lines": 41, "commit_src_lines": 38}
    card |= {"spec_questions": 7}
    rows = [
        scored("a", **card),
        {"name": "b", "kind": "feature", "status": "dropped", "note": "no test file"},
        scored("c", verdict="score failed", passed=0, note="score failed (x): run it again"),
    ]
    (tmp_path / "runs" / "a").mkdir(parents=True)
    (tmp_path / "runs" / "a" / "retro_proposals.md").write_text("1. shorter prompts\n", "utf-8")

    score.write_results(tmp_path, rows)

    lines = (tmp_path / "results.md").read_text("utf-8").splitlines()
    # 17.03: engine + chat + driver
    assert "| a | bugfix | good | 2/2 | - | 2/3 | 0 | DONE | 60 min | 17.03 |  |" in lines
    assert "| b | feature | dropped |  |  |  |  |  |  |  | no test file |" in lines
    assert any(
        line.startswith("| c | bugfix | score failed |  | - | - |  | DONE |") for line in lines
    )
    assert "score failed = the hidden tests did not run" in lines[lines.index("## Details") - 2]
    assert "| a | 6.38 | 9.40 | 1 | 0 | 1/4 | 7 | 5 | 1 | 2 | 41 / 38 |" in lines
    assert "| c | 6.38 | 9.40 | 1 | 0 | - | - | - | - | - | - / - |" in lines
    assert not any(line.startswith("| b |") for line in lines[lines.index("## Details") :])
    assert json.loads((tmp_path / "results.json").read_text("utf-8")) == rows
    assert (tmp_path / "retro-proposals.md").read_text("utf-8") == "# a\n\n1. shorter prompts\n"


# ---------- the caps ----------
def test_caps_cost_and_time_without_the_usage_limit_wait() -> None:
    caps = run.Caps(usd=25, hours=3)

    assert run.cap_hit(caps, elapsed_s=4 * 3600, waited_s=2 * 3600, usd=24.9) == ""
    assert run.cap_hit(caps, elapsed_s=4 * 3600, waited_s=0, usd=1).startswith("time cap")
    assert run.cap_hit(caps, elapsed_s=60, waited_s=0, usd=25.5).startswith("cost cap")


class Endless:
    def poll(self) -> None:
        return None


def test_driver_is_killed_at_a_cap() -> None:
    spent = iter([(5.0, 0.0), (30.0, 0.0)])
    killed: list[Any] = []
    proc = Endless()

    why = run.watch(proc, run.Caps(), lambda: next(spent), poll=0, kill=killed.append)

    assert why.startswith("cost cap: 30.00 USD") and killed == [proc]


# ---------- the scheduler ----------
@pytest.fixture
def fake_runs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[run.Options, list[str]]:
    """Options with faked sandboxes and scores, and the list of driver and retro calls. The
    driver fails for the candidate named `breaks`, reports a cap for the one named `capped`
    and ends before the task for the one named `unstarted`."""
    driven: list[str] = []

    def fake_build(c: Candidate, lay: Layout, src: pathlib.Path, env: Any, *more: Any) -> None:
        write_json(lay.run / "run.json", {"state": "prepared", "python": sys.executable})

    def driver(lay: Layout, c: Candidate, caps: run.Caps, src: pathlib.Path) -> dict[str, Any]:
        driven.append(c.name)
        if c.name == "breaks":
            raise OSError("claude not found")
        if c.name == "unstarted":
            lay.driver.mkdir(parents=True)
            (lay.driver / "outcome.txt").write_text("Setup: pytest is missing\n", "utf-8")
        return {"stopped": "cost cap" if c.name == "capped" else "", "wall_s": 60}

    def engine(sandbox: pathlib.Path, slug: str) -> dict[str, Any]:
        return {"stage": {"capped": "IMPLEMENT", "unstarted": "SPEC"}.get(slug, "DONE")}

    monkeypatch.setattr(run, "build", fake_build)
    monkeypatch.setattr(score, "engine", engine)
    monkeypatch.setattr(score, "score", lambda lay, *a: scored(lay.name))

    def retro(lay: Layout) -> str:
        driven.append(f"retro {lay.name}")
        return "ran"

    opts = run.Options(tmp_path / "out", tmp_path, parallel=2, driver=driver, retro=retro)
    opts.echo = lambda line: None
    return opts, driven


def candidates(tmp_path: pathlib.Path, *names: str) -> list[Candidate]:
    return [Candidate(n, str(tmp_path), "abc1234", "bugfix") for n in names]


def test_a_failing_candidate_does_not_stop_the_others_and_a_rerun_skips_scored_ones(
    tmp_path: pathlib.Path, fake_runs: tuple[run.Options, list[str]]
) -> None:
    opts, driven = fake_runs
    cands = candidates(tmp_path, "one", "breaks", "two", "three")
    cands[3].repo = "no-such-folder"

    rows = run.run_all(cands, opts)
    first = sorted(driven)
    run.run_all(cands, opts)

    assert [r["status"] for r in rows] == ["scored", "error", "scored", "dropped"]
    assert rows[1]["note"] == "OSError: claude not found"
    assert first == ["breaks", "one", "two"]
    assert sorted(driven) == ["breaks", "breaks", "one", "two"]


def test_a_run_stopped_at_a_cap_is_scored_and_gets_a_retro(
    tmp_path: pathlib.Path, fake_runs: tuple[run.Options, list[str]]
) -> None:
    opts, driven = fake_runs

    (row,) = run.run_all(candidates(tmp_path, "capped"), opts)

    assert (row["status"], row["note"]) == ("scored", "cost cap; retro ran")
    assert driven == ["capped", "retro capped"]
    assert read_json(opts.out / "runs" / "capped" / "run.json")["state"] == "stopped"


def test_a_run_that_ended_before_the_task_gets_no_retro_and_the_drivers_reason(
    tmp_path: pathlib.Path, fake_runs: tuple[run.Options, list[str]]
) -> None:
    opts, driven = fake_runs

    (row,) = run.run_all(candidates(tmp_path, "unstarted"), opts)

    assert row["note"] == "ended at SPEC; Setup: pytest is missing"
    assert driven == ["unstarted"]


def test_a_failed_score_is_scored_again_without_a_new_run(
    tmp_path: pathlib.Path,
    fake_runs: tuple[run.Options, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opts, driven = fake_runs
    opts.prune = True
    lines: list[str] = []
    opts.echo = lines.append
    cands = candidates(tmp_path, "one")
    failed = scored("one", verdict="score failed", passed=0, result_tail="no output (exit 137)")
    monkeypatch.setattr(score, "score", lambda lay, *a: failed)

    (first,) = run.run_all(cands, opts)
    monkeypatch.setattr(score, "score", lambda lay, *a: scored(lay.name))
    (second,) = run.run_all(cands, opts)

    assert first["note"] == "score failed (no output (exit 137)): run --score-only again"
    assert second["verdict"] == "good" and driven == ["one"]
    # scoring again needs the environment: a failed score prunes nothing
    assert [line for line in lines if "pruned" in line] == ["[one] pruned: 0 MB freed"]
    assert read_json(opts.out / "runs" / "one" / "score.json")["verdict"] == "good"


def filled(lay: Layout) -> None:
    """A run folder as a scored run leaves it, the long path of a real .venv included."""
    files = (
        "driver/log.md",
        "score.json",
        "retro_proposals.md",
        "score/result.xml",
        "score/result/src/big.py",
        "sandbox/.git/HEAD",
        "sandbox/.git/venv/pyvenv.cfg",
        "sandbox/out/__pycache__/kept.pyc",
        "sandbox/specs/one.md",
        "sandbox/.framegate/x.json",
        "sandbox/framegate.toml",
        "sandbox/src/calc.py",
        "sandbox/src/venv/calc.py",  # a package of the project, not an environment
        "sandbox/src/__pycache__/calc.pyc",
        "sandbox/.venv/pyvenv.cfg",
        "sandbox/.venv/" + "d" * 120 + "/" + "e" * 120 + "/x.h",
        "sandbox/.pytest_cache/v",
        "sandbox/web/node_modules/a/index.js",
        "sandbox/mutants/src/calc.py",
    )
    prefix = "\\\\?\\" if os.name == "nt" else ""
    for name in files:
        path = prefix + str(lay.run / name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("x" * 10)


def test_prune_deletes_what_can_be_rebuilt_and_keeps_the_evidence(tmp_path: pathlib.Path) -> None:
    lay = Layout(tmp_path, "one")
    filled(lay)

    freed = sandbox.prune(lay)

    left = {p.relative_to(lay.run).as_posix() for p in lay.run.rglob("*") if p.is_file()}
    assert left == {
        "driver/log.md",
        "score.json",
        "retro_proposals.md",
        "score/result.xml",
        "sandbox/.git/HEAD",
        "sandbox/.git/venv/pyvenv.cfg",
        "sandbox/out/__pycache__/kept.pyc",
        "sandbox/specs/one.md",
        "sandbox/.framegate/x.json",
        "sandbox/framegate.toml",
        "sandbox/src/calc.py",
        "sandbox/src/venv/calc.py",
    }
    assert freed == 70 and sandbox.prune(lay) == 0


def test_prune_alone_prunes_scored_runs_and_a_pruned_run_is_not_scored_again(
    tmp_path: pathlib.Path, fake_runs: tuple[run.Options, list[str]]
) -> None:
    opts, driven = fake_runs
    lines: list[str] = []
    opts.echo = lines.append
    cands = candidates(tmp_path, "one")
    (row,) = run.run_all(cands, opts)
    lay = Layout(opts.out, "one")
    filled(lay)
    write_json(lay.run / "score.json", row)
    py = lay.sandbox / ".venv" / "python"
    py.write_text("", "utf-8")
    write_json(lay.run / "run.json", {"state": "finished", "python": str(py)})

    opts.prune = True
    run.run_all(cands, opts)
    opts.score_only = True
    (again,) = run.run_all(cands, opts)

    assert driven == ["one"] and not (lay.sandbox / ".venv").exists()
    # the verdict stays; the numbers that need no environment are read again
    assert "[one] pruned: 0 MB freed" in lines and again == {**row, **score.scorecard(lay)}
    assert again["questions"] is None and read_json(lay.run / "score.json") == again
    assert lines[-1].startswith("[one] cannot be scored again: the sandbox's environment is gone")


def test_prepare_only_starts_no_driver(
    tmp_path: pathlib.Path, fake_runs: tuple[run.Options, list[str]]
) -> None:
    opts, driven = fake_runs
    opts.prepare_only = True

    (row,) = run.run_all(candidates(tmp_path, "one"), opts)

    assert row["status"] == "prepared" and driven == []


# ---------- the driver ----------
def test_driver_instructions_are_filled_for_the_run(tmp_path: pathlib.Path) -> None:
    lay = Layout(tmp_path, "lock-free")
    c = Candidate("lock-free", "r", "abc1234", "bugfix")

    text = run.instructions(lay, c, run.Caps(10, 2), tmp_path / "repo")

    assert "{{" not in text
    assert "`/framegate:bugfix lock-free`" in text and "after 2 hours" in text and "10 USD" in text
    assert f"  - `{lay.answer.parent}`" in text and f"  - `{tmp_path / 'repo'}`" in text
    assert "fact F1 given for: <question, short>" in text and "`facts_used.json`" in text
    # the task text goes first, in the same session as the start command
    first, start = text.index('reply only: ok" --file task.md'), text.index("`/framegate:bugfix")
    assert text.index("**Start the task.**") < first < start


def test_the_spec_dialogue_asks_again_after_a_task_description() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("feature", "bugfix", "refactor"):
        skill = " ".join((root / f"plugin/skills/{name}/SKILL.md").read_text("utf-8").split())
        assert 'does not answer your questions is not "ok" and takes no default' in skill, name
        assert "ask the ones it leaves open again" in skill, name
        assert "A task text the user gave earlier in this conversation is the" in skill, name


def test_chat_call_sends_a_line_and_a_file_as_one_message(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.chdir(tmp_path)
    (tmp_path / "task.md").write_text("Add sub.\n", encoding="utf-8")
    monkeypatch.setattr(chat, "work", lambda args, message, turn: seen.append(message) or 0)

    assert chat.main(["My task.", "--file", "task.md", "--turn", "turn-01.json"]) == 0
    assert chat.main(["--file", "task.md", "--turn", "turn-02.json"]) == 0
    with pytest.raises(SystemExit):
        chat.main(["/framegate:feature f", "--file", "task.md"])

    assert seen == ["My task.\n\nAdd sub.", "Add sub."]


def test_chat_call_sends_a_slash_command_as_argument_and_text_through_stdin() -> None:
    slash, no_stdin = chat.command("claude", "/framegate:setup")
    text, stdin = chat.command("claude", "line 1\nline 2", resume="abc")

    assert slash[-1] == "/framegate:setup" and no_stdin is None
    assert text[-4:-2] == ["--resume", "abc"] and stdin == "line 1\nline 2"
    assert text[-2:] == ["--model", "sonnet"]
    allowed = slash[slash.index("--allowedTools") + 1].split(",")
    assert "Bash" in allowed and "PowerShell" in allowed  # no prompts; the guard keeps it in
    assert "--strict-mcp-config" in slash


def test_chat_call_is_detached_and_wait_prints_its_answer(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sending returns at once (the driver's time limit cannot cut the chat); the answer is
    fetched with --wait."""
    started: list[list[str]] = []

    def detach(cmd: list[str]) -> int:
        started.append(list(cmd))
        return os.getpid()  # a process that is alive

    def claude(cmd: list[str], **kw: Any) -> Any:
        answer = json.dumps({"session_id": "s1", "total_cost_usd": 0.5, "result": "The gate."})
        return subprocess.CompletedProcess(cmd, 0, answer, "")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(chat, "detach", detach)
    monkeypatch.setattr(chat, "claude_exe", lambda: "claude")

    sent = chat.main(["Spec OK", "--resume", "s1"])
    early = chat.main(["--wait", "0"])
    twice = chat.main(["Spec OK", "--resume", "s1"])
    monkeypatch.setattr(chat.subprocess, "run", claude)
    chat.main(started[0][3:])  # the detached call itself
    capsys.readouterr()
    late = chat.main(["--wait"])

    assert (sent, early, twice, late) == (0, chat.STILL_RUNNING, 2, 0)
    assert len(started) == 1 and started[0][-2:] == ["--turn", "turn-01.json"]
    assert "turn-01.json  session: s1" in capsys.readouterr().out
    assert not list(tmp_path.glob("turn-*.pid"))


def test_the_guard_keeps_every_call_inside_the_run(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "my run"  # a space in the path
    (root / "sandbox").mkdir(parents=True)
    cwd = str(root / "sandbox")
    toml = root / "sandbox" / "framegate.toml"

    def why(tool: str, **args: str) -> str:
        return guard.verdict(
            {"tool_name": tool, "tool_input": args, "cwd": cwd}, guard.norm(str(root))
        )

    # what the setup chat ran in the replays (blocked by the old allow-list)
    for ok in (
        r'.venv\Scripts\python.exe -m pip install -e ".[dev]"',
        "framegate preflight --plugin-version 0.12.5; framegate state",
        'Get-Content "$HOME/.claude/plugins/known_marketplaces.json"',
        "Get-ChildItem src,tests -Depth 2 -Name | Select-Object -First 80; git branch -a",
        "git diff spec-approved/x..HEAD -- src",
        f"Get-Content {toml}",
        f'git -C "{toml.parent.as_posix()}" status && ls',
        'git commit -m "fix reg handling in the az region"; grep -rn claude .',
    ):
        assert why("PowerShell", command=ok) == "", ok
    assert why("Edit", file_path="src/a.py") == ""
    for bad in (
        "git push origin HEAD",
        "cd src; curl https://example.com",
        "framegate publish x",
        r"Remove-Item C:\Users\someone\project -Recurse",
        "cat /c/Users/someone/.ssh/id_rsa",
        "Set-Content ../../answers/x.txt hi",
        "Get-Content $env:USERPROFILE/.gitconfig",
        "cat ${HOME}/.gitconfig",
        "echo x > /tmp/out.txt",
        r"Copy-Item a.txt \\server\share",
        "rm -rf ~/.claude/plugins/framegate",
        f'Copy-Item "{toml}" "C:/Program Files/x"',
        f"Get-Content {toml} C:/Windows/win.ini",
        "claude plugin install other",
        "git -C sandbox push",
        "Set-Location x; & gh pr create",
    ):
        assert why("PowerShell", command=bad), bad
    assert why("Write", file_path=str(tmp_path / "elsewhere.txt"))

    lay = Layout(tmp_path, "x")
    sandbox.write_deny(lay.driver, [], up=1)
    hook = json.loads((lay.driver / ".claude/settings.local.json").read_text(encoding="utf-8"))
    command = hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == "python -m framegate.replay.guard --up 1"  # no path: any shell runs it


# ---------- the usage limit ----------
def test_a_driver_that_died_on_the_usage_limit_is_detected(tmp_path: pathlib.Path) -> None:
    now = datetime.datetime(2026, 11, 24, 12, 0, tzinfo=datetime.timezone.utc)
    assert limits.detect(tmp_path, now) is None  # no driver output at all
    write_json(tmp_path / "driver.json", {"is_error": False, "result": "the usage limit hit"})
    assert limits.detect(tmp_path, now) is None
    text = "You've hit your limit · resets 4pm (UTC)"
    write_json(tmp_path / "driver.json", {"is_error": True, "result": text})
    hit = limits.record(limits.detect(tmp_path, now))["usage_limit"]
    assert hit == {"message": text, "reset": "2026-11-24T16:00:00+00:00"}
    assert limits.wait_seconds(hit, now) == 4 * 3600 + limits.MARGIN_S
    write_json(tmp_path / "driver.json", {"is_error": True, "result": "API Error: 429 again"})
    found = limits.detect(tmp_path, now)
    assert found is not None and found.reset_at is None
    assert limits.wait_seconds({"reset": None}, now) == limits.DEFAULT_WAIT_S + limits.MARGIN_S
    far = (now + datetime.timedelta(hours=limits.WAIT_MAX_HOURS + 1)).isoformat()
    assert limits.wait_seconds({"reset": far}, now) is None
    near = (now + datetime.timedelta(hours=1)).isoformat()
    assert limits.wait_seconds({"reset": near}, now) == 3600 + limits.MARGIN_S


def test_a_usage_limit_is_not_run_and_the_candidate_runs_again_after_the_wait(
    tmp_path: pathlib.Path, fake_runs: tuple[run.Options, list[str]]
) -> None:
    opts, driven = fake_runs
    plain, waits = opts.driver, []
    limited = {"usage_limit": {"message": "hit your limit", "reset": None}, "stopped": ""}

    def once(lay: Layout, c: Candidate, caps: run.Caps, src: pathlib.Path) -> dict[str, Any]:
        first = c.name not in driven
        return {**plain(lay, c, caps, src), **(limited if first else {})}

    def always(lay: Layout, c: Candidate, caps: run.Caps, src: pathlib.Path) -> dict[str, Any]:
        return {**plain(lay, c, caps, src), **limited}

    opts.wait = lambda seconds: bool(waits.append(seconds))
    opts.driver = once
    (row,) = run.run_all(candidates(tmp_path, "one"), opts)
    assert (row["status"], row["verdict"]) == ("scored", "good")
    assert driven == ["one", "one"] and waits == [limits.DEFAULT_WAIT_S + limits.MARGIN_S]

    opts.driver = always
    cands = candidates(tmp_path, "two")
    (row,) = run.run_all(cands, opts)
    assert row["verdict"] == row["status"] == "not run: usage limit"
    assert row["note"] == "hit your limit; the next round runs it again"
    assert driven.count("two") == limits.RERUNS + 1 and len(waits) == 1 + limits.RERUNS
    folder = opts.out / "runs" / "two"
    assert read_json(folder / "run.json")["state"] == limits.LIMITED
    assert not (folder / "score.json").exists()
    assert "| two | bugfix | not run: usage limit |" in score.table([row])
    opts.driver = plain
    (row,) = run.run_all(cands, opts)  # the next round: built and run again
    assert row["verdict"] == "good" and driven.count("two") == limits.RERUNS + 2

    opts.wait = lambda seconds: True  # Ctrl+C in the wait
    opts.driver = always
    (row,) = run.run_all(candidates(tmp_path, "three"), opts)
    assert (row["status"], row["note"]) == ("error", "ReplayError: interrupted")
