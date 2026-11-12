"""Retro input: every question, failed attempt, escalation (turn or budget cap hit twice), human
rejection, review finding, final-gate failure, permission denial, implementer assumption and
parked item of a feature, with item IDs (Q1, F1, E1, R1, V1, G1, D1, A1, P1) the RETRO agent must
address in retro_proposals.md. Built from out/events.jsonl (+ the assumptions in steps.md)
before the RETRO agent runs."""

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
    "S": "Structure findings (reviewer)",
    "G": "Final gate failures",
    "D": "Permission denials",
    "A": "Assumptions (implementer, from steps.md)",
    "P": "Parked items",
}
ITEM_ID = re.compile(r"\b([QFERVSGDAP])(\d+)\b")


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


_ITEM_START = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_Q_PART = re.compile(r"^\s*\**Q\**\s*:\s*(.*)$", re.I)
_A_PART = re.compile(r"^\s*\**A\**\s*:\s*(.*)$", re.I)


def parse_answer_items(text: str) -> list[dict[str, Any]]:
    """answers.md -> [{n, q, a}], one per numbered item. The chat writes `N. Q: <question>` /
    `A: <answer>`; an item without `Q:`/`A:` is all answer (q = "")."""
    items: list[dict[str, Any]] = []
    part = "a"
    for line in text.splitlines():
        m = _ITEM_START.match(line)
        if m and not line.startswith((" " * 6, "\t\t")):
            rest = m.group(2)
            q = _Q_PART.match(rest)
            items.append({"n": int(m.group(1)), "q": q.group(1) if q else "", "a": ""})
            part = "q" if q else "a"
            if not q:
                items[-1]["a"] = rest
            continue
        if not items:
            continue
        a = _A_PART.match(line)
        if a:
            part = "a"
            items[-1]["a"] = (items[-1]["a"] + "\n" + a.group(1)).strip()
        elif line.strip():
            items[-1][part] = (items[-1][part] + "\n" + line.strip()).strip()
    return [dict(i, q=i["q"].strip(), a=i["a"].strip()) for i in items]


def _norm(text: str) -> str:
    """Lowercase words only, without the `[who]` / `[label]` prefixes the gates add."""
    text = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", text)
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def match_answer(question: str, items: list[dict[str, Any]]) -> str | None:
    """The answer of the numbered item whose question is `question` (matched on the first words,
    either way round: the gate may have clipped one of them), or None."""
    nq = _norm(question)
    if not nq:
        return None

    def within(short: str, long: str) -> bool:
        """The first words of `short` (without its last word: the gate may have clipped it
        mid-word) are words of `long`; at least 3 words, so it means something."""
        words = short.split()[:12]
        return len(words) >= 4 and f" {' '.join(words[:-1])} " in f" {long} "

    # `asked` (the question as the gate item had it) first: `q` is often the chat's rewording
    for key in ("asked", "q"):
        for it in items:
            ni = _norm(str(it.get(key) or ""))
            if ni and (ni == nq or within(nq, ni) or within(ni, nq)):
                return str(it.get("a") or "").strip() or "(empty answer)"
    return _closest_answer(question, items)


#: a spec ID, reduced to its kind and number (`FKR-R-010` and a short `R-010` are the same)
_SPEC_ID = re.compile(r"\b(?:[A-Z][A-Z0-9]*-)?([REI]-\d{3})\b")


#: an agent's own label in front of a question: `Q1:`, `Q1 (repeat):`
_Q_LABEL = re.compile(r"^\s*Q\d+\s*(?:\(repeat\))?\s*[:.]\s*", re.I)


