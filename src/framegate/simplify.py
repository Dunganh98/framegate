"""The simplify pass: after the last plan step and before the review, one agent run whose
only job is to make the task's diff smaller with the behaviour unchanged.

The frame: tests and spec are frozen as for the implementer; only source files the task
already changed may be touched; afterwards the checks of a run after an implementation step
run (lint, frame check, the task's tests, the existing tests that name its code, diff
coverage of the lines the pass wrote). Green: the pass is its own commit. Anything else (a
red check, a file outside the list, an interrupted run): the changes are dropped and the
run goes on with the code as it was. It never stops and never asks.

Phase and role: SIMPLIFY (`roles/simplify.md`), a writer-tier model with its own caps.
Switch: `[simplify] enabled`; no pass when the task added fewer source lines than
`[simplify] min_lines`, in a refactor (its diff is the structure the plan asked for), in a
port (the code follows its reference) or while work waits for an answer. Logged as a
`simplify` event with the size of the task's source diff before and after.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from framegate import references
from framegate.checks.frame_diff import approved_tag
from framegate.config import Config
from framegate.specs import feature_mode
from framegate.state import (
    GitError,
    clear_reqs,
    commit,
    dirty_lines,
    git,
    log_event,
    save_progress,
    tag_exists,
)

if TYPE_CHECKING:
    from framegate.orchestrator import Orchestrator

PHASE = "SIMPLIFY"
LABEL = "simplify"
TASK = (
    "Feature '{f}': the task's code is written and its tests pass. Make its diff smaller and "
    "simpler; the behaviour stays as it is. The task's source diff: {diff}\n"
    "You may change only these files: {files}\n"
    "Tests, specs and every other file are frozen. Check with: {check}\n"
    "Nothing to simplify: change nothing. Do not ask and do not park."
)


def diff_size(cfg: Config, tag: str) -> tuple[int, list[str]]:
    """(added + removed source lines of the task against `tag`, the source files it
    changed that exist)."""
    numstat = git(cfg, "diff", "--numstat", "--no-renames", tag, "--", *cfg.src_roots)
    lines = 0
    for row in numstat.splitlines():
        parts = row.split("\t")
        if len(parts) >= 3:
            lines += sum(int(x) for x in parts[:2] if x.isdigit())
    names = git(cfg, "diff", "--name-only", "--no-renames", tag, "--", *cfg.src_roots)
    return lines, sorted(n for n in names.splitlines() if (cfg.root / n).is_file())


def added_lines(cfg: Config, tag: str) -> int:
    numstat = git(cfg, "diff", "--numstat", "--no-renames", tag, "--", *cfg.src_roots)
    rows = [row.split("\t") for row in numstat.splitlines()]
    return sum(int(r[0]) for r in rows if len(r) >= 3 and r[0].isdigit())


def skip_reason(orch: Orchestrator, feature: str, prog: dict[str, Any]) -> str:
    """Why no pass runs ("" = it runs)."""
    cfg = orch.cfg
    if feature_mode(cfg, feature) == "refactor":
        return "a refactor: its diff is the structure the plan asked for"
    if references.feature_port(cfg, feature):
        return "a port: the code follows its reference"
    if prog.get("parked") or prog.get("provisional"):
        return "work waits for an answer at the final gate"
    tag = approved_tag(feature)
    if not tag_exists(cfg, tag):
        return "the tests are not approved yet"
    added = added_lines(cfg, tag)
    if added < cfg.simplify_min_lines:
        return f"the task added {added} source line(s) (fewer than {cfg.simplify_min_lines})"
    return ""


def _dirty(cfg: Config) -> set[str]:
    out = cfg.paths.out.rstrip("/") + "/"
    return {
        line[3:]
        for line in dirty_lines(cfg, untracked="all")
        if not line[3:].startswith(out)
        and line[3:] not in (cfg.paths.phase_file, cfg.paths.reqs_file)
    }


def restore(cfg: Config, paths: set[str]) -> None:
    """Put `paths` back as the last commit has them; a file it does not have is removed."""
    for rel in sorted(paths):
        try:
            git(cfg, "cat-file", "-e", f"HEAD:{rel}")
        except GitError:
            (cfg.root / rel).unlink(missing_ok=True)
            continue
        git(cfg, "checkout", "-q", "HEAD", "--", rel)


def _done(
    orch: Orchestrator, feature: str, prog: dict[str, Any], status: str, text: str, **data: Any
) -> None:
    prog["simplify"] = {"status": status, "text": text}
    save_progress(orch.cfg, feature, prog)
    log_event(orch.cfg, feature, "simplify", status=status, text=text, **data)
    orch.progress(feature, f"[{LABEL}] {text}")


def run(orch: Orchestrator, feature: str, prog: dict[str, Any]) -> None:
    """The pass, once per task (prog["simplify"] says how it ended). Never raises for a
    failed pass: every way out but "kept" leaves the code as the last commit has it."""
    cfg = orch.cfg
    if not cfg.simplify_enabled:
        return
    state = prog.get("simplify") or {}
    if state.get("status") == "running":  # an interrupted pass: its half-done edits go
        restore(cfg, _dirty(cfg) - set(state.get("dirty") or []))
        _done(orch, feature, prog, "dropped", "dropped: the run was interrupted")
        return
    if state:
        return
    try:
        why = skip_reason(orch, feature, prog)
        if why:
            _done(orch, feature, prog, "skipped", f"skipped: {why}")
            return
        tag = approved_tag(feature)
        before, files = diff_size(cfg, tag)
        base = git(cfg, "rev-parse", "HEAD").strip()
    except GitError as e:
        _done(orch, feature, prog, "skipped", f"skipped: git could not tell the diff ({e})")
        return
    was_dirty = _dirty(cfg)
    questions = orch.fdir(feature) / "questions.md"
    had_questions = questions.is_file()
    reqs = orch.fix_ids(feature, prog)
    task = orch.task_text(
        PHASE,
        feature,
        diff=f"git diff {tag} -- {' '.join(files)}",
        files=", ".join(files),
        check=orch.fix_check(feature, reqs),
    )
    prog["simplify"] = {"status": "running", "dirty": sorted(was_dirty)}
    save_progress(cfg, feature, prog)
    orch.progress(feature, f"[{LABEL}] start ({before} source diff lines in {len(files)} file(s))")
    orch.agent(PHASE, feature, prog, task, role=LABEL, step=LABEL, retry_turn_cap=False)
    orch.take_park_request(feature)  # the pass never parks
    if not had_questions:
        questions.unlink(missing_ok=True)  # and never asks
    changed = _dirty(cfg) - was_dirty
    if not changed:
        _done(orch, feature, prog, "unchanged", f"nothing changed ({before} source diff lines)")
        return
    outside = sorted(changed - set(files))
    if outside:
        restore(cfg, changed)
        text = "dropped: it changed files outside the task's source files: " + ", ".join(outside)
        _done(orch, feature, prog, "dropped", text, outside=outside, before=before)
        return
    orch.set_check_scope(prog, reqs, feature)
    try:
        rep = orch.gate(feature, prog, "fix", cover_base=base)
    finally:
        clear_reqs(cfg)
    if not rep.ok:
        restore(cfg, _dirty(cfg) - was_dirty)
        text = f"dropped: {', '.join(rep.failed)} failed after it; the code stays as it was"
        _done(orch, feature, prog, "dropped", text, failed=rep.failed, before=before)
        return
    orch.commit_format(feature, LABEL, sorted(orch.adapter.format_beyond))
    commit(cfg, f"{feature}: {LABEL}")
    after, _ = diff_size(cfg, tag)
    _done(
        orch,
        feature,
        prog,
        "kept",
        f"kept: the task's source diff went from {before} to {after} line(s)",
        before=before,
        after=after,
        files=sorted(changed),
    )


def facts_lines(events: list[dict[str, Any]]) -> list[str]:
    """final_facts.md: how the pass ended ([] = none ran in this spec version)."""
    last = next((e for e in reversed(events) if e.get("kind") == "simplify"), None)
    if last is None or last.get("status") == "skipped":
        return []
    out = ["## Simplify pass (one agent run before the review; behaviour unchanged)", ""]
    if last.get("status") == "kept":
        out.append(
            f"- Source diff lines of the task (added + removed): {last.get('before')} before, "
            f"{last.get('after')} after; its own commit."
        )
    else:
        out.append(f"- {str(last.get('text') or last.get('status')).capitalize()}.")
    return out
