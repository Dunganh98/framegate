"""Chat-facing commands used by the Claude Code plugin skills (/agentflow:feature, :resume,
:status): `watch` (the live view), `status --brief` / the feature overview, `gate-summary` (the
gate digest the chat shows) and `cleanup`. Output is short plain text meant to be shown as is."""

from __future__ import annotations

import pathlib
import re
import shutil
import time
from typing import Any

from agentflow import procs
from agentflow.orchestrator import (
    PROGRESS_CURSOR,
    PROGRESS_LOG,
    Orchestrator,
    WorkflowError,
    provisional_item,
)
from agentflow.report import since_spec_approval
from agentflow.specs import feature_ids, plan_steps
from agentflow.state import GitError, git, load_progress, read_events, tag_exists

#: how long `watch` waits for a run to take the lock before it decides none is running
WATCH_START_WAIT = 30.0
WATCH_POLL = 1.0
DIFF_LINES = 40
#: phase names in run log file names ({stamp}-{feature}-{PHASE}.json / .prompt.md / ...)
RUN_LOG_PHASES = "SPEC_UPDATE|TESTS|TEST_REVIEW|IMPLEMENT|DIAGNOSIS|REVIEW|TRIAGE|PR|RETRO"
TAG_PREFIXES = (
    "spec-approved",
    "tests-approved",
    "spec-update",
    "tests-update",
    "knowledge-update",
)


def _clip(text: str, n: int = 200) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _money(x: float) -> str:
    return f"${x:.2f}"


