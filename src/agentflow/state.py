"""Files the workflow keeps state in: .phase, .reqs, out/events.jsonl, out/<f>/progress.json, and
the git helpers (commit, tags)."""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
from typing import Any

from agentflow.config import Config


class GitError(Exception):
    pass


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


def tag_exists(cfg: Config, name: str) -> bool:
    return bool(git(cfg, "tag", "--list", name).strip())


def commit(cfg: Config, msg: str) -> bool:
    git(cfg, "add", "-A")
    if git(cfg, "diff", "--cached", "--name-only").strip():
        git(cfg, "commit", "-q", "-m", msg)
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
    with open(cfg.out_dir / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_events(cfg: Config, feature: str | None = None) -> list[dict[str, Any]]:
    p = cfg.out_dir / "events.jsonl"
    if not p.is_file():
        return []
    events = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]
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
        prog.update(json.loads(p.read_text(encoding="utf-8")))
    return prog


def save_progress(cfg: Config, feature: str, prog: dict[str, Any]) -> None:
    d = cfg.feature_out(feature)
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.json").write_text(json.dumps(prog, indent=2), encoding="utf-8")
