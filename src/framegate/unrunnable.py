"""Tests this machine cannot run ([gates] unrunnable_tests in framegate.toml).

Seen live (three replay runs on three repositories): tests that need a database, a service
or Linux failed on the untouched tree, and every gate blamed the task. The engine cannot
tell "this machine lacks X" from "really broken". So the setup finds the red tests once
(`framegate doctor --tree`), the chat proposes which of them cannot run here, the user
confirms, and the entries are written with their reason:

    [gates]
    unrunnable_tests = [{ test = "tests/test_db.py", reason = "needs a PostgreSQL server" }]

The tests still run with the suite (on a machine that has the database they pass and count).
A failure of a listed test is left out of every gate, in every mode, and the final facts
name the listed tests with their reason each time, so nobody reads "all green" into it.

A `test` is a name as the test report prints it, a pytest node ID
(`tests/test_db.py::test_connect`), a test file, or a folder of tests.
"""

from __future__ import annotations

import dataclasses
import re

from framegate.adapters.base import CheckResult, TestRun
from framegate.config import Config

#: how a gate result names the failures it left out
LEFT_OUT = "cannot run on this machine, left out"


def _parts(text: str) -> list[str]:
    """`tests.test_x.TestA::test_b[1]` / `Ns.Class.Method(x: 1)` -> their name parts."""
    head = re.split(r"[(\[]", text, maxsplit=1)[0]
    return [p for p in re.split(r"[.:/\\]+", head) if p]


def entry_parts(cfg: Config, entry: str) -> list[str]:
    """The name parts of a listed test. A file path loses its suffix, so
    `tests/test_db.py` reads like the report's `tests.test_db`."""
    path, sep, rest = entry.partition("::")
    try:
        is_path = "/" in path or "\\" in path or (cfg.root / path).is_file()
    except OSError:
        is_path = False
    last = path.replace("\\", "/").rsplit("/", 1)[-1]
    if is_path and "." in last:
        path = path.rsplit(".", 1)[0]
    return _parts(path + sep + rest)


def matches(cfg: Config, case_name: str, entry: str) -> bool:
    """Is the test case (a name from the test report) the listed test, or inside the listed
    file or folder? The entry's name parts must appear in the case's name, in a row."""
    want, have = entry_parts(cfg, entry), _parts(case_name)
    if not want:
        return False
    n = len(want)
    return any(have[i : i + n] == want for i in range(len(have) - n + 1))


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
        f"## Not checked on this machine: {len(cfg.unrunnable_tests)} test entry(ies)",
        "",
        "These tests cannot run on this machine ([gates] unrunnable_tests, confirmed at the "
        "setup). A failure of one is left out of every gate, so the gates say nothing about "
        'them. Name them in the summary every time, with the reason ("cannot run on this '
        'machine: <test> (<reason>)"):',
        *listed_lines(cfg),
    ]
