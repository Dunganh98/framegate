"""`agentflow doctor`: check that a project's setup works before a feature costs money.

No agents run. Checks the config and the adapter, git and the base branch, the claude CLI, the
workflow files (knowledge file, spec templates, .gitignore), the tools the gates use, the
configured lint / format / typecheck commands (on the whole code), the test selection by spec
ID, the static test scans, and with --full the whole test suite (how long the final regression
takes). Exit 0 when nothing FAILs (WARN is fine), else 1.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from agentflow.adapters import get_adapter
from agentflow.adapters.base import LanguageAdapter
from agentflow.config import Config, ConfigError
from agentflow.state import GitError, git

KNOWLEDGE_LINES_WARN = 150
SPEC_ID_PROBE = "DOCTOR-R-000"  # an ID no test has: the selection must run and select nothing


@dataclass
class Item:
    status: str  # OK | WARN | FAIL | SKIP
    name: str
    detail: str = ""
    seconds: float | None = None


def _tail(text: str, n: int = 6) -> str:
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return "\n".join(lines[-n:])


def run(cfg: Config, echo: Callable[[str], None] = print, full: bool = False) -> int:
    items: list[Item] = []

    def add(item: Item) -> None:
        items.append(item)
        secs = f" ({item.seconds:.0f} s)" if item.seconds is not None else ""
        echo(f"[{item.status}] {item.name}{secs}")
        for line in item.detail.splitlines():
            echo(f"       {line}")

    try:
        adapter: LanguageAdapter = get_adapter(cfg)
    except ConfigError as e:
        add(Item("FAIL", f"config: language '{cfg.language}'", str(e)))
        return 1
    add(Item("OK", f"config: language {cfg.language} ({type(adapter).__name__})"))

    try:
        git(cfg, "rev-parse", "--git-dir")
        try:
            git(cfg, "rev-parse", "--verify", "--quiet", cfg.git_base)
            add(Item("OK", f"git: base branch '{cfg.git_base}'"))
        except GitError:
            add(
                Item(
                    "WARN",
                    f"git: base branch '{cfg.git_base}' not found",
                    "set [git] base to the branch features start from",
                )
            )
    except GitError as e:
        add(Item("FAIL", "git: not a git repository", str(e)))

    from agentflow.orchestrator import Orchestrator, WorkflowError

    try:
        cmd = Orchestrator(cfg, adapter, echo=lambda s: None).claude_cmd()
        add(Item("OK", "claude CLI", " ".join(cmd)))
    except WorkflowError as e:
        add(Item("FAIL", "claude CLI", str(e)))

    kfile = cfg.root / cfg.knowledge
    if not kfile.is_file():
        add(Item("WARN", f"knowledge file {cfg.knowledge}", "missing: `agentflow init` creates it"))
    else:
        n = len(kfile.read_text(encoding="utf-8").splitlines())
        status = "WARN" if n > KNOWLEDGE_LINES_WARN else "OK"
        hint = f"{n} lines" + (
            f" (keep it under {KNOWLEDGE_LINES_WARN})" if status == "WARN" else ""
        )
        add(Item(status, f"knowledge file {cfg.knowledge}", hint))
    missing = [
        n
        for n in ("requirements.md", "design.md", "plan.md")
        if not (cfg.root / cfg.paths.specs / "_template" / n).is_file()
    ]
    add(
        Item(
            "WARN" if missing else "OK",
            f"spec templates in {cfg.paths.specs}/_template/",
            f"missing: {', '.join(missing)}" if missing else "",
        )
    )
    want = [cfg.paths.out + "/", cfg.paths.phase_file, cfg.paths.reqs_file]
    want += adapter.gitignore_entries()

    def ignored(path: str) -> bool:  # any .gitignore rule that covers it (e.g. out/ for out/x/)
        probe = path.rstrip("/") + ("/probe" if path.endswith("/") else "")
        try:
            git(cfg, "check-ignore", "-q", "--no-index", probe)
            return True
        except GitError:
            return False

    absent = [w for w in want if not ignored(w)]
    add(
        Item(
            "WARN" if absent else "OK",
            ".gitignore",
            f"add: {', '.join(absent)} (the workflow commits with `git add -A`)" if absent else "",
        )
    )

    for name, found, detail in adapter.tool_report():
        add(Item("OK" if found else "WARN", f"tool: {name}", detail))

    for check in adapter.lint_checks(None):
        t0 = time.monotonic()
        res = check.run()
        secs = time.monotonic() - t0
        missing_tool = "Tool not found" in res.output or "Could not start" in res.output
        if res.status == "PASS":
            add(Item("OK", f"gate command: {check.name}", seconds=secs))
        elif missing_tool:
            add(Item("FAIL", f"gate command: {check.name}", _tail(res.output), secs))
        else:
            add(
                Item(
                    "WARN",
                    f"gate command: {check.name} (findings in the existing code)",
                    _tail(res.output)
                    + "\nin a run only the changed files are checked; fix these or expect them "
                    "in features that touch those files",
                    secs,
                )
            )

    t0 = time.monotonic()
    probe = adapter.run_tests(
        req_ids=[SPEC_ID_PROBE], junit=cfg.out_dir / "doctor" / "spec_id_selection.xml"
    )
    secs = time.monotonic() - t0
    selected = len(probe.cases or [])
    if probe.status in ("no_tests", "passed") and selected == 0:
        add(Item("OK", "test selection by spec ID", adapter.test_command([SPEC_ID_PROBE]), secs))
    else:
        add(
            Item(
                "FAIL",
                "test selection by spec ID",
                f"`{adapter.test_command([SPEC_ID_PROBE])}`: status {probe.status}, "
                f"{selected} test(s) selected for an ID no test has\n" + _tail(probe.output),
                secs,
            )
        )

    try:
        tags = adapter.collect_req_tags()
        add(Item("OK", "test scan: spec-ID tags", f"{len(tags)} spec ID(s) tagged in the tests"))
    except Exception as e:  # a test file the scanner cannot read
        add(Item("FAIL", "test scan: spec-ID tags", f"{type(e).__name__}: {e}"))
    try:
        smells = adapter.test_smells()
        real = [s for s in smells if not s.startswith("NOTE")]
        notes = len(smells) - len(real)
        detail = f"{len(real)} in the existing tests, e.g. {real[0]}" if real else ""
        if notes:
            detail += ("; " if detail else "") + f"{notes} note(s) (heuristics, not blocking)"
        detail += "\nonly test files a feature changes are checked in a run" if real else ""
        add(Item("WARN" if real else "OK", "test scan: smells", detail))
    except Exception as e:
        add(Item("FAIL", "test scan: smells", f"{type(e).__name__}: {e}"))

    if full:
        t0 = time.monotonic()
        suite = adapter.run_tests(junit=cfg.out_dir / "doctor" / "whole_suite.xml")
        secs = time.monotonic() - t0
        status = {"passed": "OK", "no_tests": "WARN"}.get(suite.status, "FAIL")
        detail = {"OK": f"{len(suite.cases or [])} test(s)", "WARN": "no tests yet"}.get(
            status, f"status {suite.status}\n" + _tail(suite.output, 12)
        )
        add(Item(status, "whole test suite (the final gate's regression run)", detail, secs))
    else:
        add(Item("SKIP", "whole test suite", "run `agentflow doctor --full` to time it"))

    fails = sum(1 for i in items if i.status == "FAIL")
    warns = sum(1 for i in items if i.status == "WARN")
    echo(
        f"\n{'FAILED' if fails else 'OK'}: {fails} failure(s), {warns} warning(s)"
        + ("" if fails else ". Next: a feature, or a smoke run (see /agentflow:setup).")
    )
    return 1 if fails else 0
