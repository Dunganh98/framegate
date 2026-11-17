"""`framegate doctor --tree`: what is red on the tree as it is, found once at the setup.

Seen live (three replay runs on three repositories): the untouched tree already had failing
tests, lint findings and unformatted files, and the gates of the first task blamed the task.
A feature and a refactor start on a green tree, so the setup is where a red one is found
and handled, once per project (a bug fix records its own start: [gates]
regression_baseline).

The whole suite runs once (the final gate's regression run). The lint / format / typecheck /
imports results and the tool list are doctor's own: nothing runs twice. The result is a few
plain lines for the setup chat, each kind with its prefix:

- `TESTS:`     how many tests ran and failed
- `LISTED:`    a test of [gates] unrunnable_tests with its reason (this machine cannot run
               it, or it is red before any task)
- `PROBLEM:`   tests that fail and are not listed (the setup is not ready: doctor fails), or
               a suite that gave no result
- `DEBT:`      a lint / format / typecheck / imports check that fails, with its size
- `MUTATION:`  the mutation tool cannot run on this machine
- `  ->`       what the chat does about the lines above it

The engine cannot tell "this machine lacks a database" from "really broken": the chat
proposes, the user confirms, then the entry is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from framegate import red_tree, unrunnable
from framegate.adapters.base import CheckResult, LanguageAdapter, TestRun

#: most failing tests named (the chat reads these lines; the full list is in the junit file)
SHOWN_TESTS = 30
#: most files named behind a debt line
SHOWN_FILES = 3

HEADING = "Tree check (the code as it is, before any task):"

RED_TESTS_ADVICE = (
    "  -> The setup is not ready while one of them is left. Each test ends in one of: made "
    "to run (install what it lacks), or an entry { test, reason } in [gates] "
    "unrunnable_tests (test: its file, or file::test), proposed by you and written once "
    "the user confirmed. Two kinds of reason: this machine cannot run it (a database, a "
    f'service, another operating system), or "{unrunnable.DEBT}: <what>" (the machine '
    "runs it and it fails: the project's own debt)."
)
LISTED_DEBT_ADVICE = (
    "  -> {n} listed entry(ies) are red before any task. In your summary, suggest ONE task "
    "that fixes them (/framegate:bugfix <name>, or /framegate:refactor <name>)."
)
DEBT_ADVICE = (
    "  -> Propose ONE task that cleans this up (/framegate:refactor <name>), as a follow-up "
    "the user accepts or skips. No question per file: a task formats the files it touches "
    "anyway, the other files stay as they are."
)


@dataclass
class Tree:
    """The suite's result as a doctor item (status, detail) and the lines for the chat."""

    status: str  # OK | WARN | FAIL
    detail: str = ""
    lines: list[str] = field(default_factory=list)


def _first_line(text: str, limit: int = 160) -> str:
    line = next((x.strip() for x in text.splitlines() if x.strip()), "")
    return line[:limit]


def _tail(text: str, n: int = 8) -> list[str]:
    return [x for x in text.strip().splitlines() if x.strip()][-n:]


def listed_lines(adapter: LanguageAdapter, run: TestRun) -> list[str]:
    """`LISTED:` per entry of [gates] unrunnable_tests, every time. An entry that matches no
    test of the run is said: it is a typing slip, or the test is gone."""
    cfg = adapter.cfg
    names = [c.name for c in run.cases or []]
    out = []
    for test, reason in cfg.unrunnable_tests.items():
        stale = run.cases is not None and not any(unrunnable.matches(cfg, n, test) for n in names)
        out.append(
            f"LISTED: {test}: {reason}" + (" (matches no test of the suite)" if stale else "")
        )
    owed = len(unrunnable.debt(cfg))
    return [*out, LISTED_DEBT_ADVICE.format(n=owed)] if owed else out


