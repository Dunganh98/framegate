"""Reference folders and ports.

A reference is another project on this machine (framegate.toml `[references]`: name -> folder)
that the agents of a feature may read and never write. The feature's spec names the ones it
uses (requirements.md, `## References`); `approve-spec` pins each one's git commit, every agent
run gets the folders (`claude --add-dir` plus a deny rule for edits there) and its orientation
names them, and `framegate run` warns when a pinned reference moved.

A port is a feature whose requirements.md fills `## Ported from`: a reference, the source ->
target files, the tests to carry over and what may differ. It is no mode of its own (modes are
grown from evidence): the tasks of the test writer (also its later runs: answers at the final
gate, strengthening), the test reviewer, the implementer and the reviewer get added text
(port_note), the final gates report how far each target file is from its reference file
(fidelity, never blocking), and the file-size rule leaves the port's target files alone (they
keep the reference's file layout: port_targets).

Seen live (da-argos, a runner ported from da-otto): the agents were not given the sibling
repository, so its files were copied into the spec folder by hand; and the test writer wrote
1,217 lines of new tests "from the spec" where the reference's own tests were to be carried over.
"""

from __future__ import annotations

import difflib
import pathlib
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from framegate.checks.spec_lint import tables
from framegate.config import Config

REFERENCES_SECTION = "References"
PORT_SECTION = "Ported from"
FIDELITY_DIR = "fidelity"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_REFERENCE_LINE = re.compile(r"^[ \t>*-]*Reference\**:[ \t*`]*([A-Za-z][\w-]*)", re.M | re.I)
_COMMIT_LINE = re.compile(r"^[ \t>*-]*Commit\**:[ \t*`]*([0-9a-fA-F]{7,40})\b", re.M | re.I)
_DIFFER_HEAD = re.compile(r"^\W*What may differ\b.*$", re.M | re.I)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_NOTHING = re.compile(r"^\W*(none|n/?a|nothing)\W*$", re.I)


@dataclass
class PortFile:
    #: path in the reference, relative to its folder
    source: str
    #: path in this repository
    target: str
    #: "code" | "tests"
    kind: str = "code"


@dataclass
class Port:
    """The filled `## Ported from` section of a spec."""

    reference: str
    files: list[PortFile] = field(default_factory=list)
    #: the "What may differ" bullets: the only deviations from the reference that are allowed
    differ: list[str] = field(default_factory=list)
    #: the `Commit:` line, if the spec has one (what the spec dialogue looked at)
    commit: str = ""

    def of_kind(self, kind: str) -> list[PortFile]:
        return [f for f in self.files if f.kind == kind]


# ---------- the spec's sections ----------
def _requirements(cfg: Config, feature: str) -> str:
    path = cfg.spec_dir(feature) / "requirements.md"
    return _COMMENT.sub("", path.read_text(encoding="utf-8")) if path.is_file() else ""


def section(text: str, title: str) -> str:
    """The body of the `## <title>` section of a spec file ("" if there is none)."""
    m = re.search(rf"^##\s+{re.escape(title)}\b.*$", text, re.M | re.I)
    if not m:
        return ""
    rest = text[m.end() :]
    end = re.search(r"^##\s", rest, re.M)
    return rest[: end.start()] if end else rest


def _plain(cell: str) -> str:
    return cell.strip().strip("`*").strip()


def parse_port(text: str) -> Port | None:
    """The port a requirements.md describes; None when `## Ported from` is missing, empty or
    "None." (a normal feature). Filled = a `Reference:` line with a name."""
    body = section(_COMMENT.sub("", text), PORT_SECTION)
    ref = _REFERENCE_LINE.search(body)
    if not ref or _NOTHING.match(ref.group(1)):
        return None
    port = Port(ref.group(1))
    commit = _COMMIT_LINE.search(body)
    port.commit = commit.group(1).lower() if commit else ""
    for table in tables(body):
        src, dst, kind = table.column("source"), table.column("target"), table.column("kind")
        if src is None or dst is None:
            continue
        for row in table.rows:
            cells = [_plain(c) for c in row]
            source = cells[src] if src < len(cells) else ""
            target = cells[dst] if dst < len(cells) else ""
            if not source or not target:
                continue
            word = cells[kind].lower() if kind is not None and kind < len(cells) else ""
            port.files.append(
                PortFile(
                    source.replace("\\", "/"),
                    target.replace("\\", "/"),
                    "tests" if word.startswith("test") else "code",
                )
            )
    head = _DIFFER_HEAD.search(body)
    if head:
        for line in body[head.end() :].splitlines():
            m = _BULLET.match(line)
            if m and not _NOTHING.match(m.group(1)):
                port.differ.append(m.group(1))
    return port


