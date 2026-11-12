"""PreToolUse hook: enforce per-phase write permissions based on the .phase file, and the
one-plain-command form of Bash / PowerShell calls during a run (shell_form.py).

No framegate.toml or no .phase -> normal interactive session -> everything allowed.
Exit code 2 blocks the tool call; stderr is shown to Claude.
"""

from __future__ import annotations

import pathlib
from typing import Any

from framegate.hooks import find_root_fast, in_agent_run, parse_input
from framegate.hooks.shell_form import SHELL_TOOLS, form_problem, message

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def evaluate_shell(data: dict[str, Any]) -> tuple[int, str]:
    """Bash / PowerShell during a framegate run (a .phase other than SPEC): block a pipe,
    chain or prefix with an actionable reason instead of the permission check's silent denial.
    Only for agents started by the run: a chat session at the same time is not restricted."""
    if not in_agent_run():
        return 0, ""
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0, ""
    problem = form_problem(command)
    if problem is None:
        return 0, ""
    root = find_root_fast(data.get("cwd"))
    if root is None:
        return 0, ""
    from framegate.config import ConfigError, load_config

    try:
        cfg = load_config(root)
    except ConfigError:
        return 0, ""
    if not cfg.phase_path.is_file():
        return 0, ""
    phase = cfg.phase_path.read_text(encoding="utf-8").strip()
    if not phase or phase == "SPEC":  # the interactive spec session: the human decides
        return 0, ""
    return 2, message(phase, command, problem, cfg.language)


def evaluate(stdin_text: str) -> tuple[int, str]:
    data = parse_input(stdin_text)
    if data.get("tool_name") in SHELL_TOOLS:
        return evaluate_shell(data)
    if data.get("tool_name") not in WRITE_TOOLS:
        return 0, ""
    root = find_root_fast(data.get("cwd"))
    if root is None:
        return 0, ""
    from framegate.config import ConfigError, load_config

    try:
        cfg = load_config(root)
    except ConfigError as e:
        return 0, f"framegate: config error, write protection inactive: {e}"
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
        + (
            "If the spec or a test looks wrong: assume what fits the spec and log it in "
            f"{cfg.paths.out}/<feature>/steps.md, or (no reasonable assumption) write the "
            f"question to {cfg.paths.out}/<feature>/park.md and stop."
            if phase == "IMPLEMENT"
            else "If the spec or the tests look wrong, write the problem to "
            f"{cfg.paths.out}/<feature>/questions.md and continue."
        )
    )
