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

from agentflow import procs
from agentflow.adapters import get_adapter
from agentflow.adapters.base import LanguageAdapter
from agentflow.checks import red_stubs, test_smells, traceability
from agentflow.config import Config
from agentflow.gates import gate_report
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

PHASES = ["TESTS", "IMPLEMENT", "REVIEW", "PR", "FINAL", "RETRO", "DONE"]
METRICS_HEADER = (
    "date,feature,human_rejections,escalations,questions,gate_failures,cost_usd,parked,assumptions"
)
BRIEF_PHASES = {"TESTS", "TEST_REVIEW", "IMPLEMENT", "FIX", "REVIEW", "TRIAGE"}
#: Only the plain command forms are pre-approved (--allowedTools); a `cd`/PATH prefix or an
#: `;`/`&&` chain makes the permission check deny the call (seen in a live run on Windows).
COMMAND_FORM = (
    "- Run the commands above exactly as written, from the repository root (Bash or PowerShell "
    "tool), one command per call: no `cd`, PATH/env prefixes, `;` or `&&` chains. Other forms "
    "are denied."
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


#: re-runs of a non-IMPLEMENT agent that stopped at the turn cap before escalating
TURN_CAP_RETRIES = 1


def hit_turn_cap(res: dict[str, Any]) -> bool:
    """True if a `claude -p` JSON result says the run stopped at --max-turns."""
    return res.get("subtype") == "error_max_turns" or res.get("terminal_reason") == "max_turns"


def turn_cap_note(cap: int) -> str:
    return (
        f"Your previous run stopped at the turn cap ({cap} turns) before it finished. Its "
        "file changes are kept: check what is already done, then finish the rest with fewer, "
        "larger steps (batch reads and edits, no exploratory commands)."
    )


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
            "json",
        ]
        runs = self.cfg.out_dir / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (runs / f"{stamp}-{feature}-{phase}.prompt.md").write_text(prompt, encoding="utf-8")
        t0 = time.time()
        try:
            # own process group + kill-on-close job: Ctrl+C / SIGTERM / a killed agentflow
            # take the whole agent tree (claude, node, shells, dotnet) down with it
            _, stdout, stderr = procs.run(args, input=prompt, cwd=self.cfg.root, job=True)
        except OSError as e:
            stdout, stderr = "", f"could not start claude: {e}"
        (runs / f"{stamp}-{feature}-{phase}.json").write_text(stdout or stderr, encoding="utf-8")
        try:
            res = json.loads(stdout)
            if not isinstance(res, dict):
                raise ValueError
        except ValueError:
            res = {"is_error": True, "result": (stderr or stdout)[-2000:]}
        denials = summarize_denials(res.get("permission_denials"))
        log_event(
            self.cfg,
            feature,
            "agent_run",
            phase=phase,
            seconds=round(time.time() - t0),
            cost=res.get("total_cost_usd"),
            turns=res.get("num_turns"),
            error=res.get("is_error"),
            denials=len(denials),
        )
        if denials:
            log_event(
                self.cfg, feature, "permission_denials", phase=phase, step=step, denials=denials
            )
            self.echo(f"[{phase}] {len(denials)} tool call(s) denied (logged for the retro).")
        if hit_turn_cap(res):
            cap = self.cfg.turn_cap(phase)
            log_event(
                self.cfg,
                feature,
                "max_turns",
                phase=phase,
                step=step,
                cap=cap,
                turns=res.get("num_turns"),
            )
            self.echo(f"[{phase}] agent run stopped at the turn cap ({cap} turns).")
        elif res.get("is_error"):
            self.echo(f"[{phase}] agent run reported an error: {str(res.get('result'))[:500]}")
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
                "   (test writer + test reviewer re-run)\n"
                f"Then:     agentflow run {f}"
            )
        if gate == "final":
            return (
                f"=== FINAL GATE ({f}) ===\n{files}\n{msg}"
                f"Approve:  agentflow approve {f}   (then agentflow run {f}: retro -> DONE)\n"
                f"No retro: agentflow finish {f} --no-retro   (DONE)\n"
                f"Parked:   write {out}/{f}/answers.md, then agentflow answer {f} and "
                f"agentflow run {f}\n"
                "          (redoes only the parked items, then this gate again)"
            )
        return f"=== GATE: {gate} ({f}) ===\n{files}\n{msg}"

    def pause(self, feature: str, prog: dict[str, Any], waiting: dict[str, Any]) -> None:
        prog["waiting"] = waiting
        save_progress(self.cfg, feature, prog)
        log_event(self.cfg, feature, "waiting", wait=waiting["kind"], gate=waiting.get("gate"))
        self.echo("\n" + self.describe_waiting(feature, waiting))
        raise Paused(EXIT_GATE)

    def set_sub(self, feature: str, prog: dict[str, Any], sub: str | None) -> str | None:
        prog["sub"] = sub
        save_progress(self.cfg, feature, prog)
        return sub

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
        self.echo(f"[{phase}] {len(items)} question(s) collected for the next gate.")
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
        cap is re-run once with a note; if that one stops at the cap too, it is logged
        (escalation event, retro E item) and the run continues (retry_turn_cap=False: the caller
        handles it, e.g. the step gate decides)."""
        while True:
            res = self.run_agent(
                phase, feature, task, prog.get("feedback", ""), role=role, step=step
            )
            prog["feedback"] = ""
            save_progress(self.cfg, feature, prog)
            if (
                not retry_turn_cap
                or not hit_turn_cap(res)
                or not self.turn_cap_retry(phase, feature, prog, step)
            ):
                prog["turn_cap_retries"] = 0
                save_progress(self.cfg, feature, prog)
                return res

    def turn_cap_retry(
        self, phase: str, feature: str, prog: dict[str, Any], step: str | None
    ) -> bool:
        """After a run stopped at the turn cap: set the retry note (True), or log that the retry
        stopped at the cap too and go on without a stop (False)."""
        cap = self.cfg.turn_cap(phase)
        label = step or phase
        retries = int(prog.get("turn_cap_retries", 0))
        if retries < TURN_CAP_RETRIES:
            prog["turn_cap_retries"] = retries + 1
            prog["feedback"] = turn_cap_note(cap)
            save_progress(self.cfg, feature, prog)
            self.echo(f"[{label}] re-running once after the turn cap.")
            return True
        log_event(
            self.cfg,
            feature,
            "escalation",
            phase=phase,
            step=step,
            attempts=retries + 1,
            failed=[f"turn cap ({cap})"],
            resolution=f"continued without a stop with the files of the last run (raise "
            f"[phases.{phase}] max_turns if this recurs)",
        )
        self.echo(
            f"[{label}] stopped at the turn cap ({cap}) {retries + 1} times; continuing with what "
            "it wrote (logged for the final summary and the retro)."
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
                attempt=attempt,
                passed=rep.ok,
                failed=rep.failed,
                excerpt=rep.excerpt() if not rep.ok else "",
                seconds=round(time.time() - t0),
            )
            if rep.ok:
                commit(self.cfg, f"{feature}: {label}")
                prog["attempt"] = 0
                prog["feedback"] = ""
                save_progress(self.cfg, feature, prog)
                return True
            capped = turn_cap_note(self.cfg.turn_cap("IMPLEMENT")) if hit_turn_cap(res) else ""
            prog["feedback"] = (
                (capped + "\n" if capped else "")
                + f"Attempt {attempt} failed these gates. Tests and config are frozen; "
                f"fix {self.cfg.paths.src}/.\n{rep.report[-6000:]}"
            )
            save_progress(self.cfg, feature, prog)
            self.echo(f"[{label}] attempt {attempt} failed the step gates.")
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
        self.echo(
            f"[{item['label']}] PARKED: {reason}. The run continues; it is listed at the final "
            "gate" + (f" (its code changes: {diff})." if diff else ".")
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
                    self.echo(
                        f"[TESTS] test reviewer: {review.verdict} "
                        f"({len(review.objections)} objection(s), {len(failed)} failed check(s)); "
                        f"back to the test writer (round {rnd + 1} of "
                        f"{self.cfg.test_review_rounds})."
                    )
                    sub = self.set_sub(feature, prog, "write")
                    continue
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
        if self.cfg.tests_gate == "auto":
            log_event(self.cfg, feature, "tests_gate_auto", passed=not reasons, reasons=reasons)
            if not reasons:
                self.echo(
                    'Tests gate passed without a stop ([gates] tests = "auto"): checks PASS, no '
                    f"objections, no questions (see {self.rel(gate_file)})."
                )
                self.pass_tests_gate(feature, prog)
                return
            self.echo('[gates] tests = "auto", but the gate needs you: ' + "; ".join(reasons))
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

    def pass_tests_gate(self, feature: str, prog: dict[str, Any]) -> None:
        questions = self.fdir(feature) / "open_questions.md"
        if questions.is_file():
            self.archive_questions(
                feature,
                "tests gate",
                questions.read_text(encoding="utf-8"),
                "Approved without an answer.",
            )
        commit(self.cfg, f"{feature}: tests approved")
        git(self.cfg, "tag", "-f", f"tests-approved/{feature}")
        prog["human_note"] = ""
        prog["round"] = 0
        self.advance(feature, prog, "IMPLEMENT")

    # ---------- IMPLEMENT: per plan step, no stops ----------
    def parked_steps(self, prog: dict[str, Any]) -> dict[int, dict[str, Any]]:
        return {p["step"]: p for p in prog.get("parked") or [] if p.get("kind") == "step"}

    def done_ids(self, prog: dict[str, Any], steps: list[PlanStep]) -> list[str]:
        return [i for s in steps if s.num in prog["completed_steps"] for i in s.reqs]

    def parked_context(
        self, feature: str, prog: dict[str, Any], steps: list[PlanStep], step: PlanStep
    ) -> str:
        """Task lines about parked earlier steps; records declared dependencies on them."""
        parked = self.parked_steps(prog)
        if not parked:
            return ""
        names = ", ".join(p["label"] for n, p in sorted(parked.items()))
        text = (
            f"\n\nParked steps (not implemented; their tests are not part of your check): {names}."
            " Do not implement them."
        )
        for dep in step.depends:
            if dep in parked:
                rec = parked[dep]
                if step.num not in rec.setdefault("dependents", []):
                    rec["dependents"].append(step.num)
                    save_progress(self.cfg, feature, prog)
                    log_event(self.cfg, feature, "parked_dependency", step=step.num, depends_on=dep)
                text += (
                    f" This step depends on parked step {dep} (plan.md): build what you can "
                    "without it and log what is missing as an assumption."
                )
        return text

    def phase_implement(self, feature: str, prog: dict[str, Any]) -> None:
        steps = plan_steps(self.cfg, feature)
        if not steps:
            raise WorkflowError(
                f"No '## Step N:' sections found in {self.cfg.paths.specs}/{feature}/plan.md"
            )
        redo_mode = bool(prog.get("redo_mode"))
        for step in steps:
            if step.num in prog["completed_steps"]:
                continue
            redo = [r for r in prog.get("redo") or [] if r.get("step") == step.num]
            if redo_mode and not redo:
                continue
            if not redo_mode and step.num in self.parked_steps(prog):
                continue
            label = f"step {step.num} - {step.title}"
            covered = self.done_ids(prog, steps) + step.reqs
            task = self.task_text(
                "IMPLEMENT",
                feature,
                n=str(step.num),
                title=step.title,
                ids=", ".join(step.reqs),
                check=self.adapter.test_command(covered),
            ) + self.parked_context(feature, prog, steps, step)
            if redo:
                task += REDO_NOTE.format(**self.placeholders(feature))
            ok = self.implement_loop(
                feature, prog, task, covered, {"kind": "step", "step": step.num, "label": label}
            )
            if ok:
                prog["completed_steps"].append(step.num)
            if redo:
                log_event(self.cfg, feature, "redo_result", label=label, passed=ok)
                prog["redo"] = [r for r in prog["redo"] if r.get("step") != step.num]
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
        )
        self.echo(
            f"Final gates: {'PASSED' if rep.ok else 'FAILED'} "
            f"(see {self.cfg.paths.out}/{feature}/final_gates.md)"
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
        self.echo(f"[TRIAGE] {valid} valid finding(s), {len(records) - valid} dismissed.")
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

    def final_gate(self, feature: str, prog: dict[str, Any]) -> None:
        last = [e for e in read_events(self.cfg, feature) if e["kind"] == "final_gates"]
        passed = bool(last and last[-1].get("passed"))
        parked = prog.get("parked") or []
        state = f"Final gates {'PASSED' if passed else 'FAILED'}; " + (
            f"{len(parked)} parked: " + ", ".join(p["label"] for p in parked)
            if parked
            else "nothing parked"
        )
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
            self.echo(f"[RETRO] proposals miss {', '.join(missing)}; re-running the retro once.")
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
            self.echo(
                f"WARNING: retro_proposals.md still doesn't address {', '.join(missing)} "
                f"(see {self.cfg.paths.out}/{feature}/retro_input.md)."
            )
        self.write_metrics(feature)
        self.echo(
            f"\nRetro proposals: {self.cfg.paths.out}/{feature}/retro_proposals.md — "
            "apply the ones you accept in a normal session."
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
    def spec(self, feature: str) -> int:
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
        self.echo(f"SPEC phase set. Now run `claude` and type: /spec {feature}")
        self.echo(f"When you're satisfied: agentflow approve-spec {feature}")
        return EXIT_OK

    def approve_spec(self, feature: str) -> int:
        if not self.cfg.spec_dir(feature).is_dir():
            raise WorkflowError(
                f"No spec folder {self.cfg.paths.specs}/{feature}/ (run: agentflow spec {feature})"
            )
        clear_phase(self.cfg)
        commit(self.cfg, f"{feature}: spec approved")
        git(self.cfg, "tag", "-f", f"spec-approved/{feature}")
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
                    self.echo(f"--- {feature}: {prog['phase']} ---")
                    runners[prog["phase"]](feature, prog)
        except Paused as p:
            return p.code
        except KeyboardInterrupt as e:  # Ctrl+C, Ctrl+Break, SIGTERM (procs.Interrupted)
            self.record_interruption(feature, prog, procs.interrupt_name(e))
            return EXIT_INTERRUPTED
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
        self.echo(
            f"\nInterrupted ({how}) in {prog.get('phase')}: agent processes stopped, phase lock "
            f"cleared. Resume with: agentflow run {feature}"
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
            now = f"waiting at the {w.get('gate')} gate" if w else "not at a gate"
            raise WorkflowError(
                f"{feature} is {now}; this command needs the {' or '.join(gates)} gate"
            )
        return prog

    def approve(self, feature: str) -> int:
        prog = self._require_gate(feature)
        gate = prog["waiting"]["gate"]
        log_event(self.cfg, feature, "human_gate", gate=gate, approved=True, reason=None)
        if gate == "tests":
            self.pass_tests_gate(feature, prog)
            self.echo(f"Tests approved, tagged tests-approved/{feature}.")
        else:
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
        reply = f"### Answer (answers.md)\n{text}"
        if gate == "tests":
            self.archive_questions(feature, "tests gate", open_q, reply)
            log_event(self.cfg, feature, "answer", gate="tests", answer=text[-2000:])
            prog.update(
                waiting=None,
                sub="write",
                round=0,
                human_note=f"the human answered questions in {out}/{feature}/answers.md",
                feedback=f"The human answered the open questions in {out}/{feature}/answers.md "
                f"(questions: {out}/{feature}/questions_archive.md). Read it and update the tests.",
            )
            save_progress(self.cfg, feature, prog)
            self.echo("Answers recorded; the test writer and the test reviewer re-run with them.")
        else:
            parked = prog.get("parked") or []
            if not parked:
                raise WorkflowError(
                    f"Nothing is parked. Approve (agentflow approve {feature}) or finish "
                    f"(agentflow finish {feature} --no-retro)."
                )
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
                answer=text[-2000:],
            )
            steps = [p for p in parked if p.get("kind") == "step"]
            prog.update(waiting=None, redo=parked, parked=[], redo_mode=True, attempt=0)
            if steps:
                prog.update(phase="IMPLEMENT", sub=None)
            else:
                prog.update(phase="REVIEW", sub="fix")
            save_progress(self.cfg, feature, prog)
            self.echo(
                f"Answers recorded; the next run redoes only the parked items "
                f"({', '.join(p['label'] for p in parked)}), then the final gate again."
            )
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def finish(self, feature: str) -> int:
        """FINAL gate -> DONE without the retro (the metrics row is still written)."""
        prog = self._require_gate(feature, ("final",))
        log_event(self.cfg, feature, "human_gate", gate="final", approved=True, reason=None)
        log_event(self.cfg, feature, "finished", retro=False)
        self.write_metrics(feature)
        self.advance(feature, prog, "DONE")
        self.echo(f"Feature {feature}: DONE (no retro).")
        return EXIT_OK