def feature_port(cfg: Config, feature: str) -> Port | None:
    return parse_port(_requirements(cfg, feature))


def port_targets(cfg: Config, feature: str) -> set[str]:
    """The target files of the feature's port (set() for a normal feature): they keep the
    reference's file layout, so the file-size rule does not apply to them."""
    if not feature:
        return set()
    port = feature_port(cfg, feature)
    return {f.target for f in port.files} if port else set()


def named(cfg: Config, feature: str) -> list[str]:
    """The reference names the feature's spec uses: the rows of `## References` and the
    reference of `## Ported from`, in that order, each once (declared or not)."""
    text = _requirements(cfg, feature)
    names: list[str] = []
    for table in tables(section(text, REFERENCES_SECTION)):
        col = table.column("name", "reference")
        for row in table.rows:
            cell = _plain(row[col]) if col is not None and col < len(row) else ""
            word = cell.split()[0] if cell.split() else ""
            if word and not _NOTHING.match(word):
                names.append(_plain(word))
    port = parse_port(text)
    if port:
        names.append(port.reference)
    return list(dict.fromkeys(names))


def used(cfg: Config, feature: str) -> list[str]:
    """The declared references the feature's spec names (what its agents may read)."""
    return [n for n in named(cfg, feature) if n in cfg.references]


def spec_problems(cfg: Config, feature: str) -> list[str]:
    """Spec check: a reference the spec names that framegate.toml does not declare; a port
    without a file pair."""
    declared = ", ".join(cfg.references) or "none"
    out = [
        f"requirements.md: reference '{n}' is not declared in framegate.toml [references] "
        f"(declared: {declared})"
        for n in named(cfg, feature)
        if n not in cfg.references
    ]
    port = feature_port(cfg, feature)
    if port and not port.files:
        out.append(
            f"requirements.md, '{PORT_SECTION}': reference '{port.reference}' but no "
            "source -> target row (which files are ported?)"
        )
    return out


# ---------- state of a reference folder ----------
def _git(folder: pathlib.Path, *args: str) -> tuple[int, bytes]:
    try:
        r = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return 1, b""
    return r.returncode, r.stdout


def state(cfg: Config, name: str) -> dict[str, Any]:
    """Where a declared reference stands now: its folder, whether it is there, its git commit
    (None: no git repository) and whether its files differ from that commit."""
    folder = cfg.reference_dir(name)
    rec: dict[str, Any] = {
        "name": name,
        "path": folder.as_posix(),
        "missing": not folder.is_dir(),
        "commit": None,
        "dirty": False,
    }
    if rec["missing"]:
        return rec
    code, out = _git(folder, "rev-parse", "HEAD")
    if code == 0 and out.strip():
        rec["commit"] = out.decode("ascii", "replace").strip()
        code, out = _git(folder, "status", "--porcelain", "--", ".")
        rec["dirty"] = code == 0 and bool(out.strip())
    return rec


def short(commit: str | None) -> str:
    return commit[:10] if commit else "no git commit"


def describe(rec: dict[str, Any]) -> str:
    """One reference state in words: "otto: C:/x/da-otto at 1a2b3c4d5e (uncommitted changes)"."""
    if rec.get("missing"):
        return f"{rec['name']}: {rec['path']} (not on this machine)"
    dirty = " (uncommitted changes)" if rec.get("dirty") else ""
    return f"{rec['name']}: {rec['path']} at {short(rec.get('commit'))}{dirty}"


def pin(cfg: Config, feature: str) -> list[dict[str, Any]]:
    """`approve-spec`: the state of every reference the spec uses, kept in progress.json."""
    return [state(cfg, name) for name in used(cfg, feature)]


def pin_warnings(cfg: Config, feature: str, pins: list[dict[str, Any]] | None) -> list[str]:
    """What changed in the feature's references since "spec OK" (never blocking): gone from
    this machine, another commit, uncommitted changes, or used but never pinned."""
    by_name = {str(p.get("name")): p for p in pins or []}
    out = []
    for name in used(cfg, feature):
        now, was = state(cfg, name), by_name.get(name)
        if now["missing"]:
            out.append(f"reference '{name}' is not on this machine ({now['path']})")
        elif was is None:
            out.append(
                f"reference '{name}' was not pinned at the spec approval (now at "
                f"{short(now['commit'])})"
            )
        elif was.get("commit") != now["commit"]:
            out.append(
                f"reference '{name}' moved since the spec approval: {short(was.get('commit'))} "
                f"-> {short(now['commit'])}"
            )
        elif now["dirty"]:
            out.append(
                f"reference '{name}' has uncommitted changes (pinned at {short(now['commit'])})"
            )
    return out


