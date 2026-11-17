"""`framegate replay`: the candidates file, the sandbox, the score, the caps, the scheduler.
No test calls claude or the network."""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from framegate import cli
from framegate.replay import chat, run, score
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
- max-usd: 10

### Task
A released lock must be free at once.

```
## not a candidate
```

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
    assert first.task.startswith("A released lock") and "## not a candidate" in first.task
    assert second.name == "contract-key" and second.task == ""


def test_candidates_file_errors_name_the_line() -> None:
    bad = (
        "## one\n- repo: r\n- comit: abc1234\n- kind: fix\nstray\n"
        "## Two Words\n- repo: r\n\n"
        "## one\n- repo: r\n- commit: abc1234\n- kind: feature\n"
    )

    with pytest.raises(CandidatesError) as e:
        parse(bad)

    assert str(e.value).splitlines() == [
        "line 3: unknown field 'comit' (known: repo, commit, kind, tests, pythonpath, python, "
        "install, max-usd, max-hours)",
        "line 5: expected `- field: value` or `### Task`, got 'stray'",
        "line 1: candidate 'one' has no `- commit:`",
        "line 4: kind 'fix' is not one of feature, bugfix, refactor",
        "line 6: 'Two Words' is not a candidate name (a-z, 0-9, '-'); "
        "a heading inside a task needs ### or deeper",
        "line 9: candidate 'one' is there twice",
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
        Candidate("sub", str(repo["root"]), repo["target"][:8], "feature"),
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
    deny = read_json(sb / ".claude" / "settings.local.json")["permissions"]["deny"]
    assert any(rule.endswith("/answers/**)") for rule in deny) and "Bash(git push *)" in deny
    ex = read_json(built.answer / "exercise.json")  # the tests that prove the task
    assert ex["proving"] == ["tests.test_calc::test_sub"]
    assert ex["base_pass"] == ["tests.test_calc::test_add"]


def test_candidate_whose_tests_pass_on_the_parent_is_dropped(
    repo: dict[str, Any], tmp_path: pathlib.Path
) -> None:
    c = Candidate("later", str(repo["root"]), repo["later"], "feature")

    with pytest.raises(ReplayError, match="no test fails on the parent and passes on the commit"):
        build(c, Layout(tmp_path, "later"), repo["root"], this_python)


# ---------- the score ----------
def test_score_runs_the_hidden_tests_on_the_result(built: Layout) -> None:
    before = score.score(built)
    (built.sandbox / "src" / "calc.py").write_text(CALC + SUB, encoding="utf-8")
    after = score.score(built)

    assert (before["verdict"], before["passed"], before["failed"]) == (
        "bad",
        0,
        ["tests.test_calc::test_sub"],
    )
    assert (after["verdict"], after["passed"], after["regressions"]) == ("good", 1, [])
    assert after["stage"] == "SETUP"  # no framegate.toml in the sandbox


def test_verdict_skipped_is_not_proven_and_a_broken_old_test_is_bad() -> None:
    skipped = score.verdict(["a", "b"], ["old"], {"a": "pass", "b": "skip", "old": "pass"})
    missing = score.verdict(["a"], [], {})
    regression = score.verdict(["a"], ["old"], {"a": "pass", "old": "fail"})

    assert (skipped["verdict"], skipped["skipped"]) == ("not proven", ["b"])
    assert (missing["verdict"], missing["failed"]) == ("bad", ["a"])
    assert (regression["verdict"], regression["regressions"]) == ("bad", ["old"])


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
        "final_rounds": 1,
        "redos": 0,
        "waited_s": 600,
        "wall_s": 4200,
        "note": "",
    }
    return {**row, **over}


def test_results_table_and_collected_retros(tmp_path: pathlib.Path) -> None:
    rows = [
        scored("a"),
        {"name": "b", "kind": "feature", "status": "dropped", "note": "no test file"},
    ]
    (tmp_path / "runs" / "a").mkdir(parents=True)
    (tmp_path / "runs" / "a" / "retro_proposals.md").write_text("1. shorter prompts\n", "utf-8")

    score.write_results(tmp_path, rows)

    lines = (tmp_path / "results.md").read_text("utf-8").splitlines()
    assert "| a | bugfix | good | 2/2 | 0 | DONE | 60 min | 6.38 | 9.40 | 1 | 0 |  |" in lines
    assert "| b | feature | dropped |  |  |  |  |  |  |  |  | no test file |" in lines
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
    driver fails for the candidate named `breaks` and reports a cap for the one named
    `capped`."""
    driven: list[str] = []

    def fake_build(c: Candidate, lay: Layout, src: pathlib.Path, env: Any) -> None:
        write_json(lay.run / "run.json", {"state": "prepared", "python": sys.executable})

    def driver(lay: Layout, c: Candidate, caps: run.Caps, src: pathlib.Path) -> dict[str, Any]:
        driven.append(c.name)
        if c.name == "breaks":
            raise OSError("claude not found")
        return {"stopped": "cost cap" if c.name == "capped" else "", "wall_s": 60}

    def engine(sandbox: pathlib.Path, slug: str) -> dict[str, Any]:
        return {"stage": "IMPLEMENT" if slug == "capped" else "DONE"}

    monkeypatch.setattr(run, "build", fake_build)
    monkeypatch.setattr(score, "engine", engine)
    monkeypatch.setattr(score, "score", lambda lay: scored(lay.name))

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


def test_chat_call_sends_a_slash_command_as_argument_and_text_through_stdin() -> None:
    slash, no_stdin = chat.command("claude", "/framegate:setup")
    text, stdin = chat.command("claude", "line 1\nline 2", resume="abc")

    assert slash[-1] == "/framegate:setup" and no_stdin is None
    assert text[-2:] == ["--resume", "abc"] and stdin == "line 1\nline 2"
