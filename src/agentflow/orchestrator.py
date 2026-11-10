"""Workflow driver with three human gates, forward only:

SPEC (approve-spec) -> TESTS (test writer <-> test reviewer, mechanical checks) -> TESTS gate ->
IMPLEMENT (per plan step) -> REVIEW (adversarial reviewer -> triage by the test reviewer -> fixes
-> final gates) -> PR (final summary) -> FINAL gate -> RETRO -> DONE.

Every agent run is a fresh headless `claude -p` process: no context accumulates between steps.
`run` never blocks on input and never stops between the gates: questions of the test writer and
the test reviewer are collected and shown together at the tests gate; the implementer assumes from
the spec (logged in steps.md) or parks a step it cannot do, and so does a step that fails
max_attempts times; review findings are triaged against the spec by the test reviewer. At a gate
`run` records the wait in out/<f>/progress.json ("waiting") and exits with EXIT_GATE; the human
continues with approve / reject / answer / finish, then `run` again. Resumable at any point.
"""

from __future__ import annotations

import datetime
import importlib.resources
import json
import os
import pathlib
import re
import shutil
import sys
import time
from collections.abc import Callable
from typing import Any

from agentflow import notify, procs
from agentflow.adapters import get_adapter
from agentflow.adapters.base import LanguageAdapter
from agentflow.checks import red_stubs, test_smells, traceability
from agentflow.config import Config
from agentflow.gates import gate_report
from agentflow.hooks import AGENT_ENV
from agentflow.report import (
    TestReview,
    final_facts,
    parse_assumptions,
    parse_test_review,
    parse_triage,
    phase_table,
    since_spec_approval,
    triage_verdict,
)
from agentflow.retro import build as build_retro_input
from agentflow.retro import missing_ids, split_questions
from agentflow.specs import PlanStep, feature_ids, plan_steps
from agentflow.state import (
    GitError,
    clear_phase,
    clear_reqs,
    commit,
    fresh_progress,
    git,
    load_progress,
    log_event,
    read_events,
    read_phase,
    save_progress,
    set_phase,
    set_reqs,
    tag_exists,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GATE = 10  # waiting at a human gate (tests or final): approve / reject / answer / finish
EXIT_INTERRUPTED = 130  # Ctrl+C / SIGTERM: agents stopped, state cleared; run again
PROGRESS_LOG = "progress.log"
#: byte offset of progress.log that `agentflow watch` has printed (a re-armed watch goes on there)
PROGRESS_CURSOR = "progress.cursor"
PROGRESS_WIDTH = 160

PHASES = ["SPEC_UPDATE", "TESTS", "IMPLEMENT", "REVIEW", "PR", "FINAL", "RETRO", "DONE"]
METRICS_HEADER = (
    "date,feature,human_rejections,escalations,questions,gate_failures,cost_usd,parked,assumptions"
)
BRIEF_PHASES = {"TESTS", "TEST_REVIEW", "IMPLEMENT", "IMPLEMENT_STEPS", "FIX", "REVIEW", "TRIAGE"}
#: Only the plain command forms are pre-approved (--allowedTools); a `cd`/PATH prefix, a pipe
#: or a `;`/`&&` chain makes the permission check deny the call (seen in live runs on Windows:
#: `dotnet test ... | Select-String`, `...; echo`). The PreToolUse hook blocks them with this
#: reason too (hooks/shell_form.py).
COMMAND_FORM = (
    "- Shell commands: run the commands above exactly as written, from the repository root "
    "(Bash or PowerShell tool), **one command per call**. No pipes (`| Select-String`, "
    "`| grep`, `| head`), no `;` / `&&` / `||` chains, no `cd` or PATH/env prefixes: those "
    "calls are denied. To see less output, use the command's own options (test filters, "
    "verbosity, quiet flags) or read the result file it writes with the Read tool; to search "
    "files use Grep / Glob / Read."
)
NO_STOP = (
    "Nobody answers questions during the run. Where the spec is ambiguous, assume what fits the "
    "spec best and log it in {out}/{f}/steps.md (`- ASSUMPTION [<spec ID>]: ...`). Only if no "
    "reasonable assumption is possible, write the question to {out}/{f}/park.md and stop: the "
    "work is parked and the run continues."
)
REDO_NOTE = (
    "\n\nThis is a redo of parked work: the human answered the parked question(s) in "
    "{out}/{f}/answers.md (earlier questions: {out}/{f}/questions_archive.md). Read both first."
)

TASKS = {
    "TESTS": "Feature '{f}': write the tests from {specs}/{f}/ and the interface stubs in {src}/. "
    "Write the summary table to {out}/{f}/test_summary.md. A question the spec does not settle "
    "goes to {out}/{f}/questions.md; then continue with the rest (do not stop).",
    "TEST_REVIEW": "Feature '{f}': review the tests against {specs}/{f}/ (round {round} of at "
    "most {rounds}). Inputs: the tests tagged with the feature's spec IDs, "
    "{out}/{f}/test_summary.md, the mechanical checks in {out}/{f}/test_checks.md, and "
    "{out}/{f}/answers.md if present. Write your verdict to {out}/{f}/test_review.md. Do not "
    "edit tests.{note}",
    "IMPLEMENT": "Feature '{f}': implement plan step {n} ('{title}'). Make the tests for these "
    "IDs pass: {ids}. Tests of earlier steps must stay green. Check with: {check}\n" + NO_STOP,
    "IMPLEMENT_STEPS": "Feature '{f}': implement plan steps {n} in one go, in plan order: "
    "{titles}. Make the tests for these IDs pass: {ids}. Tests of earlier steps must stay green. "
    "Check with: {check}\n" + NO_STOP,
    "FIX": "Feature '{f}': make all currently failing tests pass without editing tests. "
    "Check with: {check}\n" + NO_STOP,
    "REVIEW": "Feature '{f}': review the implementation against {specs}/{f}/. "
    "Implementation diff: git diff tests-approved/{f}",
    "TRIAGE": "Feature '{f}': triage the adversarial reviewer's findings against {specs}/{f}/. "
    "Failing review tests:\n{findings}\nReviewer notes: {out}/{f}/review.md. Delete the tests of "
    "INVALID findings from {review_tests}/ and write one line per finding to {out}/{f}/triage.md.",
    "PR": "Feature '{f}': write the final summary for the human's final gate to "
    "{out}/{f}/final_summary.md. Start with {out}/{f}/final_facts.md (generated facts). Other "
    "inputs: {specs}/{f}/, {out}/{f}/test_summary.md, {out}/{f}/steps.md, {out}/{f}/review.md, "
    "{out}/{f}/triage.md, {out}/{f}/final_gates.md, {out}/{f}/mutation_survivors.md and "
    "{out}/{f}/answers.md if present.",
    "SPEC_UPDATE": "Feature '{f}': write the human's answers at the {gate} gate (answer "
    "reference {ref}) into the spec in {specs}/{f}/. Answers: {out}/{f}/answers.md; the questions "
    "they answer: the last section of {out}/{f}/questions_archive.md. Mark every change with "
    "'(answer {ref})'. Never renumber, rename or delete an existing ID; a new behavior or edge "
    "case gets a new ID. List the changes in {out}/{f}/spec_update.md.",
    "RETRO": "Feature '{f}': run the retrospective. Start with {out}/{f}/retro_input.md: every "
    "question, failed attempt, escalation, rejection, review finding, final-gate failure, "
    "permission denial, assumption and parked item, "
    "with item IDs. Address every ID in {out}/{f}/retro_proposals.md (a proposal, or the "
    "'Not acted on' list with a one-line reason). More detail: {out}/events.jsonl (feature={f}), "
    "{out}/{f}/, {out}/runs/*{f}*.json.",
}


def summarize_denials(raw: Any, limit: int = 300) -> list[dict[str, str]]:
    """`permission_denials` of a `claude -p` JSON result -> [{tool, input}] with the input
    trimmed to its command / file path (or compact JSON)."""
    out: list[dict[str, str]] = []
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict):
            continue
        inp = d.get("tool_input")
        if isinstance(inp, dict):
            text = next(
                (str(inp[k]) for k in ("command", "file_path", "notebook_path", "url") if k in inp),
                json.dumps(inp, ensure_ascii=False),
            )
        else:
            text = "" if inp is None else str(inp)
        if len(text) > limit:
            text = text[:limit] + "..."
        out.append({"tool": str(d.get("tool_name", "?")), "input": text})
    return out


def parse_result(stdout: str, stderr: str = "") -> dict[str, Any]:
    """The result of a `claude -p` run: `--output-format stream-json --verbose` (one JSON
    message per line, the last `type: result` line is the result) or a single JSON object
    (`--output-format json`). Adds `api_turns`: the distinct assistant messages = model round
    trips, which is what --max-turns caps (None without the stream). Unparseable output is an
    error result."""
    text = stdout.strip()
    try:
        whole = json.loads(text)
        if isinstance(whole, dict):
            whole.setdefault("api_turns", None)
            return whole
    except ValueError:
        pass
    result: dict[str, Any] | None = None
    ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "result":
            result = msg
        elif msg.get("type") == "assistant" and isinstance(msg.get("message"), dict):
            ids.add(str(msg["message"].get("id") or f"#{len(ids)}"))
    if result is None:
        return {"is_error": True, "result": (stderr or stdout)[-2000:], "api_turns": len(ids)}
    result["api_turns"] = len(ids)
    return result


#: re-runs of a non-IMPLEMENT agent that stopped at a cap (turns or budget) before escalating
TURN_CAP_RETRIES = 1


def hit_cap(res: dict[str, Any]) -> str | None:
    """'turns' if a `claude -p` result says the run stopped at --max-turns, 'budget' if it
    stopped at --max-budget-usd, else None."""
    if res.get("subtype") == "error_max_turns" or res.get("terminal_reason") == "max_turns":
        return "turns"
    if (
        res.get("subtype") == "error_max_budget_usd"
        or res.get("terminal_reason") == "budget_exhausted"
    ):
        return "budget"
    return None


def cap_label(kind: str, cap: float) -> str:
    return f"turn cap ({cap:g} turns)" if kind == "turns" else f"budget cap ({cap:g} USD)"


def turn_cap_note(cap: float, kind: str = "turns") -> str:
    return (
        f"Your previous run stopped at the {cap_label(kind, cap)} before it finished. Its "
        "file changes are kept: check what is already done, then finish the rest with fewer, "
        "larger steps (batch reads and edits, no exploratory commands)."
    )


def item_steps(item: dict[str, Any]) -> list[int]:
    """The plan steps of a step item (park / redo record): a batch lists them in "steps"."""
    steps = item.get("steps")
    return list(steps) if steps else [item["step"]]


class WorkflowError(Exception):
    """A user-facing error (exit code 1)."""