# ---------- what the agents get ----------
def _deny_rule(folder: pathlib.PurePath) -> str:
    """A permission rule that denies every file edit under an absolute folder
    (`Edit(//c/Users/x/**)`: `//` = absolute; a Windows drive is written `/c/`)."""
    posix = folder.as_posix()
    drive = re.match(r"^([A-Za-z]):(/.*)$", posix)
    path = f"/{drive.group(1).lower()}{drive.group(2)}" if drive else posix
    return f"Edit(/{path.rstrip('/')}/**)"


def agent_args(cfg: Config, feature: str) -> list[str]:
    """`claude` arguments for an agent run of the feature: its references as extra working
    directories, with edits there denied (docs/claude-cli-notes.md, "Reference folders")."""
    folders = [cfg.reference_dir(n) for n in used(cfg, feature)]
    folders = [f for f in folders if f.is_dir()]
    if not folders:
        return []
    # one argument per folder / rule: a path may hold spaces and commas
    return [
        "--add-dir",
        *(str(f) for f in folders),
        "--disallowedTools",
        *(_deny_rule(f) for f in folders),
    ]


def orientation_lines(cfg: Config, feature: str, pins: list[dict[str, Any]] | None) -> list[str]:
    """The feature's references for the task's orientation block: name, absolute path,
    purpose, pinned commit, read-only."""
    names = used(cfg, feature)
    if not names:
        return []
    by_name = {str(p.get("name")): p for p in pins or []}
    out = [
        "- References (other projects, READ-ONLY: read them with Read / Grep / Glob by the "
        "absolute path; never write there):"
    ]
    for name in names:
        folder = cfg.reference_dir(name)
        note = cfg.references[name].note
        was = by_name.get(name)
        where = (
            "not on this machine: say so in your notes, do not guess its content"
            if not folder.is_dir()
            else f"pinned at {short(was.get('commit'))}"
            + (", with uncommitted changes" if was.get("dirty") else "")
            if was
            else "not pinned"
        )
        out.append(
            f"    {name}: {folder.as_posix()}" + (f" ({note})" if note else "") + f"; {where}"
        )
    return out


def brief_lines(cfg: Config) -> list[str]:
    """`framegate spec-brief`: the declared references and how a spec uses them."""
    if not cfg.references:
        return []
    out = ["- References ([references] in framegate.toml; other projects, read-only):"]
    for name, ref in cfg.references.items():
        out.append(f"    {describe(state(cfg, name))}" + (f" ({ref.note})" if ref.note else ""))
    out += [
        "  Read them where the feature builds on them. In requirements.md, 'References', list "
        "the ones the feature uses (name, the files / folders that matter, what for): only "
        "those are given to the agents, pinned at their commit when the spec is approved. Name "
        "them in the spec brief ('Project details that matter') so the user approves them.",
        "  A port (the feature copies code of a reference): fill 'Ported from' (Reference, "
        "Commit as above, every source -> target file, the reference's test files to carry "
        "over, 'What may differ'). Then the stubs mirror the reference's public names and "
        "signatures; the spec's IDs describe what the reference does today (read it; do not "
        "decide it again); ask the user only about what is to differ.",
    ]
    return out


def _pairs(files: list[PortFile]) -> str:
    return "; ".join(f"{f.source} -> {f.target}" for f in files) or "(none listed)"


