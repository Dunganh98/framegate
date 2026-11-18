"""An answered question is never asked again in the same task.

The answer events hold every gate item as it was asked (`asked`) and its answer. A new question
about the same thing gets no gate item: a file split the same file, a doc proposal the same file
and place (doc_proposals.key), any other question the same words. The summary and the final
facts get one line instead (`asked_again` events since the last answer).
"""

from __future__ import annotations

import re
from typing import Any

from framegate import doc_proposals

STRUCTURE_WHO = "structure report"
KIND = "asked_again"
_TAG = re.compile(r"^\[([^\]]+)\]\s*")
_NO_ANSWER = re.compile(r"^\W*no answer needed\b", re.I)
_SAME_AS = re.compile(r"^\W*same as item \d+\s*:\s*", re.I)


def who(question: str) -> str:
    """The `[who]` tag of a gate item ("" = none)."""
    m = _TAG.match(question)
    return m.group(1) if m else ""


def key(question: str) -> str:
    m = _TAG.match(question)
    who, body = (m.group(1), question[m.end() :]) if m else ("", question)
    if who == STRUCTURE_WHO:
        return "split " + body.split(" ", 1)[0]
    if who == doc_proposals.WHO:
        return "doc " + doc_proposals.key(doc_proposals.asked(question))
    return " ".join(re.findall(r"[a-z0-9]+", body.lower()))


def answers(events: list[dict[str, Any]]) -> dict[str, str]:
    """key -> the owner's last answer, over every answer round of the task."""
    out: dict[str, str] = {}
    for e in events:
        if e.get("kind") != "answer":
            continue
        for it in e.get("items") or []:
            asked, a = str(it.get("asked") or ""), _SAME_AS.sub("", str(it.get("a") or "").strip())
            if asked and a and not _NO_ANSWER.match(a):
                out[key(asked)] = a
    return out


def lines(events: list[dict[str, Any]]) -> list[str]:
    """One line per question proposed again since the last answer round ([] = none)."""
    last = max((i for i, e in enumerate(events) if e.get("kind") == "answer"), default=-1)
    seen: dict[str, str] = {}
    for e in events[last + 1 :]:
        if e.get("kind") == KIND:
            q = " ".join(str(e.get("question") or "").split())
            q = _TAG.sub("", q)
            q = q if len(q) <= 120 else q[:117] + "..."
            seen[key(str(e.get("question") or ""))] = (
                f"- {q}: proposed again by {e.get('who')}; your answer '{e.get('answer')}' stands"
            )
    return list(seen.values())


def section(events: list[dict[str, Any]]) -> list[str]:
    """The generated section of final_summary.md and final_facts.md ([] = none)."""
    found = lines(events)
    if not found:
        return []
    return [
        f"## Answered before: {len(found)}",
        "",
        *found,
        "",
        "No gate item: you answered these earlier in the task; your answer stands.",
    ]
