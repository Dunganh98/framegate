"""`framegate survey`: an agent maps the project (source folders, test folder, what is not the
project's code) from its code and build files; then every code file must be accounted for.
The result is out/survey.json, which `framegate probe` reads before a framegate.toml exists."""

from __future__ import annotations

import importlib.resources
import json
import pathlib
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Sequence
from typing import Any

from framegate import agent_env
from framegate.adapters.base import LanguageAdapter
from framegate.config import Config, drop_machine_files

FILE = "survey.json"
MODEL = "sonnet"
MAX_TURNS = "60"
TOOLS = "Read,Glob,Grep"
SHOWN = 60
_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def path(cfg: Config) -> pathlib.Path:
    return cfg.root / cfg.paths.out / FILE


def load(cfg: Config) -> dict[str, Any] | None:
    try:
        data = json.loads(path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("src") else None


def code_files(cfg: Config, adapter: LanguageAdapter) -> list[str]:
    r = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=off",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=cfg.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    files = drop_machine_files(cfg, r.stdout.splitlines()) if r.returncode == 0 else []
    own = (cfg.paths.out.rstrip("/") + "/", cfg.paths.specs.rstrip("/") + "/")
    suffixes = tuple(adapter.source_suffixes)
    return sorted(f for f in files if f.endswith(suffixes) and not f.startswith(own))


def folders(files: Sequence[str]) -> list[str]:
    counts = Counter(f.rsplit("/", 1)[0] if "/" in f else f for f in files)
    return [f"{d} ({n})" for d, n in sorted(counts.items())]


def _under(f: str, p: str) -> bool:
    p = p.strip().strip("/")
    return p == "." or f == p or f.startswith(p + "/")


def uncovered(files: Sequence[str], layout: dict[str, Any]) -> list[str]:
    places = [s.get("path", "") for s in layout.get("src") or [] if isinstance(s, dict)]
    places.append((layout.get("tests") or {}).get("path", ""))
    places += [x.get("path", "") for x in layout.get("excluded") or [] if isinstance(x, dict)]
    places = [p for p in places if isinstance(p, str) and p.strip()]
    return [f for f in files if not any(_under(f, p) for p in places)]


def parse(text: str) -> dict[str, Any] | None:
    blocks = _JSON.findall(text or "")
    for raw in reversed(blocks or [text or ""]):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("src"), list):
            return data
    return None


def _claude(cfg: Config) -> list[str]:
    p = pathlib.Path(cfg.claude)
    found = str(p) if p.is_file() else shutil.which(cfg.claude)
    if not found:
        raise FileNotFoundError(f"claude CLI not found: '{cfg.claude}'")
    return [found]


def ask(
    cfg: Config, adapter: LanguageAdapter, prompt: str, resume: str = ""
) -> tuple[dict[str, Any], str]:
    """One `claude -p` turn of the survey agent: (its result object, error or '')."""
    role = (
        importlib.resources.files("framegate")
        .joinpath("roles")
        .joinpath("survey.md")
        .read_text("utf-8")
    )
    cmd = [*_claude(cfg), "-p", "--output-format", "json", "--allowedTools", TOOLS]
    cmd += ["--max-turns", MAX_TURNS, "--model", cfg.agent_model("SURVEY") or MODEL]
    cmd += ["--append-system-prompt", role] + (["--resume", resume] if resume else [])
    r = subprocess.run(
        cmd,
        input=prompt,
        cwd=cfg.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=agent_env.build(adapter, "survey"),
        check=False,
    )
    try:
        res = json.loads(r.stdout)
    except ValueError:
        return {}, (r.stderr or r.stdout).strip()[-500:] or f"exit {r.returncode}"
    return (res, "") if isinstance(res, dict) else ({}, "no result object")


def task(cfg: Config, files: Sequence[str], facts: Sequence[str]) -> str:
    listed = folders(files)
    more = f"\n... and {len(listed) - SHOWN} more folders" if len(listed) > SHOWN else ""
    return (
        f"Map the project in {cfg.root}.\n\nWhat framegate's probe found (machine facts; its "
        "source folders are a guess from the folder names):\n"
        + "\n".join(facts)
        + "\n\nFolders that hold code files (count), and loose files at the root:\n"
        + "\n".join(listed[:SHOWN])
        + more
    )


def recheck(left: Sequence[str]) -> str:
    return (
        f"These {len(left)} code files are under no source folder, test folder or exclusion "
        "of your answer. Look at them and answer again with the whole JSON:\n"
        + "\n".join(left[:SHOWN])
    )


def lines(data: dict[str, Any]) -> list[str]:
    out = [
        "Source folders: "
        + "; ".join(f"{s['path']} ({s.get('evidence', '')})" for s in data.get("src", []))
    ]
    out += [
        f"Loose source file: {s['path']} ({s.get('evidence', '')}; [paths] src takes folders: "
        "a task cannot change it)"
        for s in data.get("loose") or []
    ]
    tests = data.get("tests") or {}
    out.append(f"Test folder: {tests.get('path', '?')} ({tests.get('evidence', '')})")
    out += [f"Not project code: {x['path']} ({x.get('reason', '')})" for x in data["excluded"]]
    out += [f"Note: {n}" for n in data.get("notes") or []]
    left = data.get("uncovered") or []
    if left:
        out.append(
            f"PROBLEM: {len(left)} code file(s) under no folder: {', '.join(left[:20])}"
            + (" ..." if len(left) > 20 else "")
            + ". Ask the user: a source folder, or not the project's code (and why)."
        )
    out.append(f"Cost: {data.get('cost_usd', 0):.2f} USD")
    return out


def survey(cfg: Config, adapter: LanguageAdapter, facts: Sequence[str]) -> dict[str, Any]:
    files = code_files(cfg, adapter)
    res, err = ask(cfg, adapter, task(cfg, files, facts))
    layout = parse(str(res.get("result", "")))
    cost = float(res.get("total_cost_usd") or 0)
    left = uncovered(files, layout) if layout else []
    if layout and left and res.get("session_id"):
        again, err2 = ask(cfg, adapter, recheck(left), resume=str(res["session_id"]))
        cost += float(again.get("total_cost_usd") or 0)
        layout = parse(str(again.get("result", ""))) or layout
        left = uncovered(files, layout)
        err = err or err2
    if not layout:
        raise RuntimeError(f"the survey agent gave no layout: {err or res.get('result', '')}")
    src = [s for s in layout["src"] if isinstance(s, dict) and s.get("path")]
    clean = {
        "src": [s for s in src if not (cfg.root / s["path"]).is_file()],
        "loose": [s for s in src if (cfg.root / s["path"]).is_file()],
        "tests": layout.get("tests") or {},
        "excluded": [x for x in layout.get("excluded") or [] if isinstance(x, dict)],
        "notes": [str(n) for n in layout.get("notes") or []][:5],
        "uncovered": left,
        "cost_usd": round(cost, 2),
    }
    target = path(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(clean, indent=1), encoding="utf-8")
    return clean


def run(start: pathlib.Path | None = None) -> int:
    from framegate import probe

    cfg, adapter, _ = probe.load((start or pathlib.Path.cwd()).resolve())
    if adapter is None:
        print("framegate survey: no language adapter for this project yet")
        return 2
    facts = probe.render(probe.collect(cfg.root))
    try:
        data = survey(cfg, adapter, facts)
    except (OSError, RuntimeError) as e:
        print(f"framegate survey: {e}")
        return 1
    print(f"framegate survey: {path(cfg).relative_to(cfg.root).as_posix()}")
    for line in lines(data):
        print(line)
    return 1 if data["uncovered"] else 0