def suite(adapter: LanguageAdapter, run: TestRun) -> Tree:
    """The whole-suite run, judged: green, red tests (listed or not), or no result."""
    kept, off = unrunnable.leave_out(adapter.cfg, run)
    listed = listed_lines(adapter, run)
    if run.incomplete:
        why = f"the whole suite did not run to the end ({run.incomplete})"
        lines = [f"PROBLEM: {why}; nothing is known about the tests.", *_indent(run.output)]
        return Tree("FAIL", why, lines + listed)
    if kept.status == "no_tests":
        return Tree("WARN", "no tests yet", ["TESTS: none yet.", *listed])
    cases = kept.cases
    if kept.status not in ("passed", "failed") or (kept.status == "failed" and cases is None):
        why = f"the whole suite gave no per-test results (status {kept.status})"
        lines = [f"PROBLEM: {why}; fix the test command first.", *_indent(run.output)]
        return Tree("FAIL", why, lines + listed)
    red = [c for c in cases or [] if c.outcome in ("failed", "error")]
    total = len(cases) if cases is not None else None
    count = f"{total} run, " if total is not None else ""
    fails = len(red) + len(off)
    head = f"TESTS: {count}{fails} fail" + (
        f" ({len(off)} of them listed in [gates] unrunnable_tests, left out)." if off else "."
    )
    lines = [head, *listed]
    if not red:
        detail = (f"{total} test(s)" if total is not None else "green") + (
            f"; {len(off)} listed failure(s) left out" if off else ""
        )
        return Tree("OK", detail, lines)
    lines.append(f"PROBLEM: {len(red)} test(s) fail on the untouched tree and are not listed:")
    for c in red[:SHOWN_TESTS]:
        why = _first_line(c.message)
        lines.append(f"  {c.name}" + (f" | {why}" if why else ""))
    if len(red) > SHOWN_TESTS:
        lines.append(f"  ... and {len(red) - SHOWN_TESTS} more")
    lines.append(RED_TESTS_ADVICE)
    detail = (
        f"{len(red)} test(s) fail on the untouched tree: make each one run, or list it with "
        "its reason after the user's OK ([gates] unrunnable_tests)"
    )
    return Tree("FAIL", detail, lines)


def _indent(text: str) -> list[str]:
    return [f"  {x}" for x in _tail(text)]


def debt_lines(adapter: LanguageAdapter, red: list[tuple[str, CheckResult]]) -> list[str]:
    """`DEBT:` per failing lint / format / typecheck / imports check, with its size (files,
    output lines that name a file: red_tree.file_counts), then one proposal for all of them."""
    out = []
    for name, res in red:
        files = red_tree.file_counts(adapter, res.output)
        if not files:
            out.append(f"DEBT: {name}: fails without naming a file")
            continue
        top = sorted(files, key=lambda f: (-files[f], f))[:SHOWN_FILES]
        more = f", and {len(files) - len(top)} more" if len(files) > len(top) else ""
        out.append(
            f"DEBT: {name}: {sum(files.values())} line(s) in {len(files)} file(s) "
            f"(most in {', '.join(top)}{more})"
        )
    return [*out, DEBT_ADVICE] if out else []


def mutation_lines(tools: list[tuple[str, bool, str]]) -> list[str]:
    """`MUTATION:` when doctor's tool list says the mutation tool is not there (one line)."""
    return [
        f"MUTATION: {name.replace(' (mutation)', '')} cannot run on this machine: "
        f"{detail.rstrip('.')}. Make it run (the fix), or the user decides: [gates] "
        'mutation_tool = "none".'
        for name, found, detail in tools
        if "(mutation)" in name and not found
    ]


def run(
    adapter: LanguageAdapter,
    lint_red: list[tuple[str, CheckResult]],
    tools: list[tuple[str, bool, str]],
) -> Tree:
    """Run the whole suite once and put the lines together. `lint_red`: the failing lint /
    format / typecheck / imports checks doctor just ran; `tools`: its tool list."""
    tree = suite(adapter, adapter.regression_run())
    debt = debt_lines(adapter, lint_red)
    mutation = mutation_lines(tools)
    green = not debt and not any(x.startswith("PROBLEM:") for x in tree.lines)
    tree.lines = [
        HEADING,
        *tree.lines,
        *debt,
        *mutation,
        "Tree: green." if green else "Tree: not green (see the lines above).",
    ]
    return tree
