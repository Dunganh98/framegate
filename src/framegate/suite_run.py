"""The whole suite, run at most once per state of the code: the result is kept with a key of
the working tree's content (framegate's own files left out), the test command and the
installed packages. A second request for the same key (a setup re-check after editing only
framegate.toml, the baseline right after the setup's commit) reuses it."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import tempfile

from framegate import known_red, unrunnable
from framegate.adapters.base import LanguageAdapter, TestCase, TestRun
from framegate.config import Config

FILE = "suite_run.json"


def _own_paths(cfg: Config) -> list[str]:
    p = cfg.paths
    own = ["framegate.toml", ".framegate", p.specs, p.out, p.metrics, cfg.knowledge, cfg.code_map]
    return [x.strip("/") for x in own if x and x.strip("/")]


def tree_key(cfg: Config) -> str:
    """The content of the working tree (tracked and untracked, not ignored) without
    framegate's own files: a git tree hash built in a scratch index. '' = not a git tree."""

    def git(*args: str, env: dict[str, str] | None = None) -> str:
        r = subprocess.run(
            ["git", *args], cwd=cfg.root, env=env, capture_output=True, text=True, check=False
        )
        if r.returncode:
            raise OSError(r.stderr.strip())
        return r.stdout.strip()

    try:
        index = os.path.join(cfg.root, git("rev-parse", "--git-path", "index"))
        with tempfile.TemporaryDirectory() as tmp:
            scratch = os.path.join(tmp, "index")
            if os.path.isfile(index):
                with open(index, "rb") as src, open(scratch, "wb") as dst:
                    dst.write(src.read())
            env = dict(os.environ, GIT_INDEX_FILE=scratch)
            git("add", "-A", "--", ".", env=env)
            for p in _own_paths(cfg):
                git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", p, env=env)
            return git("write-tree", env=env)
    except OSError:
        return ""


def _key(adapter: LanguageAdapter) -> str:
    tree = tree_key(adapter.cfg)
    if not tree:
        return ""
    stamp = json.dumps([tree, adapter.suite_command(), adapter.env_stamp()])
    return hashlib.sha256(stamp.encode("utf-8")).hexdigest()


def _load(adapter: LanguageAdapter) -> dict | None:
    try:
        data = json.loads((adapter.cfg.out_dir / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(adapter: LanguageAdapter, key: str, skip: list[str], run: TestRun) -> None:
    path = adapter.cfg.out_dir / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "key": key,
        "skip": skip,
        "status": run.status,
        "returncode": run.returncode,
        "output": run.output[-20000:],
        "cases": None if run.cases is None else [dataclasses.asdict(c) for c in run.cases],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _reused(adapter: LanguageAdapter, data: dict, skip: list[str]) -> TestRun:
    cfg = adapter.cfg
    cases = data.get("cases")
    kept = None
    if cases is not None:
        kept = [
            TestCase(**c)
            for c in cases
            if not any(unrunnable.matches(cfg, c["name"], e) for e in skip)
        ]
    status = str(data.get("status"))
    if status == "failed" and kept is not None:
        red = any(c.outcome in ("failed", "error") for c in kept)
        status = "failed" if red else ("passed" if kept else "no_tests")
    output = f"(the result of an earlier run of the same code, reused)\n{data.get('output', '')}"
    return TestRun(status, int(data.get("returncode") or 0), output, kept)


def whole_suite(adapter: LanguageAdapter, skip_known_red: bool = True) -> TestRun:
    """The whole suite. `skip_known_red`: leave out the listed tests that failed on this
    machine before (known_red); doctor --tree runs them all."""
    cfg = adapter.cfg
    skip = known_red.entries(cfg) if skip_known_red else []
    key = _key(adapter)
    data = _load(adapter) if key else None
    if data and data.get("key") == key and set(data.get("skip") or []) <= set(skip):
        return _reused(adapter, data, skip)
    run = adapter.regression_run(skip)
    if key and not run.incomplete:
        _save(adapter, key, skip, run)
    return run
