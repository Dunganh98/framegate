"""Workflow driver: SPEC -> TESTS -> IMPLEMENT (per plan step) -> REVIEW -> PR -> RETRO -> DONE.

Every agent run is a fresh headless `claude -p` process: no context accumulates between steps.
`run` never blocks on input: it advances until a human gate, open agent questions or an
escalation, records the wait in out/<f>/progress.json ("waiting") and exits with a distinct code.
The human continues with `approve`, `reject`, `answer`, then `run` again. Resumable at any point.
"""

from __future__ import annotations

import datetime
import importlib.resources
import json
import pathlib
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from agentflow.adapters import get_adapter
from agentflow.adapters.base import LanguageAdapter
from agentflow.checks import red_stubs, test_smells, traceability
from agentflow.config import Config
from agentflow.gates import gate_report
from agentflow.retro import build as build_retro_input
from agentflow.retro import missing_ids
from agentflow.specs import plan_steps
from agentflow.state import (
    clear_phase,
    clear_reqs,
    commit,
    fresh_progress,
    git,
    load_progress,
    log_event,
    read_events,
    save_progress,
    set_phase,
    set_reqs,
    tag_exists,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GATE = 10  # waiting for a human gate: approve / reject
EXIT_QUESTIONS = 11  # agent questions: write answers.md, then answer
EXIT_ESCALATION = 12  # step failed max_attempts times: fix/clarify, then answer
EXIT_SPEC_CHANGE = 13  # aborted: edit the spec, then approve-spec

WAIT_CODES = {
    "gate": EXIT_GATE,
    "questions": EXIT_QUESTIONS,
    "escalation": EXIT_ESCALATION,
    "spec_change": EXIT_SPEC_CHANGE,
}

PHASES = ["TESTS", "IMPLEMENT", "REVIEW", "PR", "RETRO", "DONE"]
BRIEF_PHASES = {"TESTS", "IMPLEMENT", "FIX", "REVIEW"}
#: Only the plain command forms are pre-approved (--allowedTools); a `cd`/PATH prefix or an
#: `;`/`&&` chain makes the permission check deny the call (seen in a live run on Windows).
COMMAND_FORM = (
    "- Run the commands above exactly as written, from the repository root (Bash or PowerShell "
    "tool), one command per call: no `cd`, PATH/env prefixes, `;` or `&&` chains. Other forms "
    "are denied."
)

TASKS = {
    "TESTS": "Feature '{f}': write the tests from {specs}/{f}/ and the interface stubs in {src}/. "
    "Write the summary table to {out}/{f}/test_summary.md.",
    "IMPLEMENT": "Feature '{f}': implement plan step {n} ('{title}'). Make the tests for these "
    "IDs pass: {ids}. Tests of earlier steps must stay green. Check with: {check}",
    "FIX": "Feature '{f}': make all currently failing tests pass without editing tests. "
    "Check with: {check}",
    "REVIEW": "Feature '{f}': review the implementation against {specs}/{f}/. "
    "Implementation diff: git diff tests-approved/{f}",
    "PR": "Feature '{f}': write the pull request summary to {out}/{f}/pr.md. Inputs: {specs}/{f}/, "
    "{out}/{f}/test_summary.md, {out}/{f}/steps.md, {out}/{f}/review.md, "
    "{out}/{f}/final_gates.md, {out}/{f}/mutation_survivors.md and {out}/{f}/answers.md "
    "if present.",
    "RETRO": "Feature '{f}': run the retrospective. Start with {out}/{f}/retro_input.md: every "
    "question, failed attempt, escalation, rejection, review finding, final-gate failure and "
    "permission denial, "
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
            r = subprocess.run(
                args,
                cwd=self.cfg.root,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout, stderr = r.stdout or "", r.stderr or ""
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

    # ---------- waiting ----------
    def describe_waiting(self, feature: str, waiting: dict[str, Any]) -> str:
        kind = waiting.get("kind")
        files = "\n".join(f"  review: {f}" for f in waiting.get("files", []))
        f = feature
        if kind == "gate":
            return (
                f"=== GATE: {waiting.get('gate')} ({f}) ===\n{files}\n"
                + (f"{waiting['message']}\n" if waiting.get("message") else "")
                + f"Approve:  agentflow approve {f}\n"
                f'Reject:   agentflow reject {f} --reason "<what is wrong>"\n'
                f"Then:     agentflow run {f}"
            )
        if kind == "questions":
            qfile = self.cfg.feature_out(f) / "questions.md"
            q = qfile.read_text(encoding="utf-8").strip() if qfile.is_file() else ""
            return (
                f"=== AGENT QUESTIONS ({f}) ===\n{q}\n\n"
                f"Write your answers to {self.cfg.paths.out}/{f}/answers.md "
                "(read by all later agents), then:\n"
                f"  agentflow answer {f}            (continue)\n"
                f"  agentflow answer {f} --abort    (the spec itself must change)\n"
                f"Then: agentflow run {f}"
            )
        if kind == "escalation":
            what = (
                "stopped at the turn cap twice"
                if waiting.get("reason") == "turn_cap"
                else f"failed {self.cfg.max_attempts} times"
            )
            return (
                f"=== ESCALATION: {waiting.get('step')} {what} ({f}) ===\n"
                f"{files}\n"
                f"Fix the problem or clarify in {self.cfg.paths.out}/{f}/answers.md, then:\n"
                f"  agentflow answer {f}            (retry with fresh attempts)\n"
                f"  agentflow answer {f} --abort    (the spec itself must change)\n"
                f"Then: agentflow run {f}"
            )
        if kind == "spec_change":
            return (
                f"=== SPEC CHANGE REQUIRED ({f}) ===\n"
                f"Edit {self.cfg.paths.specs}/{f}/ in a normal session, then:\n"
                f"  agentflow approve-spec {f}   (resets progress to TESTS)\n"
                f"  agentflow run {f}"
            )
        return f"waiting: {waiting}"

    def pause(self, feature: str, prog: dict[str, Any], waiting: dict[str, Any]) -> None:
        prog["waiting"] = waiting
        save_progress(self.cfg, feature, prog)
        log_event(
            self.cfg,
            feature,
            "waiting",
            wait=waiting["kind"],
            gate=waiting.get("gate"),
            step=waiting.get("step"),
        )
        self.echo("\n" + self.describe_waiting(feature, waiting))
        raise Paused(WAIT_CODES[waiting["kind"]])

    def check_questions(self, feature: str, prog: dict[str, Any], step: str | None = None) -> None:
        """Agents can't ask interactively in -p mode; they write questions.md and stop.
        Questions never count as a failed attempt; they go to the retro as spec gaps."""
        qfile = self.fdir(feature) / "questions.md"
        if not qfile.is_file() or not qfile.read_text(encoding="utf-8").strip():
            return
        log_event(
            self.cfg,
            feature,
            "questions",
            phase=prog["phase"],
            step=step,
            questions=qfile.read_text(encoding="utf-8"),
        )
        self.pause(
            feature,
            prog,
            {"kind": "questions", "phase": prog["phase"], "step": step, "files": [self.rel(qfile)]},
        )

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
        """Run an agent with the pending feedback, consume it, then pause on questions.
        A run that stops at the turn cap is re-run once with a note, then escalated
        (retry_turn_cap=False: the caller handles it, e.g. the step gate decides)."""
        while True:
            res = self.run_agent(
                phase, feature, task, prog.get("feedback", ""), role=role, step=step
            )
            prog["feedback"] = ""
            save_progress(self.cfg, feature, prog)
            self.check_questions(feature, prog, step)
            if not retry_turn_cap or not hit_turn_cap(res):
                prog["turn_cap_retries"] = 0
                save_progress(self.cfg, feature, prog)
                return res
            self.turn_cap_retry_or_escalate(phase, feature, prog, step)

    def turn_cap_retry_or_escalate(
        self, phase: str, feature: str, prog: dict[str, Any], step: str | None
    ) -> None:
        """After a run stopped at the turn cap: set the retry note, or escalate (Paused) when
        the retry stopped at the cap too."""
        cap = self.cfg.turn_cap(phase)
        label = step or phase
        retries = int(prog.get("turn_cap_retries", 0))
        if retries < TURN_CAP_RETRIES:
            prog["turn_cap_retries"] = retries + 1
            prog["feedback"] = turn_cap_note(cap)
            save_progress(self.cfg, feature, prog)
            self.echo(f"[{label}] re-running once after the turn cap.")
            return
        prog["turn_cap_retries"] = 0
        esc = self.fdir(feature) / "escalation.md"
        esc.write_text(
            f"# {label}: the agent stopped at the turn cap ({cap} turns) "
            f"{retries + 1} times in a row\n\nRaise `[phases.{phase}] max_turns` in "
            "agentflow.toml, split the work, or clarify the task in answers.md. Run logs: "
            f"{self.cfg.paths.out}/runs/*-{feature}-{phase}.json\n",
            encoding="utf-8",
        )
        log_event(
            self.cfg,
            feature,
            "escalation",
            phase=phase,
            step=step,
            attempts=retries + 1,
            failed=[f"turn cap ({cap})"],
        )
        self.pause(
            feature,
            prog,
            {
                "kind": "escalation",
                "reason": "turn_cap",
                "phase": phase,
                "step": label,
                "files": [self.rel(esc)],
            },
        )

    # ---------- implement loop (steps and review fixes) ----------
    def implement_loop(
        self, feature: str, prog: dict[str, Any], label: str, task: str, reqs: list[str] | None
    ) -> None:
        """Run the implementer until the step gates pass; escalate after max_attempts."""
        if reqs:
            set_reqs(self.cfg, reqs)
        else:
            clear_reqs(self.cfg)
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
            except Paused:
                prog["attempt"] = attempt - 1  # questions don't count as an attempt
                save_progress(self.cfg, feature, prog)
                raise
            rep = gate_report(self.adapter, feature, "step")
            ok, out = rep.ok, rep.report
            log_event(
                self.cfg,
                feature,
                "gate",
                phase=prog["phase"],
                step=label,
                attempt=attempt,
                passed=ok,
                failed=rep.failed,
                excerpt=rep.excerpt() if not ok else "",
            )
            if ok:
                commit(self.cfg, f"{feature}: {label}")
                prog["attempt"] = 0
                prog["feedback"] = ""
                save_progress(self.cfg, feature, prog)
                return
            capped = turn_cap_note(self.cfg.turn_cap("IMPLEMENT")) if hit_turn_cap(res) else ""
            prog["feedback"] = (
                (capped + "\n" if capped else "")
                + f"Attempt {attempt} failed these gates. Tests and config are frozen; "
                f"fix {self.cfg.paths.src}/.\n{out[-6000:]}"
            )
            save_progress(self.cfg, feature, prog)
            self.echo(f"[{label}] attempt {attempt} failed the step gates.")
            if attempt >= self.cfg.max_attempts:
                esc = self.fdir(feature) / "escalation.md"
                esc.write_text(
                    f"# {label}: failed {attempt} times\n\nLast gate output:\n\n~~~\n{out}\n~~~\n",
                    encoding="utf-8",
                )
                log_event(
                    self.cfg,
                    feature,
                    "escalation",
                    phase=prog["phase"],
                    step=label,
                    attempts=attempt,
                    failed=rep.failed,
                )
                self.pause(
                    feature,
                    prog,
                    {
                        "kind": "escalation",
                        "phase": prog["phase"],
                        "step": label,
                        "files": [self.rel(esc)],
                    },
                )

    # ---------- phases ----------
    def phase_tests(self, feature: str, prog: dict[str, Any]) -> None:
        self.agent("TESTS", feature, prog, self.task_text("TESTS", feature))
        sections = []
        for name, mod in [
            ("Red against stubs", red_stubs),
            ("Traceability", traceability),
            ("Test smells", test_smells),
        ]:
            res = mod.check(self.adapter, feature)
            sections.append(f"### {name}: {res.status}\n~~~\n{res.output}\n~~~")
        gate_file = self.fdir(feature) / "test_gate.md"
        gate_file.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
        summary = self.fdir(feature) / "test_summary.md"
        self.pause(
            feature,
            prog,
            {"kind": "gate", "gate": "tests", "files": [self.rel(summary), self.rel(gate_file)]},
        )

    def phase_implement(self, feature: str, prog: dict[str, Any]) -> None:
        steps = plan_steps(self.cfg, feature)
        if not steps:
            raise WorkflowError(
                f"No '## Step N:' sections found in {self.cfg.paths.specs}/{feature}/plan.md"
            )
        covered: list[str] = []
        for step in steps:
            covered += step.reqs
            if step.num in prog["completed_steps"]:
                continue
            task = self.task_text(
                "IMPLEMENT",
                feature,
                n=str(step.num),
                title=step.title,
                ids=", ".join(step.reqs),
                check=self.adapter.test_command(covered),
            )
            self.implement_loop(feature, prog, f"step {step.num} - {step.title}", task, covered)
            prog["completed_steps"].append(step.num)
            save_progress(self.cfg, feature, prog)
        clear_reqs(self.cfg)
        self.advance(feature, prog, "REVIEW", sub="agent")

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
            findings = self.review_failures(feature)
            if findings:
                log_event(self.cfg, feature, "review_findings", tests=findings)
                prog["sub"] = "gate"
                review = self.fdir(feature) / "review.md"
                self.pause(
                    feature,
                    prog,
                    {
                        "kind": "gate",
                        "gate": "review",
                        "files": [self.rel(review), self.cfg.paths.review_tests + "/"],
                        "message": "The reviewer produced failing tests (findings). Delete invalid "
                        f"findings from {self.cfg.paths.review_tests}/ before approving; "
                        "approve = the implementer fixes the rest, reject = skip fixes.",
                    },
                )
            sub = "final"
        if sub == "review_check":
            sub = "fix" if self.review_failures(feature) else "final"
            prog["sub"] = sub
            save_progress(self.cfg, feature, prog)
        if sub == "fix":
            self.implement_loop(
                feature,
                prog,
                "review fixes",
                self.task_text("FIX", feature, check=self.adapter.test_command()),
                None,
            )
            sub = "final"
        prog["sub"] = "final"
        save_progress(self.cfg, feature, prog)
        rep = gate_report(self.adapter, feature, "final")
        ok, out = rep.ok, rep.report
        (self.fdir(feature) / "final_gates.md").write_text(f"~~~\n{out}\n~~~\n", encoding="utf-8")
        log_event(
            self.cfg,
            feature,
            "final_gates",
            passed=ok,
            failed=rep.failed,
            skipped=rep.skipped,
            excerpt=rep.excerpt() if not ok else "",
        )
        self.echo(
            f"Final gates: {'PASSED' if ok else 'FAILED'} "
            f"(see {self.cfg.paths.out}/{feature}/final_gates.md)"
        )
        self.advance(feature, prog, "PR")

    def phase_pr(self, feature: str, prog: dict[str, Any]) -> None:
        self.agent("PR", feature, prog, self.task_text("PR", feature))
        self.echo(
            f"\nPR summary: {self.cfg.paths.out}/{feature}/pr.md — "
            "open the PR yourself and review it."
        )
        self.advance(feature, prog, "RETRO")

    def write_retro_input(self, feature: str) -> list[str]:
        """{out}/{f}/retro_input.md; returns the item IDs the proposals must address."""
        d = self.fdir(feature)
        review = d / "review.md"
        notes = review.read_text(encoding="utf-8") if review.is_file() else ""
        ri = build_retro_input(feature, read_events(self.cfg, feature), notes)
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
        ev = read_events(self.cfg, feature)
        row = [
            datetime.date.today().isoformat(),
            feature,
            sum(1 for e in ev if e["kind"] == "human_gate" and not e.get("approved")),
            sum(1 for e in ev if e["kind"] == "escalation"),
            sum(1 for e in ev if e["kind"] == "questions"),
            sum(1 for e in ev if e["kind"] == "gate" and not e.get("passed")),
            round(sum((e.get("cost") or 0) for e in ev if e["kind"] == "agent_run"), 2),
        ]
        mfile = self.cfg.root / self.cfg.paths.metrics / "workflow_metrics.csv"
        mfile.parent.mkdir(parents=True, exist_ok=True)
        if not mfile.exists():
            mfile.write_text(
                "date,feature,human_rejections,escalations,questions,gate_failures,cost_usd\n",
                encoding="utf-8",
            )
        with open(mfile, "a", encoding="utf-8") as fh:
            fh.write(",".join(map(str, row)) + "\n")
        commit(self.cfg, f"{feature}: workflow metrics")
        self.echo(
            f"\nRetro proposals: {self.cfg.paths.out}/{feature}/retro_proposals.md — "
            "apply the ones you accept in a normal session."
        )
        self.advance(feature, prog, "DONE")

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
        if prog.get("waiting"):
            self.echo("\n" + self.describe_waiting(feature, prog["waiting"]))
            return WAIT_CODES.get(prog["waiting"].get("kind"), EXIT_OK)
        return EXIT_OK

    def run(self, feature: str) -> int:
        if not tag_exists(self.cfg, f"spec-approved/{feature}"):
            raise WorkflowError(f"Spec not approved. Run: agentflow approve-spec {feature}")
        prog = load_progress(self.cfg, feature)
        if prog.get("waiting"):
            self.echo(self.describe_waiting(feature, prog["waiting"]))
            return WAIT_CODES.get(prog["waiting"].get("kind"), EXIT_ERROR)
        runners = {
            "TESTS": self.phase_tests,
            "IMPLEMENT": self.phase_implement,
            "REVIEW": self.phase_review,
            "PR": self.phase_pr,
            "RETRO": self.phase_retro,
        }
        try:
            while prog["phase"] != "DONE":
                if prog["phase"] not in runners:
                    raise WorkflowError(f"unknown phase in progress.json: {prog['phase']}")
                self.echo(f"--- {feature}: {prog['phase']} ---")
                runners[prog["phase"]](feature, prog)
        except Paused as p:
            return p.code
        finally:
            clear_phase(self.cfg)
        self.echo(f"\nFeature {feature}: DONE.")
        return EXIT_OK

    def _require_waiting(self, feature: str, kinds: tuple[str, ...]) -> dict[str, Any]:
        prog = load_progress(self.cfg, feature)
        w = prog.get("waiting")
        if not w or w.get("kind") not in kinds:
            now = f"waiting for {w.get('kind')}" if w else "not waiting"
            raise WorkflowError(f"{feature} is {now}; this command needs: {' / '.join(kinds)}")
        return prog

    def approve(self, feature: str) -> int:
        prog = self._require_waiting(feature, ("gate",))
        gate = prog["waiting"].get("gate")
        log_event(self.cfg, feature, "human_gate", gate=gate, approved=True, reason=None)
        if gate == "tests":
            commit(self.cfg, f"{feature}: tests approved")
            git(self.cfg, "tag", "-f", f"tests-approved/{feature}")
            self.advance(feature, prog, "IMPLEMENT")
            self.echo(f"Tests approved, tagged tests-approved/{feature}.")
        elif gate == "review":
            commit(self.cfg, f"{feature}: review findings accepted")
            prog.update(sub="review_check", waiting=None)
            save_progress(self.cfg, feature, prog)
            self.echo("Review findings accepted; the implementer fixes them on the next run.")
        else:
            raise WorkflowError(f"unknown gate {gate}")
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def reject(self, feature: str, reason: str) -> int:
        if not reason.strip():
            raise WorkflowError("a rejection needs a reason (--reason)")
        prog = self._require_waiting(feature, ("gate",))
        gate = prog["waiting"].get("gate")
        log_event(self.cfg, feature, "human_gate", gate=gate, approved=False, reason=reason)
        if gate == "tests":
            prog.update(
                waiting=None,
                sub=None,
                feedback=(
                    f"The human rejected the tests. Reason:\n{reason}\n"
                    "Revise the tests accordingly."
                ),
            )
            self.echo("Tests rejected; the test writer reruns with your reason on the next run.")
        else:
            prog.update(waiting=None, sub="final")
            self.echo(
                "Review findings rejected; no fixes. Final gates run next (failing review "
                "tests are reported there)."
            )
        save_progress(self.cfg, feature, prog)
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def answer(self, feature: str, abort: bool = False) -> int:
        prog = self._require_waiting(feature, ("questions", "escalation"))
        w = prog["waiting"]
        d = self.fdir(feature)
        answers = d / "answers.md"
        if w["kind"] == "questions":
            qfile = d / "questions.md"
            if not abort and not answers.is_file():
                raise WorkflowError(
                    f"Write your answers to {self.cfg.paths.out}/{feature}/answers.md first."
                )
            questions = qfile.read_text(encoding="utf-8") if qfile.is_file() else ""
            answer_text = answers.read_text(encoding="utf-8") if answers.is_file() else ""
            where = " / ".join(str(x) for x in (w.get("phase"), w.get("step")) if x)
            with open(d / "questions_archive.md", "a", encoding="utf-8") as fh:
                fh.write(
                    f"\n---\n<!-- {where} -->\n{questions.rstrip()}\n\n"
                    + (
                        "Aborted: the spec must change.\n"
                        if abort
                        else f"### Answer (answers.md)\n{answer_text.rstrip()}\n"
                    )
                )
            qfile.unlink(missing_ok=True)
            feedback = (
                f"Your earlier questions (archived in "
                f"{self.cfg.paths.out}/{feature}/questions_archive.md) were answered in "
                f"{self.cfg.paths.out}/{feature}/answers.md. Read it and complete the task."
            )
        else:
            feedback = (prog.get("feedback") or "") + (
                "\n\nThe human looked at the escalation"
                + (
                    f"; read {self.cfg.paths.out}/{feature}/answers.md."
                    if answers.is_file()
                    else "."
                )
            )
            prog["attempt"] = 0
        answer_now = answers.read_text(encoding="utf-8") if answers.is_file() else ""
        log_event(
            self.cfg,
            feature,
            "answer",
            waiting=w["kind"],
            phase=w.get("phase"),
            step=w.get("step"),
            aborted=abort,
            answer=answer_now[-2000:],
        )
        if abort:
            prog["waiting"] = {"kind": "spec_change"}
            save_progress(self.cfg, feature, prog)
            self.echo(self.describe_waiting(feature, prog["waiting"]))
            return EXIT_SPEC_CHANGE
        prog.update(waiting=None, feedback=feedback.strip())
        save_progress(self.cfg, feature, prog)
        self.echo(f"Answer recorded. Next: agentflow run {feature}")
        return EXIT_OK
