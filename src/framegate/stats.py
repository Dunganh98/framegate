"""`framegate stats`: what the features cost, from {out}/events.jsonl (no agents, read-only).

Without a feature: one line per feature and the averages. With a feature: cost and agent time
per phase, every final-gate run with its slowest checks, the step gates, the time the run
waited for the human.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from framegate.config import Config
from framegate.retro import split_questions
from framegate.state import read_events

#: the phases in run order (others, e.g. TEST_REVIEW or TRIAGE, are listed after them by cost)
PHASE_ORDER = [
    "SPEC_UPDATE",
    "TESTS",
    "TEST_REVIEW",
    "IMPLEMENT",
    "REVIEW",
    "TRIAGE",
    "PR",
    "RETRO",
]


@dataclass
class PhaseStats:
    runs: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    tokens_out: int = 0


@dataclass
class FinalRun:
    passed: bool
    failed: list[str]
    seconds: float
    durations: dict[str, float]
    tool_failures: list[str] = field(default_factory=list)


@dataclass
class FeatureStats:
    feature: str
    mode: str = "feature"
    started: str = ""
    ended: str = ""
    done: bool = False
    phases: dict[str, PhaseStats] = field(default_factory=dict)
    step_gates: int = 0
    step_gates_failed: int = 0
    step_gate_seconds: float = 0.0
    final_runs: list[FinalRun] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    questions: int = 0
    parked: int = 0
    assumptions: int | None = None
    risk: str = ""
    auto_approved: bool = False
    human_seconds: float = 0.0
    human_waits: int = 0
    #: from `framegate spec` to the approval (the spec dialogue in the chat), every round
    spec_seconds: float = 0.0
    #: rounds in which every plan step was parked (no review, final checks or summary run)
    reviews_skipped: int = 0

    @property
    def cost(self) -> float:
        return sum(p.cost for p in self.phases.values())

    @property
    def agent_seconds(self) -> float:
        return sum(p.seconds for p in self.phases.values())

    @property
    def check_seconds(self) -> float:
        return self.step_gate_seconds + sum(r.seconds for r in self.final_runs)

    @property
    def wall_seconds(self) -> float:
        return _between(self.started, self.ended)

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "mode": self.mode,
            "started": self.started,
            "ended": self.ended,
            "done": self.done,
            "cost_usd": round(self.cost, 2),
            "agent_seconds": round(self.agent_seconds),
            "check_seconds": round(self.check_seconds),
            "human_seconds": round(self.human_seconds),
            "spec_seconds": round(self.spec_seconds),
            "wall_seconds": round(self.wall_seconds),
            "phases": {
                name: {
                    "runs": p.runs,
                    "cost_usd": round(p.cost, 2),
                    "seconds": round(p.seconds),
                    "tokens_out": p.tokens_out,
                }
                for name, p in self.phases.items()
            },
            "step_gates": self.step_gates,
            "step_gates_failed": self.step_gates_failed,
            "final_runs": [
                {
                    "passed": r.passed,
                    "failed": r.failed,
                    "tool_failures": r.tool_failures,
                    "seconds": round(r.seconds),
                    "durations": r.durations,
                }
                for r in self.final_runs
            ],
            "repairs": self.repairs,
            "questions": self.questions,
            "parked": self.parked,
            "assumptions": self.assumptions,
            "risk": self.risk,
            "auto_approved": self.auto_approved,
            "reviews_skipped": self.reviews_skipped,
        }


def _time(ts: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _between(start: str, end: str) -> float:
    a, b = _time(start), _time(end)
    return max(0.0, (b - a).total_seconds()) if a and b else 0.0


def duration(seconds: float) -> str:
    """`58 s`, `42 min`, `1 h 36 min`."""
    s = round(seconds)
    if s < 90:
        return f"{s} s"
    minutes = round(s / 60)
    if minutes < 90:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def feature_stats(feature: str, events: list[dict[str, Any]]) -> FeatureStats:
    """One feature's events (in file order) -> its numbers."""
    st = FeatureStats(feature)
    waiting: str | None = None
    spec_open: str | None = None
    for e in events:
        kind, ts = e.get("kind"), str(e.get("ts") or "")
        st.started = st.started or ts
        st.ended = ts or st.ended
        if waiting and kind != "waiting":  # the next event after a gate stop: the human acted
            st.human_seconds += _between(waiting, ts)
            st.human_waits += 1
            waiting = None
        if kind in ("spec_started", "spec_approved") and e.get("mode"):
            st.mode = str(e["mode"])
        if kind == "spec_started":
            spec_open = spec_open or ts
        elif kind == "spec_approved":
            st.spec_seconds += _between(spec_open, ts) if spec_open else 0
            spec_open = None
        elif kind == "agent_run":
            p = st.phases.setdefault(str(e.get("phase") or "?"), PhaseStats())
            p.runs += 1
            p.cost += float(e.get("cost") or 0)
            p.seconds += float(e.get("seconds") or 0)
            p.tokens_out += int(e.get("tokens_out") or 0)
        elif kind == "gate":
            st.step_gates += 1
            st.step_gates_failed += 0 if e.get("passed") else 1
            st.step_gate_seconds += float(e.get("seconds") or 0)
        elif kind == "final_gates":
            st.final_runs.append(
                FinalRun(
                    bool(e.get("passed")),
                    [str(x) for x in e.get("failed") or []],
                    float(e.get("seconds") or 0),
                    {str(k): float(v) for k, v in (e.get("durations") or {}).items()},
                    [str(x) for x in e.get("tool_failures") or []],
                )
            )
        elif kind == "final_repair":
            st.repairs.append(f"{e.get('owner')}{'' if e.get('changed') else ' (no change)'}")
        elif kind == "questions":
            st.questions += len(split_questions(str(e.get("questions") or "")))
        elif kind in ("parked", "provisional"):
            st.parked += 1 if kind == "parked" else 0
            st.questions += 1 if str(e.get("question") or "").strip() else 0
        elif kind == "risk":
            st.risk = str(e.get("level") or "")
            st.assumptions = (e.get("facts") or {}).get("assumptions", st.assumptions)
        elif kind == "human_gate":
            st.auto_approved = st.auto_approved or bool(e.get("auto"))
        elif kind == "review_skipped":
            st.reviews_skipped += 1
        elif kind == "waiting":
            waiting = ts
        elif kind == "retro_coverage":
            st.done = True
    return st


