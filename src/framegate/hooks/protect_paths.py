"""PreToolUse hook: enforce per-phase write permissions based on the .phase file, and the
one-plain-command form of Bash / PowerShell calls during a run (shell_form.py).

No framegate.toml or no .phase -> normal interactive session -> everything allowed.
Exit code 2 blocks the tool call; stderr is shown to Claude.

The spec phase (SPEC) is the human's chat: besides the specs and the source folders (stubs) it
may write the setup files (frame_diff.is_setup_file: framegate.toml and the language's
packaging / test / tool-config files) and its own two files under the out folder
(SPEC_OUT_FILES). `approve-spec` commits the setup files with the spec; from then on they are
frozen. Deploy / CI files (the built-in names: Dockerfile, compose files, CI yml and folders;
and `[protected] extra`: the project's deploy, CI, generated files) stay locked in every phase
of a task, the spec phase too, also inside a source folder: such a change is a separate step,
noted as a pipeline follow-up.
A path outside the repository is blocked for the agents of a run only: a chat session keeps
its memory folder and scratch files in every phase. A `[references]` folder is read-only for
both. A machine file (config.is_machine_file: tool state of this machine, `.serena/`,
`.vscode/`, `.venv/`, `[git] machine_files`) is never written in any phase: it is outside
every task.
The implementer of a plan step may also write the documentation files the approved plan
names for that step (step_doc; checks/plan_files.py has the rule), and nothing else outside
its source folders.

The pipeline task (`.phase` = `PIPELINE <name>`, set by `framegate pipeline <name>`) is the
chat's too, and the reverse of every other phase: only deploy / CI files that its list names
may be written, plus the list and its brief (pipeline.write_problem).
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

from framegate.config import CONFIG_FILE
from framegate.hooks import DOCS_ENV, find_root_fast, in_agent_run, parse_input
from framegate.hooks.shell_form import SHELL_TOOLS, form_problem, message

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
#: files the spec dialogue writes under {out}/<feature>/ (the spec gate brief; answers.md is
#: the chat's file at the later gates, where no phase is set)
SPEC_OUT_FILES = ("spec_brief.md", "answers.md")


def spec_extra_allowed(cfg: Any, rel: str) -> bool:
    """SPEC phase, beyond cfg.write_paths: the setup files and {out}/<feature>/spec_brief.md
    (answers.md)."""
    from framegate.adapters import get_adapter
    from framegate.checks.frame_diff import is_setup_file
    from framegate.config import ConfigError

    out = cfg.paths.out.rstrip("/") + "/"
    if rel.startswith(out):
        parts = rel[len(out) :].split("/")
        return len(parts) == 2 and parts[1] in SPEC_OUT_FILES
    try:
        return is_setup_file(get_adapter(cfg), rel)
    except ConfigError:  # an unknown language: only the file that fixes it
        return rel == CONFIG_FILE


def deploy_locked(cfg: Any, rel: str) -> bool:
    """Every phase of a feature, bug fix or refactor: a deploy / CI file
    (frame_diff.is_pipeline_file: the built-in names and `[protected] extra`, without the
    setup files), also inside a folder the phase may write."""
    from framegate.checks.frame_diff import is_pipeline_file, maybe_pipeline_file

    if not maybe_pipeline_file(cfg, rel):
        return False
    from framegate.adapters import get_adapter
    from framegate.config import ConfigError

    try:
        return is_pipeline_file(get_adapter(cfg), rel)
    except ConfigError:
        return True


def step_doc(cfg: Any, rel: str) -> bool:
    """IMPLEMENT phase, beyond cfg.write_paths: a documentation file the approved plan names
    for the step this agent runs (checks/plan_files.py; the orchestrator put the step's list
    into the agent's environment). A chat session has no such list."""
    if not in_agent_run() or not os.environ.get(DOCS_ENV, "").strip():
        return False
    from framegate.adapters import get_adapter
    from framegate.checks.plan_files import step_may_write
    from framegate.config import ConfigError

    try:
        return step_may_write(get_adapter(cfg), rel)
    except ConfigError:
        return False


def in_reference(cfg: Any, path: pathlib.Path) -> str | None:
    """The `[references]` folder this path lies in, if any: read-only for the chat as well."""
    for name in cfg.references:
        try:
            folder = cfg.reference_dir(name).resolve()
        except OSError:
            continue
        if path == folder or folder in path.parents:
            return str(name)
    return None


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
        reference = in_reference(cfg, path)
        if reference is None and not in_agent_run():
            return 0, ""  # the chat's own files (memory, scratch): not the run's business
        return 2, f"[{phase}] Blocked: {raw} is outside the repository" + (
            f" (reference '{reference}': read-only)." if reference else "."
        )
    from framegate.config import is_machine_file, machine_write_problem

    if is_machine_file(cfg, rel):
        # tool state of this machine: outside every task, whatever the phase may write
        return 2, machine_write_problem(phase.split(" ")[0] or "?", rel)
    if phase.startswith("PIPELINE "):
        # a pipeline task (pipeline.py): deploy / CI files on its list, nothing else
        from framegate.pipeline import write_problem

        problem = write_problem(cfg, phase.partition(" ")[2].strip(), rel)
        return (2, problem) if problem else (0, "")
    allowed = cfg.write_paths(phase)
    if deploy_locked(cfg, rel):
        what = (
            f"[{phase}] Blocked write to {rel}: it is a deploy / CI file (a built-in name such "
            "as Dockerfile, compose or CI yml, or a path in [protected] extra), which no "
            "feature, bug fix or refactor changes"
        )
        if phase == "SPEC":
            return 2, (
                f"{what}, not even in the spec phase. Write what must change there, and why, "
                "under `## Pipeline follow-up` of the spec's requirements.md: it is done as "
                "a separate step after the feature (/framegate:pipeline)."
            )
        return 2, (
            f"{what}. Do not attempt it: add the line `- PIPELINE FOLLOW-UP [{rel}]: <what "
            f"must change and why>` to your notes under {cfg.paths.out}/<feature>/ (the "
            "implementer: its step note; the reviewer: review.md). It is done as a separate "
            "step after the task. Not a deploy file of this project? Tell the user: "
            f"[protected] builtin_off in {CONFIG_FILE} takes a built-in name off the list."
        )
    if any(rel == a.rstrip("/") or rel.startswith(a) for a in allowed):
        return 0, ""
    if phase == "IMPLEMENT" and step_doc(cfg, rel):
        return 0, ""
    if phase == "SPEC":
        if spec_extra_allowed(cfg, rel):
            return 0, ""
        return 2, (
            f"[SPEC] Blocked write to {rel}. Allowed in the spec phase: {allowed}, the setup "
            f"files ({CONFIG_FILE}, packaging / test / tool-config files) and "
            f"{cfg.paths.out}/<feature>/spec_brief.md. Tests, other workflow files "
            "(.claude/, .framegate/, CLAUDE.md), deploy and CI files stay locked: a deploy / "
            "CI change the feature needs goes under `## Pipeline follow-up` of "
            "requirements.md (a separate step after the feature); anything else, tell the "
            "user."
        )
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
