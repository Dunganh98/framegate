"""Files the workflow keeps state in: .phase, .reqs, out/events.jsonl, out/<f>/progress.json, and
the git helpers (commit, tags)."""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import time
from typing import Any

from framegate.config import DEFERRED_FILE, Config, is_machine_file, phase_name


class GitError(Exception):
    pass


class StateError(GitError):
    """A state file that cannot be read (reported like a git error: exit 1 with the message)."""


def git(cfg: Config, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cfg.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


# ---------- the working tree, without the machine files ----------
def dirty_lines(cfg: Config, untracked: str = "no") -> list[str]:
    """What `git status --porcelain` lists, one `XY path` line each, without the machine
    files (config.is_machine_file: tool state of this machine, never held against a step).
    `untracked`: git's --untracked-files mode ("no": tracked files only). The one helper
    every "is the working tree clean?" question goes through."""
    out = git(cfg, "status", "--porcelain", "-z", f"--untracked-files={untracked}")
    parts = [x for x in out.split("\0") if x]
    lines: list[str] = []
    i = 0
    while i < len(parts):
        code, path = parts[i][:2], parts[i][3:]
        i += 2 if "R" in code or "C" in code else 1  # a rename is followed by its old name
        if not is_machine_file(cfg, path):
            lines.append(f"{code} {path}")
    return lines


def dirty_machine_files(cfg: Config) -> list[str]:
    """The tracked machine files with uncommitted changes."""
    names = git(cfg, "diff", "--no-renames", "--name-only", "-z", "HEAD").split("\0")
    return sorted(n for n in names if n and is_machine_file(cfg, n))


#: one line for a refusal over uncommitted changes: the three ways out
DIRTY_CHOICES = (
    "For each file: commit it, restore it (git restore <file>), or, when it is tool state of "
    "this machine and not the project's, add it to [git] machine_files in framegate.toml "
    "(framegate then leaves it alone)."
)


def commit_command(lines: list[str], message: str) -> str:
    """The one git command that commits the files of `lines` (`XY path`, as dirty_lines
    gives them) as they are, and nothing else. For a refusal: the user can copy it."""
    paths = [line[3:] for line in lines]
    quoted = " ".join(f'"{p}"' if " " in p else p for p in paths)
    return f'git commit -m "{message}" -- {quoted}'


def switch_branch(cfg: Config, *args: str) -> None:
    """`git checkout -q <args>` for a branch switch. Uncommitted machine files do not stop
    it: git carries them over. Only when the other branch holds another version of such a
    file git refuses; that is said in one line that names the file and the fix."""
    try:
        git(cfg, "checkout", "-q", *args)
    except GitError as e:
        text = str(e)
        if "overwritten by checkout" not in text:
            raise
        named = [x.strip() for x in text.splitlines() if x.startswith("\t")]
        named = named or dirty_machine_files(cfg) or ["<file>"]
        raise GitError(
            f"Cannot switch the branch: {', '.join(named)} has uncommitted changes here and "
            "the other branch holds another version of it. Restore it (git restore "
            f"{named[0]}) or commit it, then run the command again."
        ) from e


def _marker_ref(name: str) -> str:
    return f"refs/{name}"


def _ref_exists(cfg: Config, ref: str) -> bool:
    r = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", ref], cwd=cfg.root, capture_output=True
    )
    return r.returncode == 0


def tag_exists(cfg: Config, name: str) -> bool:
    """True when the workflow marker `name` (e.g. `tests-approved/<feature>`) exists: the
    private ref `refs/<name>`, or a tag of that name left by an older framegate."""
    return _ref_exists(cfg, _marker_ref(name)) or _ref_exists(cfg, f"refs/tags/{name}")


def set_marker(cfg: Config, name: str, rev: str = "HEAD") -> None:
    """Point the workflow marker `name` at `rev`. By default markers are private refs
    (`refs/<name>`), not tags: git resolves the short name (`git diff tests-approved/<f>`) the
    same way, but no `git push`, `--tags` or `--all` ever publishes them to a shared remote.
    `[git] markers = "tags"` keeps git tags (a CI job gets them through `git push --tags`).
    The other kind's marker of the same name is removed so the name stays unambiguous."""
    if cfg.git_markers == "tags":
        git(cfg, "tag", "-f", name, rev)
        if _ref_exists(cfg, _marker_ref(name)):
            git(cfg, "update-ref", "-d", _marker_ref(name))
        return
    git(cfg, "update-ref", _marker_ref(name), rev)
    if _ref_exists(cfg, f"refs/tags/{name}"):
        git(cfg, "tag", "-d", name)