def _metrics_rows(cfg: Config) -> dict[str, dict[str, str]]:
    path = cfg.root / cfg.paths.metrics / "workflow_metrics.csv"
    if not path.is_file():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    head = lines[0].split(",") if lines else []
    return {
        row["feature"]: row
        for row in (dict(zip(head, line.split(","), strict=False)) for line in lines[1:])
        if row.get("feature")
    }


def collect(cfg: Config) -> list[FeatureStats]:
    """Every feature in events.jsonl, in the order they started."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in read_events(cfg):
        if e.get("feature"):
            grouped.setdefault(str(e["feature"]), []).append(e)
    rows = _metrics_rows(cfg)
    out = []
    for name, events in grouped.items():
        st = feature_stats(name, events)
        row = rows.get(name)
        if row:
            st.done = True
            if st.assumptions is None and str(row.get("assumptions") or "").isdigit():
                st.assumptions = int(row["assumptions"])
        out.append(st)
    return out


def _table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [
        "  ".join(
            c.ljust(w) if i == 0 else c.rjust(w)
            for i, (c, w) in enumerate(zip(r, widths, strict=True))
        ).rstrip()
        for r in rows
    ]


def _final_word(st: FeatureStats) -> str:
    if not st.final_runs:
        return "-"
    fails = sum(1 for r in st.final_runs if not r.passed)
    return f"{len(st.final_runs)}" + (f" ({fails} failed)" if fails else "")


def overview(stats: list[FeatureStats]) -> list[str]:
    head = [
        "feature",
        "mode",
        "cost",
        "agents",
        "checks",
        "you",
        "spec",
        "final runs",
        "step fails",
        "questions",
        "assumed",
        "risk",
    ]
    rows = [head]
    for st in stats:
        rows.append(
            [
                st.feature + ("" if st.done else " (open)"),
                st.mode,
                f"${st.cost:.2f}",
                duration(st.agent_seconds),
                duration(st.check_seconds),
                duration(st.human_seconds),
                duration(st.spec_seconds) if st.spec_seconds else "-",
                _final_word(st),
                str(st.step_gates_failed),
                str(st.questions),
                "-" if st.assumptions is None else str(st.assumptions),
                (st.risk or "-") + (" (auto)" if st.auto_approved else ""),
            ]
        )
    lines = _table(rows)
    done = [s for s in stats if s.done]
    if len(done) > 1:
        n = len(done)
        runs = sum(len(s.final_runs) for s in done)
        lines += [
            "",
            f"Average of {n} finished feature(s): ${sum(s.cost for s in done) / n:.2f}, agents "
            f"{duration(sum(s.agent_seconds for s in done) / n)}, checks "
            f"{duration(sum(s.check_seconds for s in done) / n)}, {runs / n:.1f} final-gate "
            "run(s).",
        ]
    lines += [
        "",
        "agents / checks: time agents and gate checks ran; you: time a gate waited for you;",
        "spec: from `framegate spec` to the approval (the chat's own cost is not in the log).",
    ]
    return lines


def detail(st: FeatureStats) -> list[str]:
    lines = [
        f"{st.feature} ({st.mode}{'' if st.done else ', not finished'}): ${st.cost:.2f}; agents "
        f"{duration(st.agent_seconds)}, checks {duration(st.check_seconds)}, waited for you "
        f"{duration(st.human_seconds)} ({st.human_waits} time(s)); {st.started} to {st.ended}",
        "",
        "Agent runs per phase:",
    ]
    if st.spec_seconds:
        lines.insert(
            1,
            f"Spec: {duration(st.spec_seconds)} from `framegate spec` to the approval (the "
            "chat wrote it with you; its cost and the talk before are not in the log)",
        )
    order = [p for p in PHASE_ORDER if p in st.phases]
    order += sorted(
        (p for p in st.phases if p not in PHASE_ORDER), key=lambda p: -st.phases[p].cost
    )
    rows = [["  phase", "runs", "cost", "time", "tokens out"]]
    for name in order:
        p = st.phases[name]
        rows.append(
            [
                f"  {name}",
                str(p.runs),
                f"${p.cost:.2f}",
                duration(p.seconds),
                f"{p.tokens_out:,}" if p.tokens_out else "-",
            ]
        )
    lines += _table(rows)
    lines += [
        "",
        f"Step gates: {st.step_gates} ({st.step_gates_failed} failed), "
        f"{duration(st.step_gate_seconds)}",
        f"Final gates: {len(st.final_runs)} run(s)"
        + (f"; repair runs: {', '.join(st.repairs)}" if st.repairs else ""),
    ]
    for i, r in enumerate(st.final_runs, 1):
        failed = ", ".join(
            n + (" [tool failure]" if n in r.tool_failures else "") for n in r.failed
        )
        slow = sorted(r.durations.items(), key=lambda kv: -kv[1])[:3]
        slowest = ", ".join(f"{n} {duration(s)}" for n, s in slow if s >= 1)
        lines.append(
            f"  {i}. {'PASS' if r.passed else 'FAIL: ' + failed}, {duration(r.seconds)}"
            + (f" (slowest: {slowest})" if slowest else "")
        )
    lines.append(
        f"Questions {st.questions}; parked {st.parked}; assumptions "
        f"{'-' if st.assumptions is None else st.assumptions}; risk {st.risk or '-'}"
        + (" (approved automatically)" if st.auto_approved else "")
    )
    if st.reviews_skipped:
        lines.append(
            f"Rounds with every step parked (review, final checks and summary skipped): "
            f"{st.reviews_skipped}"
        )
    return lines


def run(
    cfg: Config,
    feature: str | None = None,
    as_json: bool = False,
    echo: Callable[[str], None] = print,
) -> int:
    stats = collect(cfg)
    if feature:
        stats = [s for s in stats if s.feature == feature]
        if not stats:
            echo(f"No events for feature '{feature}' in {cfg.paths.out}/events.jsonl.")
            return 1
    if as_json:
        import json

        echo(json.dumps([s.as_dict() for s in stats], indent=2))
        return 0
    if not stats:
        echo(f"No events yet in {cfg.paths.out}/events.jsonl.")
        return 0
    for line in detail(stats[0]) if feature else overview(stats):
        echo(line)
    return 0
