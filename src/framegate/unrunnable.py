"""Tests that are red before any task ([gates] unrunnable_tests in framegate.toml): this
machine cannot run them, or they are the project's own debt.

Seen live (three replay runs on three repositories): tests that need a database, a service
or Linux failed on the untouched tree, and every gate blamed the task; so did eleven
placeholder tests the project had left red. A feature and a refactor start on a green tree,
so the setup finds the red tests once (`framegate doctor --tree`) and is not ready until
each one runs or is listed here with its reason, after the user's OK (the engine cannot
tell "this machine lacks X" from "really broken"):

    [gates]
    unrunnable_tests = [
      { test = "tests/test_db.py", reason = "needs a PostgreSQL server" },
      { test = "tests/test_todo.py", reason = "red before any task: placeholder tests" },
    ]

A listed test that failed on this machine at the setup is not run here again (known_red.py);
on a machine that has the database it runs, and counts when it passes. A failure of a listed
test is left out of every gate, in every mode, and the final facts name the listed tests with
their reason each time, so nobody reads "all green" into it.

A `test` names the file: a whole test file (`tests/test_db.py`) or one test in it
(`tests/test_db.py::test_connect`). The config refuses a bare test name.
"""

from __future__ import annotations

import dataclasses
import re

from framegate.adapters.base import CheckResult, TestRun
from framegate.config import Config

#: how a gate result names the failures it left out
LEFT_OUT = "listed in unrunnable_tests, left out"
#: how the reason of a debt entry starts (the machine can run the test; it is red anyway)
DEBT = "red before any task"


def debt(cfg: Config) -> list[str]:
    """The listed tests that are the project's own debt (by their reason)."""
    return [t for t, why in cfg.unrunnable_tests.items() if why.lower().startswith(DEBT)]


def _parts(text: str) -> list[str]:
    """`tests.test_x.TestA::test_b[1]` / `Ns.Class.Method(x: 1)` -> their name parts."""
    head = re.split(r"[(\[]", text, maxsplit=1)[0]
    return [p for p in re.split(r"[.:/\\]+", head) if p]


def matches(cfg: Config, case_name: str, entry: str) -> bool:
    """Is the test case (a name from the test report) the listed test, or inside the listed
    file? The entry's name parts (the file without its suffix, then the test) must appear in
    the case's name in a row, or the case's name starts where the file's path ends (a .NET
    report names `Shop.Tests.DbTests.Connects` for `tests/Shop.Tests/DbTests.cs`)."""
    path, sep, rest = entry.partition("::")
    want, have = _parts(path.rsplit(".", 1)[0] + sep + rest), _parts(case_name)
    n = len(want)
    if any(have[i : i + n] == want for i in range(len(have) - n + 1)):
        return True
    return any(have[: n - i] == want[i:] for i in range(1, n))


def entry_of(cfg: Config, case_name: str) -> str | None:
    """The [gates] unrunnable_tests entry that covers this test case (None: not listed)."""
    return next((e for e in cfg.unrunnable_tests if matches(cfg, case_name, e)), None)


def leave_out(cfg: Config, run: TestRun) -> tuple[TestRun, list[str]]:
    """`run` without the failures of the listed tests (they count as skipped), and their
    names. A run without per-test results stays as it is: nothing can be told apart."""
    if not cfg.unrunnable_tests or run.cases is None or run.incomplete:
        return run, []
    off = [c.name for c in run.cases if c.outcome in ("failed", "error") and entry_of(cfg, c.name)]
    if not off:
        return run, []
    names = set(off)
    cases = [dataclasses.replace(c, outcome="skipped") if c.name in names else c for c in run.cases]
    red = any(c.outcome in ("failed", "error") for c in cases)
    status = "passed" if run.status == "failed" and not red else run.status
    return dataclasses.replace(run, status=status, cases=cases), off


def noted(res: CheckResult, off: list[str]) -> CheckResult:
    """`res` with the left-out failures counted in its one-line result."""
    if off:
        res.summary = "; ".join(x for x in (f"{LEFT_OUT}: {len(off)}", res.summary) if x)
    return res


def listed_lines(cfg: Config) -> list[str]:
    """One line per listed test with its reason."""
    return [f"- {test}: {reason}" for test, reason in cfg.unrunnable_tests.items()]


def facts_lines(cfg: Config) -> list[str]:
    """The section of final_facts.md (empty: nothing is listed)."""
    if not cfg.unrunnable_tests:
        return []
    return [
        f"## Not checked: {len(cfg.unrunnable_tests)} listed test entry(ies)",
        "",
        "These tests are listed in [gates] unrunnable_tests (confirmed at the setup): this "
        "machine cannot run them, or they were red before any task. A failure of one is left "
        "out of every gate, so the gates say nothing about them. Name them in the summary "
        'every time, with the reason ("not checked: <test> (<reason>)"):',
        *listed_lines(cfg),
        "",
        "Follow-up to offer (default: skip): ONE task that makes these tests run or pass "
        "(/framegate:bugfix <name> for broken tests, /framegate:refactor <name> to make "
        "tests that need a service runnable here), so the next tasks check them too.",
    ]
