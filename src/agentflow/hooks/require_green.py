"""Stop hook: in the IMPLEMENT phase, the agent may not finish while the selected tests fail.

Runs the tests for the current and earlier steps (IDs in .reqs; full suite if .reqs is absent).
stop_hook_active prevents an endless loop: after one forced continuation the agent may stop,
and the orchestrator's gates catch whatever is still red.
"""

from __future__ import annotations

from agentflow.hooks import find_root_fast, parse_input


def evaluate(stdin_text: str) -> tuple[int, str]:
    data = parse_input(stdin_text)
    if data.get("stop_hook_active"):
        return 0, ""
    root = find_root_fast(data.get("cwd"))
    if root is None:
        return 0, ""
    from agentflow.config import ConfigError, load_config

    try:
        cfg = load_config(root)
    except ConfigError:
        return 0, ""
    if not cfg.phase_path.is_file():
        return 0, ""
    if cfg.phase_path.read_text(encoding="utf-8").strip() != "IMPLEMENT":
        return 0, ""
    from agentflow.adapters import get_adapter
    from agentflow.state import read_reqs

    run = get_adapter(cfg).run_tests(req_ids=read_reqs(cfg), fail_fast=True)
    if run.status == "passed":
        return 0, ""
    tail = "\n".join(run.output.splitlines()[-40:])
    return 2, (
        "Tests are still failing. Do not stop yet. Fix the implementation (tests are frozen):\n"
        + tail
    )