def port_note(cfg: Config, feature: str, kind: str) -> str:
    """Text added to a task of a port ("" for a normal feature or a task it does not change)."""
    port = feature_port(cfg, feature)
    if port is None or port.reference not in cfg.references:
        return ""
    where = f"reference '{port.reference}' at {cfg.reference_dir(port.reference).as_posix()}"
    differ = "; ".join(port.differ) or "nothing"
    head = (
        f"This feature is a PORT ({cfg.paths.specs}/{feature}/requirements.md, "
        f"'{PORT_SECTION}') from {where}. What may differ from the reference: {differ}. "
    )
    code, tests = _pairs(port.of_kind("code")), _pairs(port.of_kind("tests"))
    out = cfg.paths.out
    notes = {
        "TESTS": f"Carry over the reference's tests, do not write your own: {tests}. Copy each "
        "reference test file to its target and keep the test names, their order, the "
        "structure, the fixtures and the assertions; change only the imports and what may "
        "differ, and tag every test with the spec IDs it proves, as your role says. Never "
        "rewrite a reference test or replace it by a test of your own. Write a new test only "
        "for a spec ID that no reference test covers, and list each such test in "
        f"{out}/{feature}/test_summary.md under '## New tests (not in the reference)' with the "
        "ID and the reason. A reference test you cannot carry over (it needs something this "
        "repository lacks): leave it out and list it under '## Reference tests not carried "
        f"over' with the reason; a doubt goes to {out}/{feature}/questions.md.",
        "TEST_REVIEW": f"The tests are carried over from the reference: {tests}. Read each "
        "reference test file next to its target. A reference test that is missing, renamed, "
        "rewritten or weakened (an assertion dropped or loosened, a case removed) is an "
        "objection, unless what may differ, or test_summary.md's '## Reference tests not "
        "carried over' with a reason that holds, explains it. A new test is right only for a "
        "spec ID that no reference test covers ('## New tests (not in the reference)').",
        "IMPLEMENT": f"Copy, do not rewrite: {code}. For each pair of your step, read the "
        "reference file and write it to its target, then change only what may differ. Same "
        "structure, same names, same order; comments and docstrings word for word; no "
        "rewording, no restructuring, no improvement, also where your role's readability "
        "checklist would ask for one: a diff between the two repositories must show only "
        "the listed deviations. A change the tests need that is not listed: make it as small "
        "as possible and log it (`- ASSUMPTION [<spec ID>]: differs from the reference: "
        "<file>, <what>`).",
        "REVIEW": f"Also compare each target file with its reference file: {code}. A "
        "difference that 'What may differ' does not list is a finding: when it changes "
        "behavior, a failing test as usual; otherwise a bullet under `## Structure findings` "
        "(`- <target file>: lines <n-m> differ from the reference (<what>) -> restore the "
        "reference's text`). The readability checklist of your role does not apply to "
        "ported files: their structure is the reference's, on purpose.",
        "STRUCTURE": "A finding that names a difference from the reference: restore the "
        "reference's text there. Nothing else in a ported file moves away from the "
        f"reference ({code}).",
    }
    notes["TESTS_AMEND"] = (
        f"The tests are the reference's tests, carried over ({tests}): stay with them. Where "
        "an answer needs a test change, make it the reference's way: carry over the reference "
        "test that covers it if one exists (same name, structure and assertions; only the "
        "imports and what may differ change), else change the carried-over test as little as "
        "the answer needs. A test of your own is right only for a spec ID or a case that no "
        "reference test covers: list it in "
        f"{out}/{feature}/test_summary.md under '## New tests (not in the reference)' with the "
        "ID and the reason. Never rewrite, rename or drop a carried-over test for any other "
        "reason."
    )
    notes["TESTS_STRENGTHEN"] = (
        f"The tests are the reference's tests, carried over ({tests}): leave them as they "
        "are. First look in the reference's test files for a test that catches the uncovered "
        "line or the surviving mutant and was not carried over: carry it over as it is (same "
        "name, structure and assertions; only the imports and what may differ change). Write "
        "a strengthening test of your own only where no reference test exists for it, and "
        f"list each one in {out}/{feature}/test_summary.md under '## New tests (not in the "
        "reference)' with the ID and what it catches. Code that the reference leaves untested "
        "and the spec does not cover goes under '## Not covered on purpose', as your task says."
    )
    notes["IMPLEMENT_STEPS"] = notes["IMPLEMENT"]
    notes["FIX"] = notes["FIX_FINAL"] = (
        "Fix with the smallest change, and keep the ported files as close to the reference as "
        f"the fix allows ({code})."
    )
    return head + notes[kind] if kind in notes else ""


# ---------- fidelity report (final gates, never blocking) ----------
def _reference_lines(
    folder: pathlib.Path, rel: str, pinned: dict[str, Any] | None
) -> tuple[list[str] | None, str]:
    """The lines of a reference file and where they come from: the pinned commit when the
    reference was clean at the spec approval, else the file as it is now."""
    commit = (pinned or {}).get("commit")
    if commit and not (pinned or {}).get("dirty"):
        code, out = _git(folder, "show", f"{commit}:./{rel}")
        if code == 0:
            return out.decode("utf-8", "replace").splitlines(), f"at {short(commit)}"
    path = folder / rel
    if not path.is_file():
        return None, ""
    return path.read_text(encoding="utf-8", errors="replace").splitlines(), "as it is now"


