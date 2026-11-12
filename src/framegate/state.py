"""Files the workflow keeps state in: .phase, .reqs, out/events.jsonl, out/<f>/progress.json, and
the git helpers (commit, tags)."""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
from typing import Any

from framegate.config import Config


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
    folder, the knowledge file and the code map. Anything else in the working tree (the
    user's notes, scratch files, unrelated edits) is never swept into a commit."""
    p = cfg.paths
    wanted = [*cfg.src_roots, p.tests, p.review_tests, p.specs, p.metrics, cfg.knowledge]
    wanted.append(cfg.code_map)
    out: list[str] = []
    for rel in wanted:
        if rel and rel not in out:
            out.append(rel)
    return out


def commit(cfg: Config, msg: str) -> bool:
    """Commit the workflow's paths (commit_paths), nothing else. False = nothing to commit."""
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
    if staged and git(cfg, "diff", "--cached", "--name-only", "--", *staged).strip():
        # `-- paths`: what the user staged elsewhere stays staged and out of this commit
        git(cfg, "commit", "-q", "-m", msg, "--", *staged)
        return True
    return False


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
    return prog


def save_progress(cfg: Config, feature: str, prog: dict[str, Any]) -> None:
    d = cfg.feature_out(feature)
    d.mkdir(parents=True, exist_ok=True)
    # written to a temp file and moved into place: a run killed mid-write leaves the old file
    target, tmp = d / "progress.json", d / "progress.json.tmp"
    tmp.write_text(json.dumps(prog, indent=2), encoding="utf-8")
    os.replace(tmp, target)
