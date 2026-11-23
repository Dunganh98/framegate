"""The score of a run: the hidden tests and the probes against the result (in a copy, outside
the sandbox), the engine's numbers from its events, the chat's cost from the turn files, the
facts the driver gave, the size of the change; and the results tables over all candidates."""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import shutil
import subprocess
from collections.abc import Sequence
from typing import Any

from framegate.config import ConfigError, load_config
from framegate.replay.sandbox import (
    KEPT,
    Layout,
    copy_tree,
    is_test_path,
    read_json,
    run_tests,
    write_json,
)
from framegate.state import StateError, load_progress, read_events
from framegate.stats import duration, feature_stats

COLUMNS = (
    "Candidate",
    "Kind",
    "Verdict",
    "Hidden tests",
    "Probes",
    "Regressions",
    "Stage",
    "Time",
    "Total USD",
    "Note",
)
DETAILS = (
    "Candidate",
    "Engine USD",
    "Chat USD",
    "Final rounds",
    "Redos",
    "Facts asked",
    "Questions",
    "Late bugs",
    "Send-backs",
    "Source lines (run / commit)",
)


#: stages before the engine built anything: the task was not started, or only its spec
NOT_RUN = ("SETUP", "SPEC")
#: the verdict of a scoring run that gave no result: the next run scores again
FAILED = "score failed"
#: every hidden test passes, but a probe (a "must not change" rule of the task) fails
BROKE = "broke a rule"
#: what the engine's events say about the run's course; None: no events to read
FLOW = ("questions", "late_bugs", "send_backs")


def verdict(
    proving: Sequence[str], base_pass: Sequence[str], result: dict[str, str], stage: str = ""
) -> dict[str, Any]:
    """A hidden test that did not run counts as failed; a skipped one is not proven. A run
    that ended before the engine started is "not run", not "bad": the failing tests say
    nothing about framegate there. No result at all (pytest did not run) is "score failed",
    never "bad"."""
    if not result:
        empty: dict[str, Any] = {"passed": 0, "failed": [], "skipped": [], "regressions": []}
        return {"verdict": FAILED, "hidden": len(proving), **empty}
    by = {
        k: sorted(n for n in proving if result.get(n, "fail") == k)
        for k in ("pass", "fail", "skip")
    }
    regressions = sorted(n for n in base_pass if result.get(n, "fail") == "fail")
    word = "bad" if by["fail"] or regressions else "not proven" if by["skip"] else "good"
    if word == "bad" and stage in NOT_RUN:
        word = "not run"
    return {
        "verdict": word,
        "hidden": len(proving),
        "passed": len(by["pass"]),
        "failed": by["fail"],
        "skipped": by["skip"],
        "regressions": regressions,
    }


def probed(expected: Sequence[str], result: dict[str, str]) -> dict[str, Any]:
    """The probes of a run. `expected`: the probes that passed on the parent and the commit
    (empty, a run built without them: every probe that ran)."""
    names = sorted(expected or result)
    return {
        "probes": len(names),
        "probes_passed": sum(1 for n in names if result.get(n) == "pass"),
        "probes_failed": [n for n in names if result.get(n, "fail") == "fail"],
    }


def flow(events: list[dict[str, Any]], slug: str) -> dict[str, int]:
    """Questions asked; bugs first caught at REVIEW or later (review findings judged valid,
    failed final-gate runs); send-backs (test-check rounds, test-review rounds, step retries)."""
    own = [e for e in events if e.get("feature") == slug]
    valid = {
        str(f.get("test"))
        for e in own
        if e["kind"] == "triage"
        for f in e.get("findings") or []
        if isinstance(f, dict) and f.get("verdict") == "VALID"
    }
    red = sum(1 for e in own if e["kind"] == "final_gates" and not e.get("passed"))
    back = sum(1 for e in own if e["kind"] in ("test_checks", "test_review") and e.get("sent_back"))
    retries = sum(1 for e in own if e["kind"] == "gate" and int(e.get("attempt") or 1) > 1)
    return {
        "questions": feature_stats(slug, own).questions,
        "late_bugs": len(valid) + red,
        "send_backs": back + retries,
    }


def facts(driver: pathlib.Path) -> dict[str, Any]:
    """The candidate's facts (facts.md) and the ones the driver gave: facts_used.json, else
    the `fact F1 given for:` lines of its log. None: no facts, or nothing says which."""
    try:
        known = re.findall(r"(?m)^- (F\d+) \[", (driver / "facts.md").read_text(encoding="utf-8"))
    except OSError:
        return {"facts": None, "facts_used": None}
    used: list[str] | None = None
    try:
        data = json.loads((driver / "facts_used.json").read_text(encoding="utf-8-sig"))
        used = [str(x) for x in data] if isinstance(data, list) else None
    except (OSError, ValueError):
        pass
    if used is None:
        try:
            log = (driver / "log.md").read_text(encoding="utf-8", errors="replace")
            used = re.findall(r"fact (F\d+) given for", log)
        except OSError:
            pass
    return {"facts": len(known), "facts_used": used and sorted(set(used) & set(known))}


