"""Stop hook: in the IMPLEMENT phase, the agent may not finish while the selected tests fail.

Runs the tests for the current and earlier steps (IDs in .reqs; full suite if .reqs is absent),
leaving out the contested tests of provisional steps ({out}/contested_tests.txt).
stop_hook_active prevents an endless loop: after one forced continuation the agent may stop,
and the orchestrator's gates catch whatever is still red. An agent that parks its work (wrote
<out>/<feature>/park.md: no reasonable assumption possible) may stop with red tests.

Only agents started by `agentflow run` are held (AGENTFLOW_AGENT set): a chat session in the same
repository must never start a second test run while the agent's build and tests are running
(parallel runs lock each other's build output and produce fake failures).
"""

from __future__ import annotations

from agentflow.hooks import find_root_fast, in_agent_run, parse_input


def evaluate(stdin_text: str) -> tuple[int, str]:
    if not in_agent_run():
        return 0, ""
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
    if any(p.is_file() for p in cfg.out_dir.glob("*/park.md")):
        return 0, ""  # parked: the orchestrator reverts the step's code and moves on
    from agentflow.adapters import get_adapter
    from agentflow.state import read_contested, read_reqs

    adapter = get_adapter(cfg)
    contested = read_contested(cfg)
    if contested:  # a provisional step's contested tests fail by design: judge the others
        from agentflow.gates import ExpectedRed, tests_expecting

        run = adapter.run_tests(req_ids=read_reqs(cfg), junit=cfg.out_dir / "hook_tests.xml")
        if tests_expecting(run, ExpectedRed(contested=contested), set()).status == "PASS":
            return 0, ""
    else:
        run = adapter.run_tests(req_ids=read_reqs(cfg), fail_fast=True)
    if run.status == "passed":
        return 0, ""
    tail = "\n".join(run.output.splitlines()[-40:])
    return 2, (
        "Tests are still failing. Do not stop yet. Fix the implementation (tests are frozen). "
        "Only if no reasonable assumption from the spec can make them pass, write the question "
        f"to {cfg.paths.out}/<feature>/park.md and stop.\n" + tail
    )
