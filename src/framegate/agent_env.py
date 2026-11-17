"""The environment of an agent run (the `claude -p` process and every shell it starts).

Three things differ from the environment framegate itself runs in:

- `FRAMEGATE_AGENT=<role>` marks the agent's hooks as part of this run (hooks/__init__.py).
- The language adapter's `agent_env()`: for Python the project interpreter's folder first on
  PATH (and VIRTUAL_ENV for a venv), so `python`, `pytest`, `ruff`, `black` in the agent's
  shell are the ones the gates use. Without it `python` was the machine's global interpreter,
  which has no framegate pytest plugin (`unrecognized arguments: --req`), and the agent tested
  in another environment than the gates.
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`: no per-project auto-memory of the user's chats
  (`~/.claude/projects/<repo>/memory/`). An agent works from the spec, the code and the run
  files only; a retro agent had quoted notes of an earlier chat and tried to write there.
  Probed live (docs/claude-cli-notes.md): the memory is gone, the plugin's hooks and
  `--allowedTools` still work. The user's global `~/.claude/CLAUDE.md` is still loaded (no
  switch found that keeps a user-scope plugin's hooks).

An implementer run of plan steps also gets `FRAMEGATE_STEP_DOCS`: the documentation files the
approved plan names for those steps, which the write guard then lets it write
(checks/plan_files.py). The agent cannot change the environment its hooks run in.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
from collections.abc import Mapping

from framegate.adapters.base import LanguageAdapter
from framegate.hooks import AGENT_ENV, DOCS_ENV

#: switches Claude Code's auto-memory off for the process (read and write)
NO_MEMORY_ENV = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"


def build(
    adapter: LanguageAdapter,
    role: str,
    base: Mapping[str, str] | None = None,
    docs: list[str] | None = None,
) -> dict[str, str]:
    """The whole environment for one agent run of `role`. `docs`: the documentation files
    the approved plan names for the step(s) this run implements; a list inherited from the
    caller's environment is never passed on."""
    env = dict(os.environ if base is None else base)
    env.update(adapter.agent_env())
    env[AGENT_ENV] = role
    env[NO_MEMORY_ENV] = "1"
    env.pop(DOCS_ENV, None)
    if docs:
        env[DOCS_ENV] = json.dumps(docs)
    return env


def which(name: str, adapter: LanguageAdapter) -> pathlib.Path | None:
    """What `name` resolves to in an agent's shell (None = not on its PATH)."""
    path = adapter.agent_env().get("PATH", os.environ.get("PATH", ""))
    found = shutil.which(name, path=path)
    return pathlib.Path(found) if found else None
