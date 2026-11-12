"""Parsing the agents' structured files and building the generated parts of the gate files:

- steps.md assumptions (`- ASSUMPTION [<ID>]: text`), written by the implementer
- test_review.md (`Verdict: OK|OBJECTIONS` + `## Objections` bullets), written by the test reviewer
- triage.md (`- <test>: VALID|INVALID — reason`), written by the test reviewer in TRIAGE
- the cost / time / turns table per phase (from out/events.jsonl)
- final_facts.md: the facts the summary agent turns into final_summary.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ASSUMPTION_RE = re.compile(r"^\s*[-*]\s*ASSUMPTION\s*\[([^\]]*)\]\s*:?\s*(.+?)\s*$", re.I)
VERDICT_RE = re.compile(r"^\s*\**Verdict\**\s*:\s*\**\s*([A-Za-z]+)", re.M | re.I)
TRIAGE_RE = re.compile(
    r"^\s*[-*]\s*`?(.+?)`?\s*:\s*\**(VALID|INVALID)\**\s*(?:[-—–:]+\s*(.*))?$", re.I
)
#: phases in the order they run (rows of the cost table)
PHASE_ORDER = [
    "SPEC_UPDATE",
    "TESTS",
    "TEST_REVIEW",
    "IMPLEMENT",
    "DIAGNOSIS",
    "REVIEW",
    "TRIAGE",
    "PR",
    "RETRO",
]


@dataclass
class Assumption:
    where: str  # the steps.md heading, e.g. "Step 1 — add"
    spec_id: str
    text: str


CAUSE_RE = re.compile(r"^\s*[-*]\s*(ROOT CAUSE|RULED OUT)\s*:\s*(.+)$", re.I)


def parse_causes(text: str) -> list[tuple[str, str]]:
    """steps.md -> the implementer's `- ROOT CAUSE: ...` / `- RULED OUT: ...` lines of a bug
    fix, as (kind in upper case, text)."""
    return [
        (m.group(1).upper(), m.group(2).strip())
        for m in (CAUSE_RE.match(line) for line in text.splitlines())
        if m
    ]


def mode_facts(mode: str, causes: list[tuple[str, str]]) -> list[str]:
    """final_facts.md lines for a bug fix or a refactor ([] for a feature)."""
    if mode == "bugfix":
        found = [f"- {kind}: {text}" for kind, text in causes]
        if not any(kind == "ROOT CAUSE" for kind, _ in causes):
            found.insert(0, "- ROOT CAUSE: not stated in the step notes (say so in the brief)")
        return ["## Mode: bug fix", "", "The cause, from the implementer's step notes:", *found, ""]
    if mode == "refactor":
        return [
            "## Mode: refactor",
            "",
            "The behavior must be unchanged: the characterization tests passed on the code "
            "before the refactor and pass now. Report what moved where and each structure "
            "target of the spec (met or not; the structure report is in the final gates).",
            "",
        ]
    return []


def parse_assumptions(text: str) -> list[Assumption]:
    out: list[Assumption] = []
    where = ""
    for line in text.splitlines():
        if line.startswith("#"):
            where = line.lstrip("#").strip()
            continue
        m = ASSUMPTION_RE.match(line)
        if m:
            out.append(Assumption(where, m.group(1).strip() or "none", m.group(2).strip()))
    return out


@dataclass
class TestReview:
    verdict: str  # OK | OBJECTIONS | MISSING
    objections: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.verdict == "OK" and not self.objections


def section_bullets(text: str, heading: str) -> list[str]:
    """Top-level bullets under a '## <heading>' section (continuation lines kept)."""
    items: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.lstrip("#").strip().lower().startswith(heading.lower())
            continue
        if not inside:
            continue
        m = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            items.append(m.group(1).strip())
        elif items and line.strip() and line.startswith((" ", "\t")):
            items[-1] += " " + line.strip()
    return [i for i in items if i.strip().lower().rstrip(".") not in ("none", "")]


def parse_test_review(text: str | None) -> TestReview:
    if text is None or not text.strip():
        return TestReview("MISSING", ["The test reviewer wrote no verdict (test_review.md)."])
    m = VERDICT_RE.search(text)
    verdict = m.group(1).upper() if m else "MISSING"
    objections = section_bullets(text, "Objections")
    if verdict not in ("OK", "OBJECTIONS"):
        objections = objections or [f"Unreadable verdict in test_review.md ({verdict})."]
        verdict = "OBJECTIONS"
    if verdict == "OBJECTIONS" and not objections:
        objections = ["Verdict OBJECTIONS without listed objections (see test_review.md)."]
    if objections:
        verdict = "OBJECTIONS"
    return TestReview(verdict, objections)


def parse_triage(text: str | None) -> dict[str, tuple[str, str]]:
    """triage.md -> {test name as written: (VALID|INVALID, reason)}."""
    out: dict[str, tuple[str, str]] = {}
    for line in (text or "").splitlines():
        m = TRIAGE_RE.match(line)
        if m:
            out[m.group(1).strip()] = (m.group(2).upper(), (m.group(3) or "").strip())
    return out


def short_name(test: str) -> str:
    """'tests/review/test_x.py::test_a' / 'Ns.Class.Method' -> 'test_a' / 'Method'."""
    tail = test.split("::")[-1]
    return tail.split(".")[-1].split("(")[0].strip()


def triage_verdict(test: str, verdicts: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    name = short_name(test)
    for key, value in verdicts.items():
        if key == test or short_name(key) == name or name in key:
            return value
    return None


# ---------- events -> tables ----------
def since_spec_approval(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events of the current spec version (after the last spec_approved)."""
    last = max((i for i, e in enumerate(events) if e.get("kind") == "spec_approved"), default=-1)
    return events[last + 1 :]