def _question_words(text: str) -> set[str]:
    text = _Q_LABEL.sub("", re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", text))
    return {w for w in _norm(text).split() if len(w) >= 3}


def same_question(new: str, old: str) -> bool:
    """Is `new` the question `old` again (the same asker; seen live: the test writer repeated
    a question in the next test round, and the human answered it twice)? The same words after
    the agent's label, or: they share a spec ID (or name none) and at least 80% of the shorter
    one's words are in the other. Strict on purpose: a dropped real question costs more than a
    repeated one."""
    a, b = _question_words(new), _question_words(old)
    if not a or not b:
        return False
    if a == b:
        return True
    ia, ib = set(_SPEC_ID.findall(new)), set(_SPEC_ID.findall(old))
    if (ia or ib) and not ia & ib:
        return False
    return min(len(a), len(b)) >= 4 and len(a & b) / min(len(a), len(b)) >= 0.8


def _closest_answer(question: str, items: list[dict[str, Any]]) -> str | None:
    """The gate (or the chat) often rephrases a question in the answer's `Q:` line (seen live:
    all four test-writer questions came back reworded, so none matched on the first words).
    Fallback: the item that shares the question's spec IDs and most of its words; None when no
    item is close enough or two are equally close."""
    ids = set(_SPEC_ID.findall(question))
    words = {w for w in _norm(question).split() if len(w) >= 3}
    scored: list[tuple[float, str]] = []
    for it in items:
        q = str(it.get("q") or "")
        iw = {w for w in _norm(q).split() if len(w) >= 3}
        if not iw or not words:
            continue
        iids = set(_SPEC_ID.findall(q))
        if ids and iids and not ids & iids:
            continue  # about other spec IDs
        id_score = len(ids & iids) / len(ids | iids) if ids and iids else 0.0
        overlap = len(words & iw) / min(len(words), len(iw))
        if (id_score and overlap >= 0.3) or overlap >= 0.6:
            scored.append((id_score + overlap, str(it.get("a") or "").strip() or "(empty answer)"))
    scored.sort(key=lambda s: -s[0])
    if not scored or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


def archive_rounds(archive: str) -> list[list[dict[str, Any]]]:
    """questions_archive.md -> the numbered answer items of every answer round
    (`### Answer ANS-n` blocks), oldest first. An item without its own `Q:` line gets the
    question shown at that number in the same section."""
    rounds: list[list[dict[str, Any]]] = []
    for section in re.split(r"^---\s*$", archive, flags=re.M):
        head, sep, answer = section.partition("\n### Answer ")
        if not sep:
            continue
        answer = answer.split("\n", 1)[1] if "\n" in answer else ""
        shown = split_questions(re.sub(r"^\s*<!--.*?-->\s*$", "", head, flags=re.M))
        rounds.append(
            [
                dict(it, q=it["q"] or (shown[it["n"] - 1] if 0 < it["n"] <= len(shown) else ""))
                for it in parse_answer_items(answer)
            ]
        )
    return rounds


def _answer_for_questions(
    events: list[dict[str, Any]],
    i: int,
    question: str = "",
    archive: list[list[dict[str, Any]]] | None = None,
) -> str:
    """The human's reply to `question` of the questions event at index i: the matching numbered
    item of any later answer round (the events' rounds first, then the rounds of
    questions_archive.md), an old-style questions answer, or an approval that left it open.
    Not found although answer rounds and a spec update followed: "answer lookup failed" (the
    retro must not read that as a question nobody answered)."""
    rounds = 0
    for a in events[i + 1 :]:
        kind = a.get("kind")
        if kind == "answer" and (a.get("gate") in ("tests", "final") or a.get("waiting")):
            if a.get("aborted"):
                return "aborted: the spec had to change (framegate answer --abort)"
            text = str(a.get("answer") or "").strip()
            items = a.get("items") or parse_answer_items(text)
            found = match_answer(question, items) if question else None
            if found is not None:
                return found
            if not (items and question):
                return text or "(see answers.md)"
            rounds += 1
            continue
        if rounds:
            continue
        if kind == "human_gate" and a.get("approved"):
            return f"(not answered: the {a.get('gate')} gate was approved with it open)"
        if kind == "tests_gate_auto" and a.get("passed"):
            return "(not answered)"
    for items in archive or []:
        found = match_answer(question, items) if question else None
        if found is not None:
            return found
    if not rounds:
        return "(not answered yet)"
    if any(a.get("kind") == "spec_update" for a in events[i + 1 :]):
        return (
            f"(answer lookup failed: {rounds} answer round(s) and a spec update followed, but no "
            "numbered answer matched this question; see questions_archive.md)"
        )
    return "(not answered: no numbered answer is about this question)"


def _structure_fix(later: list[dict[str, Any]]) -> str:
    """What the structure fixes after a `structure_findings` event cost: the implementer
    runs and the gates of the item "structure fixes" up to its end (one item fixes all the
    findings of the review together)."""
    runs = gates = failed = 0
    agent = checks = cost = 0.0
    done = None
    for e in later:
        kind = e.get("kind")
        if kind == "structure_fixed":
            done = True
            break
        if kind in ("structure_findings", "final_gates"):
            break
        if e.get("step") != "structure fixes":
            continue
        if kind == "agent_run":
            runs += 1
            agent += float(e.get("seconds") or 0)
            cost += float(e.get("cost") or 0)
        elif kind == "gate":
            gates += 1
            failed += 0 if e.get("passed") else 1
            checks += float(e.get("seconds") or 0)
        elif kind == "parked":
            done = False
    if not runs:
        return "(not started)"
    state = "done" if done else "parked" if done is False else "not finished"
    return (
        f"{state}, together with the other findings of this review: {runs} implementer "
        f"run(s) ({round(agent)} s, ${cost:.2f}), {gates} gate run(s) ({round(checks)} s, "
        f"{failed} failed)"
    )


def build(
    feature: str,
    events: list[dict[str, Any]],
    review_notes: str = "",
    assumptions: list[Any] | None = None,
    archive: str = "",
) -> RetroInput:
    """Render retro_input.md from the feature's events (in order), the implementer's
    assumptions (report.Assumption: where, spec_id, text) and questions_archive.md (every
    answer round, for the question -> answer lookup)."""
    sections: dict[str, list[str]] = {k: [] for k in ID_PREFIXES}
    answer_rounds = archive_rounds(archive)
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
            who = f" ({e['who']})" if e.get("who") else ""
            for q in split_questions(str(e.get("questions", ""))):
                reply = _answer_for_questions(events, i, q, answer_rounds)
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
                f"{_where(e)} stopped at the "
                f"{'budget' if e.get('cap_kind') == 'budget' else 'turn'} cap "
                f"{e.get('attempts')} times",
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
        elif kind == "structure_findings":
            fix = _structure_fix(events[i + 1 :])
            for text in e.get("findings") or []:
                head = " ".join(str(text).split())
                item(
                    "S",
                    head[:70] + ("..." if len(head) > 70 else ""),
                    [f"Finding: {_block(str(text), 800)}", f"Fix: {fix}"],
                )
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
        elif kind == "provisional":
            settled = next(
                (
                    a
                    for a in events[i + 1 :]
                    if a.get("kind") == "provisional_settled" and a.get("label") == e.get("label")
                ),
                None,
            )
            res = (
                "open (no answer yet)"
                if settled is None
                else "test fixes approved: the contested tests pass, the code stayed"
                if settled.get("accepted")
                else "test fix not applied: the code was "
                + ("reverted" if settled.get("reverted") else "kept (revert conflicted)")
                + " and the step redone"
            )
            body = [
                f"Kind: provisional step (code kept; contested tests: "
                f"{', '.join(e.get('contested') or [])})",
                "Question:",
                _block(str(e.get("question") or ""), 800),
                f"Resolution: {res}",
            ]
            item("P", f"{e.get('label')} provisional (tests contested)", body)
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
        "S": f"structure findings {counts['S']}",
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


KNOWLEDGE_ITEM = re.compile(r"^\s*[-*]\s*K(\d+)\s*\[([^\]]+)\]\s*:?\s*(.+?)\s*$")


def knowledge_items(proposals: str) -> list[tuple[int, str, str]]:
    """The `## Project knowledge` section of retro_proposals.md -> [(n, section, fact)]."""
    items: list[tuple[int, str, str]] = []
    inside = False
    for line in proposals.splitlines():
        if line.startswith("#"):
            inside = line.lstrip("#").strip().lower().startswith("project knowledge")
            continue
        m = KNOWLEDGE_ITEM.match(line) if inside else None
        if m:
            items.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    return items


def add_knowledge(text: str, section: str, line: str) -> str:
    """`text` (a knowledge file) with `line` added at the end of `## <section>` (case-insensitive;
    a missing section is appended)."""
    lines = text.rstrip("\n").splitlines()
    start = next(
        (
            i
            for i, x in enumerate(lines)
            if x.startswith("## ") and x[3:].strip().lower() == section.strip().lower()
        ),
        None,
    )
    if start is None:
        return "\n".join([*lines, "", f"## {section.strip()}", line]) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    return "\n".join([*lines[:at], line, *lines[at:]]) + "\n"


def missing_ids(proposals: str, ids: list[str]) -> list[str]:
    """IDs from retro_input.md that retro_proposals.md never mentions."""
    present = {m.group(0) for m in ITEM_ID.finditer(proposals)}
    return [i for i in ids if i not in present]