def delete_marker(cfg: Config, name: str) -> None:
    """Remove the workflow marker `name` (private ref and / or old tag); missing is fine."""
    if _ref_exists(cfg, _marker_ref(name)):
        git(cfg, "update-ref", "-d", _marker_ref(name))
    if _ref_exists(cfg, f"refs/tags/{name}"):
        git(cfg, "tag", "-d", name)


def commit_paths(cfg: Config) -> list[str]:
    """What a workflow commit may contain: the source and test folders, the specs, the metrics
    folder, the knowledge file, the deferred-work list and the code map. Anything else in the
    working tree (the user's notes, scratch files, unrelated edits) is never swept into a
    commit."""
    p = cfg.paths
    wanted = [*cfg.src_roots, p.tests, p.review_tests, p.specs, p.metrics, cfg.knowledge]
    wanted += [DEFERRED_FILE, cfg.code_map]
    out: list[str] = []
    for rel in wanted:
        if rel and rel not in out:
            out.append(rel)
    return out


def commit(cfg: Config, msg: str, extra: list[str] | None = None) -> bool:
    """Commit the workflow's paths (commit_paths) and the `extra` files (approve-spec: the
    setup files changed in the spec phase), nothing else. False = nothing to commit."""
    staged: list[str] = []
    for rel in commit_paths(cfg):
        # a path that neither exists nor is tracked makes `git add` fail: skip it
        if (cfg.root / rel).exists() or git(cfg, "ls-files", "--", rel).strip():
            try:
                git(cfg, "add", "-A", "--", rel)
            except GitError:
                continue  # a path the project ignores (.gitignore): not part of any commit
            # an empty folder holds nothing git knows: naming it would fail the commit;
            # a deleted file still counts through the index diff
            if (
                git(cfg, "ls-files", "--", rel).strip()
                or git(cfg, "diff", "--cached", "--name-only", "--", rel).strip()
            ):
                staged.append(rel)
    for rel in extra or []:
        try:
            git(cfg, "add", "-A", "--", rel)
        except GitError:
            continue  # ignored by the project (.gitignore)
        staged.append(rel)
    if staged and git(cfg, "diff", "--cached", "--name-only", "--", *staged).strip():
        # `-- paths`: what the user staged elsewhere stays staged and out of this commit
        git(cfg, "commit", "-q", "-m", msg, "--", *staged)
        return True
    return False


def feature_finished(cfg: Config, feature: str) -> bool:
    """The feature has a row in the metrics file (written at DONE)."""
    mfile = cfg.root / cfg.paths.metrics / "workflow_metrics.csv"
    if not mfile.is_file():
        return False
    rows = mfile.read_text(encoding="utf-8").splitlines()[1:]
    return any(r.split(",")[1:2] == [feature] for r in rows)


# ---------- phase lock ----------
def set_phase(cfg: Config, phase: str) -> None:
    cfg.phase_path.write_text(phase, encoding="utf-8")


def read_phase(cfg: Config) -> str | None:
    p = cfg.phase_path
    return p.read_text(encoding="utf-8").strip() if p.is_file() else None


def set_reqs(cfg: Config, ids: list[str]) -> None:
    cfg.reqs_path.write_text(",".join(ids), encoding="utf-8")


def read_reqs(cfg: Config) -> list[str]:
    p = cfg.reqs_path
    if not p.is_file():
        return []
    return [x.strip() for x in p.read_text(encoding="utf-8").split(",") if x.strip()]


def clear_reqs(cfg: Config) -> None:
    cfg.reqs_path.unlink(missing_ok=True)
    set_later_tests(cfg, [])


def later_tests_path(cfg: Config) -> pathlib.Path:
    """Tests of the current step's IDs that also need a later plan step (one `file::name`
    per line). Under {out}/, as contested_tests.txt."""
    return cfg.out_dir / "later_tests.txt"


