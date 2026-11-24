"""The default of every gate question is the option that changes nothing: the code, the tests
and the spec as they are, parked work left parked, a proposed change left for later. Answers
that only take defaults start no run: the gate is approved as it stands (Orchestrator.answer).

An answer that names an option (its letter, its number, or its own words) is that option;
the words after it are a note and decide nothing. A question with options answered with
none of them is asked once more (`unclear`). A question that says NO_DEFAULT has none: the
owner chooses.
"""

from __future__ import annotations

import re

_SAME_AS = re.compile(r"^\W*same as item \d+\s*:\s*", re.I)
#: an answer that is a letter: "B", "B: split it", "option A."
_LETTER = re.compile(r"^\W*(?:option\s+)?([A-D])(?:\s*[:.),-]|\s*$)")
#: "A: leave it (default) / B: ..." in a question
_MARKED = re.compile(
    r"(?:^|[\s/(|])([A-D])\s*[:)]\s*((?:(?! / |\n|\s[A-D]\s*[:)]).)*?)\s*"
    r"\((?:default|no change)\)"
)
#: in a question without a default; its change-nothing option is marked `(no change)`
NO_DEFAULT = "No default: choose."
_OPTION = re.compile(r"(?:^|[\s/(|])([A-D])\s*[:)]\s")
_NUMBER = re.compile(r"^\W*(?:option\s+)?([1-4])(?:\s*[:.),-]|\s*$)")
_OPTION_TEXT = re.compile(r"(?:^|[\s/(|])([A-D])\s*[:)]\s+((?:(?!\s/\s|\s[A-D]\s*[:)]\s|\n).)*)")
_MARKS = {"default", "recommended"}
#: the chat protocol's own answer for "apply what was proposed": clear without a letter
_APPROVES = re.compile(r"^\W*approved?\b", re.I)
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


def options(question: str) -> list[str]:
    """The option letters a question offers ([] = it offers no choice of letters)."""
    found = sorted(set(_OPTION.findall(question)))
    return found if found[:2] == ["A", "B"] else []


def no_default(question: str) -> bool:
    return NO_DEFAULT in question


def option_words(question: str) -> dict[str, list[str]]:
    """Letter -> the words of that option (the first wording of each letter)."""
    out: dict[str, list[str]] = {}
    for letter, text in _OPTION_TEXT.findall(question):
        out.setdefault(letter, [w for w in _words(text) if w not in _MARKS])
    return out


def chosen(question: str, answer: str) -> tuple[str, str]:
    """(the option the answer names, "" = none; the words after it): "B", "B: split it",
    "option A.", "2" (the second option), or one option's own words ("split it now")."""
    a = _SAME_AS.sub("", answer.strip())
    offered = options(question)
    m = _LETTER.match(a)
    if m and (not offered or m.group(1) in offered):
        return m.group(1), a[m.end() :].strip()
    n = _NUMBER.match(a)
    if n and int(n.group(1)) <= len(offered):
        return offered[int(n.group(1)) - 1], a[n.end() :].strip()
    first, _, rest = a.partition("\n")
    said = _words(first)
    named = [
        letter
        for letter, words in option_words(question).items()
        if letter in offered
        and words
        and said
        # the option's words, cut short ("split it now") or with more after them
        and (
            words == said
            or (len(said) > 1 and words[: len(said)] == said)
            or said[: len(words)] == words
        )
    ]
    return (named[0], rest.strip()) if len(named) == 1 else ("", a)


def _plain_default(a: str) -> bool:
    """No answer, "ok", "leave it", "Default: ...": the default in so many words."""
    return bool(not a or "(default)" in a.lower() or _KEEP.match(a) or _OK.match(a))


def unclear(question: str, answer: str) -> bool:
    """A question with options, answered with none of them and not plainly with its
    default: the owner is asked to choose (no guess)."""
    a = _SAME_AS.sub("", answer.strip())
    if not options(question) or chosen(question, answer)[0]:
        return False
    if no_default(question):
        return True
    if _STATED.match(a) or _APPROVES.match(a):
        return False
    return "\n" in a or not _plain_default(a)


def takes_default(question: str, answer: str) -> bool:
    """Does `answer` take the default of `question` (no answer, "ok", "Default: ...", "leave
    it", the default's letter or its words)? A question without a default: does it choose
    the option that changes nothing?"""
    a = _SAME_AS.sub("", answer.strip())
    letter, text = default_option(question)
    pick, rest = chosen(question, answer)
    if no_default(question):
        return bool(pick) and pick == letter
    if _STATED.match(a):
        return True
    if pick and letter:  # the option decides; the words after it are a note
        return pick == letter
    if "\n" in a:  # more than a choice: new information
        return False
    if pick:
        a = rest
    if _plain_default(a):
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
