"""The retro backlog: what became of the retros' proposals.

`{metrics}/retro_backlog.md` (tracked) holds one row per proposal; the retro's full text is
kept beside it as `{metrics}/retro/<feature>.md`, because `agentflow cleanup` deletes the run
folder. Before this, an accepted proposal lived only in retro_proposals.md until the cleanup,
and nothing said whether it was ever applied or whether the same problem came back.

- `agentflow keep <feature> <numbers | take | all | none>`: the human's pick after the retro.
  Picked proposals become `open` rows, the others `skipped` (counted if they come back).
- The next retro gets the backlog in retro_input.md and names an item it sees again
  (`repeats B3` in its brief line); agentflow adds the feature to that row's "Seen".
- `agentflow backlog [show | done | drop | reopen]`: list the rows, mark what was applied.
"""

from __future__ import annotations

import pathlib
import re
import shutil
from dataclasses import dataclass, field

from agentflow.checks.spec_lint import tables
from agentflow.config import Config

FILE = "retro_backlog.md"
#: open: accepted, not applied yet; skipped: not accepted (counted if it comes back)
STATUSES = ("open", "applied", "dropped", "skipped")
HEADER = """\
# Retro backlog

Proposals from this project's retros. Written by `agentflow keep` and `agentflow backlog`
(edit the Note column freely; change a status with `agentflow backlog done | drop | reopen`).
Status: open = accepted, not applied yet; applied; dropped; skipped = not accepted, counted
if it comes back. Seen: the features whose retro raised it. Detail: the retro's full text.

| ID | Status | Scope | Seen | Proposal | Detail | Note |
|---|---|---|---|---|---|---|
"""

_BRIEF_LINE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_RECOMMEND = re.compile(r"\(\s*recommend(?:ed)?\s*:?\s*([^()]*)\)\s*$", re.I)
_REPEATS = re.compile(r"\brepeats?\s+B(\d+)\b", re.I)
_FOLD = re.compile(r"\bfold(?:ed)?\s+into\s+(\d+)", re.I)
_SCOPE = re.compile(r"\bscope\b\W{0,8}(framework|project)\b", re.I)
_SECTION = re.compile(r"^##\s+(\d+)[.)]\s", re.M)


class BacklogError(Exception):
    pass


@dataclass
class Item:
    num: int
    status: str
    scope: str
    seen: list[str]
    text: str
    detail: str = ""
    note: str = ""

    @property
    def id(self) -> str:
        return f"B{self.num}"

    def line(self) -> str:
        times = f", seen {len(self.seen)}x" if len(self.seen) > 1 else ""
        return f"{self.id} [{self.status}, {self.scope}{times}] {self.text}"


@dataclass
class Proposal:
    num: int
    text: str
    recommend: str = ""  # take | skip | fold
    fold_into: int | None = None
    repeats: int | None = None
    scope: str = "?"


@dataclass
class Kept:
    """What `keep` did, for its report."""

    opened: list[Item] = field(default_factory=list)
    skipped: list[Item] = field(default_factory=list)
    folded: list[int] = field(default_factory=list)


def path(cfg: Config) -> pathlib.Path:
    return cfg.root / cfg.paths.metrics / FILE


def retro_copy(cfg: Config, feature: str) -> pathlib.Path:
    return cfg.root / cfg.paths.metrics / "retro" / f"{feature}.md"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def load(cfg: Config) -> list[Item]:
    p = path(cfg)
    if not p.is_file():
        return []
    items = []
    for table in tables(p.read_text(encoding="utf-8")):
        if table.column("id") != 0 or table.column("status") is None:
            continue
        for row in table.rows:
            cells = [*row, "", "", "", "", "", "", ""][:7]
            m = re.match(r"^B(\d+)$", cells[0])
            if not m:
                continue
            seen = [x.strip() for x in cells[3].split(":", 1)[-1].split(",") if x.strip()]
            status = cells[1].lower() if cells[1].lower() in STATUSES else "open"
            items.append(
                Item(int(m.group(1)), status, cells[2] or "?", seen, cells[4], cells[5], cells[6])
            )
    return items