def _counts(a: list[str], b: list[str]) -> tuple[int, int, int]:
    """(changed, added, removed) lines from a to b."""
    changed = added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        gone, new = i2 - i1, j2 - j1
        if tag == "replace":
            both = min(gone, new)
            changed, removed, added = changed + both, removed + gone - both, added + new - both
        elif tag == "delete":
            removed += gone
        elif tag == "insert":
            added += new
    return changed, added, removed


def fidelity(cfg: Config, feature: str, pins: list[dict[str, Any]] | None) -> dict[str, Any]:
    """For a port: each target file against its reference file (changed / added / removed
    lines), the diffs written to {out}/{feature}/fidelity/. {} for a normal feature;
    "skipped" names the reason when the reference cannot be read here."""
    port = feature_port(cfg, feature)
    if port is None:
        return {}
    rep: dict[str, Any] = {"reference": port.reference, "skipped": "", "files": []}
    if port.reference not in cfg.references:
        rep["skipped"] = f"reference '{port.reference}' is not declared in [references]"
        return rep
    folder = cfg.reference_dir(port.reference)
    if not folder.is_dir():
        rep["skipped"] = (
            f"reference '{port.reference}' is not on this machine ({folder.as_posix()})"
        )
        return rep
    pinned = next((p for p in pins or [] if p.get("name") == port.reference), None)
    out_dir = cfg.feature_out(feature) / FIDELITY_DIR
    for f in port.files:
        row: dict[str, Any] = {"source": f.source, "target": f.target, "kind": f.kind}
        rep["files"].append(row)
        ref_lines, origin = _reference_lines(folder, f.source, pinned)
        target = cfg.root / f.target
        if ref_lines is None:
            row["note"] = "not found in the reference"
            continue
        if not target.is_file():
            row["note"] = "target file missing"
            continue
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        row["changed"], row["added"], row["removed"] = _counts(ref_lines, lines)
        row["lines"], row["origin"] = len(ref_lines), origin
        if ref_lines != lines:
            diff = difflib.unified_diff(
                ref_lines,
                lines,
                f"{port.reference}/{f.source} ({origin})",
                f.target,
                lineterm="",
            )
            path = out_dir / (f.target.replace("/", "__") + ".diff")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(diff) + "\n", encoding="utf-8")
            row["diff"] = f"{cfg.paths.out}/{feature}/{FIDELITY_DIR}/{path.name}"
    return rep


def fidelity_lines(rep: dict[str, Any]) -> list[str]:
    """The fidelity report as lines of the final gate report ([] for a normal feature)."""
    if not rep:
        return []
    if rep.get("skipped"):
        return [f"Port fidelity (not blocking): SKIPPED, {rep['skipped']}"]
    out = [
        f"Port fidelity against reference '{rep['reference']}' (not blocking; the spec's "
        "'What may differ' says which differences are meant):"
    ]
    for row in rep.get("files") or []:
        label = f"- [{row['kind']}] {row['source']} -> {row['target']}: "
        if row.get("note"):
            out.append(label + row["note"])
        elif not row.get("diff"):
            out.append(label + f"identical ({row['lines']} lines, reference {row['origin']})")
        else:
            out.append(
                label + f"{row['changed']} changed, {row['added']} added, {row['removed']} removed "
                f"of {row['lines']} lines (reference {row['origin']}; diff: {row['diff']})"
            )
    return out or [f"Port fidelity (not blocking): no file pair in '{PORT_SECTION}'"]


def fidelity_summary(rep: dict[str, Any]) -> str:
    """One line for the final brief ("" for a normal feature)."""
    if not rep:
        return ""
    if rep.get("skipped"):
        return f"port fidelity (not blocking): SKIPPED, {rep['skipped']}"
    rows = rep.get("files") or []
    done = [r for r in rows if "lines" in r]
    same = sum(1 for r in done if not r.get("diff"))
    total = {k: sum(int(r[k]) for r in done) for k in ("changed", "added", "removed")}
    problems = len(rows) - len(done)
    return (
        f"port fidelity against '{rep['reference']}' (not blocking): {len(rows)} file(s), "
        f"{same} identical; {total['changed']} changed, {total['added']} added, "
        f"{total['removed']} removed line(s)"
        + (f"; {problems} not compared" if problems else "")
        + f" (per file: final_gates.md; diffs: {FIDELITY_DIR}/)"
    )
