"""Redoing parked work only when it can end differently, and the one change of
framegate.toml a run makes itself.

Seen live (three replay runs): a step parked on something the implementer cannot change (a
check that fails on files it may not edit, a diagnosis whose fix is a setup-file change). The
human approved the fix, nobody could apply it, and the step was redone on the same inputs: the
same failed attempts, the same diagnosis, the same park, at the price of a full cycle each time.

Two parts:
- `gate_inputs`: a digest of what the gates read and the implementer cannot write. A park of
  that kind keeps it; a redo whose digest is still the same is not run (the orchestrator parks
  it again at once and says what is missing).
- `apply_config_update`: a diagnosis with `Fix owner: config`, approved at the final gate. The
  spec updater writes the new framegate.toml to the out folder (it cannot write the file
  itself), framegate checks that it loads and puts it in place, committed and tagged
  `config-update/<feature>` (the frame check allows the file while it is exactly that).
"""

from __future__ import annotations

import difflib
import hashlib
import re

from framegate import red_tree
from framegate.adapters import get_adapter
from framegate.checks.frame_diff import changed_files, config_update_tag
from framegate.config import CONFIG_FILE, Config, ConfigError, load_config
from framegate.gates import BASELINE_FILE
from framegate.state import GitError, git, set_marker

#: {out}/<feature>/<this>: the whole new framegate.toml, written by the spec updater
CONFIG_UPDATE_FILE = "config_update.toml"
#: most changed lines of framegate.toml shown in a note
SHOWN_LINES = 12

#: an answer that only approves ("ok", "Approved: apply the proposed fix as written")
APPROVAL_RE = re.compile(
    r"^\W*(?:approved?|apply|yes|ok(?:ay)?|default|accept(?:ed)?|fine|go on|as proposed)\b",
    re.I,
)
REDO_ANYWAY_RE = re.compile(r"\bredo\s+(?:it\s+)?anyway\b", re.I)

#: the question of work that was not redone because nothing it depends on changed
UNCHANGED = (
    "Not redone: the fix was approved, but nothing it names has changed since this parked "
    "(the spec, the tests, {config} and the other files outside the source folders are the "
    "same, and so is the record of what was red before the task). The same checks would "
    "fail the same way. Make the change (or say who should), then answer again; answer "
    '"redo anyway" if you changed something framegate cannot see (an installed package, a '
    "service). Parked for: {question}"
)


def only_approves(answer: str) -> bool:
    """Does the answer to a parked item only approve what was proposed (one short line, or
    nothing at all), so a redo depends on that proposal having been applied? An answer that
    says more is new information for the implementer: its redo always runs."""
    text = answer.strip()
    if REDO_ANYWAY_RE.search(text):
        return False
    return not text or (len(text.splitlines()) == 1 and bool(APPROVAL_RE.match(text)))


def _outside(cfg: Config) -> list[str]:
    """Path prefixes that are not gate inputs of this kind: what the agents of a run write
    (the source folders, the review tests, the out folder, the code map)."""
    p = cfg.paths
    names = [*cfg.src_roots, p.review_tests, p.out, cfg.code_map]
    return [n.rstrip("/") for n in names if n]


def gate_inputs(cfg: Config, feature: str) -> str:
    """A digest of what the gates read and no implementer run changes: every tracked file
    outside the source folders, the review tests and the out folder (the spec, the frozen
    tests, framegate.toml, packaging and tool config) at HEAD, the uncommitted changes to
    such files, and the records of what was red when the spec was approved. "" when git
    gives no answer (then nothing is compared)."""
    skip = _outside(cfg)

    def counts(path: str) -> bool:
        return not any(path == s or path.startswith(s + "/") for s in skip)

    h = hashlib.sha256()
    try:
        for line in git(cfg, "ls-tree", "-r", "HEAD").splitlines():
            if counts(line.split("\t", 1)[-1]):
                h.update(line.encode("utf-8", "replace"))
        changed, _ = changed_files(cfg, "HEAD")
    except GitError:
        return ""
    for rel in sorted(changed):
        if not counts(rel):
            continue
        path = cfg.root / rel
        h.update(rel.encode("utf-8", "replace"))
        try:
            h.update(path.read_bytes() if path.is_file() else b"<gone>")
        except OSError:
            h.update(b"<unreadable>")
    for name in (BASELINE_FILE, red_tree.CHECKS_FILE):
        path = cfg.feature_out(feature) / name
        try:
            h.update(path.read_bytes() if path.is_file() else b"<none>")
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()


def apply_config_update(cfg: Config, feature: str, ref: str) -> tuple[Config | None, list[str]]:
    """Put the spec updater's {out}/<feature>/config_update.toml in place as framegate.toml:
    only when it loads as a config; committed alone and tagged `config-update/<feature>`.
    Returns (the new config, or None when framegate.toml stays as it was; notes for the
    next gate)."""
    proposal = cfg.feature_out(feature) / CONFIG_UPDATE_FILE
    where = f"{cfg.paths.out}/{feature}/{CONFIG_UPDATE_FILE}"
    if not proposal.is_file():
        return None, [
            f"{ref}: an approved fix names {CONFIG_FILE}, but the spec updater wrote no "
            f"{where}: {CONFIG_FILE} is unchanged (change it yourself, then answer again)"
        ]
    target = cfg.root / CONFIG_FILE
    new, old = proposal.read_bytes(), target.read_bytes()
    proposal.unlink()
    lines = [
        line
        for line in difflib.unified_diff(
            old.decode("utf-8", "replace").splitlines(),
            new.decode("utf-8", "replace").splitlines(),
            lineterm="",
            n=0,
        )
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    if not lines:
        return None, [f"{ref}: {where} is the same as {CONFIG_FILE}: unchanged"]
    target.write_bytes(new)
    try:
        fresh = load_config(cfg.root)
        get_adapter(fresh)
    except ConfigError as e:
        target.write_bytes(old)
        return None, [
            f"{ref}: the change to {CONFIG_FILE} was not applied, the new file is not a "
            f"valid config: {e}"
        ]
    try:
        git(cfg, "add", "--", CONFIG_FILE)
        message = f"{feature}: {CONFIG_FILE} updated from answers {ref}"
        git(cfg, "commit", "-q", "-m", message, "--", CONFIG_FILE)
        set_marker(fresh, config_update_tag(feature))
    except GitError as e:
        target.write_bytes(old)
        return None, [f"{ref}: the change to {CONFIG_FILE} was not applied (git: {e})"]
    more = f" ... and {len(lines) - SHOWN_LINES} more" if len(lines) > SHOWN_LINES else ""
    shown = "; ".join(f"`{line}`" for line in lines[:SHOWN_LINES])
    return fresh, [
        f"{ref}: {CONFIG_FILE} updated by framegate from the approved diagnosis "
        f"({len(lines)} changed line(s): {shown}{more})"
    ]
