"""Chat-facing commands used by the Claude Code plugin skills (/framegate:feature, :resume,
:status): `watch` (the live view), `status --brief` / the feature overview, `gate-summary` (the
gate brief the chat shows: decision first, plain words) and `cleanup`. Output is short plain
text meant to be shown as is."""

from __future__ import annotations

import pathlib
import re
import shutil
import time
from typing import Any

from framegate import brief as briefs
from framegate import procs, references, risk
from framegate.orchestrator import (
    PROGRESS_CURSOR,
    PROGRESS_LOG,
    Orchestrator,
    WorkflowError,
)
from framegate.report import since_spec_approval
from framegate.specs import feature_ids, plan_steps
from framegate.state import (
    GitError,
    delete_marker,
    git,
    load_progress,
    read_events,
    tag_exists,
)

#: how long `watch` waits for a run to take the lock before it decides none is running
WATCH_START_WAIT = 30.0
WATCH_POLL = 1.0
DIFF_LINES = 12
#: most lines per plain-words section of a gate brief (the rest on request)
BRIEF_SECTION_LINES = 8
#: Proven / Not proven at the final gate: the most important points (the rest on request)
BRIEF_PROVEN_LINES = 3
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
            f"; tags left: {', '.join(tags)} (after the PR merge: framegate cleanup "
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
        return f"waiting at the {w.get('gate')} gate -> framegate gate-summary {feature}"
    if prog.get("phase") == "DONE":
        if prog.get("retro") == "skipped":
            return (
                f"DONE without the retro (framegate approve {feature} still runs it) -> PR / "
                f"framegate cleanup {feature}"
            )
        return "DONE -> retro proposals / PR / framegate cleanup " + feature
    if prog.get("interrupted"):
        i = prog["interrupted"]
        return f"interrupted in {i.get('phase')} ({i.get('how')}) -> framegate run {feature}"
    if not tag_exists(orch.cfg, f"spec-approved/{feature}"):
        return f"SPEC (not approved yet) -> framegate approve-spec {feature}"
    return f"phase {prog.get('phase')}, not running -> framegate run {feature}"


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
    """`framegate status` without a feature: one line per feature with a progress.json."""
    names = features(orch)
    try:
        branch = git(orch.cfg, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitError:
        branch = "?"
    print(f"Branch: {branch}")
    if not names:
        print("No framegate features in progress here (start one: /framegate:feature <name>).")
        return 0
    for f in names:
        print(f"- {f}: {run_state(orch, f)}")
    warning = orch.stale_warning()
    if warning and not warning.startswith("framegate run is active"):
        print(warning)
    return 0


def brief(orch: Orchestrator, feature: str) -> int:
    """`framegate status <f> --brief`: state, branch, cost, the last progress lines, next."""
    if not orch.cfg.feature_out(feature).is_dir() and not orch.cfg.spec_dir(feature).is_dir():
        print(f"No feature '{feature}' here (start it: /framegate:feature {feature}).")
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
        return []
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


def _costs(events: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Total cost, agent seconds, check seconds (step and final gates)."""
    cost = agent = checks = 0.0
    for e in events:
        if e.get("kind") == "agent_run":
            cost += float(e.get("cost") or 0)
            agent += float(e.get("seconds") or 0)
        elif e.get("kind") in ("gate", "final_gates"):
            checks += float(e.get("seconds") or 0)
    return cost, agent, checks


brief_sections = briefs.sections


def _section(sections: dict[str, list[str]], *names: str) -> list[str]:
    for key, lines in sections.items():
        if any(key.startswith(n) for n in names):
            return lines
    return []


def _block(lines: list[str], limit: int = BRIEF_SECTION_LINES, indent: str = "  ") -> list[str]:
    shown = [indent + _clip(x.strip(), 300) for x in lines[:limit]]
    if len(lines) > limit:
        shown.append(f"{indent}... {len(lines) - limit} more line(s) (on request)")
    return shown


def _titled(title: str, lines: list[str], limit: int = BRIEF_SECTION_LINES) -> list[str]:
    """A brief section: `Title: line` when it has one line, else the title and its lines."""
    head = title if title.endswith("?") else title + ":"
    if len(lines) == 1:
        return [_clip(f"{head} {lines[0].strip().lstrip('-* ').strip()}", 300)]
    return [head, *_block(lines, limit)]


def _items_block(title: str, texts: list[str]) -> list[str]:
    if not texts:
        return []
    out = [f"{title}:"]
    for i, text in enumerate(texts, 1):
        first, *rest = text.splitlines() or [""]
        out.append(f"  {i}. {_clip(first, 600)}")
        out += [f"     {_clip(x, 300)}" for x in rest[:6]]
    return out


def gate_summary(orch: Orchestrator, feature: str) -> int:
    """The gate brief the chat shows: the decision and its default first, then about one
    screen in plain words; the gate files hold the detail (shown on request)."""
    prog = load_progress(orch.cfg, feature)
    w = prog.get("waiting") or {}
    if w.get("kind") != "gate":
        print(f"{feature} is not at a gate: {run_state(orch, feature)}")
        return 1
    events = since_spec_approval(read_events(orch.cfg, feature))
    lines = (
        _tests_brief(orch, feature, prog)
        if w.get("gate") == "tests"
        else _final_brief(orch, feature, prog, events)
    )
    print("\n".join(lines))
    return 0


def _numbers(what: str, n: int) -> str:
    return f"{what} 1" if n == 1 else f"{what}s 1-{n}"


def _money_time(events: list[dict[str, Any]]) -> str:
    cost, agent, checks = _costs(events)
    return f"{_money(cost)}, {_minutes(agent)} of agent time + {_minutes(checks)} of checks"


def _tests_brief(orch: Orchestrator, feature: str, prog: dict[str, Any]) -> list[str]:
    out = orch.cfg.paths.out
    questions = orch.open_questions(feature)
    review = orch.read_test_review(feature)
    events = since_spec_approval(read_events(orch.cfg, feature))
    lines = [f"TESTS GATE: {feature} ({_money_time(events)})"]
    if questions:
        lines.append(
            f'Decision: answer {_numbers("question", len(questions))} ("ok" takes every '
            "default); the test writer and reviewer then re-run with your answers."
        )
    else:
        lines.append('Decision: "ok" approves the tests (default); or say what is wrong.')
    d = orch.cfg.feature_out(feature)
    tr = d / "test_review.md"
    checks = _section(
        brief_sections(tr.read_text(encoding="utf-8") if tr.is_file() else ""),
        "what the tests check",
    )
    if not checks:
        checks = _summary_column(d / "test_summary.md")
    lines.append("What the tests check:")
    lines += _block(checks) if checks else ["  (not written; on request: test_summary.md)"]
    if review.objections:
        lines.append(
            f"Test reviewer: {len(review.objections)} objection(s) left after "
            f"{int(prog.get('round') or 0)} round(s) with the writer:"
        )
        lines += _block(review.objections, 6)
    else:
        lines.append("Test reviewer: no objections.")
    lines.append(_tests_checks_line(orch, feature, prog))
    lines += _items_block('Questions (answer by number; "ok" takes every default)', questions)
    lines += _spec_diff_lines(orch, feature)
    lines.append(
        f"On request: the full gate file ({out}/{feature}/tests_gate.md), the test table "
        f"({out}/{feature}/test_summary.md), the cost per phase."
    )
    return lines


def _summary_column(path: pathlib.Path) -> list[str]:
    """The "What it checks" column of test_summary.md (plain words, without the IDs)."""
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[2] and not set(cells[2]) <= set("-: "):
            if cells[2].lower().startswith("what it checks"):
                continue
            rows.append("- " + cells[2])
    return rows


def _tests_checks_line(orch: Orchestrator, feature: str, prog: dict[str, Any]) -> str:
    checks = prog.get("test_checks") or {}
    failed = [n for n, s in checks.items() if s != "PASS"]
    text = (
        "Mechanical checks: " + ("all pass" if checks and not failed else "not run")
        if not failed
        else "Mechanical checks FAILED: " + ", ".join(failed)
    )
    try:
        tags = orch.adapter.collect_req_tags()
    except Exception as e:  # a broken test file must not hide the gate
        return f"{text}; test scan failed ({type(e).__name__}: {e})"
    ids = sorted(feature_ids(orch.cfg, feature))
    missing = [i for i in ids if not tags.get(i)]
    n_tests = len({t for i in ids for t in tags.get(i, [])})
    return (
        f"{text}; {n_tests} test(s), "
        + (
            f"NO TESTS for {', '.join(missing)} ({_spec_meaning(orch, feature, missing)})"
            if missing
            else "every requirement has tests"
        )
        + "."
    )


def _spec_meaning(orch: Orchestrator, feature: str, ids: list[str]) -> str:
    """The words a spec ID stands for (its requirements.md row), for the human."""
    req = orch.cfg.spec_dir(feature) / "requirements.md"
    text = req.read_text(encoding="utf-8") if req.is_file() else ""
    parts = []
    for i in ids:
        row = next((x for x in text.splitlines() if i in x), "")
        words = " ".join(c.strip() for c in row.strip().strip("|").split("|")[1:2])
        parts.append(f"{i}: {_clip(words, 80)}" if words else i)
    return "; ".join(parts)


def _final_brief(
    orch: Orchestrator, feature: str, prog: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    out = orch.cfg.paths.out
    sections = orch.final_brief_sections(feature)
    plan = orch.final_brief_plan(feature, prog)
    questions = [text for _, text in plan.questions]
    prov = bool(prog.get("provisional"))
    lines = [f"FINAL GATE: {feature}"]
    if questions:
        lines.append(
            f'Decision: answer {_numbers("question", len(questions))} ("ok" takes every '
            "default below)"
            + (
                "; the provisional-steps item must be answered before the gate can be approved."
                if prov
                else '; "approve as is" leaves them open (retro, then DONE).'
            )
        )
    else:
        lines.append(
            'Decision: "ok" approves (default: retro, then DONE); "no retro" finishes now.'
        )
    followed = _section(sections, "plan followed", "plan")
    lines += _titled("Plan followed?", followed or _step_lines(orch, feature, prog, events))
    lines += _outcomes(orch, feature, prog, events)
    rated = prog.get("risk") or {}
    if rated.get("level"):
        rating = risk.Assessment(rated["level"], list(rated.get("reasons") or []))
        lines.append(rating.line(auto=orch.cfg.final_gate == "auto"))
    struggles = _section(sections, "struggles")
    lines += _titled("Struggles", struggles or _struggle_lines(prog, events))
    for title, names in (
        ("Proven by the tests", ("proven",)),
        ("Not proven (check it by using it)", ("not proven",)),
    ):
        found = _section(sections, *names)
        if prog.get("review_skipped"):
            continue  # nothing built: nothing proven, nothing to try
        lines += (
            _titled(title, found, BRIEF_PROVEN_LINES)
            if found
            else [f"{title}: (not written; on request: final_summary.md)"]
        )
    lines.append(_code_map_line(orch, prog, _section(sections, "code map")))
    lines += _items_block('Questions (answer by number; "ok" takes every default)', questions)
    decided = [text for _, text in plan.decided]
    if decided:
        lines.append("Decisions I made:")
        lines += [f"  - {_clip(x.splitlines()[0], 300)}" for x in decided]
    if plan.left_out:
        lines.append(
            f"Left out of the questions: {len(plan.left_out)} gate item(s) (asked twice, "
            "answered, or a note; on request)."
        )
    lines += _spec_diff_lines(orch, feature)
    lines.append(
        f"On request: the full summary ({out}/{feature}/final_summary.md), the gate report "
        f"({out}/{feature}/final_gates.md), the assumptions, the cost per phase."
    )
    return lines


def _step_lines(
    orch: Orchestrator, feature: str, prog: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    """Generated "Plan followed?" lines (when the summary agent wrote no brief)."""
    parked = {n for p in prog.get("parked") or [] for n in (p.get("steps") or [p.get("step")])}
    prov = {n for p in prog.get("provisional") or [] for n in (p.get("steps") or [p.get("step")])}
    done = set(prog.get("completed_steps") or [])
    steps = plan_steps(orch.cfg, feature)
    if steps and all(s.num in done and s.num not in prov for s in steps):
        return [f"All {len(steps)} step(s) done."]
    out = []
    for s in steps:
        state = (
            "kept as provisional (its tests are contested)"
            if s.num in prov
            else "done"
            if s.num in done
            else "parked (see the items)"
            if s.num in parked
            else "not run"
        )
        out.append(f"- Step {s.num} ({s.title}): {state}")
    return out


def _struggle_lines(prog: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    failed: dict[str, int] = {}
    for e in events:
        if e.get("kind") == "gate" and not e.get("passed"):
            failed[str(e.get("step"))] = failed.get(str(e.get("step")), 0) + 1
    out = [f"- {k}: {v} failed attempt(s)" for k, v in failed.items()]
    out += [f"- {p['label']}: parked ({p.get('reason')})" for p in prog.get("parked") or []]
    out += [
        f"- final gates: {', '.join(e.get('failed') or [])} failed, repaired by the "
        f"{'implementer' if e.get('owner') == 'src' else 'test writer'}"
        for e in events
        if e.get("kind") == "final_repair" and e.get("changed")
    ]
    return out or ["No struggles."]


def _outcomes(
    orch: Orchestrator, feature: str, prog: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    fg = [e for e in events if e.get("kind") == "final_gates"]
    last = fg[-1] if fg else {}
    verdict = (
        "nothing built, so no review and no final checks"
        if prog.get("review_skipped")
        else "final gates PASSED"
        if last.get("passed")
        else f"final gates FAILED ({', '.join(last.get('failed') or [])})"
        if last
        else "final gates not run"
    )
    if last.get("skipped"):
        verdict += f"; skipped: {', '.join(last['skipped'])}"
    scores = [
        f"{name} {_clip(res, 90)}"
        for name, res in (last.get("results") or {}).items()
        if ("coverage" in name or "mutation" in name)
        and "regression" not in name
        and not res.startswith("SKIPPED")
    ]
    fails = sum(1 for e in events if e.get("kind") == "gate" and not e.get("passed"))
    lines = [
        f"Outcomes: {verdict}; {fails} failed attempt(s), {len(prog.get('parked') or [])} "
        f"parked; {_money_time(events)}."
    ]
    if scores:
        lines.append("  " + "; ".join(scores))
    extra = last.get("extra") or {}
    if extra:
        lines.append(
            "  project checks (not blocking): "
            + ", ".join(f"{n} {'OK' if c == 0 else f'exit {c}'}" for n, c in extra.items())
        )
    fidelity = references.fidelity_summary(last.get("fidelity") or {})
    if fidelity:
        lines.append("  " + fidelity)
    shape = last.get("structure") or {}
    longest = shape.get("longest") or []
    if shape.get("files"):
        biggest = sorted((shape.get("files") or {}).items(), key=lambda x: -x[1])[:3]
        lines.append(
            "  structure (not blocking): "
            + ", ".join(f"{pathlib.PurePosixPath(p).name} {n} lines" for p, n in biggest)
            + (
                "; longest method "
                + f"{longest[0][1]} ({longest[0][2]} lines, "
                + f"{pathlib.PurePosixPath(longest[0][0]).name})"
                if longest
                else ""
            )
        )
    return lines


def _code_map_line(orch: Orchestrator, prog: dict[str, Any], written: list[str]) -> str:
    state = prog.get("code_map") or {}
    text = "Code map: " + (" ".join(x.strip().lstrip("-* ") for x in written[:2]) or "-")
    if state and not state.get("exists"):
        text += f" (WARN: no code map at {orch.cfg.code_map})"
    elif state.get("missing"):
        text += f" (WARN: new public types not in it: {', '.join(state['missing'])})"
    return _clip(text, 400)


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
            delete_marker(orch.cfg, name)
        print("Tags deleted (local): " + (", ".join(names) or "none"))
        if names and orch.cfg.git_markers == "tags":
            print("On the remote (if pushed): git push origin --delete " + " ".join(names))
    else:
        left = feature_tags(orch, feature)
        if left:
            print(
                f"Tags kept for the CI frame diff: {', '.join(left)} (after the PR is merged: "
                f"framegate cleanup {feature} --tags)"
            )
    return 0