class Paused(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def package_text(*parts: str) -> str:
    res = importlib.resources.files("agentflow")
    for p in parts:
        res = res.joinpath(p)
    return res.read_text(encoding="utf-8")


def package_path(*parts: str) -> Any:
    res = importlib.resources.files("agentflow")
    for p in parts:
        res = res.joinpath(p)
    return res


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        adapter: LanguageAdapter | None = None,
        echo: Callable[[str], None] = print,
    ) -> None:
        self.cfg = cfg
        self.adapter = adapter or get_adapter(cfg)
        self.echo = echo
        self._claude: list[str] | None = None

    # ---------- helpers ----------
    def fdir(self, feature: str) -> pathlib.Path:
        d = self.cfg.feature_out(feature)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rel(self, path: pathlib.Path) -> str:
        return path.relative_to(self.cfg.root).as_posix()

    def placeholders(self, feature: str) -> dict[str, str]:
        p = self.cfg.paths
        return {
            "feature": feature,
            "f": feature,
            "specs": p.specs,
            "src": p.src,
            "tests": p.tests,
            "review_tests": p.review_tests,
            "out": p.out,
        }

    def render(self, text: str, feature: str) -> str:
        for key, value in self.placeholders(feature).items():
            text = text.replace("{" + key + "}", value)
        return text

    def role_text(self, role: str, feature: str) -> str:
        text = package_text("roles", f"{role}.md")
        if self.cfg.roles_extra_dir:
            extra = self.cfg.root / self.cfg.roles_extra_dir / f"{role}.md"
            if extra.is_file():
                text = (
                    text.rstrip()
                    + "\n\n# Project additions\n\n"
                    + extra.read_text(encoding="utf-8")
                )
        return self.render(text, feature)

    def task_text(self, kind: str, feature: str, **extra: str) -> str:
        task = TASKS[kind].format(**self.placeholders(feature), **extra)
        if kind in BRIEF_PHASES:
            task += "\n\n## Conventions\n" + self.adapter.conventions_brief().strip()
            task += "\n" + COMMAND_FORM
        return task

    def claude_cmd(self) -> list[str]:
        if self._claude is None:
            value = self.cfg.claude
            path = pathlib.Path(value)
            if value.endswith(".py") and path.is_file():
                self._claude = [sys.executable, str(path)]
            elif path.is_file():
                self._claude = [str(path)]
            else:
                found = shutil.which(value)
                if not found:
                    raise WorkflowError(
                        f"claude CLI not found: '{value}' (set [agents] claude or AGENTFLOW_CLAUDE)"
                    )
                self._claude = [found]
        return self._claude

    def allowed_tools(self, phase: str) -> list[str]:
        if phase in self.cfg.phase_tools:
            return list(self.cfg.phase_tools[phase])
        return self.adapter.allowed_tools(phase)

    def run_agent(
        self,
        phase: str,
        feature: str,
        task: str,
        feedback: str = "",
        role: str | None = None,
        step: str | None = None,
    ) -> dict[str, Any]:
        """One fresh headless Claude Code run. The task goes via stdin and the role via
        --append-system-prompt-file (no multi-line text on the command line)."""
        cmd = self.claude_cmd()
        set_phase(self.cfg, phase)
        role_name = (role or phase).lower()
        prompts = self.fdir(feature) / "prompts"
        prompts.mkdir(exist_ok=True)
        role_file = prompts / f"{role_name}.system.md"
        role_file.write_text(self.role_text(role_name, feature), encoding="utf-8")
        prompt = task + (f"\n\n{feedback}" if feedback else "")
        args = [
            *cmd,
            "-p",
            "--append-system-prompt-file",
            str(role_file),
            "--allowedTools",
            ",".join(self.allowed_tools(phase)),
            "--permission-mode",
            self.cfg.permission_mode,
            "--max-turns",
            str(self.cfg.turn_cap(phase)),
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        budget = self.cfg.budget_cap(phase)
        if budget is not None:
            args += ["--max-budget-usd", f"{budget:g}"]
        model, effort = self.cfg.agent_model(phase), self.cfg.agent_effort(phase)
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        runs = self.cfg.out_dir / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (runs / f"{stamp}-{feature}-{phase}.prompt.md").write_text(prompt, encoding="utf-8")
        t0 = time.time()
        try:
            # own process group + kill-on-close job: Ctrl+C / SIGTERM / a killed agentflow
            # take the whole agent tree (claude, node, shells, dotnet) down with it
            # AGENTFLOW_AGENT marks the agent's hooks as part of this run (hooks/__init__.py)
            env = {**os.environ, AGENT_ENV: role_name}
            _, stdout, stderr = procs.run(args, input=prompt, cwd=self.cfg.root, job=True, env=env)
        except OSError as e:
            stdout, stderr = "", f"could not start claude: {e}"
        res = parse_result(stdout, stderr)
        # the result object (read by the retro), and the whole message stream for debugging
        (runs / f"{stamp}-{feature}-{phase}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if stdout.count("\n") > 1:
            (runs / f"{stamp}-{feature}-{phase}.stream.jsonl").write_text(stdout, encoding="utf-8")
        denials = summarize_denials(res.get("permission_denials"))
        log_event(
            self.cfg,
            feature,
            "agent_run",
            phase=phase,
            model=model or None,
            effort=effort or None,
            seconds=round(time.time() - t0),
            cost=res.get("total_cost_usd"),
            # API round trips (what --max-turns caps); num_turns counts messages
            turns=res["api_turns"] if res.get("api_turns") is not None else res.get("num_turns"),
            messages=res.get("num_turns"),
            error=res.get("is_error"),
            denials=len(denials),
        )
        if denials:
            log_event(
                self.cfg, feature, "permission_denials", phase=phase, step=step, denials=denials
            )
            self.progress(
                feature, f"[{phase}] {len(denials)} tool call(s) denied (logged for the retro)."
            )
        kind = hit_cap(res)
        if kind == "turns":
            cap = self.cfg.turn_cap(phase)
            log_event(
                self.cfg,
                feature,
                "max_turns",
                phase=phase,
                step=step,
                cap=cap,
                turns=res.get("api_turns"),
                messages=res.get("num_turns"),
            )
            self.progress(feature, f"[{phase}] agent run stopped at the turn cap ({cap} turns).")
        elif kind == "budget":
            budget_cap = self.cfg.budget_cap(phase) or 0.0
            log_event(
                self.cfg,
                feature,
                "max_budget",
                phase=phase,
                step=step,
                cap=budget_cap,
                cost=res.get("total_cost_usd"),
            )
            self.progress(
                feature, f"[{phase}] agent run stopped at the budget cap ({budget_cap:g} USD)."
            )
        elif res.get("is_error"):
            self.progress(
                feature, f"[{phase}] agent run reported an error: {str(res.get('result'))[:500]}"
            )
        return res

    # ---------- waiting (only the tests gate and the final gate) ----------
    def describe_waiting(self, feature: str, waiting: dict[str, Any]) -> str:
        gate = waiting.get("gate")
        f, out = feature, self.cfg.paths.out
        files = "\n".join(f"  read: {x}" for x in waiting.get("files", []))
        msg = f"{waiting['message']}\n" if waiting.get("message") else ""
        if waiting.get("kind") != "gate":
            return f"waiting: {waiting}"
        if gate == "tests":
            return (
                f"=== TESTS GATE ({f}) ===\n{files}\n{msg}"
                f"Approve:  agentflow approve {f}\n"
                f'Reject:   agentflow reject {f} --reason "<what is wrong>"'
                "   (test writer + test reviewer re-run)\n"
                f"Answer:   write {out}/{f}/answers.md, then agentflow answer {f}"
                "   (answers go into the spec, then test writer + test reviewer re-run)\n"
                f"Then:     agentflow run {f}"
            )
        if gate == "final":
            return (
                f"=== FINAL GATE ({f}) ===\n{files}\n{msg}"
                f"Approve:  agentflow approve {f}   (then agentflow run {f}: retro -> DONE)\n"
                f"No retro: agentflow finish {f} --no-retro   (DONE)\n"
                f"Parked:   write {out}/{f}/answers.md, then agentflow answer {f} and "
                f"agentflow run {f}\n"
                "          (answers go into the spec, then only the parked items are redone and "
                "this gate comes again)"
            )
        return f"=== GATE: {gate} ({f}) ===\n{files}\n{msg}"

    def pause(self, feature: str, prog: dict[str, Any], waiting: dict[str, Any]) -> None:
        prog["waiting"] = waiting
        save_progress(self.cfg, feature, prog)
        log_event(self.cfg, feature, "waiting", wait=waiting["kind"], gate=waiting.get("gate"))
        gate = str(waiting.get("gate") or waiting["kind"]).upper()
        message = str(waiting.get("message") or "")
        self.progress(feature, f"{gate} GATE: waiting for you. {message}")
        self.echo("\n" + self.describe_waiting(feature, waiting))
        self.notify(f"agentflow: {feature} at the {gate.lower()} gate", message)
        raise Paused(EXIT_GATE)

    def set_sub(self, feature: str, prog: dict[str, Any], sub: str | None) -> str | None:
        prog["sub"] = sub
        save_progress(self.cfg, feature, prog)
        return sub

    # ---------- live view: one short line per milestone ----------
    def cost_so_far(self, feature: str) -> float:
        """USD of all agent runs of the current spec version."""
        ev = since_spec_approval(read_events(self.cfg, feature))
        return sum(float(e.get("cost") or 0) for e in ev if e.get("kind") == "agent_run")

    def progress(self, feature: str, text: str) -> None:
        """A progress line: printed and appended to {out}/{f}/progress.log, which the chat's live
        view streams (`agentflow watch`). Milestones only (~10-30 per feature), never tool calls."""
        text = " ".join(text.split())
        if len(text) > PROGRESS_WIDTH:
            text = text[: PROGRESS_WIDTH - 3] + "..."
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"{stamp} {text} [${self.cost_so_far(feature):.2f}]"
        with open(self.fdir(feature) / PROGRESS_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.echo(line)

    def notify(self, title: str, text: str) -> None:
        """Windows toast (no-op when off, not on Windows, or failing). Called only where the run
        ends, so waiting a few seconds for the toast process costs the run nothing."""
        notify.settle(notify.toast(self.cfg, title, text))

    # ---------- questions: collected, never a stop ----------
    def collect_questions(self, feature: str, phase: str, who: str) -> int:
        """Move questions.md (written by an agent) to open_questions.md, tagged with who asked.
        They are shown at the next gate; the run goes on."""
        d = self.fdir(feature)
        qfile = d / "questions.md"
        if not qfile.is_file():
            return 0
        text = qfile.read_text(encoding="utf-8").strip()
        qfile.unlink()
        items = split_questions(text)
        if not items:
            return 0
        with open(d / "open_questions.md", "a", encoding="utf-8") as fh:
            for q in items:
                fh.write(f"- [{who}] " + q.replace("\n", "\n  ") + "\n")
        log_event(self.cfg, feature, "questions", phase=phase, who=who, questions=text)
        self.progress(feature, f"[{phase}] {len(items)} question(s) collected for the next gate.")
        return len(items)

    def open_questions(self, feature: str) -> list[str]:
        p = self.fdir(feature) / "open_questions.md"
        return split_questions(p.read_text(encoding="utf-8")) if p.is_file() else []

    def archive_questions(self, feature: str, where: str, questions: str, answer: str) -> None:
        if not questions.strip():
            return
        with open(self.fdir(feature) / "questions_archive.md", "a", encoding="utf-8") as fh:
            fh.write(f"\n---\n<!-- {where} -->\n{questions.rstrip()}\n\n{answer.rstrip()}\n")
        (self.fdir(feature) / "open_questions.md").unlink(missing_ok=True)

    # ---------- agent runs ----------
    def agent(
        self,
        phase: str,
        feature: str,
        prog: dict[str, Any],
        task: str,
        role: str | None = None,
        step: str | None = None,
        retry_turn_cap: bool = True,
    ) -> dict[str, Any]:
        """Run an agent with the pending feedback and consume it. A run that stops at the turn
        cap or the budget cap is re-run once with a note; if that one stops at a cap too, it is
        logged (escalation event, retro E item) and the run continues (retry_turn_cap=False: the
        caller handles it, e.g. the step gate decides)."""
        while True:
            res = self.run_agent(
                phase, feature, task, prog.get("feedback", ""), role=role, step=step
            )
            prog["feedback"] = ""
            save_progress(self.cfg, feature, prog)
            kind = hit_cap(res)
            if (
                not retry_turn_cap
                or kind is None
                or not self.turn_cap_retry(phase, feature, prog, step, kind)
            ):
                prog["turn_cap_retries"] = 0
                save_progress(self.cfg, feature, prog)
                return res

    def cap_value(self, phase: str, kind: str) -> float:
        if kind == "turns":
            return self.cfg.turn_cap(phase)
        return self.cfg.budget_cap(phase) or 0.0

    def turn_cap_retry(
        self,
        phase: str,
        feature: str,
        prog: dict[str, Any],
        step: str | None,
        kind: str = "turns",
    ) -> bool:
        """After a run stopped at a cap (turns or budget): set the retry note (True), or log
        that the retry stopped at a cap too and go on without a stop (False)."""
        cap = self.cap_value(phase, kind)
        label = step or phase
        name = "turn cap" if kind == "turns" else "budget cap"
        retries = int(prog.get("turn_cap_retries", 0))
        if retries < TURN_CAP_RETRIES:
            prog["turn_cap_retries"] = retries + 1
            prog["feedback"] = turn_cap_note(cap, kind)
            save_progress(self.cfg, feature, prog)
            self.progress(feature, f"[{label}] re-running once after the {name}.")
            return True
        key = "max_turns" if kind == "turns" else "max_budget_usd"
        log_event(
            self.cfg,
            feature,
            "escalation",
            phase=phase,
            step=step,
            attempts=retries + 1,
            cap_kind=kind,
            failed=[f"{name} ({cap:g})"],
            resolution=f"continued without a stop with the files of the last run (raise "
            f"[phases.{phase}] {key} if this recurs)",
        )
        self.progress(
            feature,
            f"[{label}] stopped at the {name} ({cap:g}) {retries + 1} times; continuing with what "
            "it wrote (logged for the final summary and the retro).",
        )
        return False

    # ---------- implement loop (steps and review fixes): pass or park ----------
    def take_park_request(self, feature: str) -> str:
        """The implementer's park request (park.md; questions.md counts too), removed."""
        d = self.fdir(feature)
        text = ""
        for name in ("park.md", "questions.md"):
            p = d / name
            if p.is_file():
                text += p.read_text(encoding="utf-8").strip() + "\n"
                p.unlink()
        return text.strip()

    def implement_loop(
        self,
        feature: str,
        prog: dict[str, Any],
        task: str,
        reqs: list[str] | None,
        item: dict[str, Any],
    ) -> bool:
        """Run the implementer until the step gates pass (True). A park request or max_attempts
        failed attempts park the item (False); the run goes on either way."""
        label = item["label"]
        if reqs:
            set_reqs(self.cfg, reqs)
        else:
            clear_reqs(self.cfg)
        self.take_park_request(feature)  # a stale request of an interrupted run
        while True:
            attempt = int(prog.get("attempt", 0)) + 1
            prog["attempt"] = attempt
            save_progress(self.cfg, feature, prog)
            try:
                res = self.agent(
                    "IMPLEMENT",
                    feature,
                    prog,
                    task,
                    role="implement",
                    step=label,
                    retry_turn_cap=False,
                )
                question = self.take_park_request(feature)
                self.report_new_assumptions(feature, prog)
                t0 = time.time()
                rep = None if question else gate_report(self.adapter, feature, "step")
            except KeyboardInterrupt:
                prog["attempt"] = attempt - 1  # an interruption is not an attempt
                save_progress(self.cfg, feature, prog)
                raise
            if rep is None:
                self.park(
                    feature,
                    prog,
                    item,
                    "no reasonable assumption possible (question)",
                    attempts=attempt - 1,
                    question=question,
                )
                return False
            log_event(
                self.cfg,
                feature,
                "gate",
                phase=prog["phase"],
                step=label,
                steps=item.get("steps"),
                attempt=attempt,
                passed=rep.ok,
                failed=rep.failed,
                excerpt=rep.excerpt() if not rep.ok else "",
                seconds=round(time.time() - t0),
            )
            if rep.ok:
                commit(self.cfg, f"{feature}: {label}")
                self.progress(feature, f"[{label}] green (attempt {attempt}), committed")
                prog["attempt"] = 0
                prog["feedback"] = ""
                save_progress(self.cfg, feature, prog)
                return True
            kind = hit_cap(res)
            capped = turn_cap_note(self.cap_value("IMPLEMENT", kind), kind) if kind else ""
            prog["feedback"] = (
                (capped + "\n" if capped else "")
                + f"Attempt {attempt} failed these gates. Tests and config are frozen; "
                f"fix {self.cfg.paths.src}/.\n{rep.report[-6000:]}"
            )
            save_progress(self.cfg, feature, prog)
            self.progress(
                feature,
                f"[{label}] attempt {attempt} of {self.cfg.max_attempts} failed: "
                + (", ".join(rep.failed) or "step gates"),
            )
            if attempt >= self.cfg.max_attempts:
                self.park(
                    feature,
                    prog,
                    item,
                    f"failed {attempt} attempts",
                    attempts=attempt,
                    failed=rep.failed,
                    excerpt=rep.excerpt(),
                )
                return False

    def revert_src(self, feature: str, label: str) -> str:
        """Save the parked work's uncommitted source changes as a diff and restore the sources
        to the last commit, so later steps start from code that passed its gates."""
        src = self.cfg.paths.src
        if not (self.cfg.root / src).exists():
            return ""
        git(self.cfg, "add", "-A", "--", src)
        diff = git(self.cfg, "diff", "--cached", "HEAD", "--", src)
        if not diff.strip():
            return ""
        pdir = self.fdir(feature) / "parked"
        pdir.mkdir(exist_ok=True)
        path = pdir / (re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower() + ".diff")
        path.write_text(diff, encoding="utf-8")
        git(self.cfg, "restore", "--source=HEAD", "--staged", "--worktree", "--", src)
        return self.rel(path)

    def park(
        self,
        feature: str,
        prog: dict[str, Any],
        item: dict[str, Any],
        reason: str,
        attempts: int,
        question: str = "",
        failed: list[str] | None = None,
        excerpt: str = "",
    ) -> None:
        diff = self.revert_src(feature, item["label"])
        rec = dict(item, reason=reason, attempts=attempts, question=question, failed=failed or [])
        rec["diff"] = diff
        prog["parked"] = [*(prog.get("parked") or []), rec]
        prog.update(attempt=0, feedback="")
        save_progress(self.cfg, feature, prog)
        log_event(
            self.cfg,
            feature,
            "parked",
            item=item["kind"],
            label=item["label"],
            step=item.get("step"),
            tests=item.get("tests"),
            reason=reason,
            attempts=attempts,
            question=question,
            failed=failed or [],
            excerpt=excerpt,
            diff=diff,
        )
        self.progress(
            feature,
            f"[{item['label']}] PARKED: {reason}. The run continues; it is listed at the final "
            "gate" + (f" (its code changes: {diff})." if diff else "."),
        )

    # ---------- TESTS: writer <-> reviewer, then the tests gate ----------
    def phase_tests(self, feature: str, prog: dict[str, Any]) -> None:
        sub = prog.get("sub") or "write"
        while True:
            if sub == "write":
                self.agent("TESTS", feature, prog, self.task_text("TESTS", feature))
                self.collect_questions(feature, "TESTS", "test writer")
                sub = self.set_sub(feature, prog, "checks")
            if sub == "checks":
                self.run_test_checks(feature, prog)
                sub = self.set_sub(feature, prog, "review")
            if sub == "review":
                rnd = int(prog.get("round") or 0)
                (self.fdir(feature) / "test_review.md").unlink(missing_ok=True)
                note = prog.get("human_note") or ""
                task = self.task_text(
                    "TEST_REVIEW",
                    feature,
                    round=str(rnd + 1),
                    rounds=str(self.cfg.test_review_rounds + 1),
                    note=f"\n\nThe human's note at the tests gate (check the tests address it): "
                    f"{note}"
                    if note
                    else "",
                )
                self.agent("TEST_REVIEW", feature, prog, task, role="test_review")
                self.collect_questions(feature, "TEST_REVIEW", "test reviewer")
                review = self.read_test_review(feature, keep_round=rnd + 1)
                failed = [n for n, s in (prog.get("test_checks") or {}).items() if s != "PASS"]
                back = (not review.clean or bool(failed)) and rnd < self.cfg.test_review_rounds
                log_event(
                    self.cfg,
                    feature,
                    "test_review",
                    round=rnd + 1,
                    verdict=review.verdict,
                    objections=review.objections,
                    failed_checks=failed,
                    sent_back=back,
                )
                if back:
                    prog["round"] = rnd + 1
                    prog["feedback"] = self.objection_feedback(feature, review, failed)
                    self.progress(
                        feature,
                        f"[TESTS] test reviewer: {review.verdict} "
                        f"({len(review.objections)} objection(s), {len(failed)} failed check(s)); "
                        f"back to the test writer (round {rnd + 1} of "
                        f"{self.cfg.test_review_rounds}).",
                    )
                    sub = self.set_sub(feature, prog, "write")
                    continue
                self.progress(
                    feature,
                    f"[TESTS] test reviewer round {rnd + 1}: {review.verdict}, "
                    f"{len(review.objections)} objection(s) left",
                )
                sub = self.set_sub(feature, prog, "gate")
            break
        self.tests_gate(feature, prog)

    def run_test_checks(self, feature: str, prog: dict[str, Any]) -> None:
        sections = []
        status: dict[str, str] = {}
        for name, mod in [
            ("Red against stubs", red_stubs),
            ("Traceability", traceability),
            ("Test smells", test_smells),
        ]:
            res = mod.check(self.adapter, feature)
            status[name] = res.status
            sections.append(f"### {name}: {res.status}\n~~~\n{res.output}\n~~~")
        path = self.fdir(feature) / "test_checks.md"
        path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
        self.progress(
            feature, "[TESTS] checks: " + ", ".join(f"{n} {s}" for n, s in status.items())
        )
        prog["test_checks"] = status
        save_progress(self.cfg, feature, prog)

    def read_test_review(self, feature: str, keep_round: int | None = None) -> TestReview:
        p = self.fdir(feature) / "test_review.md"
        text = p.read_text(encoding="utf-8") if p.is_file() else None
        if keep_round is not None and text is not None:
            (self.fdir(feature) / f"test_review_round{keep_round}.md").write_text(
                text, encoding="utf-8"
            )
        return parse_test_review(text)

    def objection_feedback(self, feature: str, review: TestReview, failed: list[str]) -> str:
        out = self.cfg.paths.out
        parts = []
        if review.objections:
            parts.append(
                "The test reviewer objected to your tests (" + f"{out}/{feature}/test_review.md). "
                "Address every objection: fix the test or add the case; if an objection is wrong "
                "per the spec, say why in test_summary.md.\n"
                + "\n".join(f"- {o}" for o in review.objections)
            )
        if failed:
            parts.append(
                f"These mechanical checks failed ({out}/{feature}/test_checks.md): "
                + ", ".join(failed)
            )
        return "\n\n".join(parts)

    def tests_gate(self, feature: str, prog: dict[str, Any]) -> None:
        review = self.read_test_review(feature)
        questions = self.open_questions(feature)
        failed = [n for n, s in (prog.get("test_checks") or {}).items() if s != "PASS"]
        gate_file = self.write_tests_gate(feature, prog, review, questions, failed)
        reasons = []
        if failed:
            reasons.append(f"failed checks: {', '.join(failed)}")
        if not review.clean:
            reasons.append(f"test reviewer: {len(review.objections)} objection(s)")
        if questions:
            reasons.append(f"{len(questions)} open question(s)")
        if self.spec_diff(feature):
            reasons.append("spec changed from your answers (approve the diff)")
        if self.cfg.tests_gate == "defer":
            self.defer_tests_gate(feature, prog, review, failed, reasons)
            return
        if self.cfg.tests_gate == "auto":
            log_event(self.cfg, feature, "tests_gate_auto", passed=not reasons, reasons=reasons)
            if not reasons:
                self.progress(
                    feature,
                    'Tests gate passed without a stop ([gates] tests = "auto"): checks PASS, no '
                    f"objections, no questions (see {self.rel(gate_file)}).",
                )
                self.pass_tests_gate(feature, prog)
                return
            self.progress(
                feature, '[gates] tests = "auto", but the gate needs you: ' + "; ".join(reasons)
            )
        self.pause(
            feature,
            prog,
            {
                "kind": "gate",
                "gate": "tests",
                "files": [self.rel(gate_file)],
                "message": "State: " + ("; ".join(reasons) if reasons else "all clean") + ".",
            },
        )

    def write_tests_gate(
        self,
        feature: str,
        prog: dict[str, Any],
        review: TestReview,
        questions: list[str],
        failed: list[str],
    ) -> pathlib.Path:
        f, out, d = feature, self.cfg.paths.out, self.fdir(feature)
        rnd, cap = int(prog.get("round") or 0), self.cfg.test_review_rounds
        lines = [
            f"# Tests gate: {f}",
            "",
            f'Decide: `agentflow approve {f}` | `agentflow reject {f} --reason "..."` | write '
            f"answers to {out}/{f}/answers.md, then `agentflow answer {f}`. Reject and answer "
            "re-run the test writer and the test reviewer, then this gate again.",
            "",
            f"## Open questions: {len(questions)}",
            "",
        ]
        lines += [f"{i}. " + q.replace("\n", "\n   ") for i, q in enumerate(questions, 1)] or [
            "None."
        ]
        lines += self.spec_change_lines(feature, prog, "Approving this gate approves them")
        state = (
            f"{len(review.objections)} objection(s) left" if review.objections else "no objections"
        )
        lines += [
            "",
            f"## Test reviewer: {review.verdict}, {state}",
            "",
            f"Rounds back to the test writer: {rnd} of at most {cap}. Full text: "
            f"{out}/{f}/test_review.md",
            "",
        ]
        lines += [f"- {o}" for o in review.objections] or ["None."]
        lines += ["", "## Mechanical checks" + (": all PASS" if not failed else ""), ""]
        lines += [f"- {n}: {s}" for n, s in (prog.get("test_checks") or {}).items()]
        lines += ["", f"Details: {out}/{f}/test_checks.md", "", "## Spec ID -> tests", ""]
        lines += ["| ID | Tests |", "|---|---|"]
        try:
            tags = self.adapter.collect_req_tags()
        except Exception as e:  # a broken test file must not hide the gate
            tags = {}
            lines.insert(-2, f"(test scan failed: {type(e).__name__}: {e})")
        for i in sorted(feature_ids(self.cfg, feature)):
            lines.append(f"| {i} | {'<br>'.join(tags.get(i, [])) or '**none**'} |")
        summary = d / "test_summary.md"
        lines += [
            "",
            "## What each test checks (test writer's test_summary.md)",
            "",
            summary.read_text(encoding="utf-8").strip() if summary.is_file() else "(missing)",
        ]
        path = d / "tests_gate.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def defer_tests_gate(
        self,
        feature: str,
        prog: dict[str, Any],
        review: TestReview,
        failed: list[str],
        reasons: list[str],
    ) -> None:
        """[gates] tests = "defer": no stop. Objections the test reviewer still has and failed
        test checks join the open questions, which the final gate shows (numbered, answerable)."""
        items = [
            f"test check failed: {n} (see {self.cfg.paths.out}/{feature}/test_checks.md)"
            for n in failed
        ]
        items += [f"test reviewer objection left: {o}" for o in review.objections]
        if items:
            with open(self.fdir(feature) / "open_questions.md", "a", encoding="utf-8") as fh:
                for item in items:
                    fh.write("- [tests gate] " + item.replace("\n", "\n  ") + "\n")
        log_event(self.cfg, feature, "tests_gate_deferred", reasons=reasons)
        self.progress(
            feature,
            'Tests gate: no stop ([gates] tests = "defer")'
            + (f"; for the final gate: {'; '.join(reasons)}" if reasons else "; all clean")
            + ".",
        )
        self.pass_tests_gate(feature, prog, keep_questions=True)

    def pass_tests_gate(
        self, feature: str, prog: dict[str, Any], keep_questions: bool = False
    ) -> None:
        questions = self.fdir(feature) / "open_questions.md"
        if questions.is_file() and not keep_questions:
            self.archive_questions(
                feature,
                "tests gate",
                questions.read_text(encoding="utf-8"),
                "Approved without an answer.",
            )
        commit(self.cfg, f"{feature}: tests approved")
        self.reapprove_spec(feature, prog, "tests")
        git(self.cfg, "tag", "-f", f"tests-approved/{feature}")
        prog["human_note"] = ""
        prog["round"] = 0
        self.advance(feature, prog, "IMPLEMENT")

    # ---------- IMPLEMENT: per plan step (or batch of small steps), no stops ----------
    def parked_steps(self, prog: dict[str, Any]) -> dict[int, dict[str, Any]]:
        """Plan step number -> its park record (a batch's record under each of its steps)."""
        return {
            n: p for p in prog.get("parked") or [] if p.get("kind") == "step" for n in item_steps(p)
        }

    def done_ids(self, prog: dict[str, Any], steps: list[PlanStep]) -> list[str]:
        return [i for s in steps if s.num in prog["completed_steps"] for i in s.reqs]

    def step_batches(self, steps: list[PlanStep]) -> list[list[PlanStep]]:
        """Consecutive steps share one implementer run while their spec IDs add up to at most
        [agents] step_batch_ids (0 = one run per step): a small plan costs one run instead of
        one per step (each run re-reads the spec and the code)."""
        cap = self.cfg.step_batch_ids
        batches: list[list[PlanStep]] = []
        for step in steps:
            last = batches[-1] if batches else None
            if last and cap and sum(len(s.reqs) for s in last) + len(step.reqs) <= cap:
                last.append(step)
            else:
                batches.append([step])
        return batches

    def parked_context(
        self, feature: str, prog: dict[str, Any], steps: list[PlanStep], batch: list[PlanStep]
    ) -> str:
        """Task lines about parked earlier steps; records declared dependencies on them."""
        parked = self.parked_steps(prog)
        if not parked:
            return ""
        names = ", ".join(dict.fromkeys(p["label"] for n, p in sorted(parked.items())))
        text = (
            f"\n\nParked steps (not implemented; their tests are not part of your check): {names}."
            " Do not implement them."
        )
        for step in batch:
            for dep in step.depends:
                if dep in parked:
                    rec = parked[dep]
                    if step.num not in rec.setdefault("dependents", []):
                        rec["dependents"].append(step.num)
                        save_progress(self.cfg, feature, prog)
                        log_event(
                            self.cfg, feature, "parked_dependency", step=step.num, depends_on=dep
                        )
                    text += (
                        f" Step {step.num} depends on parked step {dep} (plan.md): build what "
                        "you can without it and log what is missing as an assumption."
                    )
        return text

    def phase_implement(self, feature: str, prog: dict[str, Any]) -> None:
        steps = plan_steps(self.cfg, feature)
        if not steps:
            raise WorkflowError(
                f"No '## Step N:' sections found in {self.cfg.paths.specs}/{feature}/plan.md"
            )
        redo_mode = bool(prog.get("redo_mode"))

        def redo_for(nums: list[int]) -> list[dict[str, Any]]:
            return [
                r
                for r in prog.get("redo") or []
                if r.get("kind") == "step" and set(item_steps(r)) & set(nums)
            ]

        pending = [
            s
            for s in steps
            if s.num not in prog["completed_steps"]
            and (redo_for([s.num]) if redo_mode else s.num not in self.parked_steps(prog))
        ]
        for batch in self.step_batches(pending):
            nums = [s.num for s in batch]
            redo = redo_for(nums)
            reqs = [i for s in batch for i in s.reqs]
            one = len(batch) == 1
            label = (
                f"step {nums[0]} - {batch[0].title}"
                if one
                else "steps " + ", ".join(map(str, nums))
            )
            self.progress(
                feature,
                f"[{label}] {'redo' if redo else 'start'} ({len(steps)} steps; {', '.join(reqs)})",
            )
            covered = self.done_ids(prog, steps) + reqs
            check = self.adapter.test_command(covered)
            if one:
                task = self.task_text(
                    "IMPLEMENT",
                    feature,
                    n=str(nums[0]),
                    title=batch[0].title,
                    ids=", ".join(reqs),
                    check=check,
                )
            else:
                task = self.task_text(
                    "IMPLEMENT_STEPS",
                    feature,
                    n=", ".join(map(str, nums)),
                    titles="; ".join(f"{s.num}. {s.title} ({', '.join(s.reqs)})" for s in batch),
                    ids=", ".join(reqs),
                    check=check,
                )
            task += self.parked_context(feature, prog, steps, batch)
            if redo:
                task += REDO_NOTE.format(**self.placeholders(feature))
            item: dict[str, Any] = {"kind": "step", "step": nums[0], "label": label}
            if not one:
                item["steps"] = nums
            ok = self.implement_loop(feature, prog, task, covered, item)
            if ok:
                prog["completed_steps"] += nums
            if redo:
                log_event(self.cfg, feature, "redo_result", label=label, passed=ok)
                prog["redo"] = [r for r in prog["redo"] if r not in redo]
            save_progress(self.cfg, feature, prog)
        clear_reqs(self.cfg)
        if redo_mode:
            findings = [r for r in prog.get("redo") or [] if r.get("kind") == "finding"]
            self.advance(feature, prog, "REVIEW", sub="fix" if findings else "final")
        else:
            self.advance(feature, prog, "REVIEW", sub="agent")

    # ---------- REVIEW: adversarial reviewer -> triage -> fixes -> final gates ----------
    def review_failures(self, feature: str) -> list[str]:
        """Names of the failing review tests (the reviewer's findings); [] = none."""
        rdir = self.cfg.root / self.cfg.paths.review_tests
        if not rdir.exists():
            return []
        junit = self.fdir(feature) / "review_tests.xml"
        run = self.adapter.run_tests(paths=[self.cfg.paths.review_tests], junit=junit)
        if run.status not in ("failed", "error"):
            return []
        names = [c.name for c in run.cases or [] if c.outcome in ("failed", "error")]
        return names or [f"review tests ({run.status}, no per-test report)"]

    def phase_review(self, feature: str, prog: dict[str, Any]) -> None:
        sub = prog.get("sub") or "agent"
        if sub == "agent":
            self.agent("REVIEW", feature, prog, self.task_text("REVIEW", feature))
            self.collect_questions(feature, "REVIEW", "adversarial reviewer")
            findings = self.review_failures(feature)
            if findings:
                log_event(self.cfg, feature, "review_findings", tests=findings)
                prog["findings"] = findings
            sub = self.set_sub(feature, prog, "triage" if findings else "final")
        if sub == "triage":
            sub = self.set_sub(feature, prog, "fix" if self.triage(feature, prog) else "final")
        if sub == "fix":
            self.fix_findings(feature, prog)
            sub = self.set_sub(feature, prog, "final")
        t0 = time.time()
        rep = gate_report(self.adapter, feature, "final")
        timing = "\n".join(f"| {name} | {secs:.0f} |" for name, secs in rep.durations.items())
        (self.fdir(feature) / "final_gates.md").write_text(
            f"~~~\n{rep.report}\n~~~\n\nTime per check (s):\n\n| Check | Seconds |\n|---|---|\n"
            f"{timing}\n",
            encoding="utf-8",
        )
        log_event(
            self.cfg,
            feature,
            "final_gates",
            passed=rep.ok,
            failed=rep.failed,
            skipped=rep.skipped,
            excerpt=rep.excerpt() if not rep.ok else "",
            seconds=round(time.time() - t0),
            durations=rep.durations,
            extra=rep.extra,
        )
        self.progress(
            feature,
            f"[REVIEW] final gates: {'PASSED' if rep.ok else 'FAILED'}"
            + (f" ({', '.join(rep.failed)})" if rep.failed else "")
            + (f"; skipped: {', '.join(rep.skipped)}" if rep.skipped else "")
            + (
                "; project checks (not blocking): "
                + ", ".join(f"{n} {'OK' if c == 0 else f'exit {c}'}" for n, c in rep.extra.items())
                if rep.extra
                else ""
            ),
        )
        self.advance(feature, prog, "PR")

    def triage(self, feature: str, prog: dict[str, Any]) -> bool:
        """The test reviewer decides per finding against the spec: VALID stays (fix loop),
        INVALID is deleted with a reason. Returns True if valid findings remain."""
        findings = list(prog.get("findings") or [])
        tfile = self.fdir(feature) / "triage.md"
        tfile.unlink(missing_ok=True)
        task = self.task_text("TRIAGE", feature, findings="\n".join(f"- {t}" for t in findings))
        self.agent("TRIAGE", feature, prog, task, role="test_review")
        self.collect_questions(feature, "TRIAGE", "test reviewer")
        verdicts = parse_triage(tfile.read_text(encoding="utf-8") if tfile.is_file() else "")
        remaining = self.review_failures(feature)
        records = []
        for t in findings:
            v = triage_verdict(t, verdicts)
            if t in remaining:
                reason = (
                    v[1]
                    if v and v[0] == "VALID"
                    else "marked INVALID, but the test was not removed: kept"
                    if v
                    else "no verdict line in triage.md: kept"
                )
                records.append({"test": t, "verdict": "VALID", "reason": reason})
            else:
                reason = v[1] if v else "test removed without a verdict line"
                verdict = "DISMISSED" if not v or v[0] == "INVALID" else "NO LONGER FAILING"
                records.append({"test": t, "verdict": verdict, "reason": reason})
        records += [
            {"test": t, "verdict": "VALID", "reason": "new failing review test after triage"}
            for t in remaining
            if t not in findings
        ]
        log_event(self.cfg, feature, "triage", findings=records, remaining=remaining)
        valid = sum(1 for r in records if r["verdict"] == "VALID")
        self.progress(
            feature, f"[TRIAGE] {valid} valid finding(s), {len(records) - valid} dismissed."
        )
        commit(self.cfg, f"{feature}: review findings triaged ({valid} valid)")
        prog["valid_findings"] = remaining
        save_progress(self.cfg, feature, prog)
        return bool(remaining)

    def fix_findings(self, feature: str, prog: dict[str, Any]) -> None:
        steps = plan_steps(self.cfg, feature)
        redo = [r for r in prog.get("redo") or [] if r.get("kind") == "finding"]
        tests = list(prog.get("valid_findings") or [])
        for r in redo:
            tests += [t for t in r.get("tests") or [] if t not in tests]
        # with parked steps, their tests are red by design: check only the done steps' IDs
        reqs = self.done_ids(prog, steps) if self.parked_steps(prog) else None
        task = self.task_text("FIX", feature, check=self.adapter.test_command(reqs))
        if redo:
            task += REDO_NOTE.format(**self.placeholders(feature))
        item = {"kind": "finding", "step": None, "label": "review fixes", "tests": tests}
        ok = self.implement_loop(feature, prog, task, reqs, item)
        clear_reqs(self.cfg)
        if ok:
            log_event(self.cfg, feature, "findings_fixed", tests=tests)
        if redo:
            log_event(self.cfg, feature, "redo_result", label="review fixes", passed=ok)
            prog["redo"] = [r for r in prog.get("redo") or [] if r.get("kind") != "finding"]
            save_progress(self.cfg, feature, prog)

    # ---------- PR: final summary -> FINAL gate ----------
    def phase_pr(self, feature: str, prog: dict[str, Any]) -> None:
        d = self.fdir(feature)
        self.write_final_facts(feature, prog)
        (d / "final_summary.md").unlink(missing_ok=True)
        self.agent("PR", feature, prog, self.task_text("PR", feature))
        self.collect_questions(feature, "PR", "summary agent")
        summary = d / "final_summary.md"
        text = (
            summary.read_text(encoding="utf-8").rstrip()
            if summary.is_file()
            else f"# Final summary: {feature}\n\n(The summary agent wrote no final_summary.md; "
            f"see {self.cfg.paths.out}/{feature}/final_facts.md.)"
        )
        table = phase_table(since_spec_approval(read_events(self.cfg, feature)))
        spec = self.spec_change_lines(
            feature, prog, "Approving this gate (or finish --no-retro) approves them"
        )
        if spec[3:]:  # more than the heading: changes or notes
            text += "\n" + "\n".join(spec).replace(
                "## Spec changes from your answers", "## Spec changes from your answers (generated)"
            )
        summary.write_text(
            text + "\n\n## Cost, time, turns per phase (generated by agentflow)\n\n" + table + "\n",
            encoding="utf-8",
        )
        prog["redo_mode"] = False
        self.advance(feature, prog, "FINAL")
        self.final_gate(feature, prog)

    def write_final_facts(self, feature: str, prog: dict[str, Any]) -> None:
        d = self.fdir(feature)
        gates = d / "final_gates.md"
        oq = d / "open_questions.md"
        facts = final_facts(
            feature,
            plan_steps(self.cfg, feature),
            prog,
            read_events(self.cfg, feature),
            self.assumptions(feature),
            gates.read_text(encoding="utf-8") if gates.is_file() else "(no final gate run)",
            oq.read_text(encoding="utf-8") if oq.is_file() else "",
        )
        (d / "final_facts.md").write_text(facts, encoding="utf-8")

    def assumptions(self, feature: str) -> list[Any]:
        p = self.fdir(feature) / "steps.md"
        return parse_assumptions(p.read_text(encoding="utf-8")) if p.is_file() else []

    def report_new_assumptions(self, feature: str, prog: dict[str, Any]) -> None:
        """One progress line per assumption the implementer added to steps.md."""
        found = self.assumptions(feature)
        seen = int(prog.get("assumptions_seen") or 0)
        for a in found[seen:]:
            self.progress(feature, f"assumption [{a.spec_id}]: {a.text}")
        if len(found) != seen:
            prog["assumptions_seen"] = len(found)
            save_progress(self.cfg, feature, prog)

    def final_gate(self, feature: str, prog: dict[str, Any]) -> None:
        last = [e for e in read_events(self.cfg, feature) if e["kind"] == "final_gates"]
        passed = bool(last and last[-1].get("passed"))
        parked = prog.get("parked") or []
        state = f"Final gates {'PASSED' if passed else 'FAILED'}; " + (
            f"{len(parked)} parked: " + ", ".join(p["label"] for p in parked)
            if parked
            else "nothing parked"
        )
        questions = self.open_questions(feature)
        if questions:
            state += f"; {len(questions)} open question(s)"
        if self.spec_diff(feature):
            state += "; spec changed from your answers (diff in final_summary.md)"
        d = self.fdir(feature)
        self.pause(
            feature,
            prog,
            {
                "kind": "gate",
                "gate": "final",
                "files": [self.rel(d / "final_summary.md"), self.rel(d / "final_gates.md")],
                "message": state + ".",
            },
        )

    def phase_final(self, feature: str, prog: dict[str, Any]) -> None:
        self.final_gate(feature, prog)

    # ---------- SPEC_UPDATE: answers -> spec (tests gate, final gate) ----------
    def answer_ref(self, feature: str) -> str:
        n = sum(1 for e in read_events(self.cfg, feature) if e["kind"] == "answer")
        return f"ANS-{n + 1}"

    def spec_files(self, feature: str) -> list[str]:
        """The spec files the SPEC_UPDATE step may change (posix, relative to the root)."""
        base = f"{self.cfg.paths.specs}/{feature}/"
        return [base + n for n in ("requirements.md", "design.md", "plan.md")]

    def spec_diff(self, feature: str, ref: str | None = None) -> str:
        """The feature's spec changes since `ref` (default: the spec-approved tag), incl.
        uncommitted ones; "" if none."""
        try:
            return git(
                self.cfg,
                "diff",
                ref or f"spec-approved/{feature}",
                "--",
                f"{self.cfg.paths.specs}/{feature}/",
            )
        except GitError:
            return ""

    def defined_ids(self, feature: str) -> set[str]:
        """IDs in requirements.md (where they are defined)."""
        p = self.cfg.spec_dir(feature) / "requirements.md"
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
        return set(re.findall(self.cfg.id_pattern, text))

    def restore_spec_paths(self, paths: list[str]) -> None:
        tracked = set(git(self.cfg, "ls-files", "--", *paths).split())
        for rel in paths:
            if rel in tracked:
                git(self.cfg, "restore", "--source=HEAD", "--staged", "--worktree", "--", rel)
            else:
                path = self.cfg.root / rel
                path.unlink(missing_ok=True)
                specs = self.cfg.root / self.cfg.paths.specs
                parent = path.parent
                while parent != specs and specs in parent.parents and not any(parent.iterdir()):
                    parent.rmdir()  # a folder the agent created, now empty
                    parent = parent.parent

    def revert_spec_strays(self, allowed: list[str]) -> list[str]:
        """Undo changes under the specs folder outside `allowed` (other files, other features)
        and deletions of allowed files; returns what was undone."""
        specs = self.cfg.paths.specs + "/"
        changed = set(git(self.cfg, "diff", "--name-only", "HEAD", "--", specs).split())
        changed |= set(
            git(self.cfg, "ls-files", "--others", "--exclude-standard", "--", specs).split()
        )
        stray = sorted(p for p in changed if p not in allowed or not (self.cfg.root / p).is_file())
        if stray:
            self.restore_spec_paths(stray)
        return stray

    def spec_change_lines(self, feature: str, prog: dict[str, Any], approval: str) -> list[str]:
        """Gate-file section: the spec diff since spec-approved (from the answers) + notes."""
        diff = self.spec_diff(feature).strip()
        notes = list(prog.get("spec_update_notes") or [])
        head = "## Spec changes from your answers" + ("" if diff else ": none")
        lines = ["", head, ""]
        if diff:
            lines += [
                f"{approval} (the tag spec-approved/{feature} moves to them). Each change is "
                "marked with its answer reference (ANS-n).",
                "",
            ]
        lines += [f"- {n}" for n in notes]
        if diff:
            if len(diff) > 12000:
                diff = (
                    diff[:12000] + "\n... (cut; full diff: git diff spec-approved/" + feature + ")"
                )
            lines += ["", "~~~diff", diff, "~~~"]
        return lines

    def reapprove_spec(self, feature: str, prog: dict[str, Any], gate: str) -> None:
        """The human approved a gate whose spec diff (from answers) was shown: move
        spec-approved/<f> to it."""
        if not self.spec_diff(feature):
            if prog.get("spec_update_notes"):
                prog["spec_update_notes"] = []
                save_progress(self.cfg, feature, prog)
            return
        changed = sorted(
            git(
                self.cfg,
                "diff",
                "--name-only",
                f"spec-approved/{feature}",
                "--",
                f"{self.cfg.paths.specs}/{feature}/",
            ).split()
        )
        commit(self.cfg, f"{feature}: spec updated from answers")
        git(self.cfg, "tag", "-f", f"spec-approved/{feature}")
        prog["spec_update_notes"] = []
        save_progress(self.cfg, feature, prog)
        log_event(self.cfg, feature, "spec_reapproved", gate=gate, files=changed)
        self.echo(f"Spec changes from the answers approved: spec-approved/{feature} moved.")

    def drop_spec_update_tag(self, feature: str) -> None:
        tag = f"spec-update/{feature}"
        if tag_exists(self.cfg, tag):
            git(self.cfg, "tag", "-d", tag)

    def phase_spec_update(self, feature: str, prog: dict[str, Any]) -> None:
        """One agent run writes the answers into requirements.md / design.md (/ plan.md Covers
        lines). agentflow then undoes edits outside those files, reverts the whole update if an
        existing ID disappeared, commits it (final gate: tags it spec-update/<f>, the one change
        the frame diff allows after the tests were approved) and goes on to the step after."""
        su = dict(prog.get("spec_update") or {})
        ref, gate = str(su.get("ref") or "ANS-?"), str(su.get("gate") or "tests")
        then = su.get("then") or {"phase": "TESTS", "sub": "write"}
        allowed = self.spec_files(feature)
        d = self.fdir(feature)
        (d / "spec_update.md").unlink(missing_ok=True)
        before = self.defined_ids(feature)
        task = self.task_text("SPEC_UPDATE", feature, ref=ref, gate=gate)
        self.agent("SPEC_UPDATE", feature, prog, task, role="spec_update")
        self.collect_questions(feature, "SPEC_UPDATE", "spec updater")
        stray = self.revert_spec_strays(allowed)
        after = self.defined_ids(feature)
        lost = sorted(before - after)
        if lost:
            self.restore_spec_paths(allowed)
        changed = sorted(set(git(self.cfg, "diff", "--name-only", "HEAD", "--", *allowed).split()))
        new_ids = sorted(after - before) if changed else []
        added = "\n".join(
            line
            for line in (git(self.cfg, "diff", "HEAD", "--", *changed) if changed else "").split(
                "\n"
            )
            if line.startswith("+") and not line.startswith("+++")
        )
        marked = not changed or ref in added
        if changed:
            git(self.cfg, "add", "--", *changed)
            git(
                self.cfg,
                "commit",
                "-q",
                "-m",
                f"{feature}: spec updated from answers {ref}",
                "--",
                *changed,
            )
            if gate == "final":
                git(self.cfg, "tag", "-f", f"spec-update/{feature}")
        notes = []
        if changed:
            notes.append(
                f"{ref}: {', '.join(changed)} updated"
                + (f"; new IDs: {', '.join(new_ids)}" if new_ids else "")
            )
        else:
            notes.append(
                f"{ref}: no spec change (see {self.cfg.paths.out}/{feature}/spec_update.md)"
            )
        if lost:
            notes.append(
                f"{ref}: update reverted, it removed or renumbered existing IDs: {', '.join(lost)}"
            )
        if stray:
            notes.append(
                f"{ref}: edits outside the feature's spec files undone: {', '.join(stray)}"
            )
        if not marked:
            notes.append(f"{ref}: the changes are not marked with '{ref}'")
        if gate == "final" and new_ids:
            notes.append(
                f"{ref}: new IDs after the tests were approved have no tests yet (traceability "
                "fails); to add tests, change the spec and run agentflow approve-spec"
            )
        prog["spec_update_notes"] = [*(prog.get("spec_update_notes") or []), *notes]
        log_event(
            self.cfg,
            feature,
            "spec_update",
            gate=gate,
            ref=ref,
            files=changed,
            new_ids=new_ids,
            lost_ids=lost,
            reverted=stray,
            marked=marked,
        )
        self.progress(feature, "[SPEC_UPDATE] " + "; ".join(notes))
        feedback = str(su.get("feedback") or "")
        if changed:
            feedback += (
                f"\n\nThe spec was updated from the answers ({ref}; see `git diff HEAD~1 -- "
                f"{self.cfg.paths.specs}/{feature}/`)."
                + (f" New spec IDs that need tests: {', '.join(new_ids)}." if new_ids else "")
            )
        if not changed and su.get("unchanged"):  # nothing to redo for an unchanged spec
            then = su["unchanged"]
        prog.update(
            phase=then.get("phase"),
            sub=then.get("sub"),
            attempt=0,
            feedback=feedback.strip(),
            spec_update=None,
        )
        save_progress(self.cfg, feature, prog)

    # ---------- RETRO ----------
    def write_retro_input(self, feature: str) -> list[str]:
        """{out}/{f}/retro_input.md; returns the item IDs the proposals must address."""
        d = self.fdir(feature)
        review = d / "review.md"
        notes = review.read_text(encoding="utf-8") if review.is_file() else ""
        ri = build_retro_input(
            feature, read_events(self.cfg, feature), notes, self.assumptions(feature)
        )
        (d / "retro_input.md").write_text(ri.text, encoding="utf-8")
        return ri.ids

    def retro_missing(self, feature: str, ids: list[str]) -> list[str]:
        f = self.fdir(feature) / "retro_proposals.md"
        return missing_ids(f.read_text(encoding="utf-8") if f.is_file() else "", ids)

    def phase_retro(self, feature: str, prog: dict[str, Any]) -> None:
        ids = self.write_retro_input(feature)
        self.agent("RETRO", feature, prog, self.task_text("RETRO", feature))
        missing = self.retro_missing(feature, ids)
        first = list(missing)
        if missing:
            self.progress(
                feature, f"[RETRO] proposals miss {', '.join(missing)}; re-running the retro once."
            )
            prog["feedback"] = (
                f"{self.cfg.paths.out}/{feature}/retro_proposals.md does not address these items "
                f"of {self.cfg.paths.out}/{feature}/retro_input.md: {', '.join(missing)}. Keep "
                "the existing proposals and add each missing ID: in a proposal, or in the "
                "'Not acted on' list with a one-line reason."
            )
            self.agent("RETRO", feature, prog, self.task_text("RETRO", feature))
            missing = self.retro_missing(feature, ids)
        log_event(
            self.cfg,
            feature,
            "retro_coverage",
            items=len(ids),
            missing_first=first,
            missing=missing,
            rerun=bool(first),
        )
        if missing:
            self.progress(
                feature,
                f"WARNING: retro_proposals.md still doesn't address {', '.join(missing)} "
                f"(see {self.cfg.paths.out}/{feature}/retro_input.md).",
            )
        if not prog.get("metrics_written"):  # finish --no-retro wrote it already
            self.write_metrics(feature)
            prog["metrics_written"] = True
        self.progress(
            feature,
            f"\nRetro proposals: {self.cfg.paths.out}/{feature}/retro_proposals.md — "
            "apply the ones you accept in a normal session.",
        )
        self.advance(feature, prog, "DONE")

    def write_metrics(self, feature: str) -> None:
        ev = read_events(self.cfg, feature)
        row = [
            datetime.date.today().isoformat(),
            feature,
            sum(1 for e in ev if e["kind"] == "human_gate" and not e.get("approved")),
            sum(1 for e in ev if e["kind"] == "escalation"),
            sum(1 for e in ev if e["kind"] == "questions"),
            sum(1 for e in ev if e["kind"] == "gate" and not e.get("passed")),
            round(sum((e.get("cost") or 0) for e in ev if e["kind"] == "agent_run"), 2),
            sum(1 for e in ev if e["kind"] == "parked"),
            len(self.assumptions(feature)),
        ]
        mfile = self.cfg.root / self.cfg.paths.metrics / "workflow_metrics.csv"
        mfile.parent.mkdir(parents=True, exist_ok=True)
        if not mfile.exists():
            mfile.write_text(METRICS_HEADER + "\n", encoding="utf-8")
        else:  # an older file: extend the header, old rows keep fewer columns
            lines = mfile.read_text(encoding="utf-8").splitlines()
            if lines and lines[0] != METRICS_HEADER and METRICS_HEADER.startswith(lines[0]):
                mfile.write_text("\n".join([METRICS_HEADER, *lines[1:]]) + "\n", encoding="utf-8")
        with open(mfile, "a", encoding="utf-8") as fh:
            fh.write(",".join(map(str, row)) + "\n")
        commit(self.cfg, f"{feature}: workflow metrics")

    def advance(
        self, feature: str, prog: dict[str, Any], phase: str, sub: str | None = None
    ) -> None:
        prog.update(phase=phase, sub=sub, attempt=0, feedback="", waiting=None)
        save_progress(self.cfg, feature, prog)

    # ---------- commands ----------
    def feature_branch(self, feature: str) -> str | None:
        """Create the feature's branch (`[git] branch_prefix` + feature) from `[git] base` in
        this checkout, or switch to it. Refuses with uncommitted changes to tracked files
        (untracked ones are fine) and when a branch of that name exists that agentflow did not
        create for this feature. None = `[git] branch = false`."""
        if not self.cfg.git_branch:
            return None
        name = self.cfg.branch_prefix + feature
        try:
            current = git(self.cfg, "rev-parse", "--abbrev-ref", "HEAD").strip()
        except GitError as e:
            raise WorkflowError(f"cannot read the current branch (no commit yet?): {e}") from e
        if current == name:
            return name
        exists = bool(git(self.cfg, "branch", "--list", name).strip())
        if exists and not self.is_feature_branch(name, feature):
            raise WorkflowError(
                f"Branch {name} exists but is not agentflow's branch for '{feature}' (no "
                f"{self.cfg.paths.specs}/{feature}/ on it, not created by `agentflow spec`). "
                "Rename or delete that branch, or choose another feature name."
            )
        dirty = git(self.cfg, "status", "--porcelain", "--untracked-files=no").rstrip()
        if dirty:
            raise WorkflowError(
                f"Uncommitted changes to tracked files on {current}; commit or stash them before "
                f"agentflow switches to {name} (untracked files are fine):\n{dirty[:1500]}"
            )
        if exists:
            git(self.cfg, "checkout", "-q", name)
            self.echo(f"Switched to the feature branch {name}.")
            return name
        base = self.cfg.git_base
        try:
            git(self.cfg, "rev-parse", "--verify", "-q", f"{base}^{{commit}}")
        except GitError as e:
            raise WorkflowError(
                f"Base branch '{base}' not found; set [git] base in agentflow.toml "
                "(or [git] branch = false to stay on the current branch)."
            ) from e
        git(self.cfg, "checkout", "-q", "-b", name, base)
        git(self.cfg, "config", f"branch.{name}.agentflowFeature", feature)
        self.echo(f"Created the feature branch {name} from {base}.")
        return name

    def is_feature_branch(self, branch: str, feature: str) -> bool:
        """Created by `agentflow spec <feature>` (git config mark) or holds the feature's spec."""
        try:
            if git(self.cfg, "config", "--get", f"branch.{branch}.agentflowFeature").strip() == (
                feature
            ):
                return True
        except GitError:
            pass
        try:
            git(self.cfg, "cat-file", "-e", f"{branch}:{self.cfg.paths.specs}/{feature}")
            return True
        except GitError:
            return False

    def spec(self, feature: str) -> int:
        lock = procs.read_lock(self.cfg.out_dir)
        if procs.lock_alive(lock):
            raise WorkflowError(
                f"an agentflow run is active (feature {(lock or {}).get('feature')}, pid "
                f"{(lock or {}).get('pid')}); a branch switch or the SPEC phase now would break "
                "it. Reattach with /agentflow:resume, or wait until it stops."
            )
        self.feature_branch(feature)
        if tag_exists(self.cfg, f"spec-approved/{feature}"):
            self.echo(
                f"NOTE: the spec of {feature} is already approved (SPEC phase not set). Continue "
                f"the feature (agentflow status {feature} --brief), or change the spec and run "
                f"agentflow approve-spec {feature} again (restarts from the tests)."
            )
            return EXIT_OK
        target = self.cfg.spec_dir(feature)
        if not target.exists():
            local = self.cfg.root / self.cfg.paths.specs / "_template"
            target.mkdir(parents=True)
            for name in ("requirements.md", "design.md", "plan.md"):
                src = local / name
                text = (
                    src.read_text(encoding="utf-8")
                    if src.is_file()
                    else package_text("templates", "specs", "_template", name)
                )
                (target / name).write_text(text, encoding="utf-8")
        set_phase(self.cfg, "SPEC")
        log_event(self.cfg, feature, "spec_started")
        self.echo(f"SPEC phase set for {feature}.")
        self.echo(
            f"Spec dialogue: /agentflow:feature {feature} in Claude Code (it runs this command "
            f"too); manual end of the phase: agentflow approve-spec {feature}"
        )
        return EXIT_OK

    def approve_spec(self, feature: str) -> int:
        if not self.cfg.spec_dir(feature).is_dir():
            raise WorkflowError(
                f"No spec folder {self.cfg.paths.specs}/{feature}/ (run: agentflow spec {feature})"
            )
        clear_phase(self.cfg)
        commit(self.cfg, f"{feature}: spec approved")
        git(self.cfg, "tag", "-f", f"spec-approved/{feature}")
        self.drop_spec_update_tag(feature)
        save_progress(self.cfg, feature, fresh_progress())
        log_event(self.cfg, feature, "spec_approved")
        self.echo(f"Tagged spec-approved/{feature}. Next: agentflow run {feature}")
        return EXIT_OK

    def status(self, feature: str) -> int:
        prog = load_progress(self.cfg, feature)
        self.echo(json.dumps(prog, indent=2))
        warning = self.stale_warning()
        if warning:
            self.echo("\n" + warning)
        w = prog.get("waiting")
        if w and w.get("kind") == "gate":
            self.echo("\n" + self.describe_waiting(feature, w))
            return EXIT_GATE
        self.echo(f"\nPhase {prog.get('phase')}: not at a gate. Next: agentflow run {feature}")
        return EXIT_OK

    def run(self, feature: str) -> int:
        if not tag_exists(self.cfg, f"spec-approved/{feature}"):
            raise WorkflowError(f"Spec not approved. Run: agentflow approve-spec {feature}")
        prog = load_progress(self.cfg, feature)
        w = prog.get("waiting")
        if w and w.get("kind") == "gate":
            self.echo(self.describe_waiting(feature, w))
            return EXIT_GATE
        if w:  # a pause kind of an older agentflow (questions / escalation): no longer a stop
            self.echo(f"Dropping an old pause ({w.get('kind')}); the run continues.")
            prog["waiting"] = None
            save_progress(self.cfg, feature, prog)
        runners: dict[str, Callable[[str, dict[str, Any]], None]] = {
            "SPEC_UPDATE": self.phase_spec_update,
            "TESTS": self.phase_tests,
            "IMPLEMENT": self.phase_implement,
            "REVIEW": self.phase_review,
            "PR": self.phase_pr,
            "FINAL": self.phase_final,
            "RETRO": self.phase_retro,
        }
        self.take_run_lock(feature)
        try:
            with procs.stop_signals():
                while prog["phase"] != "DONE":
                    if prog["phase"] not in runners:
                        raise WorkflowError(f"unknown phase in progress.json: {prog['phase']}")
                    phase = prog["phase"]
                    self.echo(f"--- {feature}: {phase} ---")
                    self.progress(feature, f"{phase} start")
                    runners[phase](feature, prog)
                    self.progress(feature, f"{phase} done")
                # before the lock goes: `agentflow watch` ends when it sees no lock
                self.progress(feature, "DONE")
                self.notify(f"agentflow: {feature} DONE", "Feature finished; see the chat.")
        except Paused as p:
            return p.code
        except KeyboardInterrupt as e:  # Ctrl+C, Ctrl+Break, SIGTERM (procs.Interrupted)
            self.record_interruption(feature, prog, procs.interrupt_name(e))
            return EXIT_INTERRUPTED
        except Exception as e:
            self.progress(feature, f"ERROR in {prog.get('phase')}: {type(e).__name__}: {e}")
            self.notify(f"agentflow: {feature} stopped with an error", str(e))
            raise
        finally:
            clear_phase(self.cfg)
            procs.remove_lock(self.cfg.out_dir)
        self.echo(f"\nFeature {feature}: DONE.")
        return EXIT_OK

    # ---------- run lock, interruption, stale state ----------
    def take_run_lock(self, feature: str) -> None:
        """One orchestrator per repo. A lock left by a killed run is taken over (and the
        interruption recorded); a live one is an error."""
        lock = procs.read_lock(self.cfg.out_dir)
        if lock is not None and int(lock.get("pid") or 0) != os.getpid():
            if procs.lock_alive(lock):
                raise WorkflowError(
                    f"agentflow run is already active (pid {lock.get('pid')}, feature "
                    f"{lock.get('feature')}, since {lock.get('started')})."
                )
            self.recover_stale(lock, "run")
        procs.write_lock(self.cfg.out_dir, feature)

    def record_interruption(self, feature: str, prog: dict[str, Any], how: str) -> None:
        prog["interrupted"] = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "phase": prog.get("phase"),
            "how": how,
        }
        save_progress(self.cfg, feature, prog)
        log_event(
            self.cfg,
            feature,
            "interrupted",
            phase=prog.get("phase"),
            sub=prog.get("sub"),
            completed_steps=prog.get("completed_steps"),
            how=how,
        )
        self.progress(
            feature,
            f"INTERRUPTED ({how}) in {prog.get('phase')}: agent processes stopped, phase lock "
            f"cleared. Resume with: agentflow run {feature}",
        )

    def recover_stale(self, lock: dict[str, Any] | None, by: str) -> None:
        """Clear .phase/.reqs/lock left by an orchestrator that died without cleanup, and
        record the interruption for its feature."""
        phase = read_phase(self.cfg)
        feature = str((lock or {}).get("feature") or "")
        clear_phase(self.cfg)
        procs.remove_lock(self.cfg.out_dir, force=True)
        if feature:
            prog = load_progress(self.cfg, feature)
            prog["interrupted"] = {
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "phase": prog.get("phase"),
                "how": f"orchestrator gone (pid {(lock or {}).get('pid')}), found by {by}",
            }
            save_progress(self.cfg, feature, prog)
            log_event(
                self.cfg,
                feature,
                "interrupted",
                phase=prog.get("phase"),
                how=prog["interrupted"]["how"],
                stale_phase=phase,
            )
        self.echo(
            f"Cleared stale state of a run that ended without cleanup (pid "
            f"{(lock or {}).get('pid', '?')}, phase lock {phase or 'none'})."
        )

    def stale_warning(self) -> str:
        """A warning when .phase is set but no orchestrator is running ("" otherwise). SPEC is
        set by `agentflow spec` for an interactive session and never stale."""
        phase = read_phase(self.cfg)
        lock = procs.read_lock(self.cfg.out_dir)
        if procs.lock_alive(lock):
            return (
                f"agentflow run is active: pid {(lock or {}).get('pid')}, feature "
                f"{(lock or {}).get('feature')}, phase lock {phase or 'none'}."
            )
        if (phase and phase != "SPEC") or lock is not None:
            return (
                f"WARNING: stale phase lock '{phase or 'none'}'"
                + (f" (left by pid {lock.get('pid')})" if lock else "")
                + ": no agentflow run is active, so the hooks still apply that phase's rules. "
                "Clear it with: agentflow unlock"
            )
        return ""

    def unlock(self, force: bool = False) -> int:
        lock = procs.read_lock(self.cfg.out_dir)
        if procs.lock_alive(lock) and not force:
            raise WorkflowError(
                f"agentflow run is active (pid {(lock or {}).get('pid')}); stop it first "
                "(Ctrl+C) or use --force."
            )
        if read_phase(self.cfg) is None and lock is None and not self.cfg.reqs_path.exists():
            self.echo("Nothing to unlock.")
            return EXIT_OK
        self.recover_stale(lock, "unlock")
        return EXIT_OK

    # ---------- gate commands ----------
    def _require_gate(
        self, feature: str, gates: tuple[str, ...] = ("tests", "final")
    ) -> dict[str, Any]:
        prog = load_progress(self.cfg, feature)
        w = prog.get("waiting")
        if not w or w.get("kind") != "gate" or w.get("gate") not in gates:
            if prog.get("phase") == "DONE":
                now = "already DONE" + (
                    f" (finished without the retro; `agentflow approve {feature}` still runs it)"
                    if prog.get("retro") == "skipped"
                    else ""
                )
            else:
                now = f"waiting at the {w.get('gate')} gate" if w else "not at a gate"
            raise WorkflowError(
                f"{feature} is {now}; this command needs the {' or '.join(gates)} gate"
            )
        return prog

    def approve(self, feature: str) -> int:
        prog = load_progress(self.cfg, feature)
        if prog.get("phase") == "DONE" and prog.get("retro") == "skipped":
            # `finish --no-retro` cannot be taken back, but the retro can still run after it
            log_event(self.cfg, feature, "retro_after_finish")
            prog["retro"] = None
            self.advance(feature, prog, "RETRO")
            self.echo(
                "The feature was finished without the retro; the next run does the retro, then "
                f"DONE again (the metrics row stays as written).\nNext: agentflow run {feature}"
            )
            return EXIT_OK
        prog = self._require_gate(feature)
        gate = prog["waiting"]["gate"]
        log_event(self.cfg, feature, "human_gate", gate=gate, approved=True, reason=None)
        if gate == "tests":
            self.pass_tests_gate(feature, prog)
            self.echo(f"Tests approved, tagged tests-approved/{feature}.")
        else:
            self.reapprove_spec(feature, prog, "final")
            self.advance(feature, prog, "RETRO")
            self.echo("Final gate approved; the next run does the retro, then DONE.")
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def reject(self, feature: str, reason: str) -> int:
        if not reason.strip():
            raise WorkflowError("a rejection needs a reason (--reason)")
        prog = self._require_gate(feature)
        if prog["waiting"]["gate"] != "tests":
            raise WorkflowError(
                "The final gate has no reject. Answer the parked questions (answers.md, then "
                f"agentflow answer {feature}), approve, or finish --no-retro. To change the spec: "
                f"edit it, then agentflow approve-spec {feature}."
            )
        log_event(self.cfg, feature, "human_gate", gate="tests", approved=False, reason=reason)
        prog.update(
            waiting=None,
            sub="write",
            round=0,
            human_note=reason,
            feedback=f"The human rejected the tests. Reason:\n{reason}\n"
            "Revise the tests accordingly.",
        )
        save_progress(self.cfg, feature, prog)
        self.echo("Tests rejected; the test writer and the test reviewer re-run with your reason.")
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def answer(self, feature: str) -> int:
        prog = self._require_gate(feature)
        gate = prog["waiting"]["gate"]
        d = self.fdir(feature)
        out = self.cfg.paths.out
        answers = d / "answers.md"
        text = answers.read_text(encoding="utf-8").strip() if answers.is_file() else ""
        if not text:
            raise WorkflowError(f"Write your answers to {out}/{feature}/answers.md first.")
        oq = d / "open_questions.md"
        open_q = oq.read_text(encoding="utf-8") if oq.is_file() else ""
        ref = self.answer_ref(feature)
        reply = f"### Answer {ref} (answers.md)\n{text}"
        if gate == "tests":
            self.archive_questions(feature, "tests gate", open_q, reply)
            log_event(self.cfg, feature, "answer", gate="tests", ref=ref, answer=text[-2000:])
            prog.update(
                waiting=None,
                phase="SPEC_UPDATE",
                sub=None,
                round=0,
                human_note=f"the human answered questions in {out}/{feature}/answers.md",
                spec_update={
                    "gate": "tests",
                    "ref": ref,
                    "then": {"phase": "TESTS", "sub": "write"},
                    "feedback": f"The human answered the open questions in "
                    f"{out}/{feature}/answers.md (questions: {out}/{feature}/questions_archive.md)"
                    ". Read it and update the tests.",
                },
            )
            save_progress(self.cfg, feature, prog)
            self.echo(
                f"Answers recorded ({ref}); the next run writes them into the spec, then the test "
                "writer and the test reviewer re-run with them."
            )
        else:
            parked = prog.get("parked") or []
            if not parked:
                if not open_q.strip():
                    raise WorkflowError(
                        f"Nothing is parked and no question is open. Approve (agentflow approve "
                        f"{feature}) or finish (agentflow finish {feature} --no-retro)."
                    )
                # only questions (e.g. deferred from the tests gate): the answers go into the
                # spec; if it changed, the reviewer checks the code against it again (findings
                # -> fix loop), else straight back to this gate
                self.archive_questions(feature, "final gate: questions", open_q, reply)
                log_event(
                    self.cfg,
                    feature,
                    "answer",
                    gate="final",
                    parked=[],
                    ref=ref,
                    answer=text[-2000:],
                )
                prog.update(
                    waiting=None,
                    redo_mode=False,
                    attempt=0,
                    phase="SPEC_UPDATE",
                    sub=None,
                    spec_update={
                        "gate": "final",
                        "ref": ref,
                        "then": {"phase": "REVIEW", "sub": "agent"},
                        "unchanged": {"phase": "PR", "sub": None},
                        "feedback": "",
                    },
                )
                save_progress(self.cfg, feature, prog)
                self.echo(
                    f"Answers recorded ({ref}); the next run writes them into the spec; if it "
                    "changes, the reviewer checks the code against it again; then the final gate "
                    "again."
                )
                self.echo(f"Next: agentflow run {feature}")
                return EXIT_OK
            questions = "\n".join(
                f"- [{p['label']}] {p.get('question') or p.get('reason')}" for p in parked
            )
            self.archive_questions(feature, "final gate: parked", questions + "\n" + open_q, reply)
            log_event(
                self.cfg,
                feature,
                "answer",
                gate="final",
                parked=[p["label"] for p in parked],
                ref=ref,
                answer=text[-2000:],
            )
            steps = [p for p in parked if p.get("kind") == "step"]
            then = (
                {"phase": "IMPLEMENT", "sub": None} if steps else {"phase": "REVIEW", "sub": "fix"}
            )
            prog.update(
                waiting=None,
                redo=parked,
                parked=[],
                redo_mode=True,
                attempt=0,
                phase="SPEC_UPDATE",
                sub=None,
                spec_update={"gate": "final", "ref": ref, "then": then, "feedback": ""},
            )
            save_progress(self.cfg, feature, prog)
            self.echo(
                f"Answers recorded ({ref}); the next run writes them into the spec, then redoes "
                f"only the parked items ({', '.join(p['label'] for p in parked)}), then the final "
                "gate again."
            )
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def finish(self, feature: str) -> int:
        """FINAL gate -> DONE without the retro (the metrics row is still written)."""
        prog = self._require_gate(feature, ("final",))
        log_event(self.cfg, feature, "human_gate", gate="final", approved=True, reason=None)
        log_event(self.cfg, feature, "finished", retro=False)
        self.reapprove_spec(feature, prog, "final")
        self.write_metrics(feature)
        prog.update(retro="skipped", metrics_written=True)
        self.advance(feature, prog, "DONE")
        self.echo(
            f"Feature {feature}: DONE (no retro). Changed your mind? `agentflow approve {feature}` "
            "then `agentflow run` still does the retro."
        )
        return EXIT_OK
