"""Retro input: every question, failed attempt, escalation (turn cap hit twice), human rejection,
review finding, final-gate failure, permission denial, implementer assumption and parked item of a
feature, with item IDs (Q1, F1, E1, R1, V1, G1, D1, A1, P1) the RETRO agent must address in
retro_proposals.md. Built from out/events.jsonl (+ the assumptions in steps.md) before the RETRO
agent runs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ID_PREFIXES = {
    "Q": "Questions (spec gaps)",
    "F": "Failed attempts",
    "E": "Escalations",
    "R": "Human rejections",
    "V": "Review findings",
    "G": "Final gate failures",
    "D": "Permission denials",
    "A": "Assumptions (implementer, from steps.md)",
    "P": "Parked items",
}
ITEM_ID = re.compile(r"\b([QFERVGDAP])(\d+)\b")


@dataclass
class RetroInput:
    text: str
    ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def split_questions(text: str) -> list[str]:
    """questions.md -> one entry per top-level bullet / numbered line (continuation lines are
    kept with their item); text without bullets is a single question."""
    items: list[str] = []
    for line in text.strip().splitlines():
        if re.match(r"^(?:[-*+]|\d+[.)])\s+", line):
            items.append(re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", line).strip())
        elif items and line.strip():
            items[-1] += "\n" + line.strip()
        elif line.strip() and not line.startswith("#"):
            items.append(line.strip())
    return items or ([text.strip()] if text.strip() else [])


def _where(e: dict[str, Any]) -> str:
    return " / ".join(str(x) for x in (e.get("phase"), e.get("step")) if x)


def _block(text: str, limit: int = 1500) -> str:
    text = text.strip()
    if len(text) > limit:
        text = text[:limit] + "\n..."
    return f"~~~\n{text}\n~~~"


def _answer_for_questions(events: list[dict[str, Any]], i: int) -> str:
    """The human's reply to the questions event at index i: the next answer at a gate (or an
    old-style questions answer), or an approval that left them open."""
    for a in events[i + 1 :]:
        kind = a.get("kind")
        if kind == "answer" and (a.get("gate") in ("tests", "final") or a.get("waiting")):
            if a.get("aborted"):
                return "aborted: the spec had to change (agentflow answer --abort)"
            return str(a.get("answer") or "(see answers.md)").strip()
        if kind == "human_gate" and a.get("approved"):
            return f"(not answered: the {a.get('gate')} gate was approved with it open)"
        if kind == "tests_gate_auto" and a.get("passed"):
            return "(not answered)"
    return "(not answered yet)"


def build(
    feature: str,
    events: list[dict[str, Any]],
    review_notes: str = "",
    assumptions: list[Any] | None = None,
) -> RetroInput:
    """Render retro_input.md from the feature's events (in order) and the implementer's
    assumptions (report.Assumption: where, spec_id, text)."""
    sections: dict[str, list[str]] = {k: [] for k in ID_PREFIXES}
    ids: list[str] = []

    def item(prefix: str, title: str, body: list[str]) -> None:
        item_id = f"{prefix}{len(sections[prefix]) + 1}"
        ids.append(item_id)
        sections[prefix].append("\n".join([f"### {item_id} — {title}", *body]))

    rounds = 0
    for i, e in enumerate(events):
        kind = e.get("kind")
        if kind == "questions":
            rounds += 1
            reply = _answer_for_questions(events, i)
            who = f" ({e['who']})" if e.get("who") else ""
            for q in split_questions(str(e.get("questions", ""))):
                item(
                    "Q",
                    (_where(e) or "unknown phase") + who,
                    [f"Question: {q}", "Answer:", _block(reply, 800)],
                )
        elif kind == "gate" and not e.get("passed"):
            failed = ", ".join(e.get("failed") or []) or "(not recorded)"
            item(
                "F",
                f"{_where(e)}, attempt {e.get('attempt')}",
                [f"Failing checks: {failed}", _block(str(e.get("excerpt") or "(no excerpt)"))],
            )
        elif kind == "escalation" and e.get("resolution"):
            failed = ", ".join(e.get("failed") or []) or "(not recorded)"
            item(
                "E",
                f"{_where(e)} stopped at the turn cap {e.get('attempts')} times",
                [f"Failing checks: {failed}", f"Resolution: {e['resolution']}"],
            )
        elif kind == "escalation":
            resolution = next(
                (
                    a
                    for a in events[i + 1 :]
                    if a.get("kind") == "answer" and a.get("waiting") == "escalation"
                ),
                None,
            )
            res = (
                "(not resolved yet)"
                if resolution is None
                else "aborted: the spec had to change"
                if resolution.get("aborted")
                else "retried after the human's answer: "
                + (str(resolution.get("answer") or "").strip()[:500] or "(no answers.md)")
            )
            failed = ", ".join(e.get("failed") or []) or "(not recorded)"
            item(
                "E",
                f"{_where(e)} failed {e.get('attempts')} attempts",
                [f"Failing checks (last attempt): {failed}", f"Resolution: {res}"],
            )
        elif kind == "human_gate" and e.get("approved") is False:
            item("R", f"{e.get('gate')} gate rejected", [f"Reason: {e.get('reason')}"])
        elif kind == "review_findings" and any(a.get("kind") == "triage" for a in events[i + 1 :]):
            later = events[i + 1 :]
            triage = next(a for a in later if a.get("kind") == "triage")
            verdicts = {f.get("test"): f for f in triage.get("findings") or []}
            fixed = any(a.get("kind") == "findings_fixed" for a in later)
            parked = any(a.get("kind") == "parked" and a.get("item") == "finding" for a in later)
            for name in e.get("tests") or []:
                v = verdicts.get(name) or {}
                verdict = str(v.get("verdict") or "VALID (no verdict line)")
                body = [f"Triage (test reviewer): {verdict}"]
                if v.get("reason"):
                    body.append(f"Reason: {v['reason']}")
                if verdict.startswith("VALID"):
                    res = (
                        "fixed by the implementer"
                        if fixed
                        else "parked (not fixed)"
                        if parked
                        else "(open)"
                    )
                    body.append(f"Resolution: {res}")
                item("V", str(name), body)
        elif kind == "review_findings":
            decision = next(
                (
                    a
                    for a in events[i + 1 :]
                    if a.get("kind") == "human_gate" and a.get("gate") == "review"
                ),
                None,
            )
            verdict = (
                "(no decision yet)"
                if decision is None
                else "accepted: the implementer fixed it"
                if decision.get("approved")
                else f"rejected (no fix): {decision.get('reason')}"
            )
            for name in e.get("tests") or []:
                item("V", str(name), [f"Human decision: {verdict}"])
        elif kind == "final_gates" and not e.get("passed"):
            failed = ", ".join(e.get("failed") or []) or "(not recorded)"
            item(
                "G",
                f"final gates failed: {failed}",
                [_block(str(e.get("excerpt") or "(see final_gates.md)"))],
            )

        elif kind == "parked":
            body = [f"Kind: {e.get('item')}; reason: {e.get('reason')}"]
            if e.get("question"):
                body += ["Question:", _block(str(e["question"]), 800)]
            if e.get("failed"):
                body.append(f"Failing checks (last attempt): {', '.join(e['failed'])}")
            if e.get("tests"):
                body.append(f"Failing review tests: {', '.join(e['tests'])}")
            if e.get("excerpt"):
                body.append(_block(str(e["excerpt"]), 800))
            redo = next(
                (
                    a
                    for a in events[i + 1 :]
                    if a.get("kind") == "redo_result" and a.get("label") == e.get("label")
                ),
                None,
            )
            res = (
                "open (not redone)"
                if redo is None
                else "answered at the final gate and redone: "
                + ("passed" if redo.get("passed") else "parked again")
            )
            body.append(f"Resolution: {res}")
            item("P", f"{e.get('label')} parked ({e.get('attempts')} failed attempts)", body)
        elif kind == "permission_denials":
            for d in e.get("denials") or []:
                item(
                    "D",
                    f"{_where(e) or 'unknown phase'}: {d.get('tool', '?')} denied",
                    ["Denied call:", _block(str(d.get("input", "")), 400)],
                )

    for a in assumptions or []:
        item("A", f"{a.where or 'steps.md'} [{a.spec_id}]", [f"Assumption: {a.text}"])

    counts = {k: len(v) for k, v in sections.items()}
    names = {
        "Q": f"questions {counts['Q']} ({rounds} round{'s' if rounds != 1 else ''})",
        "F": f"failed attempts {counts['F']}",
        "E": f"escalations {counts['E']}",
        "R": f"human rejections {counts['R']}",
        "V": f"review findings {counts['V']}",
        "G": f"final gate failures {counts['G']}",
        "D": f"permission denials {counts['D']}",
        "A": f"assumptions {counts['A']}",
        "P": f"parked items {counts['P']}",
    }
    out = [
        f"# Retro input: {feature}",
        "",
        "Counts: " + "; ".join(names.values()),
        "",
        "Every item ID below (Q1, F1, ...) must appear in retro_proposals.md: in a proposal, or",
        'in the "Not acted on" list with a one-line reason.',
    ]
    for prefix, title in ID_PREFIXES.items():
        out += ["", f"## {title}", ""]
        out += ["\n\n".join(sections[prefix]) if sections[prefix] else "None."]
    if review_notes.strip():
        out += ["", "## Review notes (review.md, for context)", "", _block(review_notes, 2000)]
    return RetroInput("\n".join(out).rstrip() + "\n", ids, counts)


def missing_ids(proposals: str, ids: list[str]) -> list[str]:
    """IDs from retro_input.md that retro_proposals.md never mentions."""
    present = {m.group(0) for m in ITEM_ID.finditer(proposals)}
    return [i for i in ids if i not in present]