def save(cfg: Config, items: list[Item]) -> None:
    rows = [
        f"| {i.id} | {i.status} | {i.scope} | {len(i.seen)}: {', '.join(i.seen)} | "
        f"{_cell(i.text)} | {_cell(i.detail)} | {_cell(i.note)} |"
        for i in sorted(items, key=lambda i: i.num)
    ]
    p = path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HEADER + "\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def parse_proposals(text: str) -> list[Proposal]:
    """retro_proposals.md -> the proposals of its `## Brief` (one numbered line each, with the
    retro's `(recommend: take | skip | fold into N[; repeats B3])`), each with the scope its
    detailed section names."""
    out: list[Proposal] = []
    inside = False
    wrapped = False  # the lines right under a numbered line continue it; a blank line ends it
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.lstrip("#").strip().lower().startswith("brief")
            continue
        if not inside:
            continue
        m = _BRIEF_LINE.match(line)
        if m:
            out.append(Proposal(int(m.group(1)), m.group(2).strip()))
            wrapped = True
        elif wrapped and line.strip():
            out[-1].text += " " + line.strip()
        else:
            wrapped = False
    sections = _detail_sections(text)
    for p in out:
        rec = _RECOMMEND.search(p.text)
        tail = rec.group(1).lower() if rec else ""
        rep = _REPEATS.search(p.text)
        p.repeats = int(rep.group(1)) if rep else None
        fold = _FOLD.search(tail)
        p.fold_into = int(fold.group(1)) if fold else None
        p.recommend = "fold" if fold else "skip" if "skip" in tail else "take" if rec else ""
        p.text = (p.text[: rec.start()] if rec else p.text).strip()
        scope = _SCOPE.search(sections.get(p.num, ""))
        p.scope = scope.group(1).lower() if scope else "?"
    return out


def _detail_sections(text: str) -> dict[int, str]:
    """`## N. title` sections of retro_proposals.md -> their text (heading included)."""
    out: dict[int, list[str]] = {}
    current: list[str] | None = None
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith("## "):  # a heading inside a code block is text
            m = _SECTION.match(line)
            current = out.setdefault(int(m.group(1)), []) if m else None
        if current is not None:
            current.append(line)
    return {n: "\n".join(lines).rstrip() for n, lines in out.items()}


def retro_section(cfg: Config) -> str:
    """For retro_input.md: the backlog rows the retro compares its findings with ("" = none)."""
    items = load(cfg)
    if not items:
        return ""
    return (
        "## Backlog (proposals of earlier retros; see your instructions)\n\n"
        + "\n".join(f"- {i.line()}" for i in items)
        + "\n"
    )


def note_repeats(cfg: Config, feature: str, proposals: str) -> list[str]:
    """After a retro: add the feature to "Seen" of every backlog row a proposal names
    (`repeats B3`). Returns the IDs that changed (the file is saved, not committed)."""
    items = {i.num: i for i in load(cfg)}
    changed = []
    for p in parse_proposals(proposals):
        item = items.get(p.repeats) if p.repeats else None
        if item and feature not in item.seen:
            item.seen.append(feature)
            changed.append(item.id)
    if changed:
        save(cfg, list(items.values()))
    return changed


def _picked(picks: list[str], proposals: list[Proposal]) -> set[int]:
    words = [w.strip().lower() for x in picks for w in x.split(",") if w.strip()]
    if not words:
        raise BacklogError("Name the proposals to keep: numbers (1 3 5), take, all or none.")
    nums = {p.num for p in proposals}
    chosen: set[int] = set()
    for w in words:
        if w == "all":
            chosen |= {p.num for p in proposals if p.recommend != "fold"}
        elif w == "take":
            chosen |= {p.num for p in proposals if p.recommend == "take"}
        elif w == "none":
            continue
        elif w.isdigit() and int(w) in nums:
            chosen.add(int(w))
        else:
            have = ", ".join(str(n) for n in sorted(nums))
            raise BacklogError(
                f"Unknown pick '{w}': numbers of the brief ({have}), take, all, none."
            )
    return chosen


