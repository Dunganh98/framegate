"""Claude Code hook logic, called by the plugin as `framegate hook <name>`.

Contract: hook JSON on stdin; exit 0 = allow / nothing to do; exit 2 = block, reason on stderr.
Both hooks are no-ops (fast, silent exit 0) outside a framegate project or without a .phase.

The orchestrator starts its agents with FRAMEGATE_AGENT=<role> in the environment, and Claude Code
passes its environment on to hook commands. Checks meant for those agents only (the Stop hook's
test run, the shell-form block) use it to stay out of an interactive chat session that runs at
the same time in the same repository (in_agent_run).
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

from framegate.config import CONFIG_FILE

#: set by the orchestrator on every `claude -p` agent run (value: the role, e.g. "implement")
AGENT_ENV = "FRAMEGATE_AGENT"


def in_agent_run() -> bool:
    """True inside an agent process started by `framegate run` (and in its hooks)."""
    return bool(os.environ.get(AGENT_ENV, "").strip())


def parse_input(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def find_root_fast(cwd: str | None) -> pathlib.Path | None:
    start = pathlib.Path(cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        start = start.resolve()
    except OSError:
        return None
    for d in (start, *start.parents):
        if (d / CONFIG_FILE).is_file():
            return d
    return None
