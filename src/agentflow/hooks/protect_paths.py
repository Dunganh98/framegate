"""PreToolUse hook: enforce per-phase write permissions based on the .phase file.

No agentflow.toml or no .phase -> normal interactive session -> everything allowed.
Exit code 2 blocks the tool call; stderr is shown to Claude.
"""

from __future__ import annotations

import pathlib

from agentflow.hooks import find_root_fast, parse_input

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def evaluate(stdin_text: str) -> tuple[int, str]:
    data = parse_input(stdin_text)
    if data.get("tool_name") not in WRITE_TOOLS:
        return 0, ""
    root = find_root_fast(data.get("cwd"))
    if root is None:
        return 0, ""
    from agentflow.config import ConfigError, load_config

    try:
        cfg = load_config(root)
    except ConfigError as e:
        return 0, f"agentflow: config error, write protection inactive: {e}"
    if not cfg.phase_path.is_file():
        return 0, ""
    phase = cfg.phase_path.read_text(encoding="utf-8").strip()
    tool_input = data.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return 0, ""
    path = pathlib.Path(raw)
    base = pathlib.Path(data.get("cwd") or root)
    path = (path if path.is_absolute() else base / path).resolve()
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return 2, f"[{phase}] Blocked: {raw} is outside the repository."
    allowed = cfg.write_paths(phase)
    if any(rel == a.rstrip("/") or rel.startswith(a) for a in allowed):
        return 0, ""
    return 2, (
        f"[{phase}] Blocked write to {rel}. Allowed in this phase: {allowed}. "
        f"If the spec or the tests look wrong, write the problem to "
        f"{cfg.paths.out}/<feature>/questions.md and stop."
    )