def _minutes(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


# ---------- live view ----------
def watch(orch: Orchestrator, feature: str, start_wait: float = WATCH_START_WAIT) -> int:
    """Print new lines of {out}/{f}/progress.log as they come (flushed per line) and exit when
    no run of this feature holds the lock any more. Remembers what it printed
    (progress.cursor), so a re-armed watch goes on where the last one stopped."""
    d = orch.fdir(feature)
    log = d / PROGRESS_LOG
    cursor = d / PROGRESS_CURSOR
    try:
        pos = int(cursor.read_text(encoding="utf-8").strip() or 0) if cursor.is_file() else 0
    except ValueError:
        pos = 0
    if log.is_file() and pos > log.stat().st_size:
        pos = 0  # the log was cleaned and started again

    def drain() -> None:
        nonlocal pos
        if not log.is_file():
            return
        with open(log, "rb") as fh:
            fh.seek(pos)
            data = fh.read()
        end = data.rfind(b"\n")
        if end < 0:
            return
        for line in data[: end + 1].decode("utf-8", "replace").splitlines():
            print(line, flush=True)
        pos += end + 1
        cursor.write_text(str(pos), encoding="utf-8")

    t0 = time.time()
    seen = False
    while True:
        drain()
        lock = procs.read_lock(orch.cfg.out_dir)
        if procs.lock_alive(lock) and (lock or {}).get("feature") == feature:
            seen = True
        elif seen or time.time() - t0 >= start_wait:
            time.sleep(WATCH_POLL)  # last lines written just before the lock went
            drain()
            print(f"watch ended: {run_state(orch, feature)}", flush=True)
            return 0
        time.sleep(WATCH_POLL)


def run_state(orch: Orchestrator, feature: str) -> str:
    """One phrase: where the feature stands and what comes next."""
    if not orch.cfg.feature_out(feature).is_dir() and finished(orch, feature):
        tags = feature_tags(orch, feature)
        return "DONE, run files cleaned up" + (
            f"; tags left: {', '.join(tags)} (after the PR merge: agentflow cleanup "
            f"{feature} --tags)"
            if tags
            else ""
        )
    prog = load_progress(orch.cfg, feature)
    lock = procs.read_lock(orch.cfg.out_dir)
    w = prog.get("waiting") or {}
    if procs.lock_alive(lock) and (lock or {}).get("feature") == feature:
        return f"run active in {prog.get('phase')} (pid {(lock or {}).get('pid')})"
    if w.get("kind") == "gate":
        return f"waiting at the {w.get('gate')} gate -> agentflow gate-summary {feature}"
    if prog.get("phase") == "DONE":
        if prog.get("retro") == "skipped":
            return (
                f"DONE without the retro (agentflow approve {feature} still runs it) -> PR / "
                f"agentflow cleanup {feature}"
            )
        return "DONE -> retro proposals / PR / agentflow cleanup " + feature
    if prog.get("interrupted"):
        i = prog["interrupted"]
        return f"interrupted in {i.get('phase')} ({i.get('how')}) -> agentflow run {feature}"
    if not tag_exists(orch.cfg, f"spec-approved/{feature}"):
        return f"SPEC (not approved yet) -> agentflow approve-spec {feature}"
    return f"phase {prog.get('phase')}, not running -> agentflow run {feature}"


def finished(orch: Orchestrator, feature: str) -> bool:
    """The feature has a row in the metrics file (written at DONE)."""
    mfile = orch.cfg.root / orch.cfg.paths.metrics / "workflow_metrics.csv"
    if not mfile.is_file():
        return False
    rows = mfile.read_text(encoding="utf-8").splitlines()[1:]
    return any(r.split(",")[1:2] == [feature] for r in rows)


def feature_tags(orch: Orchestrator, feature: str) -> list[str]:
    return [f"{p}/{feature}" for p in TAG_PREFIXES if tag_exists(orch.cfg, f"{p}/{feature}")]


def last_progress(orch: Orchestrator, feature: str, n: int = 5) -> list[str]:
    log = orch.cfg.feature_out(feature) / PROGRESS_LOG
    if not log.is_file():
        return []
    return log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


# ---------- status ----------
def features(orch: Orchestrator) -> list[str]:
    """Features with run files, and features still in the spec dialogue (spec folder, no
    spec-approved tag, not finished)."""
    out = orch.cfg.out_dir
    names = {p.parent.name for p in out.glob("*/progress.json")} if out.is_dir() else set()
    specs = orch.cfg.root / orch.cfg.paths.specs
    if specs.is_dir():
        for d in specs.iterdir():
            if (
                d.is_dir()
                and not d.name.startswith("_")
                and d.name not in names
                and not tag_exists(orch.cfg, f"spec-approved/{d.name}")
                and not finished(orch, d.name)
            ):
                names.add(d.name)
    return sorted(names)


def overview(orch: Orchestrator) -> int:
    """`agentflow status` without a feature: one line per feature with a progress.json."""
    names = features(orch)
    try:
        branch = git(orch.cfg, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitError:
        branch = "?"
    print(f"Branch: {branch}")
    if not names:
        print("No agentflow features in progress here (start one: /agentflow:feature <name>).")
        return 0
    for f in names:
        print(f"- {f}: {run_state(orch, f)}")
    warning = orch.stale_warning()
    if warning and not warning.startswith("agentflow run is active"):
        print(warning)
    return 0


def brief(orch: Orchestrator, feature: str) -> int:
    """`agentflow status <f> --brief`: state, branch, cost, the last progress lines, next."""
    if not orch.cfg.feature_out(feature).is_dir() and not orch.cfg.spec_dir(feature).is_dir():
        print(f"No feature '{feature}' here (start it: /agentflow:feature {feature}).")
        return 0
    try:
        branch = git(orch.cfg, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitError:
        branch = "?"
    want = orch.cfg.branch_prefix + feature
    print(f"{feature}: {run_state(orch, feature)}")
    note = "" if not orch.cfg.git_branch or branch == want else f" (feature branch: {want})"
    print(f"Branch: {branch}{note}; cost so far {_money(orch.cost_so_far(feature))}")
    lines = last_progress(orch, feature)
    if lines:
        print("Last progress:")
        print("\n".join("  " + x for x in lines))
    return 0  # always 0: the skills inject this output (a non-zero exit aborts a skill)


# ---------- gate summary ----------
def _spec_diff_lines(orch: Orchestrator, feature: str) -> list[str]:
    diff = orch.spec_diff(feature).strip()
    if not diff:
        return ["Spec changes from your answers: none"]
    files = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
    plus = sum(1 for x in diff.splitlines() if x.startswith("+") and not x.startswith("+++"))
    minus = sum(1 for x in diff.splitlines() if x.startswith("-") and not x.startswith("---"))
    body = [
        x
        for x in diff.splitlines()
        if (x.startswith("+") or x.startswith("-")) and not x.startswith(("+++", "---"))
    ]
    out = [
        f"Spec changes from your answers ({', '.join(files)}; +{plus} -{minus}; approving the "
        "gate approves them):"
    ]
    out += ["  " + _clip(x, 160) for x in body[:DIFF_LINES]]
    if len(body) > DIFF_LINES:
        out.append(f"  ... {len(body) - DIFF_LINES} more lines (git diff spec-approved/{feature})")
    return out


def _costs(events: list[dict[str, Any]]) -> tuple[float, float, str]:
    """Total cost, total seconds, and 'TESTS $0.62; IMPLEMENT $1.10 ...' per phase."""
    per: dict[str, float] = {}
    secs = 0.0
    for e in events:
        if e.get("kind") == "agent_run":
            per[str(e.get("phase"))] = per.get(str(e.get("phase")), 0.0) + float(e.get("cost") or 0)
            secs += float(e.get("seconds") or 0)
        elif e.get("kind") in ("gate", "final_gates"):
            secs += float(e.get("seconds") or 0)
    line = "; ".join(f"{p} {_money(c)}" for p, c in per.items())
    return sum(per.values()), secs, line


def _questions(orch: Orchestrator, feature: str) -> list[str]:
    return [_clip(q, 400) for q in orch.open_questions(feature)]


def gate_summary(orch: Orchestrator, feature: str) -> int:
    prog = load_progress(orch.cfg, feature)
    w = prog.get("waiting") or {}
    if w.get("kind") != "gate":
        print(f"{feature} is not at a gate: {run_state(orch, feature)}")
        return 1
    events = since_spec_approval(read_events(orch.cfg, feature))
    total, secs, per_phase = _costs(events)
    gate = str(w.get("gate"))
    out = orch.cfg.paths.out
    lines = [
        f"{gate.upper()} GATE: {feature} ({_money(total)} so far, {_minutes(secs)} of agent and "
        "check time)",
        "State: " + (str(w.get("message") or "-").removeprefix("State: ")),
    ]
    if gate == "tests":
        lines += _tests_gate(orch, feature, prog)
        questions = _questions(orch, feature)
        lines += _numbered("Questions (answer by number)", questions)
        lines += _spec_diff_lines(orch, feature)
        lines += [
            f"Cost per phase: {per_phase or '-'}",
            f"Full gate file: {out}/{feature}/tests_gate.md; answers file: "
            f"{out}/{feature}/answers.md",
            "Reply: approve | reject + what is wrong | answers to the numbered questions",
        ]
    else:
        lines += _final_gate(orch, feature, prog, events)
        parked = [
            _clip(
                f"{p['label']}: {p.get('reason')}"
                + (f"; question: {p['question']}" if p.get("question") else ""),
                900,  # a diagnosis (root cause + proposed fix) must stay readable
            )
            for p in prog.get("parked") or []
        ]
        lines += _numbered("Parked (answer by number to redo them)", parked)
        prov = provisional_item(prog)
        if prov:
            lines += _numbered(
                "Provisional steps (one answer approves all test fixes)",
                [_clip(prov, 1500)],
                start=len(parked) + 1,
            )
        start = len(parked) + (2 if prov else 1)
        lines += _numbered("Open questions", _questions(orch, feature), start=start)
        lines += _spec_diff_lines(orch, feature)
        lines += [
            f"Cost per phase: {per_phase or '-'}",
            f"Full: {out}/{feature}/final_summary.md, {out}/{feature}/final_gates.md; "
            f"answers file: {out}/{feature}/answers.md",
            "Reply: approve (retro, then DONE) | skip the retro (DONE now) | answers to the "
            "numbered items",
        ]
    print("\n".join(lines))
    return 0


def _numbered(title: str, items: list[str], start: int = 1) -> list[str]:
    if not items:
        return [f"{title}: none"]
    return [f"{title}:"] + [f"  {i}. {q}" for i, q in enumerate(items, start)]


def _tests_gate(orch: Orchestrator, feature: str, prog: dict[str, Any]) -> list[str]:
    checks = prog.get("test_checks") or {}
    review = orch.read_test_review(feature)
    lines = [
        "Checks: " + (", ".join(f"{n} {s}" for n, s in checks.items()) or "not run"),
        f"Test reviewer: {review.verdict}, {len(review.objections)} objection(s) left "
        f"(rounds back to the writer: {int(prog.get('round') or 0)} of at most "
        f"{orch.cfg.test_review_rounds})",
    ]
    lines += ["  - " + _clip(o) for o in review.objections[:8]]
    if len(review.objections) > 8:
        lines.append(f"  ... {len(review.objections) - 8} more (test_review.md)")
    try:
        tags = orch.adapter.collect_req_tags()
    except Exception as e:  # a broken test file must not hide the gate
        return [*lines, f"Spec IDs -> tests: scan failed ({type(e).__name__}: {e})"]
    ids = sorted(feature_ids(orch.cfg, feature))
    missing = [i for i in ids if not tags.get(i)]
    counts = ", ".join(f"{i} ({len(tags.get(i, []))})" for i in ids)
    n_tests = len({t for i in ids for t in tags.get(i, [])})
    lines.append(
        f"Spec IDs -> tests: {len(ids) - len(missing)}/{len(ids)} IDs have tests, {n_tests} tests"
        + (f"; NO TESTS: {', '.join(missing)}" if missing else "")
    )
    lines.append("  " + counts)
    return lines


def _final_gate(
    orch: Orchestrator, feature: str, prog: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    fg = [e for e in events if e.get("kind") == "final_gates"]
    last = fg[-1] if fg else {}
    verdict = "PASSED" if last.get("passed") else "FAILED" if last else "not run"
    if last.get("failed"):
        verdict += f" ({', '.join(last['failed'])})"
    if last.get("skipped"):
        verdict += f"; skipped: {', '.join(last['skipped'])}"
    steps = plan_steps(orch.cfg, feature)
    done = len(prog.get("completed_steps") or [])
    failed: dict[str, int] = {}
    for e in events:
        if e.get("kind") == "gate" and not e.get("passed"):
            failed[str(e.get("step"))] = failed.get(str(e.get("step")), 0) + 1
    triage = [e for e in events if e.get("kind") == "triage"]
    findings = triage[-1].get("findings") if triage else []
    valid = sum(1 for r in findings or [] if r.get("verdict") == "VALID")
    assumptions = orch.assumptions(feature)
    extra = last.get("extra") or {}
    lines = [
        f"Final gates: {verdict}",
        *(
            [
                "Project checks (not blocking; output in final_gates.md): "
                + ", ".join(f"{n} {'OK' if c == 0 else f'exit {c}'}" for n, c in extra.items())
            ]
            if extra
            else []
        ),
        f"Plan steps: {done}/{len(steps)} green, {len(prog.get('parked') or [])} parked"
        + (
            f", {len(prog['provisional'])} of them provisional (tests contested)"
            if prog.get("provisional")
            else ""
        ),
        "Failed attempts: "
        + (
            f"{sum(failed.values())} (" + ", ".join(f"{k}: {v}" for k, v in failed.items()) + ")"
            if failed
            else "0"
        ),
        f"Review findings: {len(findings or [])} ({valid} valid, "
        f"{len(findings or []) - valid} dismissed)",
        f"Assumptions: {len(assumptions)}",
    ]
    lines += [f"  - [{a.spec_id}] {_clip(a.text, 160)}" for a in assumptions[:10]]
    if len(assumptions) > 10:
        lines.append(f"  ... {len(assumptions) - 10} more (steps.md)")
    summary = orch.cfg.feature_out(feature) / "final_summary.md"
    if summary.is_file():
        para = _first_paragraph(summary.read_text(encoding="utf-8"))
        if para:
            lines.append(f"Summary: {_clip(para, 600)}")
    return lines


def _first_paragraph(text: str) -> str:
    """The first non-heading paragraph of a markdown text."""
    para: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or (not s and not para):
            if para:
                break
            continue
        if not s:
            break
        para.append(s)
    return " ".join(para)


# ---------- cleanup ----------
def run_logs(orch: Orchestrator, feature: str) -> list[pathlib.Path]:
    runs = orch.cfg.out_dir / "runs"
    if not runs.is_dir():
        return []
    pat = re.compile(rf"^\d{{8}}-\d{{6}}-\d{{6}}-{re.escape(feature)}-({RUN_LOG_PHASES})\.")
    return sorted(p for p in runs.iterdir() if p.is_file() and pat.match(p.name))


def cleanup(orch: Orchestrator, feature: str, tags: bool = False, force: bool = False) -> int:
    """Delete {out}/{f}/ and the feature's run logs in {out}/runs/ (specs/<f>/, the metrics row
    and the shared events.jsonl stay). --tags also deletes the feature's local tags. Refuses
    while a run of the feature is active or (if {out}/{f}/ still exists) before DONE."""
    lock = procs.read_lock(orch.cfg.out_dir)
    if procs.lock_alive(lock) and (lock or {}).get("feature") == feature:
        raise WorkflowError(f"a run of {feature} is active (pid {(lock or {}).get('pid')})")
    fout = orch.cfg.feature_out(feature)
    if fout.is_dir() and not force:
        phase = load_progress(orch.cfg, feature).get("phase")
        if phase != "DONE":
            raise WorkflowError(
                f"{feature} is not DONE (phase {phase}); finish it first or use --force"
            )
    logs = run_logs(orch, feature)
    removed = []
    if fout.is_dir():
        shutil.rmtree(fout)
        removed.append(f"{orch.cfg.paths.out}/{feature}/")
    for p in logs:
        p.unlink()
    if logs:
        removed.append(f"{len(logs)} run log file(s) in {orch.cfg.paths.out}/runs/")
    print("Removed: " + (", ".join(removed) if removed else "nothing (already clean)"))
    print(
        f"Kept: {orch.cfg.paths.specs}/{feature}/, the metrics row, "
        f"{orch.cfg.paths.out}/events.jsonl"
    )
    if tags:
        names = feature_tags(orch, feature)
        for name in names:
            git(orch.cfg, "tag", "-d", name)
        print("Tags deleted (local): " + (", ".join(names) or "none"))
        if names:
            print("On the remote (if pushed): git push origin --delete " + " ".join(names))
    else:
        left = feature_tags(orch, feature)
        if left:
            print(
                f"Tags kept for the CI frame diff: {', '.join(left)} (after the PR is merged: "
                f"agentflow cleanup {feature} --tags)"
            )
    return 0
