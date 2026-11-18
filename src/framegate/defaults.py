"""The default of every gate question is the option that changes nothing: the code, the tests
and the spec as they are, parked work left parked, a proposed change left for later. Answers
that only take defaults start no run: the gate is approved as it stands (Orchestrator.answer).
"""

from __future__ import annotations

import re

_SAME_AS = re.compile(r"^\W*same as item \d+\s*:\s*", re.I)
#: an answer that is a letter: "B", "B: split it", "option A."
_LETTER = re.compile(r"^\W*(?:option\s+)?([A-D])(?:\s*[:.),-]|\s*$)")
#: "A: leave it (default) / B: ..." in a question
_MARKED = re.compile(
    r"(?:^|[\s/(|])([A-D])\s*[:)]\s*((?:(?! / |\n|\s[A-D]\s*[:)]).)*?)\s*\(default\)"
)
_NAMED = re.compile(r"\bDefault\s*:\s*([A-D])(?=[\s.):]|$)")
_DEFAULT_TEXT = re.compile(r"\bDefault\s*:\s*(.+?)(?:\.(?:\s|$)|\n|$)", re.I)
#: the default written out by the chat or framegate ("Default: ...", "No answer needed (...)")
_STATED = re.compile(r"^\W*(?:default\b|no answer needed\b)", re.I)
_KEEP = re.compile(
    r"^\W*(?:default|leave|keep|later|as is|no change|no answer needed|not now|skip|defer"
    r"|approved?\s+as\s+(?:it\s+)?(?:is|stands))\b",
    re.I,
)
_OK = re.compile(
    r"^\W*(?:ok(?:ay)?|fine|accept(?:ed)?|agreed?)\b(?:\W+(?:both|all|it|that|them))?"
    r"\W*(?:\(default\))?\W*$",
    re.I,
)
_LATER = re.compile(
    r"\b(?:later|defer\w*|follow-?up|separate task|another task|next task|not now)\b", re.I
)
_CUT = re.compile(r"\s+(?:Options?\s*:|A\s*[:)]|Default\s*:)|\n", re.I)
_TAG = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def default_option(question: str) -> tuple[str | None, str]:
    """The default of a question: (its letter or None, its words or "")."""
    m = _MARKED.search(question)
    if m:
        return m.group(1), m.group(2).strip()
    m = _NAMED.search(question)
    if m:
        return m.group(1), ""
    m = _DEFAULT_TEXT.search(question)
    return None, m.group(1).strip() if m else ""


def takes_default(question: str, answer: str) -> bool:
    """Does `answer` take the default of `question` (no answer, "ok", "Default: ...", "leave
    it", the default's letter or its words)?"""
    a = _SAME_AS.sub("", answer.strip())
    if _STATED.match(a):
        return True
    if "\n" in a:  # more than a choice: new information
        return False
    letter, text = default_option(question)
    m = _LETTER.match(a)
    if m and letter:
        return m.group(1) == letter
    if m:
        a = a[m.end() :].strip()
    if not a or "(default)" in a.lower() or _KEEP.match(a) or _OK.match(a):
        return True
    if _LATER.search(a) and _LATER.search(text):
        return True
    said, dflt = _words(a), _words(text)
    if not (said and dflt):
        return False
    lead = 2 if len(said) <= 4 else 3  # "finish without the check" for "finish without this"
    return dflt[: len(said)] == said or said[:lead] == dflt[:lead]


def all_default(pairs: list[tuple[str, str]]) -> bool:
    """Every (question, answer) takes its default."""
    return all(takes_default(q, a) for q, a in pairs)


def defers_change(question: str) -> bool:
    """Is the question's default to leave a proposed change for later?"""
    return bool(_LATER.search(default_option(question)[1]))


def gist(question: str) -> str:
    """The question without its `[who]` tags and its options, one line."""
    text = _CUT.split(_TAG.sub("", question.strip()), maxsplit=1)[0].strip()
    return " ".join(text.split())
