"""Retro input: every question, failed attempt, escalation, human rejection, review finding and
final-gate failure of a feature, with item IDs (Q1, F1, E1, R1, V1, G1) the RETRO agent must
address in retro_proposals.md. Built from out/events.jsonl before the RETRO agent runs."""

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
}
ITEM_ID = re.compile(r"\b([QFERVG])(\d+)\b")


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


def build(feature: str, events: list[dict[str, Any]], review_notes: str = "") -> RetroInput:
    """Render retro_input.md from the feature's events (in order)."""
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
            answer = next(
                (
                    a
                    for a in events[i + 1 :]
                    if a.get("kind") == "answer" and a.get("waiting") == "questions"
                ),
                None,
            )
            if answer is None:
                reply = "(not answered yet)"
            elif answer.get("aborted"):
                reply = "aborted: the spec had to change (agentflow answer --abort)"
            else:
                reply = str(answer.get("answer") or "(see answers.md)").strip()
            for q in split_questions(str(e.get("questions", ""))):
                item(
                    "Q",
                    _where(e) or "unknown phase",
                    [f"Question: {q}", "Answer:", _block(reply, 800)],
                )
        elif kind == "gate" and not e.get("passed"):
            failed = ", ".join(e.get("failed") or []) or "(not recorded)"
            item(
                "F",
                f"{_where(e)}, attempt {e.get('attempt')}",
                [f"Failing checks: {failed}", _block(str(e.get("excerpt") or "(no excerpt)"))],
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

    counts = {k: len(v) for k, v in sections.items()}
    names = {
        "Q": f"questions {counts['Q']} ({rounds} round{'s' if rounds != 1 else ''})",
        "F": f"failed attempts {counts['F']}",
        "E": f"escalations {counts['E']}",
        "R": f"human rejections {counts['R']}",
        "V": f"review findings {counts['V']}",
        "G": f"final gate failures {counts['G']}",
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