def phase_table(events: list[dict[str, Any]]) -> str:
    """Markdown table: agent runs, time, cost, turns per phase + step / final gate time."""
    rows: dict[str, list[float]] = {}
    for e in events:
        if e.get("kind") != "agent_run":
            continue
        r = rows.setdefault(str(e.get("phase")), [0, 0.0, 0.0, 0])
        r[0] += 1
        r[1] += float(e.get("seconds") or 0)
        r[2] += float(e.get("cost") or 0)
        r[3] += int(e.get("turns") or 0)
    order = [p for p in PHASE_ORDER if p in rows] + sorted(p for p in rows if p not in PHASE_ORDER)
    lines = ["| Phase | Agent runs | Time | Cost (USD) | Turns |", "|---|---|---|---|---|"]
    total = [0, 0.0, 0.0, 0]
    for p in order:
        r = rows[p]
        total = [a + b for a, b in zip(total, r, strict=True)]
        lines.append(f"| {p} | {int(r[0])} | {fmt_s(r[1])} | {r[2]:.2f} | {int(r[3])} |")
    for kind, name in (("gate", "step gates"), ("final_gates", "final gates")):
        secs = [float(e.get("seconds") or 0) for e in events if e.get("kind") == kind]
        if secs:
            total[1] += sum(secs)
            lines.append(f"| {name} ({len(secs)} runs) | - | {fmt_s(sum(secs))} | - | - |")
    lines.append(
        f"| **Total** | {int(total[0])} | {fmt_s(total[1])} | {total[2]:.2f} | {int(total[3])} |"
    )
    return "\n".join(lines)


