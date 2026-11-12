"""`framegate stats`: cost and time per feature from events.jsonl."""

from __future__ import annotations

import json

from conftest import DemoRepo
from framegate import stats
from framegate.config import load_config


def ev(ts: str, kind: str, feature: str = "demo", **data: object) -> dict[str, object]:
    return {"ts": f"2026-11-12T{ts}", "feature": feature, "kind": kind, **data}


EVENTS = [
    ev("10:00:00", "spec_approved", mode="bugfix"),
    ev("10:05:00", "agent_run", phase="TESTS", cost=1.5, seconds=300, tokens_out=1200),
    ev("10:06:00", "questions", questions="- one?\n- two?"),
    ev("10:10:00", "agent_run", phase="IMPLEMENT", cost=0.5, seconds=120),
    ev("10:11:00", "gate", passed=False, seconds=60),
    ev("10:13:00", "agent_run", phase="IMPLEMENT", cost=0.25, seconds=90),
    ev("10:14:00", "gate", passed=True, seconds=60),
    ev(
        "10:40:00",
        "final_gates",
        passed=False,
        failed=["mutation score"],
        tool_failures=["mutation score"],
        seconds=1500,
        durations={"lint": 0.4, "mutation score": 900.0, "coverage run": 500.0, "regression": 99},
    ),
    ev("10:41:00", "waiting", wait="gate", gate="final"),
    ev("11:41:00", "answer", gate="final"),
    ev("12:00:00", "final_gates", passed=True, failed=[], seconds=600, durations={"lint": 1.0}),
    ev("12:00:30", "final_repair", owner="tests", changed=True),
    ev("12:01:00", "risk", level="elevated", facts={"assumptions": 4}),
    ev("12:02:00", "waiting", wait="gate", gate="final"),
    ev("12:12:00", "human_gate", gate="final", approved=True),
    ev("12:20:00", "retro_coverage", items=3),
]


def test_duration() -> None:
    assert [stats.duration(s) for s in (58, 89, 91, 2520, 5340, 5760)] == [
        "58 s",
        "89 s",
        "2 min",
        "42 min",
        "89 min",
        "1 h 36 min",
    ]


def test_feature_numbers() -> None:
    st = stats.feature_stats("demo", EVENTS)
    assert st.mode == "bugfix" and st.done
    assert round(st.cost, 2) == 2.25 and st.agent_seconds == 510
    assert (st.phases["IMPLEMENT"].runs, st.phases["TESTS"].tokens_out) == (2, 1200)
    assert (st.step_gates, st.step_gates_failed, st.step_gate_seconds) == (2, 1, 120)
    assert [r.passed for r in st.final_runs] == [False, True] and st.check_seconds == 2220
    assert st.repairs == ["tests"] and st.questions == 2
    assert (st.risk, st.assumptions) == ("elevated", 4)
    # the two gate stops: an hour until the answer, ten minutes until the approval
    assert (st.human_seconds, st.human_waits) == (4200, 2)
    assert st.wall_seconds == 8400
    data = st.as_dict()
    assert data["cost_usd"] == 2.25 and data["final_runs"][0]["tool_failures"] == ["mutation score"]


def test_stats_command(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    lines: list[str] = []
    other = [ev("09:00:00", "agent_run", feature="other", phase="TESTS", cost=4, seconds=60)]
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    (cfg.out_dir / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in [*other, *EVENTS]), encoding="utf-8"
    )
    assert stats.run(cfg, echo=lines.append) == 0
    head, first, second = lines[0], lines[1], lines[2]
    assert head.split()[:4] == ["feature", "mode", "cost", "agents"]
    assert first.startswith("other (open)") and "$4.00" in first
    assert second.split()[:5] == ["demo", "bugfix", "$2.25", "8", "min"]
    assert "2 (1 failed)" in second and second.rstrip().endswith("elevated")

    lines.clear()
    assert stats.run(cfg, "demo", echo=lines.append) == 0
    text = "\n".join(lines)
    assert text.startswith("demo (bugfix): $2.25; agents 8 min, checks 37 min, waited for you")
    row = next(x for x in lines if x.strip().startswith("IMPLEMENT"))
    assert row.split() == ["IMPLEMENT", "2", "$0.75", "4", "min", "-"]
    assert "Final gates: 2 run(s); repair runs: tests" in text
    assert (
        "  1. FAIL: mutation score [tool failure], 25 min (slowest: mutation score 15 min, "
        "coverage run 8 min, regression 2 min)"
    ) in text
    assert "  2. PASS, 10 min" in text

    lines.clear()
    assert stats.run(cfg, "nope", echo=lines.append) == 1
    assert r.af("stats", "demo", "--json") == 0


def test_spec_time_is_from_the_spec_command_to_the_approval() -> None:
    events = [
        ev("09:00:00", "spec_started", mode="refactor"),
        ev("09:01:00", "spec_started", mode="refactor"),  # `framegate spec` again: same round
        ev("09:10:00", "spec_approved", mode="refactor"),
        ev("09:30:00", "spec_started"),  # reopened after an answer
        ev("09:35:00", "spec_approved"),
    ]
    st = stats.feature_stats("demo", events)
    assert st.spec_seconds == 900 and st.as_dict()["spec_seconds"] == 900
    assert stats.detail(st)[1].startswith("Spec: 15 min from `framegate spec` to the approval")
    assert "spec" in stats.overview([st])[0].split() and "15 min" in stats.overview([st])[1]
    # logs from before the spec command was recorded: no spec time, no line
    old = stats.feature_stats("demo", EVENTS)
    assert old.spec_seconds == 0 and not stats.detail(old)[1].startswith("Spec:")