def set_later_tests(cfg: Config, places: list[str]) -> None:
    """The tests the step gates and the Stop hook leave out because a later plan step
    builds part of what they check; [] removes the file."""
    p = later_tests_path(cfg)
    if not places:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{n}\n" for n in dict.fromkeys(places)), encoding="utf-8")


def read_later_tests(cfg: Config) -> list[str]:
    p = later_tests_path(cfg)
    if not p.is_file():
        return []
    return [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def contested_path(cfg: Config) -> pathlib.Path:
    """Tests of provisional steps whose fix waits for the human (one name per line). Under
    {out}/ (ignored by git), so `git add -A` never commits it."""
    return cfg.out_dir / "contested_tests.txt"


def set_contested(cfg: Config, names: list[str]) -> None:
    """The tests the step gates and the Stop hook leave out (a provisional step's contested
    tests); [] removes the file."""
    p = contested_path(cfg)
    if not names:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{n}\n" for n in dict.fromkeys(names)), encoding="utf-8")


def read_contested(cfg: Config) -> list[str]:
    p = contested_path(cfg)
    if not p.is_file():
        return []
    return [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def clear_phase(cfg: Config) -> None:
    cfg.phase_path.unlink(missing_ok=True)
    clear_reqs(cfg)
    set_contested(cfg, [])


# ---------- events ----------
def log_event(cfg: Config, feature: str, kind: str, **data: Any) -> None:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "feature": feature,
        "kind": kind,
        **data,
    }
    path = cfg.out_dir / "events.jsonl"
    lead = ""
    if path.is_file() and path.stat().st_size:
        with open(path, "rb") as fh:  # a line cut short by a killed run: start a new one
            fh.seek(-1, 2)
            lead = "" if fh.read(1) == b"\n" else "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(lead + json.dumps(rec, ensure_ascii=False) + "\n")


def read_events(cfg: Config, feature: str | None = None) -> list[dict[str, Any]]:
    p = cfg.out_dir / "events.jsonl"
    if not p.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:  # a line cut short by a killed run: skip it, keep the rest
            continue
        if isinstance(rec, dict) and "kind" in rec:
            if isinstance(rec.get("phase"), str):  # an event written under an earlier name
                rec["phase"] = phase_name(rec["phase"])
            events.append(rec)
    return [e for e in events if feature is None or e.get("feature") == feature]


# ---------- progress ----------
def fresh_progress() -> dict[str, Any]:
    return {
        "phase": "TESTS",
        "completed_steps": [],
        "sub": None,
        "attempt": 0,
        "feedback": "",
        "waiting": None,
        "parked": [],
        "redo": [],
    }


def load_progress(cfg: Config, feature: str) -> dict[str, Any]:
    p = cfg.feature_out(feature) / "progress.json"
    prog = fresh_progress()
    if p.is_file():
        try:
            prog.update(json.loads(p.read_text(encoding="utf-8")))
        except ValueError as e:
            raise StateError(f"{p} is not valid JSON ({e}); fix or delete it") from e
    # a feature in progress that an earlier version left in a renamed phase ("PR")
    prog["phase"] = phase_name(str(prog.get("phase")))
    then = (prog.get("amend") or {}).get("then")
    if isinstance(then, dict) and then.get("phase"):
        then["phase"] = phase_name(str(then["phase"]))
    return prog


def save_progress(cfg: Config, feature: str, prog: dict[str, Any]) -> None:
    d = cfg.feature_out(feature)
    d.mkdir(parents=True, exist_ok=True)
    # written to a temp file and moved into place: a run killed mid-write leaves the old file
    target, tmp = d / "progress.json", d / "progress.json.tmp"
    tmp.write_text(json.dumps(prog, indent=2), encoding="utf-8")
    replace_file(tmp, target)


#: waits (s) between tries of the move below: about 3 s in all
REPLACE_WAITS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.5)


def replace_file(tmp: pathlib.Path, target: pathlib.Path) -> None:
    """os.replace, tried again on PermissionError. On Windows the move is refused (WinError 5)
    while another process has the target open: the chat's `framegate watch` / `status` reading
    it, a virus scanner, a file-sync client (seen in the test suite on a OneDrive folder)."""
    for wait in REPLACE_WAITS:
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            time.sleep(wait)
    os.replace(tmp, target)
