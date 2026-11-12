"""The final brief's questions (final_brief.md, written by the PR agent).

The final gate has numbered items (parked work, the provisional steps, the open questions;
`Orchestrator.final_items`). The PR agent sorts every item into one of three sections, naming it
by its gate-item number `[N]`:

    ## Questions          a real decision: the problem in one line, then
    1. [3] <problem>        "A: ... (default) / B: ..." on the next line; ordered by impact
       A: ... (default) / B: ...
    ## Decisions I made   only one sensible option: decided, one line
    - [2] <decision>        (untagged lines are decisions that came from no gate item)
    ## Left out           answered, resolved, a note, or the same question twice
    - [4] same as [3]

The human sees the questions numbered 1..k in the brief's order; `BriefPlan` maps those numbers
back to the gate items, and `expand_answers` turns the human's answers into answers for every
gate item (a decision: its decision; the same question: the other one's answer). Parked work and
the provisional steps block the run, so they are always asked. A gate item the brief does not
place is asked as it is. `check_brief` lists what the PR agent should rewrite (not blocking)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: first line of an answers.md mapped to the gate items (`expand_answers`)
MAPPED_HEADER = "# Answers (final gate, numbered by gate item)"
_ENTRY = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
_TAG = re.compile(r"^\[(\d+)\]\s*(.*)$", re.S)
_SAME_AS = re.compile(r"\bsame as (?:item |question )?\[?(\d+)\]?", re.I)
_OPTION_B = re.compile(r"(?:^|[\s/(])B\s*[:)]")
_CLOSED = re.compile(
    r"\b(closed|resolved|already answered|answered (?:at|in|by)|no action|nothing to decide|"
    r"no decision|note only|just a note|for information|for your information|fyi|"
    r"informational)\b",
    re.I,
)


@dataclass
class Entry:
    tag: int | None  # the gate-item number, None when the entry names none
    text: str  # without the tag; continuation lines joined with newlines
    num: int | None = None  # the list number of a `N. text` line


@dataclass
class BriefPlan:
    #: (gate item, text) the human is asked, in the order shown (1..k)
    questions: list[tuple[int, str]] = field(default_factory=list)
    #: (gate item or None, the decision) made by the PR agent, shown as "Decisions I made"
    decided: list[tuple[int | None, str]] = field(default_factory=list)
    #: (gate item, reason, the item it repeats or None), not shown
    left_out: list[tuple[int, str, int | None]] = field(default_factory=list)

    @property
    def remapped(self) -> bool:
        """The brief's numbers differ from the gate items' (an answer must be mapped)."""
        hidden = any(t is not None for t, _ in self.decided) or bool(self.left_out)
        return hidden or [t for t, _ in self.questions] != list(range(1, len(self.questions) + 1))


def entries(lines: list[str]) -> list[Entry]:
    """Top-level `- x` / `N. x` lines of a brief section (indented lines continue the entry
    above) -> entries, with the leading `[N]` gate-item tag split off."""
    out: list[Entry] = []
    for line in lines:
        m = _ENTRY.match(line)
        if m and not line[:1].isspace():
            text = m.group(1).strip()
            t = _TAG.match(text)
            n = re.match(r"^(\d+)[.)]", line)
            out.append(
                Entry(
                    int(t.group(1)) if t else None,
                    t.group(2).strip() if t else text,
                    int(n.group(1)) if n else None,
                )
            )
        elif out and line.strip():
            out[-1].text = (out[-1].text + "\n" + line.strip()).strip()
    return out


def sections(text: str) -> dict[str, list[str]]:
    """A brief file (final_brief.md, the `## What the tests check` part of test_review.md) ->
    {heading in lower case: its non-empty lines}."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = out.setdefault(line[3:].strip().lower(), [])
        elif current is not None and line.strip() and not line.startswith("#"):
            current.append(line.rstrip())
    return out


def _section(sections: dict[str, list[str]], *names: str) -> list[str]:
    for key, lines in sections.items():
        if any(key.startswith(n) for n in names):
            return lines
    return []


def question_entries(sections: dict[str, list[str]]) -> list[Entry]:
    """The brief's questions. An older brief's `## Items` numbered by gate item (`3. text`,
    no tag) counts as tagged with that number."""
    qs = _section(sections, "questions", "open questions")
    if qs:
        return entries(qs)
    return [
        Entry(e.tag if e.tag is not None else e.num, e.text, e.num)
        for e in entries(_section(sections, "items"))
    ]


