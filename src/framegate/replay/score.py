"""The score of a run: the hidden tests against the result (in a copy, outside the sandbox),
the engine's numbers from its events, the chat's cost from the turn files; and the results
table over all candidates."""

from __future__ import annotations

import datetime
import pathlib
from collections.abc import Sequence
from typing import Any

from framegate.config import ConfigError, load_config
from framegate.replay.sandbox import Layout, copy_tree, read_json, run_tests, write_json
from framegate.state import StateError, load_progress, read_events
from framegate.stats import duration, feature_stats

COLUMNS = (
    "Candidate",
    "Kind",
    "Verdict",
    "Hidden tests",
    "Regressions",
    "Stage",
    "Time",
    "Engine USD",
    "Chat USD",
    "Final rounds",
    "Redos",
    "Note",
)


def verdict(
    proving: Sequence[str], base_pass: Sequence[str], result: dict[str, str]
) -> dict[str, Any]:
    """A hidden test that did not run counts as failed; a skipped one is not proven."""
    by = {
        k: sorted(n for n in proving if result.get(n, "fail") == k)
        for k in ("pass", "fail", "skip")
    }
    regressions = sorted(n for n in base_pass if result.get(n, "fail") == "fail")
    word = "bad" if by["fail"] or regressions else "not proven" if by["skip"] else "good"
    return {
        "verdict": word,
        "hidden": len(proving),
        "passed": len(by["pass"]),
        "failed": by["fail"],
        "skipped": by["skip"],
        "regressions": regressions,
    }


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


def score(lay: Layout) -> dict[str, Any]:
    ex, run = read_json(lay.answer / "exercise.json"), read_json(lay.run / "run.json")
    tree = lay.run / "score" / "result"
    copy_tree(lay.sandbox, tree, lay.answer / "hidden")
    result, tail = run_tests(
        pathlib.Path(run["python"]),
        tree,
        ex["pythonpath"],
        ex["files"],
        lay.run / "score" / "result.xml",
    )
    numbers = engine(lay.sandbox, lay.name)
    (lay.run / "retro_proposals.md").write_text(numbers.pop("retro"), encoding="utf-8")
    return {
        **verdict(ex["proving"], ex["base_pass"], result),
        "result_tail": tail,
        **numbers,
        "chat_usd": chat_cost(lay.driver),
    }


def _cells(row: dict[str, Any]) -> list[str]:
    if row.get("status") != "scored":
        return [row["name"], row["kind"], row["status"], *[""] * 8, row.get("note", "")]
    hidden = f"{row['passed']}/{row['hidden']}"
    if row["skipped"]:
        hidden += f", {len(row['skipped'])} skipped"
    counted = max(0.0, float(row.get("wall_s", 0)) - float(row.get("waited_s", 0)))
    return [
        row["name"],
        row["kind"],
        row["verdict"],
        hidden,
        str(len(row["regressions"])),
        row["stage"],
        duration(counted),
        f"{row['engine_usd']:.2f}",
        f"{row['chat_usd']:.2f}",
        str(row["final_rounds"]),
        str(row["redos"]),
        row.get("note", ""),
    ]


def table(rows: list[dict[str, Any]]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += [
        "| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in _cells(r)) + " |"
        for r in rows
    ]
    return "\n".join(lines) + "\n"


def write_results(out: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    """results.md, results.json and retro-proposals.md over all candidates."""
    write_json(out / "results.json", rows)
    note = (
        "Verdict: good = every hidden test passes, no regression; not proven = a hidden test "
        "was skipped; bad = a hidden test fails or a test that passed before fails. "
        "Time leaves out usage-limit waits.\n"
    )
    (out / "results.md").write_text(f"# Replay results\n\n{table(rows)}\n{note}", encoding="utf-8")
    retros = []
    for row in rows:
        path = out / "runs" / row["name"] / "retro_proposals.md"
        text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        if text:
            retros.append(f"# {row['name']}\n\n{text}\n")
    (out / "retro-proposals.md").write_text("\n".join(retros), encoding="utf-8")
