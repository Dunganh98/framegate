"""`agentflow doctor`: check that a project's setup works before a feature costs money.

No agents run. Checks the config and the adapter, git and the base branch, the claude CLI, the
workflow files (knowledge file, spec templates, .gitignore), the tools the gates use, the
configured lint / format / typecheck commands (on the whole code), the test selection by spec
ID, the static test scans, and with --full the whole test suite (how long the final regression
takes). Exit 0 when nothing FAILs (WARN is fine), else 1.

Output ends with a brief: a one-line verdict (ready / not ready), then only the FAILs with their
fix and the WARNs that need a decision (warnings about findings in the existing code need none).
`--brief` prints only that (the chat shows it; the full list on request).
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
CODE_MAP_LINES_WARN = 300
SPEC_ID_PROBE = "DOCTOR-R-000"  # an ID no test has: the selection must run and select nothing


@dataclass
class Item:
    status: str  # OK | WARN | FAIL | SKIP
    name: str
    detail: str = ""
    seconds: float | None = None
    #: a WARN that needs no decision (e.g. findings in the existing code): not in the brief
    info: bool = False


def _tail(text: str, n: int = 6) -> str:
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return "\n".join(lines[-n:])


def _sections(text: str) -> list[str]:
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def outdated_templates(cfg: Config) -> dict[str, list[str]]:
    """Spec template copy in the project (`agentflow init` writes one, and `agentflow spec`
    prefers it) -> the `## ` sections agentflow's packaged template has and the copy lacks.
    Compared by heading, so a customized copy is fine as long as it keeps the sections."""
    from agentflow.orchestrator import package_text

    out: dict[str, list[str]] = {}
    for name in ("requirements.md", "design.md", "plan.md"):
        local = cfg.root / cfg.paths.specs / "_template" / name
        if not local.is_file():
            continue  # the packaged template is used
        have = {s.lower() for s in _sections(local.read_text(encoding="utf-8"))}
        lacks = [
            s
            for s in _sections(package_text("templates", "specs", "_template", name))
            if s.lower() not in have
        ]
        if lacks:
            out[name] = lacks
    return out


def brief(items: list[Item]) -> list[str]:
    """The verdict line, then the FAILs and the WARNs that need a decision, one line each."""
    fails = [i for i in items if i.status == "FAIL"]
    decide = [i for i in items if i.status == "WARN" and not i.info]
    if fails:
        verdict = f"Verdict: NOT READY ({len(fails)} failure(s) to fix)"
    else:
        verdict = "Verdict: ready" + (
            f" ({len(decide)} warning(s) need a decision)" if decide else ""
        )
    out = [verdict]
    for i in [*fails, *decide]:
        detail = i.detail.strip().splitlines()
        out.append(f"- {i.status} {i.name}" + (f": {' / '.join(detail[:2])}" if detail else ""))
    if not fails:
        out.append("Next: a feature, or a smoke run (see /agentflow:setup).")
    return out


def run(
    cfg: Config, echo: Callable[[str], None] = print, full: bool = False, only_brief: bool = False
) -> int:
    items: list[Item] = []

    def add(item: Item) -> None:
        items.append(item)
        if only_brief:
            return
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
    cmap = cfg.root / cfg.code_map
    if cmap.is_file():
        n = len(cmap.read_text(encoding="utf-8").splitlines())
        long = n > CODE_MAP_LINES_WARN
        hint = f"{n} lines" + (f" (keep it under {CODE_MAP_LINES_WARN})" if long else "")
        add(Item("WARN" if long else "OK", f"code map {cfg.code_map}", hint))
    else:
        add(
            Item(
                "WARN",
                f"code map {cfg.code_map}",
                "missing: /agentflow:setup drafts it from the code (or set [docs] code_map); "
                "without it the final gate cannot show what changed in it",
            )
        )
    stale = outdated_templates(cfg)
    tpl_notes = [f"missing: {', '.join(missing)}"] if missing else []
    tpl_notes += [f"{name} lacks: {', '.join(secs)}" for name, secs in stale.items()]
    if stale:
        tpl_notes.append(
            "the project's copy wins over agentflow's templates: add these sections, or delete "
            "the copy to use agentflow's (they grow with each retro)"
        )
    add(
        Item(
            "WARN" if missing or stale else "OK",
            f"spec templates in {cfg.paths.specs}/_template/",
            "\n".join(tpl_notes),
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
                    info=True,
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
        add(Item("WARN" if real else "OK", "test scan: smells", detail, info=True))
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
    if not only_brief:
        warns = sum(1 for i in items if i.status == "WARN")
        echo(f"\n{'FAILED' if fails else 'OK'}: {fails} failure(s), {warns} warning(s)\n")
    for line in brief(items):
        echo(line)
    return 1 if fails else 0