def plan(sections: dict[str, list[str]], items: list[str], must_ask: set[int]) -> BriefPlan:
    """Which gate items (1..len(items)) the human is asked, which the PR agent decided or left
    out. `must_ask`: items that are always asked (parked work, the provisional steps)."""
    n_items = len(items)
    p = BriefPlan()
    placed: set[int] = set()

    def ok(tag: int | None) -> bool:
        return tag is not None and 1 <= tag <= n_items and tag not in placed

    for e in question_entries(sections):
        if ok(e.tag):
            assert e.tag is not None
            p.questions.append((e.tag, e.text))
            placed.add(e.tag)
    forced: list[int] = []
    for e in entries(_section(sections, "decisions")):
        if e.tag is None:
            p.decided.append((None, e.text))
        elif ok(e.tag):
            placed.add(e.tag)
            if e.tag in must_ask:
                forced.append(e.tag)
            else:
                p.decided.append((e.tag, e.text))
    asked = {t for t, _ in p.questions}
    for e in entries(_section(sections, "left out")):
        if not ok(e.tag):
            continue
        assert e.tag is not None
        placed.add(e.tag)
        m = _SAME_AS.search(e.text)
        same = int(m.group(1)) if m else None
        if e.tag in must_ask and same not in asked:
            forced.append(e.tag)
        else:
            p.left_out.append((e.tag, e.text, same))
    for tag in sorted(forced) + [n for n in range(1, n_items + 1) if n not in placed]:
        p.questions.append((tag, items[tag - 1]))
    # "same as" an item that is not asked: ask this one as it is
    asked = {t for t, _ in p.questions}
    keep = []
    for tag, reason, same in p.left_out:
        if same is not None and same not in asked and same not in {t for t, _ in p.decided}:
            p.questions.append((tag, items[tag - 1]))
        else:
            keep.append((tag, reason, same))
    p.left_out = keep
    return p


def expand_answers(p: BriefPlan, given: list[dict[str, Any]], items: list[str]) -> str:
    """answers.md numbered as the brief showed the questions -> answers.md numbered by gate
    item, with an answer for every item the brief decided or left out."""
    out: dict[int, tuple[str, str]] = {}
    for it in given:
        k = int(it["n"])
        if 1 <= k <= len(p.questions):
            tag, text = p.questions[k - 1]
            out[tag] = (str(it.get("q") or text), str(it.get("a") or ""))
    for dtag, decision in p.decided:
        if dtag is not None and dtag not in out:
            out[dtag] = (items[dtag - 1], f"Default: {decision} (decided in the final brief)")
    for tag, reason, same in p.left_out:
        if tag in out:
            continue
        if same is not None and same in out:
            out[tag] = (items[tag - 1], f"Same as item {same}: {out[same][1]}")
        else:
            out[tag] = (items[tag - 1], f"No answer needed (left out of the brief: {reason})")
    lines = [MAPPED_HEADER, ""]
    for tag in sorted(out):
        q, a = out[tag]
        q_lines = q.strip().splitlines() or [""]
        a_lines = a.strip().splitlines() or [""]
        lines.append(f"{tag}. Q: {q_lines[0]}")
        lines += [f"      {x}" for x in q_lines[1:]]
        lines.append(f"   A: {a_lines[0]}")
        lines += [f"      {x}" for x in a_lines[1:]]
    return "\n".join(lines) + "\n"


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.splitlines()[0].lower())) if text else ""


def check_brief(sections: dict[str, list[str]], n_items: int, must_ask: set[int]) -> list[str]:
    """What the PR agent should rewrite in final_brief.md: questions without a gate-item number
    or with one option, duplicates, closed items or notes asked as questions, parked work left
    out, gate items placed nowhere. [] = fine."""
    problems: list[str] = []
    seen: dict[int, str] = {}
    texts: dict[str, int | None] = {}
    for e in question_entries(sections):
        name = f"question [{e.tag}]" if e.tag is not None else f'question "{_clip(e.text)}"'
        if e.tag is None or not 1 <= e.tag <= n_items:
            problems.append(
                f"{name}: name the gate item it answers as [N] (1-{n_items}, final_facts.md)"
            )
        elif e.tag in seen:
            problems.append(f"{name}: gate item {e.tag} is asked twice; ask it once")
        else:
            seen[e.tag] = e.text
        key = _norm(e.text)
        if key and key in texts and texts[key] != e.tag:
            problems.append(
                f"{name}: the same question as [{texts[key]}]; ask it once and put the other "
                "under ## Left out as `same as [N]`"
            )
        texts.setdefault(key, e.tag)
        if _CLOSED.search(e.text):
            problems.append(
                f"{name}: reads as closed or a note, not a decision; move it to ## Left out "
                "with the reason"
            )
        if not _OPTION_B.search(e.text):
            problems.append(
                f"{name}: has one option; a question needs two (A: ... (default) / B: ...), "
                "else decide it and list it under ## Decisions I made"
            )
    placed = set(seen)
    for e in entries(_section(sections, "decisions")):
        if e.tag is None:
            continue
        placed.add(e.tag)
        if e.tag in must_ask:
            problems.append(
                f"decision [{e.tag}]: parked work or the provisional steps must be asked "
                "(## Questions, your decision as the default)"
            )
    for e in entries(_section(sections, "left out")):
        if e.tag is None:
            continue
        placed.add(e.tag)
        if e.tag in must_ask and not _SAME_AS.search(e.text):
            problems.append(
                f"left out [{e.tag}]: parked work or the provisional steps must be asked "
                "(## Questions)"
            )
    missing = [n for n in range(1, n_items + 1) if n not in placed]
    if missing:
        problems.append(
            "gate items "
            + ", ".join(f"[{n}]" for n in missing)
            + " are in none of ## Questions / ## Decisions I made / ## Left out"
        )
    return problems


def _clip(text: str, n: int = 60) -> str:
    first = text.splitlines()[0] if text else ""
    return first if len(first) <= n else first[: n - 3] + "..."