def is_source(path: str) -> bool:
    """Code of the project: no test, no document, none of framegate's own files."""
    p = pathlib.PurePosixPath(path)
    own = p.parts[0] in (*KEPT, ".claude") or p.name == "framegate.toml"
    return not (own or p.suffix == ".md" or is_test_path(path))


def source_lines(numstat: str) -> int:
    """Added + removed lines of the source files in `git ... --numstat` output."""
    total = 0
    for line in numstat.splitlines():
        added, removed, path = (line.split("\t", 2) + ["", ""])[:3]
        if added.isdigit() and removed.isdigit() and path and is_source(path):
            total += int(added) + int(removed)
    return total


def patch_lines(patch: str) -> int:
    """Added + removed lines of the source files in a `git diff` patch."""
    total, path, hunk = 0, "", False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            path, hunk = line.rsplit(" b/", 1)[-1], False
        elif line.startswith("@@"):
            hunk = True
        elif hunk and line[:1] in ("+", "-") and path and is_source(path):
            total += 1
    return total


def changed(lay: Layout) -> dict[str, int | None]:
    """Changed source lines of the run (the sandbox against the parent commit) and of the
    real commit (its patch). None: no parent, no patch, or git could not say."""
    run: int | None = None
    parent = str(read_json(lay.answer / "exercise.json").get("parent") or "")
    if parent and (lay.sandbox / ".git").exists():
        cmd = ["git", "-C", str(lay.sandbox), "-c", "core.quotepath=off", "diff", "--numstat"]
        try:
            r = subprocess.run(
                [*cmd, "--no-renames", parent, "--"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            run = None if r.returncode else source_lines(r.stdout)
        except OSError:
            pass
    try:
        patch = (lay.answer / "commit.patch").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"src_lines": run, "commit_src_lines": None}
    return {"src_lines": run, "commit_src_lines": patch_lines(patch)}


def scorecard(lay: Layout) -> dict[str, Any]:
    """The numbers that need no environment: a pruned run still has them."""
    numbers = engine(lay.sandbox, lay.name)
    return {**{k: numbers.get(k) for k in FLOW}, **facts(lay.driver), **changed(lay)}


def waited_seconds(events: list[dict[str, Any]], now: datetime.datetime) -> float:
    """Seconds the engine waited for the usage limit: from each `usage_limit` event to the
    next event (a killed wait leaves no end event), or to now."""
    total, since = 0.0, None
    for e in events:
        try:
            ts = datetime.datetime.fromisoformat(str(e.get("ts")))
        except ValueError:
            continue
        if since is not None:
            total += (ts - since).total_seconds()
            since = None
        if e.get("kind") == "usage_limit":
            since = ts
    return total + ((now - since).total_seconds() if since else 0.0)


def engine(sandbox: pathlib.Path, slug: str) -> dict[str, Any]:
    """What the engine's files say: stage reached, cost, final-gate rounds, redos, waits."""
    out: dict[str, Any] = {
        "stage": "SETUP",
        "engine_usd": 0.0,
        "final_rounds": 0,
        "redos": 0,
        "waited_s": 0,
        "retro": "",
        **dict.fromkeys(FLOW),
    }
    try:
        cfg = load_config(sandbox)
        events = read_events(cfg)
        progress = cfg.feature_out(slug) / "progress.json"
        out["stage"] = str(load_progress(cfg, slug)["phase"]) if progress.is_file() else "SPEC"
    except (ConfigError, StateError, OSError):
        return out
    own = [e for e in events if e.get("feature") == slug]
    out["engine_usd"] = round(
        sum(float(e.get("cost") or 0) for e in events if e["kind"] == "agent_run"), 2
    )
    out["final_rounds"] = len(feature_stats(slug, own).final_runs)
    out["redos"] = sum(1 for e in own if e["kind"] == "redo_result")
    out.update(flow(events, slug))
    out["waited_s"] = round(waited_seconds(events, datetime.datetime.now()))
    retro = cfg.feature_out(slug) / "retro_proposals.md"
    out["retro"] = retro.read_text(encoding="utf-8", errors="replace") if retro.is_file() else ""
    return out


def chat_cost(driver: pathlib.Path) -> float:
    """`total_cost_usd` of a turn is the session's total so far: the highest per session."""
    sessions: dict[str, float] = {}
    for path in driver.glob("turn-*.json"):
        try:
            turn = read_json(path)
            cost = float(turn.get("total_cost_usd") or 0)
        except (ValueError, OSError):
            continue
        sid = str(turn.get("session_id") or path.name)
        sessions[sid] = max(sessions.get(sid, 0.0), cost)
    return round(sum(sessions.values()), 2)


def score(
    lay: Layout, probe_files: Sequence[str] = (), probes: pathlib.Path | None = None
) -> dict[str, Any]:
    """`probes`: the folder of the candidate's probe files. They are laid over the scored
    copy after the hidden tests ran, never over the sandbox."""
    ex, run = read_json(lay.answer / "exercise.json"), read_json(lay.run / "run.json")
    tree = lay.run / "score" / "result"
    py, xml = pathlib.Path(run["python"]), lay.run / "score" / "result.xml"
    pins: dict[str, str] | None = None
    try:
        copy_tree(lay.sandbox, tree, lay.answer / "hidden")
        result, tail = run_tests(py, tree, ex["pythonpath"], ex["files"], xml)
        if result and probe_files and probes:
            shutil.copytree(probes, tree, dirs_exist_ok=True)
            pins, pin_tail = run_tests(
                py, tree, ex["pythonpath"], probe_files, xml.with_name("probes.xml")
            )
            if not pins:  # probes that did not run prove nothing: no verdict
                result, tail = {}, f"probes: {pin_tail}"
    except (OSError, MemoryError) as e:  # no memory, no disk: pytest could not start
        result, tail = {}, f"{type(e).__name__}: {e}"
    numbers = engine(lay.sandbox, lay.name)
    (lay.run / "retro_proposals.md").write_text(numbers.pop("retro"), encoding="utf-8")
    row = {
        **verdict(ex["proving"], ex["base_pass"], result, numbers["stage"]),
        **(probed(ex.get("probes") or [], pins) if result and pins else {}),
        "result_tail": tail,
        **numbers,
        "chat_usd": chat_cost(lay.driver),
        **facts(lay.driver),
        **changed(lay),
    }
    if row.get("probes_failed") and row["verdict"] in ("good", "not proven"):
        row["verdict"] = BROKE
    return row


def _n(value: Any) -> str:
    return "-" if value is None else str(value)


def _cells(row: dict[str, Any]) -> list[str]:
    if row.get("status") != "scored":
        return [row["name"], row["kind"], row["status"], *[""] * 6, row.get("note", "")]
    failed = row["verdict"] == FAILED
    hidden = "" if failed else f"{row['passed']}/{row['hidden']}"
    if row["skipped"]:
        hidden += f", {len(row['skipped'])} skipped"
    probes = "-" if row.get("probes") is None else f"{row['probes_passed']}/{row['probes']}"
    counted = max(0.0, float(row.get("wall_s", 0)) - float(row.get("waited_s", 0)))
    total = row["engine_usd"] + row["chat_usd"] + float(row.get("driver_usd", 0))
    return [
        row["name"],
        row["kind"],
        row["verdict"],
        hidden,
        probes,
        "" if failed else str(len(row["regressions"])),
        row["stage"],
        duration(counted),
        f"{total:.2f}",
        row.get("note", ""),
    ]


def _details(row: dict[str, Any]) -> list[str]:
    used, known = row.get("facts_used"), row.get("facts")
    asked = "-" if known is None else f"{'-' if used is None else len(used)}/{known}"
    return [
        row["name"],
        f"{row['engine_usd']:.2f}",
        f"{row['chat_usd']:.2f}",
        str(row["final_rounds"]),
        str(row["redos"]),
        asked,
        *[_n(row.get(k)) for k in FLOW],
        f"{_n(row.get('src_lines'))} / {_n(row.get('commit_src_lines'))}",
    ]


def _table(columns: Sequence[str], cells: list[list[str]]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += [
        "| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in row) + " |"
        for row in cells
    ]
    return "\n".join(lines) + "\n"


def table(rows: list[dict[str, Any]]) -> str:
    return _table(COLUMNS, [_cells(r) for r in rows])


def details(rows: list[dict[str, Any]]) -> str:
    """The scored runs: costs, rounds and what the run's course says."""
    return _table(DETAILS, [_details(r) for r in rows if r.get("status") == "scored"])


def write_results(out: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    """results.md, results.json and retro-proposals.md over all candidates."""
    write_json(out / "results.json", rows)
    note = (
        "Verdict: good = every hidden test passes, no regression; not proven = a hidden test "
        "was skipped; broke a rule = the hidden tests pass, a probe (a rule of the task on "
        "what must not change) fails; bad = a hidden test fails or a test that passed before "
        "fails; "
        "not run = the run ended before framegate built anything (the Note says where and "
        "why): no result for framegate; score failed = the hidden tests did not run (the Note "
        "says why): `framegate replay --score-only` scores again. "
        "Total USD = engine + chat + driver. Time leaves out usage-limit waits.\n"
    )
    more = (
        "Facts asked = facts the driver gave on a question / facts of the candidate. Questions "
        "= questions the engine's agents collected for a gate. Late bugs = review findings "
        "judged valid + failed final-gate runs. Send-backs = test-check rounds + test-review "
        "rounds sent back + step retries. Source lines = added + removed, without tests, "
        "documents and framegate's files. `-` = no data.\n"
    )
    text = f"# Replay results\n\n{table(rows)}\n{note}\n## Details\n\n{details(rows)}\n{more}"
    (out / "results.md").write_text(text, encoding="utf-8")
    retros = []
    for row in rows:
        path = out / "runs" / row["name"] / "retro_proposals.md"
        text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        if text:
            retros.append(f"# {row['name']}\n\n{text}\n")
    (out / "retro-proposals.md").write_text("\n".join(retros), encoding="utf-8")
