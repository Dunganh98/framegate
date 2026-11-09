"""Claude Code hook logic, called by the plugin as `agentflow hook <name>`.

Contract: hook JSON on stdin; exit 0 = allow / nothing to do; exit 2 = block, reason on stderr.
Both hooks are no-ops (fast, silent exit 0) outside an agentflow project or without a .phase.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

from agentflow.config import CONFIG_FILE


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