def _one_line(text: str, limit: int = 1500) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def fmt_s(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"


def final_facts(
    feature: str,
    steps: list[Any],
    prog: dict[str, Any],
    events: list[dict[str, Any]],
    assumptions: list[Assumption],
    final_gates: str,
    open_questions: str,
    items: list[str] | None = None,
    code_map: str = "",
    mode: str = "feature",
    causes: list[tuple[str, str]] | None = None,
) -> str:
    """{out}/{f}/final_facts.md: what the summary agent must report, from the run's records."""
    ev = since_spec_approval(events)
    parked = prog.get("parked") or []
    parked_steps = {
        n: p
        for p in parked
        if p.get("kind") == "step"
        for n in (p.get("steps") or [p.get("step")])  # a batch of steps: one record
    }
    out = [f"# Final facts: {feature}", "", "Generated by framegate from the run records.", ""]
    out += mode_facts(mode, causes or [])

    out += ["## Steps", "", "| Step | Status | Failed attempts |", "|---|---|---|"]
    for s in steps:
        label = f"step {s.num} - {s.title}"
        nfail = sum(
            1
            for e in ev
            if e.get("kind") == "gate"
            and not e["passed"]
            and (e.get("step") == label or s.num in (e.get("steps") or []))
        )
        prov = next(
            (
                r
                for r in prog.get("provisional") or []
                if s.num in (r.get("steps") or [r.get("step")])
            ),
            None,
        )
        if prov is not None:
            status = f"done, PROVISIONAL ({len(prov.get('contested') or [])} contested tests)"
        elif s.num in prog.get("completed_steps", []):
            status = "done"
        elif s.num in parked_steps:
            status = f"PARKED ({parked_steps[s.num].get('reason')})"
        else:
            status = "not run"
        out.append(f"| {label} | {status} | {nfail} |")

    out += ["", f"## Assumptions (steps.md): {len(assumptions)}", ""]
    out += [f"- [{a.spec_id}] {a.text} ({a.where})" for a in assumptions] or ["None."]

    fails = [e for e in ev if e.get("kind") == "gate" and not e.get("passed")]
    out += ["", f"## Failed attempts: {len(fails)}", ""]
    out += [
        f"- {e.get('step')}, attempt {e.get('attempt')}: {', '.join(e.get('failed') or [])}"
        for e in fails
    ] or ["None."]

    esc = [e for e in ev if e.get("kind") == "escalation"]
    if esc:
        out += ["", f"## Agent runs stopped at the turn cap twice: {len(esc)}", ""]
        out += [
            f"- {e.get('phase')} {e.get('step') or ''}: {', '.join(e.get('failed') or [])}, "
            "continued without a stop"
            for e in esc
        ]

    out += ["", "## Review findings", ""]
    triaged = [e for e in ev if e.get("kind") == "triage"]
    if not triaged:
        out.append("None (the adversarial reviewer's tests all passed, or no review ran).")
    for t in triaged:
        for f in t.get("findings") or []:
            reason = f" ({f.get('reason')})" if f.get("reason") else ""
            out.append(f"- {f.get('test')}: {f.get('verdict')}{reason}")
    fixed = [e for e in ev if e.get("kind") == "findings_fixed"]
    if fixed:
        out.append(f"- valid findings fixed: {', '.join(fixed[-1].get('tests') or [])}")

    out += ["", f"## Parked items: {len(parked)}", ""]
    for p in parked:
        out.append(f"- {p.get('label')} ({p.get('kind')}): {p.get('reason')}")
        if p.get("question"):
            out.append(f"  - question: {p['question'].strip()}")
        if p.get("failed"):
            out.append(f"  - failing checks: {', '.join(p['failed'])}")
        if p.get("tests"):
            out.append(f"  - failing review tests: {', '.join(p['tests'])}")
        if p.get("dependents"):
            deps = ", ".join(f"step {d}" for d in p["dependents"])
            out.append(f"  - later steps that depend on it (plan.md): {deps}")
    if not parked:
        out.append("None.")

    prov = prog.get("provisional") or []
    if prov:
        out += ["", f"## Provisional steps (code kept, test fixes proposed): {len(prov)}", ""]
        out.append(
            "Only their frozen tests were found wrong; the code passes every other check and "
            "later steps build on it. One answer approves all proposed test fixes; a rejected "
            "fix reverts that step's code and redoes it against the tests as they are."
        )
        for r in prov:
            out.append(f"- {r.get('label')}: contested {', '.join(r.get('contested') or [])}")
            if r.get("question"):
                out.append(f"  - proposed fix: {str(r['question']).strip()}")

    repairs = [e for e in ev if e.get("kind") == "final_repair"]
    if repairs:
        out += ["", f"## Final-gate repairs (automatic, before this gate): {len(repairs)}", ""]
        out += [
            f"- {', '.join(e.get('failed') or [])} failed; the "
            f"{'implementer' if e.get('owner') == 'src' else 'test writer'} "
            + ("repaired it (the gates ran again)" if e.get("changed") else "changed nothing")
            for e in repairs
        ]
        out.append("Name them under Struggles in the brief (one line).")

    out += ["", "## Final gates (final_gates.md)", "", "~~~", final_gates.strip(), "~~~"]
    out += ["", "## Open questions", "", open_questions.strip() or "None."]
    items = items or []
    out += ["", f"## Gate items (numbered as the final gate shows them): {len(items)}", ""]
    out += [f"{n}. {_one_line(t)}" for n, t in enumerate(items, 1)] or ["None."]
    out += [
        "",
        "Put each one in final_brief.md under `## Questions`, `## Decisions I made` or "
        "`## Left out`, named by its number in brackets (`[3]`; the PR role says which). "
        f"Questions you add (questions.md) are numbered after these ({len(items)} + 1, ...).",
    ]
    if code_map:
        out += ["", "## Code map", "", code_map]
    out += ["", "## Cost, time, turns per phase", "", phase_table(ev)]
    return "\n".join(out).rstrip() + "\n"