def keep(cfg: Config, feature: str, picks: list[str]) -> Kept:
    """Record the human's pick of a retro's proposals: the retro's text is copied to
    {metrics}/retro/<feature>.md, picked proposals become (or stay) open rows, the others
    skipped rows; a proposal folded into another gets no row of its own. Run again for the
    same feature, it changes that feature's rows instead of adding new ones."""
    src = cfg.feature_out(feature) / "retro_proposals.md"
    copy = retro_copy(cfg, feature)
    if src.is_file():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, copy)
    if not copy.is_file():
        raise BacklogError(
            f"No retro proposals for {feature} ({cfg.paths.out}/{feature}/retro_proposals.md): "
            "run the retro first."
        )
    proposals = parse_proposals(copy.read_text(encoding="utf-8"))
    if not proposals:
        raise BacklogError(f"{copy.name} has no numbered proposals under '## Brief'.")
    chosen = _picked(picks, proposals)
    items = load(cfg)
    by_num = {i.num: i for i in items}
    rel = copy.relative_to(cfg.root).as_posix()
    result = Kept()
    for p in proposals:
        ref = f"{rel} #{p.num}"
        picked = p.num in chosen
        item = by_num.get(p.repeats) if p.repeats else None
        item = item or next((i for i in items if i.detail == ref), None)
        if item is None:
            if p.recommend == "fold" and not picked:
                result.folded.append(p.num)
                continue
            item = Item(max(by_num, default=0) + 1, "skipped", p.scope, [feature], p.text, ref)
            items.append(item)
            by_num[item.num] = item
        elif feature not in item.seen:
            item.seen.append(feature)
        if item.detail != ref and ref not in item.note:
            item.note = f"{item.note}; again: {ref}".strip("; ")
        if picked and item.status != "open":  # also an applied fix that did not hold
            item.status = "open"
        elif not picked and item.status == "open" and item.detail == ref:
            item.status = "skipped"  # a second `keep` for this feature that leaves it out
        (result.opened if picked else result.skipped).append(item)
    save(cfg, items)
    return result


def find(items: list[Item], ids: list[str]) -> list[Item]:
    wanted = []
    for raw in ids:
        for word in raw.split(","):
            m = re.match(r"^B?(\d+)$", word.strip(), re.I)
            if not m:
                raise BacklogError(f"'{word}' is not a backlog ID (B3).")
            item = next((i for i in items if i.num == int(m.group(1))), None)
            if item is None:
                raise BacklogError(f"B{m.group(1)} is not in the backlog.")
            wanted.append(item)
    if not wanted:
        raise BacklogError("Name the backlog IDs (B3 B5).")
    return wanted


def set_status(cfg: Config, ids: list[str], status: str, note: str = "") -> list[Item]:
    items = load(cfg)
    changed = find(items, ids)
    for item in changed:
        item.status = status
        if note:
            item.note = f"{item.note}; {note}".strip("; ")
    save(cfg, items)
    return changed


def listing(cfg: Config, every: bool = False, scope: str | None = None) -> list[str]:
    """The rows as lines: open ones (most often seen first); `every`: all of them."""
    items = [i for i in load(cfg) if every or i.status == "open"]
    if scope:
        items = [i for i in items if i.scope == scope]
    items.sort(key=lambda i: (STATUSES.index(i.status), -len(i.seen), i.num))
    return [i.line() + (f" ({i.note})" if i.note else "") for i in items]


def show(cfg: Config, item_id: str) -> str:
    """One row with the retro's full section for it."""
    item = find(load(cfg), [item_id])[0]
    lines = [item.line(), f"Seen in: {', '.join(item.seen)}", f"Detail: {item.detail}"]
    if item.note:
        lines.append(f"Note: {item.note}")
    name, _, num = item.detail.partition(" #")
    source = cfg.root / name
    if source.is_file() and num.isdigit():
        section = _detail_sections(source.read_text(encoding="utf-8")).get(int(num))
        if section:
            lines += ["", section]
    return "\n".join(lines)
