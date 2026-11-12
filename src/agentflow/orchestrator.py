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

from agentflow import backlog, notify, procs, risk
from agentflow import brief as briefs
from agentflow.adapters import get_adapter
from agentflow.adapters.base import GATE_NAMES, CheckResult, LanguageAdapter
from agentflow.checks import (
    baseline,
    existing_members,
    scope_coverage,
    spec_lint,
    test_smells,
    traceability,
)
from agentflow.checks import structure as structure_check
from agentflow.checks.frame_diff import knowledge_tag, path_matches, tests_update_tag
from agentflow.config import Config
from agentflow.gates import GateReport, expected_red, gate_report, split_failures
from agentflow.hooks import AGENT_ENV
from agentflow.report import (
    TestReview,
    final_facts,
    parse_assumptions,
    parse_causes,
    parse_test_review,
    parse_triage,
    phase_table,
    section_bullets,
    short_name,
    since_spec_approval,
    triage_verdict,
)
from agentflow.retro import (
    add_knowledge,
    knowledge_items,
    missing_ids,
    parse_answer_items,
    same_question,
    split_questions,
)
from agentflow.retro import build as build_retro_input
from agentflow.specs import (
    MODES,
    PlanStep,
    feature_ids,
    feature_mode,
    plan_steps,
    spec_mode_line,
    template_dir,
)
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
    set_contested,
    set_phase,
    set_reqs,
    tag_exists,
)
from agentflow.usage_limit import UsageLimit, detect_usage_limit

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GATE = 10  # waiting at a human gate (tests or final): approve / reject / answer / finish
EXIT_INTERRUPTED = 130  # Ctrl+C / SIGTERM: agents stopped, state cleared; run again
PROGRESS_LOG = "progress.log"
#: byte offset of progress.log that `agentflow watch` has printed (a re-armed watch goes on there)
PROGRESS_CURSOR = "progress.cursor"
PROGRESS_WIDTH = 160

#: who asks the generated file-size questions at the final gate (open_questions.md tag)
STRUCTURE_WHO = "structure report"
#: who asks whether to run the final gates again after a tool failure (always asked)
GATES_WHO = "final gates"
PHASES = ["SPEC_UPDATE", "TESTS", "IMPLEMENT", "REVIEW", "PR", "FINAL", "RETRO", "DONE"]
METRICS_HEADER = (
    "date,feature,human_rejections,escalations,questions,gate_failures,cost_usd,parked,assumptions"
)
#: appended to a task of a bug fix / a refactor (mode -> task kind -> text)
MODE_NOTES: dict[str, dict[str, str]] = {
    "bugfix": {
        "TEST_REVIEW": "This is a bug fix: the tests ran against today's code (test_checks.md, "
        "'Bug reproduced on today's code', lists why each reproduction test fails). Check for "
        "every reproduction test that this failure is the bug the report describes (its "
        "'Observed now' value), not another failure: a test that fails for another reason, or "
        "would also fail after a correct fix, is an objection.",
        "IMPLEMENT": "This is a bug fix: find the cause before you change code. Your step note "
        "has one line `- ROOT CAUSE: <what, where (file: method)>` and one line `- RULED OUT: "
        "<suspected cause>: <evidence>` for every other suspected cause of the bug report. "
        "Make the smallest fix; no cleanup or refactor beside it.",
        "REVIEW": "This is a bug fix: check that the cause is fixed, not only the reported "
        "input: the same bug through another input or another caller, and the neighbouring "
        "cases that were right before.",
    },
    "refactor": {
        "TEST_REVIEW": "This is a refactor: every test must pass on today's code "
        "(test_checks.md, 'Green on today's code'). Check that the tests pin the behavior of "
        "the code in the spec's Scope table (branches, errors, edge inputs, order, stored "
        "formats) through the interface callers use, and that expected values were measured "
        "from today's code, not guessed. Behavior in scope that no test pins is an objection. "
        "Existing tests come first (test_checks.md lists the tagged ones): for each, check "
        "that it asserts its row exactly, and that the diff of its file adds tag lines "
        "only. A new test that repeats what a tagged or untagged existing test already "
        "asserts is an objection too: name the existing test. test_checks.md, 'Scope "
        "coverage', lists the lines of the files in scope that no tagged test runs: read "
        "each range in the code; behavior there that the refactor moves or merges and no "
        "test pins is an objection (name the lines and what to test). A range that "
        "cannot matter (unreachable, outside what the steps touch): say so in one line.",
        "IMPLEMENT": "This is a refactor: move, split and rename as the step says; the behavior "
        "must not change. All characterization tests pass before and after (your check "
        "command runs all of them). A test that fails means your change altered behavior: "
        "undo that part; never adapt code to make a changed result pass. Public signatures "
        "stay as design.md says. A step that changes no source file fails.",
        "REVIEW": "This is a refactor: the behavior must be the same as before. A finding is a "
        "difference: an input for which the code now returns, raises or stores something else "
        "than the code before (read it with `git show tests-approved/{f}:<path>`). Write it as "
        "a failing test and say in review.md what the old code did. Also check the spec's "
        "structure targets.",
    },
}
MODE_NOTES["bugfix"]["IMPLEMENT_STEPS"] = MODE_NOTES["bugfix"]["IMPLEMENT"]
MODE_NOTES["refactor"]["IMPLEMENT_STEPS"] = MODE_NOTES["refactor"]["IMPLEMENT"]

BRIEF_PHASES = {
    "TESTS",
    "TESTS_AMEND",
    "TEST_REVIEW",
    "IMPLEMENT",
    "IMPLEMENT_STEPS",
    "FIX",
    "FIX_FINAL",
    "TESTS_STRENGTHEN",
    "STRUCTURE",
    "DIAGNOSIS",
    "REVIEW",
    "TRIAGE",
}
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
#: roles that get the project knowledge file in their instructions (the PR summary does not
#: need it); the spec dialogue gets it through `agentflow spec-brief`
KNOWLEDGE_ROLES = {
    "tests",
    "test_review",
    "implement",
    "review",
    "spec_update",
    "diagnosis",
    "retro",
}
KNOWLEDGE_MAX_LINES = 300
#: failed final checks one automatic repair may fix before the human sees them (by the start
#: of the check's name): the implementer owns the first group, the test writer the second
FINAL_SRC_CHECKS = (*GATE_NAMES.values(), "regression", "coverage run", "full test suite")
FINAL_TESTS_CHECKS = ("diff coverage", "mutation score")
FIX_OWNER_RE = re.compile(r"^\W*Fix owner\W*:\W*(src|tests|spec)\b", re.I | re.M)
#: a park request (or diagnosis) saying only the frozen tests are wrong, and which ones
TESTS_WRONG_RE = re.compile(r"^\W*Park kind\W*:\W*tests[ -]wrong\b", re.I | re.M)
CONTESTED_RE = re.compile(r"^\W*Contested tests\W*:\s*(.+)$", re.I | re.M)


def contested_tests(text: str, tests_owner: bool = False) -> list[str]:
    """The tests a park request (`Park kind: tests-wrong`) or a diagnosis with `Fix owner:
    tests` contests (`Contested tests: A, B`); [] if it is not such a park or names none."""
    if not (tests_owner or TESTS_WRONG_RE.search(text)):
        return []
    m = CONTESTED_RE.search(text)
    if not m:
        return []
    names = [n.strip().strip("`*").strip() for n in re.split(r"[,;]", m.group(1))]
    return [n for n in names if n and n.lower() != "none"]


def provisional_item(prog: dict[str, Any]) -> str:
    """The one numbered final-gate item for all provisional steps ("" if none): one answer
    approves every proposed test fix."""
    recs = prog.get("provisional") or []
    if not recs:
        return ""
    parts = [
        f"[{r['label']}] contested: {', '.join(r.get('contested') or [])}. "
        + str(r.get("question") or "").strip()
        for r in recs
    ]
    return (
        "Proposed test fixes (the code of these steps is kept as provisional; approve all with "
        "one answer, or say which fix is wrong: then its code is reverted and the step redone "
        "against the tests as they are): " + " | ".join(parts)
    )


#: first lines of every task: most denied calls in live runs were chained `ls; cat; git log`
#: calls just to look around
LOOK_AROUND = (
    "Look around with the Read, Glob and Grep tools (never `ls`, `cat`, `head`, `grep` or "
    "`git show` in a shell). The shell is only for the commands your task and the conventions "
    "name, one plain command per call. Write files with the Write / Edit tools, never with shell "
    "redirection (`>`, `>>`, heredocs)."
)
#: names left out of the orientation's file lists
ORIENTATION_SKIP = {"prompts", "progress.log", "progress.cursor", "progress.json", "parked"}
NO_STOP = (
    "Nobody answers questions during the run. Where the spec is ambiguous, assume what fits the "
    "spec best and log it in your step note (`- ASSUMPTION [<spec ID>]: ...`). Only if no "
    "reasonable assumption is possible, write the question to {out}/{f}/park.md and stop: the "
    "work is parked and the run continues.\n"
    "Step note: write it with the Write tool to {note} (agentflow appends it to "
    "{out}/{f}/steps.md; do not edit steps.md yourself)."
)
REDO_NOTE = (
    "\n\nThis is a redo of parked work: the human answered the parked question(s) in "
    "{out}/{f}/answers.md (earlier questions: {out}/{f}/questions_archive.md). Read both first. "
    "An answer that approves a diagnosis' proposed fix means: apply that proposal as written."
)

#: the failed "check" of a refactor step that left the source as it was
IDLE_STEP = "refactor step changed nothing"

TASKS = {
    # a bug fix / a refactor: the code exists, so the first proof is not "red against stubs"
    "TESTS:bugfix": "Bug fix '{f}': the code exists, there are no stubs. Write tests from the bug "
    "report in {specs}/{f}/ against today's code. For every reproduction row (R and E IDs): a "
    "test that FAILS now because of the bug and asserts the expected (right) result, so it "
    "passes once the bug is fixed. For every must-not-change row (I IDs): a test that PASSES "
    "now. Where your role says the tests must fail against the stubs, this rule replaces it. "
    "Run the tests: a reproduction test must fail at its assertion, with the wrong result the "
    "report names, not with an error and not for another reason. Do not fix the bug. Write the "
    "summary table to {out}/{f}/test_summary.md, with one line per reproduction test saying "
    "how it fails today. A question the report does not settle goes to {out}/{f}/questions.md; "
    "then continue with the rest (do not stop).",
    "TESTS:refactor": "Refactor '{f}': the code exists, there are no stubs, and its behavior must "
    "not change. Every behavior and invariant row of {specs}/{f}/ needs tests that PASS on "
    "today's code. Existing tests first: search {tests}/ for the tests that already "
    "exercise the code in the spec's Scope table (its types, methods, the golden / hash "
    "tests that run it). A row that an existing test already asserts exactly gets that "
    "test: add the row's ID to its tags, a tag line only, and change nothing else in an "
    "existing test. Write a new characterization test only for what no existing test "
    "asserts: a row, a branch, an error, an edge input. Do not write again what is "
    "already pinned. Where your role says the tests "
    "must fail against the stubs, this rule replaces it: every test passes now. Test through "
    "the interface callers use, so the tests survive the moves and renames design.md lists. "
    "Expected values are what today's code returns (run it; say in the test how each value "
    "was measured). Cover the code in the spec's Scope table, its branches, errors and edge "
    "inputs: after the refactor the tagged tests, old and new, are the only proof that "
    "nothing changed, and the only tests the step checks and the mutation check run. Write "
    "the summary table to {out}/{f}/test_summary.md; it says per row whether an existing "
    "test (which) or a new one pins it. A question the spec does not settle goes to "
    "{out}/{f}/questions.md; then continue with the rest (do not stop).",
    "TESTS": "Feature '{f}': write the tests from {specs}/{f}/ and the interface stubs in {src}/. "
    "Write the summary table to {out}/{f}/test_summary.md. A question the spec does not settle "
    "goes to {out}/{f}/questions.md; then continue with the rest (do not stop).",
    "TESTS_AMEND": "Feature '{f}': the human answered questions at the final gate (answer "
    "reference {ref}; answers: {out}/{f}/answers.md, the questions: the last section of "
    "{out}/{f}/questions_archive.md); the spec was updated from them where needed. The "
    "implementation exists and the tests are frozen for everyone but you. Change the tests only "
    "where an answer requires it: fixtures or seeds, expected values, tests for spec IDs that "
    "have none yet, a review test in {review_tests}/ that an answer names, a diagnosis' proposed "
    "test fix that an answer approves (the question shows the proposal), the proposed fixes of a "
    "provisional step's contested tests when the answer approves them (all of them, unless it "
    "names the ones that are wrong). Change nothing else; "
    "if no answer needs a test change, change nothing. Then append to {out}/{f}/test_summary.md "
    "a section '## Changes from {ref}' (what changed, or 'No test change: <why>'). A question the "
    "answers do not settle goes to {out}/{f}/questions.md.",
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
    "DIAGNOSIS": "Feature '{f}': '{label}' failed the same checks ({failed}) in {n} implementer "
    "attempts in a row. Find the root cause; do not fix anything. Write {out}/{f}/diagnosis.md "
    "(Root cause / Fix owner: src | tests | spec / Proposed fix / Evidence). The implementer's "
    "check command: {check}\nThe last gate output:\n{report}",
    "FIX": "Feature '{f}': make all currently failing tests pass without editing tests. "
    "Check with: {check}\n" + NO_STOP,
    "FIX_FINAL": "Feature '{f}': the final gates failed these checks: {failed}. Make them pass "
    "without editing tests: fix the code. A failing test that existed before this feature means "
    "the feature broke behavior that test pins: restore it, unless the spec asks for the new "
    "behavior (then park with that question). Check with: {check}\n" + NO_STOP,
    "TESTS_STRENGTHEN": "Feature '{f}': the implementation passes its tests, but the final gates "
    "found the tests too weak: {failed}. Add or sharpen tests so they catch what is listed "
    "below (uncovered changed lines; surviving mutants in {out}/{f}/mutation_survivors.md). "
    "Every new assertion must follow from {specs}/{f}/ and carry its spec ID; do not derive "
    "expected values from the implementation. If such a test fails against the code, keep it: "
    "it is a finding and the implementer fixes the code. A line or mutant the spec does not "
    "cover (logging, an equivalent mutant, unreachable defensive code): write no test for it "
    "and list it with the reason under '## Not covered on purpose' in "
    "{out}/{f}/test_summary.md. Change no existing test beyond that. A question the spec does "
    "not settle goes to {out}/{f}/questions.md.",
    "REVIEW": "Feature '{f}': review the implementation against {specs}/{f}/. "
    "Implementation diff: git diff tests-approved/{f}",
    "TRIAGE": "Feature '{f}': triage the adversarial reviewer's findings against {specs}/{f}/. "
    "Failing review tests:\n{findings}\nReviewer notes: {out}/{f}/review.md. Delete the tests of "
    "INVALID findings from {review_tests}/ and write one line per finding to {out}/{f}/triage.md.",
    "PR": "Feature '{f}': write the final summary ({out}/{f}/final_summary.md, the detailed "
    "file) and the final brief ({out}/{f}/final_brief.md, what the human reads at the final "
    "gate), and update the code map {code_map} for what this feature changed. Start with "
    "{out}/{f}/final_facts.md (generated facts, with the numbered gate items). Other inputs: "
    "{out}/{f}/spec_brief.md (what the human approved at the spec gate), {specs}/{f}/, "
    "{out}/{f}/test_summary.md, {out}/{f}/steps.md, {out}/{f}/review.md, {out}/{f}/triage.md, "
    "{out}/{f}/final_gates.md, {out}/{f}/mutation_survivors.md and {out}/{f}/answers.md if "
    "present.",
    "STRUCTURE": "Feature '{f}': restructure the code without changing its behavior, as these "
    "structure findings say:\n{findings}\nMove code, split files and rename private parts as "
    "needed; keep the public signatures the tests use. Every test must stay green; do not edit "
    "tests. A finding that says to leave the code as it is: change nothing for it. Check with: "
    "{check}\n" + NO_STOP,
    "SPEC_UPDATE": "Feature '{f}': write the human's answers at the {gate} gate (answer "
    "reference {ref}) into the spec in {specs}/{f}/. Answers: {out}/{f}/answers.md; the questions "
    "they answer: the last section of {out}/{f}/questions_archive.md. Mark every change with "
    "'(answer {ref})'. Never renumber, rename or delete an existing ID; a new behavior or edge "
    "case gets a new ID. List the changes in {out}/{f}/spec_update.md.",
    "RETRO": "Feature '{f}': run the retrospective. Start with {out}/{f}/retro_input.md: every "
    "question, failed attempt, escalation, rejection, review finding, structure finding, "
    "final-gate failure, "
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
#: usage limit: wait when claude names no reset time, the margin after a reset, and how often
#: one agent run may wait before the run stops (a limit that does not lift as announced)
LIMIT_DEFAULT_WAIT_S = 15 * 60
LIMIT_MARGIN_S = 60
LIMIT_MAX_WAITS = 4


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def sleep_until(until: datetime.datetime) -> None:
    """Sleep until `until` (aware), in short slices so Ctrl+C / SIGTERM stop it at once."""
    while True:
        left = (until - now_utc()).total_seconds()
        if left <= 0:
            return
        time.sleep(min(left, 30.0))


#: `usage` fields of a `claude -p` result -> the agent_run event's token columns
TOKEN_FIELDS = {
    "input_tokens": "tokens_in",
    "output_tokens": "tokens_out",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
}


def token_usage(res: dict[str, Any]) -> dict[str, int]:
    """Token counts of a run for its agent_run event ({} when the result has no `usage`)."""
    usage = res.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        name: int(usage[key])
        for key, name in TOKEN_FIELDS.items()
        if isinstance(usage.get(key), (int, float))
    }


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


def metric_counts(events: list[dict[str, Any]]) -> list[int | float]:
    """The metrics row's human_rejections, escalations, questions, gate_failures, cost_usd and
    parked columns from a feature's events. questions: every single question the agents asked
    (a questions event holds a batch) plus every park or provisional step that asks the human;
    gate_failures: failed step-gate attempts plus failed final-gate runs."""
    asked = sum(
        len(split_questions(str(e.get("questions", ""))))
        for e in events
        if e["kind"] == "questions"
    )
    asked += sum(
        1
        for e in events
        if e["kind"] in ("parked", "provisional") and str(e.get("question") or "").strip()
    )
    return [
        sum(1 for e in events if e["kind"] == "human_gate" and not e.get("approved")),
        sum(1 for e in events if e["kind"] == "escalation"),
        asked,
        sum(1 for e in events if e["kind"] in ("gate", "final_gates") and not e.get("passed")),
        round(sum((e.get("cost") or 0) for e in events if e["kind"] == "agent_run"), 2),
        sum(1 for e in events if e["kind"] == "parked"),
    ]


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
            # several source folders read "core/, autoload" (the texts add the last slash)
            "src": "/, ".join(self.cfg.src_roots),
            "tests": p.tests,
            "review_tests": p.review_tests,
            "out": p.out,
            "knowledge": self.cfg.knowledge,
            "code_map": self.cfg.code_map,
            "max_file_lines": str(self.cfg.max_file_lines),
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
        knowledge = self.knowledge() if role in KNOWLEDGE_ROLES else ""
        if knowledge:
            text = (
                text.rstrip()
                + f"\n\n# Project knowledge ({self.cfg.knowledge}, kept by the human: facts about"
                " this project that the spec does not repeat; follow them)\n\n" + knowledge
            )
        return self.render(text, feature)

    def knowledge(self) -> str:
        """The project knowledge file ("" if there is none), at most KNOWLEDGE_MAX_LINES."""
        path = self.cfg.root / self.cfg.knowledge
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) > KNOWLEDGE_MAX_LINES:
            lines = [
                *lines[:KNOWLEDGE_MAX_LINES],
                f"... (cut at {KNOWLEDGE_MAX_LINES} lines; the rest: {self.cfg.knowledge})",
            ]
        return "\n".join(lines)

    def task_text(self, kind: str, feature: str, **extra: str) -> str:
        mode = feature_mode(self.cfg, feature)
        values = {**self.placeholders(feature), **extra}
        task = TASKS.get(f"{kind}:{mode}", TASKS[kind]).format(**values)
        note = MODE_NOTES.get(mode, {}).get(kind)
        if note:
            task += "\n\n" + note.format(**values)
        task += "\n\n" + LOOK_AROUND + "\n\n" + self.orientation(feature)
        if kind in BRIEF_PHASES:
            task += "\n\n## Conventions\n" + self.adapter.conventions_brief().strip()
            task += "\n" + COMMAND_FORM
        return task

    def orientation(self, feature: str) -> str:
        """What agents otherwise look up with chained `ls` / `git log --stat` calls at the start
        of a run (denied in a run): the feature's spec and run files and the last commits."""

        def files(folder: pathlib.Path) -> str:
            if not folder.is_dir():
                return "(none)"
            names = sorted(
                p.relative_to(folder).as_posix() + ("/" if p.is_dir() else "")
                for p in folder.iterdir()
                if p.name not in ORIENTATION_SKIP
            )
            return ", ".join(names) or "(empty)"

        try:
            log = git(self.cfg, "log", "-3", "--stat", "--format=%h %s").strip().splitlines()
        except GitError:
            log = []
        if len(log) > 40:
            log = [*log[:40], "..."]
        p = self.cfg.paths
        return "\n".join(
            [
                "## Orientation (gathered by agentflow just now; no need to look it up again)",
                f"- {p.specs}/{feature}/: {files(self.cfg.spec_dir(feature))}",
                f"- {p.out}/{feature}/: {files(self.cfg.feature_out(feature))}",
                "- Last commits (git log -3 --stat):",
                *(f"    {line}" for line in log or ["(none)"]),
            ]
        )

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
        attempt: int | None = None,
    ) -> dict[str, Any]:
        """One fresh headless Claude Code run. The task goes via stdin and the role via
        --append-system-prompt-file (no multi-line text on the command line). A run refused by
        the usage limit waits for the reset and runs again (never a failed attempt)."""
        waits = 0
        while True:
            res, limit = self.run_agent_once(phase, feature, task, feedback, role, step, attempt)
            if limit is None:
                return res
            waits += 1
            self.wait_for_usage_limit(feature, phase, step, limit, waits)

    def wait_for_usage_limit(
        self, feature: str, phase: str, step: str | None, limit: UsageLimit, waits: int
    ) -> None:
        """Sleep until the limit lifts (plus a margin). A reset further away than
        [agents] limit_wait_max_hours, or a limit that is still there after LIMIT_MAX_WAITS
        waits, stops the run like Ctrl+C: the state is kept, `agentflow run` resumes it."""
        now = now_utc()
        reset = limit.reset_at if limit.reset_at and limit.reset_at > now else None
        until = (reset or now + datetime.timedelta(seconds=LIMIT_DEFAULT_WAIT_S)) + (
            datetime.timedelta(seconds=LIMIT_MARGIN_S)
        )
        seconds = (until - now).total_seconds()
        local = until.astimezone().strftime("%a %H:%M")
        label = step or phase
        log_event(
            self.cfg,
            feature,
            "usage_limit",
            phase=phase,
            step=step,
            message=limit.message,
            reset=limit.reset_at.isoformat() if limit.reset_at else None,
            wait_s=round(seconds),
            waits=waits,
        )
        max_s = self.cfg.limit_wait_max_hours * 3600
        if seconds > max_s or waits > LIMIT_MAX_WAITS:
            why = "still there after waiting" if waits > LIMIT_MAX_WAITS else "too far away"
            self.notify(
                f"agentflow: {feature} stopped at the usage limit",
                f"Resets {local}. Then: agentflow run {feature}",
            )
            raise procs.Interrupted(f"usage limit, resets {local} ({why})")
        self.progress(
            feature,
            f"[{label}] usage limit reached ({limit.message[:80]}); waiting until {local}, then "
            "the same run again (not a failed attempt).",
        )
        self.notify(
            f"agentflow: {feature} waits for the usage limit", f"Continues by itself at {local}."
        )
        sleep_until(until)
        self.progress(feature, f"[{label}] usage limit wait over; running again.")

    def run_agent_once(
        self,
        phase: str,
        feature: str,
        task: str,
        feedback: str = "",
        role: str | None = None,
        step: str | None = None,
        attempt: int | None = None,
    ) -> tuple[dict[str, Any], UsageLimit | None]:
        """One `claude -p` run: its result, and the usage limit that refused it (or None)."""
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
        limit = detect_usage_limit(stdout, res)
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
            role=role_name,
            step=step,
            attempt=attempt,
            model=model or None,
            effort=effort or None,
            seconds=round(time.time() - t0),
            cost=res.get("total_cost_usd"),
            # API round trips (what --max-turns caps); num_turns counts messages
            turns=res["api_turns"] if res.get("api_turns") is not None else res.get("num_turns"),
            messages=res.get("num_turns"),
            error=res.get("is_error"),
            denials=len(denials),
            session=res.get("session_id"),
            **token_usage(res),
            **({"usage_limit": True} if limit else {}),
        )
        if limit:
            return res, limit
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
        return res, None

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
            prov = (
                f"Provisional steps: answer their proposed test fixes in {out}/{f}/answers.md "
                "first (one answer approves all); approve / finish refuse until then.\n"
                if "provisional (code kept" in msg
                else ""
            )
            return (
                f"=== FINAL GATE ({f}) ===\n{files}\n{msg}{prov}"
                f"Approve:  agentflow approve {f}   (then agentflow run {f}: retro -> DONE)\n"
                f"No retro: agentflow finish {f} --no-retro   (DONE)\n"
                f"Parked:   write {out}/{f}/answers.md, then agentflow answer {f} and "
                f"agentflow run {f}\n"
                "          (answers go into the spec and, where needed, the tests; then only the "
                "parked items are redone and this gate comes again)"
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
        tag = f"[{who}] "
        mine = [o.removeprefix(tag) for o in self.open_questions(feature) if o.startswith(tag)]
        items: list[str] = []
        repeats = 0
        for q in split_questions(text):
            while q.startswith(tag):  # an agent that tags its own bullets
                q = q.removeprefix(tag)
            # asked again in a later round (seen live: the human answered one question twice)
            if any(same_question(q, o) for o in [*mine, *items]):
                repeats += 1
            else:
                items.append(q)
        if repeats:
            self.progress(feature, f"[{phase}] {repeats} repeated question(s) dropped.")
        if not items:
            return 0
        with open(d / "open_questions.md", "a", encoding="utf-8") as fh:
            for q in items:
                fh.write(f"- {tag}" + q.replace("\n", "\n  ") + "\n")
        kept = "\n".join("- " + q.replace("\n", "\n  ") for q in items)
        log_event(
            self.cfg, feature, "questions", phase=phase, who=who, questions=kept, repeats=repeats
        )
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
                phase,
                feature,
                task,
                prog.get("feedback", ""),
                role=role,
                step=step,
                attempt=int(prog.get("attempt") or 0) or None,
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

    def note_path(self, feature: str, slug: str) -> str:
        """The implementer's step note (written with the Write tool, appended to steps.md by
        agentflow: an append through the shell is denied in a run)."""
        return f"{self.cfg.paths.out}/{feature}/notes/{slug}.md"

    def take_step_notes(self, feature: str) -> None:
        """Append the implementer's step notes to steps.md (oldest first) and remove them."""
        notes = self.fdir(feature) / "notes"
        if not notes.is_dir():
            return
        found = sorted(notes.glob("*.md"), key=lambda p: p.stat().st_mtime)
        for p in found:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                with open(self.fdir(feature) / "steps.md", "a", encoding="utf-8") as fh:
                    fh.write("\n" + text + "\n")
            p.unlink()

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
        self.set_check_scope(prog, reqs)
        self.take_park_request(feature)  # a stale request of an interrupted run
        if not prog.get("attempt"):  # a fresh item (not a resumed one)
            prog.update(fail_streak=0, last_failed=[], diagnosed=False)
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
                self.take_step_notes(feature)
                self.report_new_assumptions(feature, prog)
                t0 = time.time()
                rep = None if question else gate_report(self.adapter, feature, "step")
                if rep is not None and rep.ok and self.idle_refactor_step(feature, item):
                    # green tests prove nothing here: they were green before the step
                    text = (
                        "The step changed no source file. A refactor step must make the move, "
                        "split or rename the plan describes; the tests then show the behavior "
                        "is still the same."
                    )
                    rep = GateReport(False, text, [IDLE_STEP], outputs={IDLE_STEP: text})
            except KeyboardInterrupt:
                prog["attempt"] = attempt - 1  # an interruption is not an attempt
                save_progress(self.cfg, feature, prog)
                raise
            if rep is None:
                contested = contested_tests(question)
                if contested and self.keep_provisional(
                    feature, prog, item, reqs, contested, question, attempt - 1
                ):
                    return True
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
            unfixable = self.unfixable(rep)
            if unfixable:
                self.park(
                    feature,
                    prog,
                    item,
                    f"unfixable in IMPLEMENT: {unfixable}",
                    attempts=attempt,
                    failed=rep.failed,
                    excerpt=rep.excerpt(),
                )
                return False
            self.progress(
                feature,
                f"[{label}] attempt {attempt} of {self.cfg.max_attempts} failed: "
                + (", ".join(rep.failed) or "step gates"),
            )
            same = sorted(rep.failed) == sorted(prog.get("last_failed") or [])
            prog["fail_streak"] = int(prog.get("fail_streak") or 0) + 1 if same else 1
            prog["last_failed"] = rep.failed
            diagnosis: dict[str, str] = {}
            if (
                self.cfg.diagnose_after
                and prog["fail_streak"] >= self.cfg.diagnose_after
                and not prog.get("diagnosed")
            ):
                diagnosis = self.diagnose(feature, prog, label, reqs, rep)
                contested = (
                    contested_tests(diagnosis["text"], tests_owner=True)
                    if diagnosis.get("owner") == "tests"
                    else []
                )
                if contested and self.keep_provisional(
                    feature, prog, item, reqs, contested, diagnosis["question"], attempt
                ):
                    return True
                if diagnosis.get("owner") in ("tests", "spec"):
                    self.park(
                        feature,
                        prog,
                        item,
                        f"diagnosis: the fix needs a {diagnosis['owner']} change",
                        attempts=attempt,
                        question=diagnosis["question"],
                        failed=rep.failed,
                        excerpt=rep.excerpt(),
                    )
                    return False
            kind = hit_cap(res)
            capped = turn_cap_note(self.cap_value("IMPLEMENT", kind), kind) if kind else ""
            prog["feedback"] = (
                (capped + "\n" if capped else "")
                + f"Attempt {attempt} failed these gates. Tests and config are frozen; "
                f"fix {'/, '.join(self.cfg.src_roots)}/.\n{rep.report[-6000:]}"
                + (
                    "\n\nA diagnosis of the repeated failure (follow it):\n" + diagnosis["text"]
                    if diagnosis.get("owner") == "src"
                    else ""
                )
            )
            save_progress(self.cfg, feature, prog)
            # a src diagnosis always gets its attempt, even after the last regular one
            if attempt >= self.cfg.max_attempts and diagnosis.get("owner") != "src":
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

    def diagnose(
        self,
        feature: str,
        prog: dict[str, Any],
        label: str,
        reqs: list[str] | None,
        rep: Any,
    ) -> dict[str, str]:
        """The same checks failed diagnose_after attempts in a row: one diagnosis run (Opus,
        reads everything, runs tests, changes nothing) finds the root cause and proposes one fix
        with its owner. src -> the implementer's next attempt; tests / spec -> parked with the
        proposal as the question, approved at the final gate in one reply. {} = no usable
        diagnosis (the loop goes on as before)."""
        prog["diagnosed"] = True
        save_progress(self.cfg, feature, prog)
        d = self.fdir(feature)
        report = d / "diagnosis.md"
        report.unlink(missing_ok=True)
        task = self.task_text(
            "DIAGNOSIS",
            feature,
            label=label,
            failed=", ".join(rep.failed) or "step gates",
            n=str(prog.get("fail_streak")),
            check=self.adapter.test_command(reqs),
            report=rep.report[-6000:],
        )
        saved = prog.get("feedback", "")
        prog["feedback"] = ""  # the gate feedback is for the implementer, not the diagnosis
        self.agent("DIAGNOSIS", feature, prog, task, role="diagnosis", step=label)
        prog["feedback"] = saved
        set_phase(self.cfg, "IMPLEMENT")
        text = report.read_text(encoding="utf-8").strip() if report.is_file() else ""
        m = FIX_OWNER_RE.search(text)
        if not m:
            log_event(self.cfg, feature, "diagnosis", step=label, owner=None, failed=rep.failed)
            self.progress(feature, f"[{label}] diagnosis: no usable diagnosis.md; going on")
            return {}
        owner = m.group(1).lower()
        keep = d / "diagnoses"
        keep.mkdir(exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower()
        kept = keep / f"{slug}.md"
        kept.write_text(text + "\n", encoding="utf-8")
        report.unlink()

        def line(name: str) -> str:
            found = re.search(rf"^\W*{name}\W*:\s*(.+)$", text, re.I | re.M)
            return found.group(1).strip() if found else ""

        root, fix = line("Root cause"), line("Proposed fix")
        log_event(
            self.cfg,
            feature,
            "diagnosis",
            step=label,
            owner=owner,
            root_cause=root,
            proposed_fix=fix,
            failed=rep.failed,
            file=self.rel(kept),
        )
        self.progress(feature, f"[{label}] diagnosis ({owner}): {root}")
        question = (
            f"Diagnosis ({self.rel(kept)}): approve the proposed fix, or answer otherwise.\n"
            f"Root cause: {root}\nProposed fix ({owner}): {fix}"
        )
        return {"owner": owner, "text": text, "question": question}

    def unfixable(self, rep: Any) -> str:
        """If every failing check of a step gate is a lint / format check whose findings are all
        in files the implementer may not edit (frozen tests, review tests): what and where, so
        the item parks at once instead of looping through attempts it cannot win. "" else."""
        style = {GATE_NAMES["lint"], GATE_NAMES["format"]}
        if not rep.failed or any(name not in style for name in rep.failed):
            return ""
        writable = self.cfg.write_paths("IMPLEMENT")
        parts = []
        for name in rep.failed:
            paths = self.failure_paths(rep.full_outputs.get(name) or rep.outputs.get(name, ""))
            if not paths or any(path_matches(p, writable) for p in paths):
                return ""
            parts.append(f"{name} fails only in files it may not edit: {', '.join(sorted(paths))}")
        return "; ".join(parts)

    def failure_paths(self, text: str) -> set[str]:
        """Repository files (posix, relative) named in a check's output."""
        sfx = "|".join(re.escape(s.lstrip(".")) for s in self.adapter.source_suffixes) or r"\w+"
        root = self.cfg.root.resolve()
        found = set()
        for m in re.finditer(rf"(?:[A-Za-z]:)?[\w.\-/\\]+\.(?:{sfx})\b", text):
            path = pathlib.Path(m.group(0).replace("\\", "/"))
            try:
                rel = (path if path.is_absolute() else root / path).resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            if (root / rel).is_file():
                found.add(rel.as_posix())
        return found

    def revert_src(self, feature: str, label: str) -> str:
        """Save the parked work's uncommitted source changes as a diff and restore the sources
        to the last commit, so later steps start from code that passed its gates."""
        src = [r for r in self.cfg.src_roots if (self.cfg.root / r).exists()]
        if not src:
            return ""
        git(self.cfg, "add", "-A", "--", *src)
        diff = git(self.cfg, "diff", "--cached", "HEAD", "--", *src)
        if not diff.strip():
            return ""
        pdir = self.fdir(feature) / "parked"
        pdir.mkdir(exist_ok=True)
        path = pdir / (re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower() + ".diff")
        path.write_text(diff, encoding="utf-8")
        git(self.cfg, "restore", "--source=HEAD", "--staged", "--worktree", "--", *src)
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
        prog.update(attempt=0, feedback="", fail_streak=0, last_failed=[], diagnosed=False)
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

    # ---------- provisional steps: code kept while only their tests are contested ----------
    def contested(self, prog: dict[str, Any]) -> list[str]:
        """The contested tests of all provisional steps."""
        return [t for r in prog.get("provisional") or [] for t in r.get("contested") or []]

    def set_check_scope(self, prog: dict[str, Any], reqs: list[str] | None) -> None:
        """What the step gates and the Stop hook check: the IDs (.reqs) minus the contested
        tests of provisional steps."""
        if reqs:
            set_reqs(self.cfg, reqs)
        else:
            clear_reqs(self.cfg)
        set_contested(self.cfg, self.contested(prog))

    def keep_provisional(
        self,
        feature: str,
        prog: dict[str, Any],
        item: dict[str, Any],
        reqs: list[str] | None,
        contested: list[str],
        question: str,
        attempts: int,
    ) -> bool:
        """A step parked only because named frozen tests are wrong (they contradict the spec or
        existing code): if its code passes every other check of the step gate, commit it as
        provisional so the dependent steps build on it (seen live: a correct step 1 parked for
        two wrong tests made steps 2-3 wait, and their own wrong test showed only a round
        later). The contested tests stay out of the step checks; the final gate lists the
        proposed test fixes. False (nothing kept): the code fails other checks too."""
        if item.get("kind") != "step":
            return False
        label = item["label"]
        set_contested(self.cfg, self.contested(prog) + contested)
        rep = gate_report(self.adapter, feature, "step")
        if not rep.ok:
            set_contested(self.cfg, self.contested(prog))
            self.progress(
                feature,
                f"[{label}] contests {len(contested)} test(s), but its code fails other checks "
                f"too ({', '.join(rep.failed)}): parked as usual",
            )
            return False
        names = ", ".join(contested)
        commit(self.cfg, f"{feature}: {label} (PROVISIONAL: tests contested: {names})")
        sha = git(self.cfg, "rev-parse", "HEAD").strip()
        rec = dict(item, contested=contested, question=question, commit=sha, attempts=attempts)
        prog["provisional"] = [*(prog.get("provisional") or []), rec]
        prog.update(attempt=0, feedback="", fail_streak=0, last_failed=[], diagnosed=False)
        save_progress(self.cfg, feature, prog)
        log_event(
            self.cfg,
            feature,
            "provisional",
            label=label,
            step=item.get("step"),
            steps=item.get("steps"),
            contested=contested,
            question=question,
            commit=sha,
            attempts=attempts,
        )
        self.progress(
            feature,
            f"[{label}] PROVISIONAL: only its tests are contested ({names}); its code passes "
            "every other check, is committed as provisional and later steps build on it. The "
            "proposed test fixes are listed at the final gate.",
        )
        return True

    def provisional_context(self, prog: dict[str, Any]) -> str:
        """Task lines about provisional steps: their contested tests fail by design."""
        recs = prog.get("provisional") or []
        if not recs:
            return ""
        return (
            "\n\nProvisional steps (code kept and committed; the human decides at the final gate "
            "on the proposed fixes of their contested tests): "
            + "; ".join(f"{r['label']} ({', '.join(r.get('contested') or [])})" for r in recs)
            + ". The contested tests fail by design and are left out of your step gate: do not "
            "change code to make them pass, and build on the provisional code as it is."
        )

    def settle_provisional(self, feature: str, prog: dict[str, Any], steps: list[PlanStep]) -> None:
        """After the answer at the final gate (and the test writer's amendment): the contested
        tests must pass now. A step whose contested tests pass is final. One whose tests still
        fail had its fix rejected (or not applied): its provisional commit is reverted and the
        step is redone against the tests as they are."""
        recs = list(prog.get("provisional") or [])
        if not recs:
            return
        set_contested(self.cfg, [])
        junit = self.fdir(feature) / "provisional_tests.xml"
        run = self.adapter.run_tests(req_ids=self.done_ids(prog, steps), junit=junit)
        for rec in recs:
            label = rec["label"]
            names = list(rec.get("contested") or [])
            _, still, _ = split_failures(run.cases or [], set(), names)
            if not still:
                unknown = run.status != "passed" and run.cases is None
                log_event(self.cfg, feature, "provisional_settled", label=label, accepted=True)
                self.progress(
                    feature,
                    f"[{label}] provisional code accepted: its contested tests pass after the "
                    "answer"
                    + (" (no per-test report: the review fixes check them)" if unknown else ""),
                )
                continue
            diff = self.save_commit_diff(feature, label, str(rec.get("commit") or ""))
            reverted = self.revert_commit(str(rec.get("commit") or ""))
            nums = item_steps(rec)
            prog["completed_steps"] = [n for n in prog["completed_steps"] if n not in nums]
            redo = {k: v for k, v in rec.items() if k not in ("contested", "commit", "attempts")}
            redo.update(
                reason="contested tests still fail after the answer",
                diff=diff,
                provisional_reverted=reverted,
                still_failing=still,
            )
            prog["redo"] = [*(prog.get("redo") or []), redo]
            log_event(
                self.cfg,
                feature,
                "provisional_settled",
                label=label,
                accepted=False,
                still_failing=still,
                reverted=reverted,
                diff=diff,
            )
            self.progress(
                feature,
                f"[{label}] proposed test fix not applied ({len(still)} contested test(s) still "
                "fail): provisional code "
                + ("reverted" if reverted else "kept (the revert conflicts with later commits)")
                + f", the step is redone against the tests (old code: {diff or '-'})",
            )
        prog["provisional"] = []
        save_progress(self.cfg, feature, prog)

    def save_commit_diff(self, feature: str, label: str, sha: str) -> str:
        """A commit's source changes as parked/<label>.diff (the redo can reuse what fits)."""
        if not sha:
            return ""
        try:
            diff = git(self.cfg, "show", "--format=", sha, "--", *self.cfg.src_roots)
        except GitError:
            return ""
        if not diff.strip():
            return ""
        pdir = self.fdir(feature) / "parked"
        pdir.mkdir(exist_ok=True)
        path = pdir / (re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower() + ".diff")
        path.write_text(diff, encoding="utf-8")
        return self.rel(path)

    def revert_commit(self, sha: str) -> bool:
        """Revert a provisional commit; False (nothing changed) if it conflicts with later
        commits or is unknown."""
        if not sha:
            return False
        try:
            git(self.cfg, "revert", "--no-edit", sha)
            return True
        except GitError:
            try:
                git(self.cfg, "revert", "--abort")
            except GitError:
                pass
            return False

    # ---------- TESTS: writer <-> reviewer, then the tests gate ----------
    def changed_tests(self) -> list[str]:
        """Test files changed since the last commit (incl. untracked), posix, relative."""
        tests = self.cfg.paths.tests.rstrip("/") + "/"
        changed = set(git(self.cfg, "diff", "--name-only", "HEAD", "--", tests).split())
        changed |= set(
            git(self.cfg, "ls-files", "--others", "--exclude-standard", "--", tests).split()
        )
        return sorted(f for f in changed if (self.cfg.root / f).is_file())

    def format_written_tests(self, feature: str) -> None:
        """Format the test writer's files ([gates] format_fix): the implementer may not edit
        tests, so a formatting slip in them would fail every later gate."""
        if not self.adapter.format_fix_enabled():
            return
        try:
            note = self.adapter.format_files(self.changed_tests(), "HEAD")
        except Exception as e:  # the format check still reports it
            note = f"skipped ({type(e).__name__}: {e})"
        if note:
            self.progress(feature, f"[TESTS] format fix: {note}")

    def phase_tests(self, feature: str, prog: dict[str, Any]) -> None:
        sub = prog.get("sub") or "write"
        if sub == "amend":
            self.amend_tests(feature, prog)
            return
        while True:
            if sub == "write":
                self.agent("TESTS", feature, prog, self.task_text("TESTS", feature))
                self.collect_questions(feature, "TESTS", "test writer")
                self.format_written_tests(feature)
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

    def amend_tests(self, feature: str, prog: dict[str, Any]) -> None:
        """After answers at the final gate (and their spec update): the test writer applies
        the answers that need a test change (fixtures, seeds, expectations, tests for new IDs,
        a review test an answer names); the implementer may not touch tests, so without this
        such an answer could never be applied. Changed tests get one test-reviewer pass (its
        objections go to the final gate), are committed and tagged tests-update/<f>, the one
        test change the frame diff allows after the tests were approved."""
        amend = dict(prog.get("amend") or {})
        ref = str(amend.get("ref") or "ANS-?")
        nxt = dict(amend.get("then") or {"phase": "REVIEW", "sub": "final"})
        self.agent("TESTS", feature, prog, self.task_text("TESTS_AMEND", feature, ref=ref))
        self.collect_questions(feature, "TESTS", "test writer")
        self.format_written_tests(feature)
        changed = self.commit_test_changes(
            feature,
            prog,
            f"from the answers {ref}",
            f"{feature}: tests updated from answers {ref}",
        )
        if changed:
            prog["tests_amended"] = True  # the review fixes make the changed tests pass
            if nxt.get("phase") == "PR":  # nothing else to redo
                nxt = {"phase": "REVIEW", "sub": "fix"}
        log_event(self.cfg, feature, "tests_amended", ref=ref, files=changed)
        self.progress(
            feature,
            f"[TESTS] answers {ref}: "
            + (f"{len(changed)} test file(s) changed, committed" if changed else "no test change"),
        )
        prog.update(phase=nxt.get("phase"), sub=nxt.get("sub"), amend=None, attempt=0)
        save_progress(self.cfg, feature, prog)

    def commit_test_changes(
        self, feature: str, prog: dict[str, Any], what: str, message: str
    ) -> list[str]:
        """Test changes made after the tests were approved (`what`: where they come from): one
        test-reviewer pass over just them (its objections go to the final gate), then the
        commit and the tag tests-update/<f>, the one test change the frame diff allows.
        Returns the changed files ([] = nothing changed, nothing done)."""
        changed = self.changed_tests()
        if not changed:
            return []
        note = (
            f"\n\nThis pass reviews only the test changes made {what} "
            f"(git diff HEAD -- {self.cfg.paths.tests}/); objections go to the human."
        )
        (self.fdir(feature) / "test_review.md").unlink(missing_ok=True)
        task = self.task_text("TEST_REVIEW", feature, round="1", rounds="1", note=note)
        self.agent("TEST_REVIEW", feature, prog, task, role="test_review")
        self.collect_questions(feature, "TEST_REVIEW", "test reviewer")
        review = self.read_test_review(feature)
        if review.objections:
            with open(self.fdir(feature) / "open_questions.md", "a", encoding="utf-8") as fh:
                for o in review.objections:
                    fh.write(f"- [tests amended] test reviewer objection: {o}\n")
        git(self.cfg, "add", "-A", "--", self.cfg.paths.tests)
        git(self.cfg, "commit", "-q", "-m", message)
        git(self.cfg, "tag", "-f", tests_update_tag(feature))
        return changed

    def run_test_checks(self, feature: str, prog: dict[str, Any]) -> None:
        sections = []
        status: dict[str, str] = {}
        mode = feature_mode(self.cfg, feature)
        for name, mod in [
            (baseline.name(mode), baseline),
            ("Traceability", traceability),
            ("Test smells", test_smells),
        ]:
            res = mod.check(self.adapter, feature)
            status[name] = res.status
            sections.append(f"### {name}: {res.status}\n~~~\n{res.output}\n~~~")
        # a note for the test reviewer, never blocking (not in `status`: that sends tests back)
        try:
            members = (
                existing_members.check(self.adapter, feature)
                if mode == "feature"
                # a bug fix / a refactor asserts existing members on purpose, and the run
                # against today's code already checks those claims
                else CheckResult("SKIPPED", "Not listed: the tests ran against today's code.")
            )
        except Exception as e:  # a scan problem must not stop the tests phase
            members = CheckResult("SKIPPED", f"SKIPPED: scan failed ({type(e).__name__}: {e})")
        listed = sum(1 for line in members.output.splitlines() if line.startswith("- "))
        sections.append(
            f"### Existing members asserted (note, not blocking): {listed}\n~~~\n"
            f"{members.output}\n~~~"
        )
        uncovered = ""
        if mode == "refactor":
            # before any code moves: the lines in scope that no tagged test runs (a note)
            try:
                cover = scope_coverage.check(self.adapter, feature)
            except Exception as e:  # a tool problem must not stop the tests phase
                cover = CheckResult("SKIPPED", f"SKIPPED: failed ({type(e).__name__}: {e})")
            uncovered = cover.summary
            sections.append(
                f"### {scope_coverage.NAME} (note, not blocking): "
                f"{cover.summary or cover.status}\n~~~\n{cover.output}\n~~~"
            )
        path = self.fdir(feature) / "test_checks.md"
        path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
        self.progress(
            feature,
            "[TESTS] checks: "
            + ", ".join(f"{n} {s}" for n, s in status.items())
            + (f"; existing members asserted without a fixture test: {listed}" if listed else "")
            + (f"; scope coverage: {uncovered}" if uncovered else ""),
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

    def idle_refactor_step(self, feature: str, item: dict[str, Any]) -> bool:
        """A plan step of a refactor whose implementer run left the source folders as they
        were (in a feature a step is proven by its tests turning green; here they were green
        all along)."""
        if item.get("kind") != "step" or feature_mode(self.cfg, feature) != "refactor":
            return False
        return not git(self.cfg, "status", "--porcelain", "--", *self.cfg.src_roots).strip()

    def step_batches(self, steps: list[PlanStep], single: bool = False) -> list[list[PlanStep]]:
        """Consecutive steps share one implementer run while their spec IDs add up to at most
        [agents] step_batch_ids (0 = one run per step): a small plan costs one run instead of
        one per step (each run re-reads the spec and the code)."""
        cap = 0 if single else self.cfg.step_batch_ids
        batches: list[list[PlanStep]] = []
        for step in steps:
            last = batches[-1] if batches else None
            if last and cap and sum(len(s.reqs) for s in last) + len(step.reqs) <= cap:
                last.append(step)
            else:
                batches.append([step])
        return batches

    def parked_ids(self, prog: dict[str, Any], steps: list[PlanStep]) -> list[str]:
        """The spec IDs of the parked plan steps."""
        parked = self.parked_steps(prog)
        return [i for s in steps if s.num in parked for i in s.reqs]

    def parked_context(self, prog: dict[str, Any], steps: list[PlanStep]) -> str:
        """Task lines about parked steps: their IDs are off limits until they are redone."""
        parked = self.parked_steps(prog)
        if not parked:
            return ""
        names = ", ".join(dict.fromkeys(p["label"] for n, p in sorted(parked.items())))
        return (
            f"\n\nParked steps (not implemented; their tests are not part of your check): {names}."
            f" Their spec IDs: {', '.join(self.parked_ids(prog, steps))}. Do not implement any of "
            "them, also not the parts your work shares code with: they are redone after the "
            "human's answer."
        )

    def block_dependents(
        self, feature: str, prog: dict[str, Any], batch: list[PlanStep]
    ) -> list[PlanStep]:
        """Steps of the batch that depend on a parked step (plan.md "Depends on") wait for it:
        parked without a run, redone with it. Returns the steps that can run."""
        runnable = []
        for step in batch:
            parked = self.parked_steps(prog)
            dep = next((d for d in step.depends if d in parked), None)
            if dep is None:
                runnable.append(step)
                continue
            rec = parked[dep]
            rec.setdefault("dependents", []).append(step.num)
            log_event(self.cfg, feature, "step_blocked", step=step.num, depends_on=dep)
            item = {"kind": "step", "step": step.num, "label": f"step {step.num} - {step.title}"}
            self.park(feature, prog, item, f"waits for parked step {dep} (plan.md)", attempts=0)
            if prog.get("redo_mode"):  # its redo ends here: it waits again
                ended = [
                    r
                    for r in prog.get("redo") or []
                    if r.get("kind") == "step" and step.num in item_steps(r)
                ]
                for r in ended:
                    log_event(self.cfg, feature, "redo_result", label=r["label"], passed=False)
                prog["redo"] = [r for r in prog.get("redo") or [] if r not in ended]
                save_progress(self.cfg, feature, prog)
        return runnable

    def phase_implement(self, feature: str, prog: dict[str, Any]) -> None:
        steps = plan_steps(self.cfg, feature)
        if not steps:
            raise WorkflowError(
                f"No '## Step N:' sections found in {self.cfg.paths.specs}/{feature}/plan.md"
            )
        redo_mode = bool(prog.get("redo_mode"))
        if redo_mode:
            self.settle_provisional(feature, prog, steps)

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
        # a refactor: one move per run, each checked on its own
        single = feature_mode(self.cfg, feature) == "refactor"
        for batch in self.step_batches(pending, single):
            batch = self.block_dependents(feature, prog, batch)
            if not batch:
                continue
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
            if feature_mode(self.cfg, feature) == "refactor":
                # every step keeps every pinned behavior: all the characterization tests
                covered = sorted(feature_ids(self.cfg, feature))
                reqs = covered
            check = self.adapter.test_command(covered)
            note = self.note_path(feature, "step-" + "-".join(map(str, nums)))
            if one:
                task = self.task_text(
                    "IMPLEMENT",
                    feature,
                    n=str(nums[0]),
                    title=batch[0].title,
                    ids=", ".join(reqs),
                    check=check,
                    note=note,
                )
            else:
                task = self.task_text(
                    "IMPLEMENT_STEPS",
                    feature,
                    n=", ".join(map(str, nums)),
                    titles="; ".join(f"{s.num}. {s.title} ({', '.join(s.reqs)})" for s in batch),
                    ids=", ".join(reqs),
                    check=check,
                    note=note,
                )
            task += self.parked_context(prog, steps) + self.provisional_context(prog)
            if redo:
                task += REDO_NOTE.format(**self.placeholders(feature))
            for r in redo:
                if "still_failing" in r:
                    task += (
                        f"\n\nYour earlier code for '{r['label']}' was kept as provisional, but "
                        "the human did not approve changing its contested tests: they stand and "
                        f"must pass ({', '.join(r['still_failing'])}). The old code "
                        + (
                            "was reverted"
                            if r.get("provisional_reverted")
                            else "is still in place (the revert conflicted)"
                        )
                        + (f"; its diff: {r['diff']} (reuse what fits)." if r.get("diff") else ".")
                    )
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
            fix = findings or prog.get("tests_amended")
            self.advance(feature, prog, "REVIEW", sub="fix" if fix else "final")
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
            task = self.task_text("REVIEW", feature)
            parked = self.parked_ids(prog, plan_steps(self.cfg, feature))
            if parked:
                task += (
                    f"\n\nParked plan steps (not implemented yet, redone after the human's answer)"
                    f" own these spec IDs: {', '.join(parked)}. Write no findings about them."
                )
            contested = self.contested(prog)
            if contested:
                task += (
                    "\n\nContested tests (a provisional step's frozen tests that its implementer "
                    "found wrong; their fix waits for the human's answer): "
                    f"{', '.join(contested)}. Write no findings about them."
                )
            self.agent("REVIEW", feature, prog, task)
            self.collect_questions(feature, "REVIEW", "adversarial reviewer")
            self.take_structure_findings(feature, prog)
            findings = self.review_failures(feature)
            if findings:
                log_event(self.cfg, feature, "review_findings", tests=findings)
                prog["findings"] = findings
            amended = prog.get("tests_amended")
            sub = self.set_sub(
                feature, prog, "triage" if findings else "fix" if amended else "final"
            )
        if sub == "triage":
            fix = self.triage(feature, prog) or prog.get("tests_amended")
            sub = self.set_sub(feature, prog, "fix" if fix else "final")
        if sub == "fix":
            self.fix_findings(feature, prog)
            sub = self.set_sub(feature, prog, "final")
        self.fix_structure(feature, prog)
        rep = self.run_final_gates(feature, prog)
        # at most one repair per owner (code, then tests) before the human sees a failure
        while not rep.ok and self.repair_final(feature, prog, rep):
            rep = self.run_final_gates(feature, prog)
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
        deferred = self.defer_parked_findings(feature, prog, remaining)
        for r in records:
            if r["test"] in deferred:
                r["verdict"] = "DEFERRED"
                r["reason"] = f"{r['reason']}; only IDs of a parked step: fixed with its redo"
        remaining = [t for t in remaining if t not in deferred]
        log_event(self.cfg, feature, "triage", findings=records, remaining=remaining)
        valid = sum(1 for r in records if r["verdict"] == "VALID")
        self.progress(
            feature,
            f"[TRIAGE] {valid} valid finding(s), "
            + (f"{len(deferred)} deferred to parked steps, " if deferred else "")
            + f"{len(records) - valid - len(deferred)} dismissed.",
        )
        commit(self.cfg, f"{feature}: review findings triaged ({valid} valid)")
        prog["valid_findings"] = remaining
        save_progress(self.cfg, feature, prog)
        return bool(remaining)

    def defer_parked_findings(
        self, feature: str, prog: dict[str, Any], findings: list[str]
    ) -> list[str]:
        """Valid findings whose review test is tagged only with IDs of parked steps: not for the
        review fixes (they would build the parked step); the step's redo covers them (its check
        runs the tests of its IDs, review tests included). Returns them."""
        parked = set(self.parked_ids(prog, plan_steps(self.cfg, feature)))
        if not parked or not findings:
            return []
        try:
            tags = self.adapter.collect_req_tags()
        except Exception:  # a broken test file: nothing is deferred
            return []
        review = self.cfg.paths.review_tests.rstrip("/") + "/"
        ids_of: dict[str, set[str]] = {}
        for req, where in tags.items():
            for w in where:
                if w.startswith(review):
                    ids_of.setdefault(short_name(w), set()).add(req)
        deferred = [t for t in findings if ids_of.get(short_name(t), {""}) <= parked]
        if deferred:
            log_event(self.cfg, feature, "findings_deferred", tests=deferred, ids=sorted(parked))
        return deferred

    def fix_findings(self, feature: str, prog: dict[str, Any]) -> None:
        steps = plan_steps(self.cfg, feature)
        redo = [r for r in prog.get("redo") or [] if r.get("kind") == "finding"]
        tests = list(prog.get("valid_findings") or [])
        for r in redo:
            tests += [t for t in r.get("tests") or [] if t not in tests]
        # with parked steps, their tests are red by design: check only the done steps' IDs
        reqs = self.done_ids(prog, steps) if self.parked_steps(prog) else None
        amended = bool(prog.pop("tests_amended", False))
        save_progress(self.cfg, feature, prog)
        if amended and not tests and not redo:
            # only tests changed from answers: no fix run if they pass already
            self.set_check_scope(prog, reqs)
            rep = gate_report(self.adapter, feature, "step")
            clear_reqs(self.cfg)
            if rep.ok:
                self.progress(feature, "[review fixes] the changed tests pass; no fix needed")
                return
        task = self.task_text(
            "FIX",
            feature,
            check=self.adapter.test_command(reqs),
            note=self.note_path(feature, "review-fixes"),
        )
        task += self.parked_context(prog, steps) + self.provisional_context(prog)
        shape = [x for r in redo for x in r.get("structure") or []]
        if shape:
            task += (
                "\n\nAlso apply these structure findings (restructure without changing "
                "behavior; a finding the human said to leave: change nothing for it):\n"
                + "\n".join(f"- {x}" for x in shape)
            )
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

    def run_final_gates(self, feature: str, prog: dict[str, Any]) -> GateReport:
        """Run the final gates, write final_gates.md and log the result."""
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
            results=rep.results,
            structure=rep.structure,
            tool_failures=rep.tool_failures,
        )
        names = [n + (" [tool failure]" if n in rep.tool_failures else "") for n in rep.failed]
        self.progress(
            feature,
            f"[REVIEW] final gates: {'PASSED' if rep.ok else 'FAILED'}"
            + (f" ({', '.join(names)})" if names else "")
            + (f"; skipped: {', '.join(rep.skipped)}" if rep.skipped else "")
            + (
                "; project checks (not blocking): "
                + ", ".join(f"{n} {'OK' if c == 0 else f'exit {c}'}" for n, c in rep.extra.items())
                if rep.extra
                else ""
            ),
        )
        return rep

    def repair_final(self, feature: str, prog: dict[str, Any], rep: GateReport) -> bool:
        """One bounded repair of failed final gates, so only what the agents cannot fix reaches
        the human: failures the code owns (lint, types, a red test) go to the implementer;
        tests too weak for the diff-coverage or mutation threshold go to the test writer (new
        tests reviewed against the spec, as after an answer). Each owner gets one round per
        final-gate cycle ([gates] final_repair = false: none). False = nothing (more) to
        repair: the gate report stands. Not while parked or provisional work waits: those
        failures are expected and the human's answer comes first."""
        if not self.cfg.final_repair or expected_red(self.cfg, feature):
            return False
        done = list(prog.get("final_repair") or [])
        # a tool that gave no result says nothing about the tests: no agent run, the human is
        # asked whether to run the gates again (tool_failure_questions)
        real = [n for n in rep.failed if n not in rep.tool_failures]
        src = [n for n in real if n.startswith(FINAL_SRC_CHECKS)]
        weak = [n for n in real if n.startswith(FINAL_TESTS_CHECKS)]
        if src and "src" not in done:
            owner, failed = "src", src
        elif weak and not src and "tests" not in done:
            if feature_mode(self.cfg, feature) == "refactor":
                # new tests would pin what the code does AFTER the refactor: no proof that
                # nothing changed. The human decides (more characterization tests need the
                # old code: a new refactor task, or approve as is).
                self.progress(
                    feature,
                    f"[final gates] {', '.join(weak)} failed; no test repair in a refactor "
                    "(tests written now would pin the changed code).",
                )
                return False
            owner, failed = "tests", weak
        else:
            return False
        prog["final_repair"] = [*done, owner]
        save_progress(self.cfg, feature, prog)
        who = "implementer" if owner == "src" else "test writer"
        self.progress(
            feature, f"[final gates] {', '.join(failed)} failed; one repair run by the {who}."
        )
        detail = "\n\n".join(f"[FAIL] {n}\n{rep.outputs.get(n, '')}".rstrip() for n in failed)
        prog["feedback"] = "The failing final checks:\n" + detail[:8000]
        if owner == "src":
            changed = self.repair_final_src(feature, prog, failed)
        else:
            changed = self.strengthen_tests(feature, prog, failed)
        log_event(self.cfg, feature, "final_repair", owner=owner, failed=failed, changed=changed)
        if not changed:
            self.progress(feature, f"[final gates] the {who} changed nothing; the result stands.")
        return changed

    def repair_final_src(self, feature: str, prog: dict[str, Any], failed: list[str]) -> bool:
        """The implementer fixes what the final gates found in the code (whole dev-loop suite
        as its step check). True = fixed and committed; False = parked like a review fix."""
        task = self.task_text(
            "FIX_FINAL",
            feature,
            failed=", ".join(failed),
            check=self.adapter.test_command([]),
            note=self.note_path(feature, "final-gate-fixes"),
        )
        item: dict[str, Any] = {
            "kind": "finding",
            "step": None,
            "label": "final gate fixes",
            "tests": [],
        }
        head = git(self.cfg, "rev-parse", "HEAD").strip()
        ok = self.implement_loop(feature, prog, task, None, item)
        clear_reqs(self.cfg)
        # green step gates without a commit: nothing changed, the same final result would come
        return ok and git(self.cfg, "rev-parse", "HEAD").strip() != head

    def strengthen_tests(self, feature: str, prog: dict[str, Any], failed: list[str]) -> bool:
        """The test writer adds tests for uncovered lines / surviving mutants (from the spec,
        not from the code). True = tests changed (reviewed, committed, and the code fixed if a
        new test fails); False = no test change."""
        task = self.task_text("TESTS_STRENGTHEN", feature, failed=", ".join(failed))
        self.agent("TESTS", feature, prog, task)
        self.collect_questions(feature, "TESTS", "test writer")
        self.format_written_tests(feature)
        changed = self.commit_test_changes(
            feature,
            prog,
            "to cover what the final gates found too weak",
            f"{feature}: tests strengthened (final gates)",
        )
        if not changed:
            return False
        self.progress(feature, f"[final gates] {len(changed)} test file(s) strengthened, committed")
        # a new test that follows from the spec and fails: the code is wrong, the implementer
        # fixes it (or parks it); green: nothing more to do before the gates run again
        self.set_check_scope(prog, None)
        step = gate_report(self.adapter, feature, "step")
        clear_reqs(self.cfg)
        if not step.ok:
            prog["feedback"] = "The strengthened tests fail:\n" + step.report[-6000:]
            self.repair_final_src(feature, prog, step.failed)
        return True

    # ---------- structure: readability findings and file sizes ----------
    def take_structure_findings(self, feature: str, prog: dict[str, Any]) -> None:
        """The reviewer's `## Structure findings` (review.md): restructuring without a behavior
        change, done by the implementer before the final gates (fix_structure)."""
        review = self.fdir(feature) / "review.md"
        text = review.read_text(encoding="utf-8") if review.is_file() else ""
        pending = list(prog.get("structure_findings") or [])
        found = [x for x in section_bullets(text, "Structure findings") if x not in pending]
        if found:  # (a resumed review run must not add the same findings twice)
            prog["structure_findings"] = [*pending, *found]
            log_event(self.cfg, feature, "structure_findings", findings=found)
            save_progress(self.cfg, feature, prog)

    def fix_structure(self, feature: str, prog: dict[str, Any]) -> None:
        """One implementer item (with attempts) that restructures the code as the pending
        structure findings say (the reviewer's, or the human's answers to file-size questions),
        with every test green. Failing it parks the work like a review fix."""
        findings = list(prog.get("structure_findings") or [])
        if not findings:
            return
        steps = plan_steps(self.cfg, feature)
        reqs = self.done_ids(prog, steps) if self.parked_steps(prog) else None
        if reqs is None and feature_mode(self.cfg, feature) == "refactor":
            # like a refactor step: all the characterization tests. The whole suite runs
            # once, in the final gates (live: a helper extraction ran it three times)
            reqs = sorted(feature_ids(self.cfg, feature)) or None
        task = self.task_text(
            "STRUCTURE",
            feature,
            findings="\n".join(f"- {x}" for x in findings),
            check=self.adapter.test_command(reqs),
            note=self.note_path(feature, "structure-fixes"),
        )
        task += self.parked_context(prog, steps) + self.provisional_context(prog)
        item = {
            "kind": "finding",
            "step": None,
            "label": "structure fixes",
            "tests": [],
            "structure": findings,
        }
        self.progress(feature, f"[structure fixes] {len(findings)} finding(s) to apply")
        ok = self.implement_loop(feature, prog, task, reqs, item)
        clear_reqs(self.cfg)
        prog.pop("structure_findings", None)
        save_progress(self.cfg, feature, prog)
        if ok:
            log_event(self.cfg, feature, "structure_fixed", findings=findings)

    def file_size_questions(self, feature: str, prog: dict[str, Any]) -> None:
        """A question at the final gate for every changed source file over [gates]
        max_file_lines (from the last final-gate structure report), with a default: split it
        now when this feature took it over the limit, else leave it for later."""
        last = [e for e in read_events(self.cfg, feature) if e.get("kind") == "final_gates"]
        shape = (last[-1].get("structure") if last else None) or {}
        over = shape.get("over") or []
        if not over:
            return
        asked = "\n".join(self.open_questions(feature))
        before = shape.get("before") or {}
        files = shape.get("files") or {}
        limit = int(shape.get("limit") or 0)
        lines = []
        for path in over:
            if path in asked or path in (prog.get("size_answered") or []):
                continue
            was = before.get(path)
            grew = was is None or was <= limit
            default = (
                "split it now by concept (the implementer restructures it, the tests stay as "
                "they are; one more check round)"
                if grew
                else f"leave it for a later cleanup (it had {was} lines before this feature)"
            )
            lines.append(
                f"{path} has {files.get(path)} lines (limit {limit}). Split it into one file per "
                f"concept? Default: {default}."
            )
        if not lines:
            return
        with open(self.fdir(feature) / "open_questions.md", "a", encoding="utf-8") as fh:
            for q in lines:
                fh.write(f"- [{STRUCTURE_WHO}] {q}\n")
        text = "\n".join(lines)
        log_event(self.cfg, feature, "questions", phase="PR", who=STRUCTURE_WHO, questions=text)

    def tool_failure_questions(self, feature: str) -> None:
        """A question at the final gate (always asked) for every final check whose tool gave
        no result in the last run: run the final gates again (any answer does), or approve
        without that check."""
        last = [e for e in read_events(self.cfg, feature) if e.get("kind") == "final_gates"]
        names = (last[-1].get("tool_failures") if last else None) or []
        asked = "\n".join(self.open_questions(feature))
        out = self.cfg.paths.out
        lines = [
            f"{name}: the tool gave no result, so this check says nothing about the code or "
            f"the tests (its output: {out}/{feature}/final_gates.md). Run the final checks "
            "again? Options: yes, run them again; no, finish without this check (approve as "
            "is). Default: yes, run them again."
            for name in names
            if f"] {name}: the tool gave no result" not in asked
        ]
        if not lines:
            return
        with open(self.fdir(feature) / "open_questions.md", "a", encoding="utf-8") as fh:
            for q in lines:
                fh.write(f"- [{GATES_WHO}] {q}\n")
        text = "\n".join(lines)
        log_event(self.cfg, feature, "questions", phase="PR", who=GATES_WHO, questions=text)

    def commit_code_map(self, feature: str) -> bool:
        """Commit the summary agent's code map update (its only file outside the out folder)."""
        path = self.cfg.code_map
        if not (self.cfg.root / path).is_file():
            return False
        git(self.cfg, "add", "--", path)
        if not git(self.cfg, "diff", "--cached", "--name-only", "--", path).strip():
            return False
        git(self.cfg, "commit", "-q", "-m", f"{feature}: code map", "--", path)
        return True

    def code_map_state(self, feature: str, updated: bool) -> dict[str, Any]:
        """Is there a code map, did this PR phase update it, which new public types it lacks."""
        exists, missing = structure_check.missing_from_code_map(self.adapter, feature)
        state = {"exists": exists, "updated": updated, "missing": missing}
        log_event(self.cfg, feature, "code_map", **state)
        return state

    # ---------- PR: final summary -> FINAL gate ----------
    def phase_pr(self, feature: str, prog: dict[str, Any]) -> None:
        d = self.fdir(feature)
        self.file_size_questions(feature, prog)
        self.tool_failure_questions(feature)
        self.write_final_facts(feature, prog)
        (d / "final_summary.md").unlink(missing_ok=True)
        (d / "final_brief.md").unlink(missing_ok=True)
        self.agent("PR", feature, prog, self.task_text("PR", feature))
        self.collect_questions(feature, "PR", "summary agent")
        self.check_final_brief(feature, prog)
        prog["code_map"] = self.code_map_state(feature, self.commit_code_map(feature))
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
        steps = d / "steps.md"
        facts = final_facts(
            feature,
            plan_steps(self.cfg, feature),
            prog,
            read_events(self.cfg, feature),
            self.assumptions(feature),
            gates.read_text(encoding="utf-8") if gates.is_file() else "(no final gate run)",
            oq.read_text(encoding="utf-8") if oq.is_file() else "",
            self.final_items(feature, prog),
            self.code_map_note(feature),
            feature_mode(self.cfg, feature),
            parse_causes(steps.read_text(encoding="utf-8") if steps.is_file() else ""),
        )
        (d / "final_facts.md").write_text(facts, encoding="utf-8")

    def final_items(self, feature: str, prog: dict[str, Any]) -> list[str]:
        """The numbered items of the final gate, in the order it shows them: parked work, the
        provisional steps (one item), then the open questions."""
        parked = [
            f"[parked] {p['label']}: {p.get('reason')}"
            + (f"; question: {p['question']}" if p.get("question") else "")
            for p in prog.get("parked") or []
        ]
        prov = [f"[provisional] {provisional_item(prog)}"] if prog.get("provisional") else []
        return [*parked, *prov, *self.open_questions(feature)]

    def must_ask(self, feature: str, prog: dict[str, Any]) -> set[int]:
        """Final-gate items the brief always asks: parked work and the provisional steps (the
        first items of final_items; they block the run until answered), and a failed check
        whose tool gave no result (only the human can say: again, or finish without it)."""
        n = len(prog.get("parked") or []) + (1 if prog.get("provisional") else 0)
        tool = {
            i
            for i, item in enumerate(self.final_items(feature, prog), 1)
            if item.startswith(f"[{GATES_WHO}] ")
        }
        return set(range(1, n + 1)) | tool

    def final_brief_sections(self, feature: str) -> dict[str, list[str]]:
        p = self.fdir(feature) / "final_brief.md"
        return briefs.sections(p.read_text(encoding="utf-8") if p.is_file() else "")

    def final_brief_plan(self, feature: str, prog: dict[str, Any]) -> briefs.BriefPlan:
        """The final gate's questions as the brief shows them (numbered 1..k), mapped to the
        gate items; the items the PR agent decided or left out."""
        return briefs.plan(
            self.final_brief_sections(feature),
            self.final_items(feature, prog),
            self.must_ask(feature, prog),
        )

    def check_final_brief(self, feature: str, prog: dict[str, Any]) -> None:
        """Not blocking: duplicates, closed items or notes asked as questions, questions with
        one option, gate items placed nowhere -> the PR agent rewrites final_brief.md once."""
        if not (self.fdir(feature) / "final_brief.md").is_file():
            return  # the gate brief falls back to generated lines
        n_items = len(self.final_items(feature, prog))
        problems = briefs.check_brief(
            self.final_brief_sections(feature), n_items, self.must_ask(feature, prog)
        )
        log_event(self.cfg, feature, "brief_check", problems=problems, rewrite=bool(problems))
        if not problems:
            return
        self.progress(feature, f"[PR] final brief: {len(problems)} problem(s), rewriting once")
        out = self.cfg.paths.out
        task = (
            f"Feature '{feature}': rewrite {out}/{feature}/final_brief.md only (the PR role's "
            "brief rules; the gate items: the end of "
            f"{out}/{feature}/final_facts.md, plus any you added to questions.md, numbered "
            f"after them). A check of the brief found:\n"
            + "\n".join(f"- {x}" for x in problems)
            + "\nFix these; keep the rest of the brief. Do not change final_summary.md or the "
            "code map."
        )
        self.agent("PR", feature, prog, task)
        self.collect_questions(feature, "PR", "summary agent")
        n_items = len(self.final_items(feature, prog))
        left = briefs.check_brief(
            self.final_brief_sections(feature), n_items, self.must_ask(feature, prog)
        )
        log_event(self.cfg, feature, "brief_check", problems=left, rewrite=False)
        if left:
            self.progress(feature, f"[PR] final brief: {len(left)} problem(s) left (not blocking)")

    def map_brief_answers(self, feature: str, prog: dict[str, Any], text: str) -> str:
        """answers.md as the chat wrote it (numbered as the brief showed the questions) ->
        numbered by gate item, with the answers the brief implies for the items it decided or
        left out; written back to answers.md (the agents read that file). Unchanged when the
        brief keeps the gate's numbers, or answers.md was mapped already."""
        if text.startswith(briefs.MAPPED_HEADER):
            return text
        p = self.final_brief_plan(feature, prog)
        if not p.remapped:
            return text
        mapped = briefs.expand_answers(
            p, parse_answer_items(text), self.final_items(feature, prog)
        ).strip()
        (self.fdir(feature) / "answers.md").write_text(mapped + "\n", encoding="utf-8")
        return mapped

    def code_map_note(self, feature: str) -> str:
        """For the summary agent: where the code map is and which new public types it lacks."""
        exists, missing = structure_check.missing_from_code_map(self.adapter, feature)
        if not exists:
            return (
                f"{self.cfg.code_map} does not exist yet: create it (format: the code map "
                "template in your role) with the modules this feature touched."
            )
        return f"{self.cfg.code_map}: update the sections this feature touched." + (
            f" New public types it does not name yet: {', '.join(missing)}." if missing else ""
        )

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
        prov = prog.get("provisional") or []
        if prov:
            state += f"; {len(prov)} provisional (code kept, test fixes proposed): " + ", ".join(
                p["label"] for p in prov
            )
        questions = self.open_questions(feature)
        if questions:
            state += f"; {len(questions)} open question(s)"
        if self.spec_diff(feature):
            state += "; spec changed from your answers (diff in final_summary.md)"
        rating = self.assess_risk(feature, prog)
        if rating.low and self.cfg.final_gate == "auto":
            self.auto_approve_final(feature, prog)
            return
        state += f"; risk {rating.level}"
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

    def assess_risk(self, feature: str, prog: dict[str, Any]) -> risk.Assessment:
        """Rate the finished feature (risk.py) from the run records and the change itself;
        kept in progress.json for the brief and logged, so the rating can be compared with
        the human's decision."""
        events = since_spec_approval(read_events(self.cfg, feature))
        rating = risk.assess(
            self.adapter,
            feature,
            prog,
            events,
            items=len(self.final_items(feature, prog)),
            assumptions=len(self.assumptions(feature)),
        )
        prog["risk"] = rating.as_dict()
        save_progress(self.cfg, feature, prog)
        log_event(self.cfg, feature, "risk", auto=self.cfg.final_gate == "auto", **rating.as_dict())
        return rating

    def auto_approve_final(self, feature: str, prog: dict[str, Any]) -> None:
        """[gates] final = "auto" and the engine rates the feature low risk: the final gate is
        passed without a stop (retro, then DONE). Nothing is pushed; the PR stays the human's."""
        log_event(
            self.cfg,
            feature,
            "human_gate",
            gate="final",
            approved=True,
            reason="low risk",
            auto=True,
            risk=risk.LOW,
        )
        self.progress(
            feature,
            "FINAL GATE: approved automatically (low risk: clean run, small change, coverage "
            "and mutation measured). The brief is kept; retro next.",
        )
        self.notify(f"agentflow: {feature} approved automatically", "Low risk; retro, then DONE.")
        self.advance(feature, prog, "RETRO")

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
        """A new spec approval restarts from the tests: the answer tags of the old one go."""
        for tag in (f"spec-update/{feature}", tests_update_tag(feature), knowledge_tag(feature)):
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
            notes.append(f"{ref}: new IDs get their tests from the test writer next")
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
        if su.get("amend"):  # final gate: the test writer applies answers that need tests
            prog["amend"] = {"ref": ref, "then": then}
            then = {"phase": "TESTS", "sub": "amend"}
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
        archive = d / "questions_archive.md"
        ri = build_retro_input(
            feature,
            read_events(self.cfg, feature),
            notes,
            self.assumptions(feature),
            archive.read_text(encoding="utf-8") if archive.is_file() else "",
        )
        # what earlier retros proposed: the retro names a problem it sees again
        earlier = backlog.retro_section(self.cfg)
        text = ri.text.rstrip("\n") + ("\n\n" + earlier if earlier else "\n")
        (d / "retro_input.md").write_text(text, encoding="utf-8")
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
        prop = self.fdir(feature) / "retro_proposals.md"
        again = backlog.note_repeats(
            self.cfg, feature, prop.read_text(encoding="utf-8") if prop.is_file() else ""
        )
        if again:
            self.progress(
                feature, f"[RETRO] seen again, counted in the backlog: {', '.join(again)}."
            )
            commit(self.cfg, f"{feature}: retro backlog, seen again: {', '.join(again)}")
        if not prog.get("metrics_written"):  # finish --no-retro wrote it already
            self.write_metrics(feature)
            prog["metrics_written"] = True
        self.progress(
            feature,
            f"\nRetro proposals: {self.cfg.paths.out}/{feature}/retro_proposals.md — "
            f"keep the ones you accept (agentflow keep {feature} ...); apply them in a normal "
            "session.",
        )
        self.advance(feature, prog, "DONE")

    def write_metrics(self, feature: str) -> None:
        ev = read_events(self.cfg, feature)
        row = [
            datetime.date.today().isoformat(),
            feature,
            *metric_counts(ev),
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

    def spec(self, feature: str, mode: str | None = None) -> int:
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
            folder = template_dir(mode or "feature")
            local = self.cfg.root / self.cfg.paths.specs / folder
            target.mkdir(parents=True)
            for name in ("requirements.md", "design.md", "plan.md"):
                src = local / name
                text = (
                    src.read_text(encoding="utf-8")
                    if src.is_file()
                    else package_text("templates", "specs", folder, name)
                )
                (target / name).write_text(text, encoding="utf-8")
        has = feature_mode(self.cfg, feature)
        if mode and mode != has:
            raise WorkflowError(
                f"{self.cfg.paths.specs}/{feature}/ is a {has} spec (its requirements.md says "
                f"so), not {mode}. Use the {has} flow, or delete the folder to start over."
            )
        set_phase(self.cfg, "SPEC")
        log_event(self.cfg, feature, "spec_started", mode=has)
        self.echo(f"SPEC phase set for {feature}" + (f" ({has})." if has != "feature" else "."))
        self.echo(
            f"Spec dialogue: /agentflow:feature {feature} in Claude Code (it runs this command "
            f"too); manual end of the phase: agentflow approve-spec {feature}"
        )
        return EXIT_OK

    def approve_spec(self, feature: str, force: bool = False) -> int:
        if not self.cfg.spec_dir(feature).is_dir():
            raise WorkflowError(
                f"No spec folder {self.cfg.paths.specs}/{feature}/ (run: agentflow spec {feature})"
            )
        word = spec_mode_line(self.cfg, feature)
        if word and word not in MODES:
            raise WorkflowError(
                f"{self.cfg.paths.specs}/{feature}/requirements.md says 'Mode: {word}'; "
                f"known modes: {', '.join(MODES)}."
            )
        found = spec_lint.problems(self.cfg, feature)
        if found:
            log_event(self.cfg, feature, "spec_check", problems=found, forced=force)
            listed = "\n".join(f"- {x}" for x in found)
            if not force:
                raise WorkflowError(
                    f"Spec check: {len(found)} problem(s); fix the spec, then approve again "
                    f"(or: agentflow approve-spec {feature} --force)\n{listed}"
                )
            self.echo(f"Spec check: {len(found)} problem(s), approved anyway (--force)\n{listed}")
        clear_phase(self.cfg)
        commit(self.cfg, f"{feature}: spec approved")
        git(self.cfg, "tag", "-f", f"spec-approved/{feature}")
        self.drop_spec_update_tag(feature)
        save_progress(self.cfg, feature, fresh_progress())
        log_event(self.cfg, feature, "spec_approved", mode=feature_mode(self.cfg, feature))
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
        if gate == "final":
            self.refuse_open_provisional(feature, prog)
        log_event(
            self.cfg,
            feature,
            "human_gate",
            gate=gate,
            approved=True,
            reason=None,
            # the engine's rating next to the human's decision (final gate; risk.py)
            **({"risk": (prog.get("risk") or {}).get("level")} if gate == "final" else {}),
        )
        if gate == "tests":
            self.pass_tests_gate(feature, prog)
            self.echo(f"Tests approved, tagged tests-approved/{feature}.")
        else:
            self.reapprove_spec(feature, prog, "final")
            self.advance(feature, prog, "RETRO")
            self.echo("Final gate approved; the next run does the retro, then DONE.")
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def refuse_open_provisional(self, feature: str, prog: dict[str, Any]) -> None:
        """Approving the final gate would leave the contested tests red: the proposed test fixes
        need an answer first (one answer approves them all)."""
        prov = prog.get("provisional") or []
        if prov:
            raise WorkflowError(
                f"{len(prov)} provisional step(s) wait for your answer to their proposed test "
                f"fixes ({', '.join(p['label'] for p in prov)}). Answer that item in "
                f"{self.cfg.paths.out}/{feature}/answers.md (e.g. 'Approved: apply all proposed "
                f"test fixes as written.'), then agentflow answer {feature}."
            )

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
        given = text
        if gate == "final":
            text = self.map_brief_answers(feature, prog, text)
        oq = d / "open_questions.md"
        open_q = oq.read_text(encoding="utf-8") if oq.is_file() else ""
        ref = self.answer_ref(feature)
        reply = f"### Answer {ref} (answers.md)\n{text}"
        # the numbered items as the gate showed them (final: parked first), so the retro can
        # pair every question with its own answer
        shown = split_questions(open_q)
        prov_item = provisional_item(prog) if gate == "final" else ""
        if gate == "final":
            shown = (
                [
                    f"[{p['label']}] {p.get('question') or p.get('reason')}"
                    for p in prog.get("parked") or []
                ]
                + ([prov_item] if prov_item else [])
                + shown
            )
        # `asked`: the question as the agent wrote it (the chat rewords it in plain words in
        # `Q:`, so the retro could not find the answers of reworded questions)
        items = [
            dict(it, q=it["q"] or asked, asked=asked)
            for it in parse_answer_items(text)
            for asked in [shown[it["n"] - 1] if 0 < it["n"] <= len(shown) else ""]
        ]
        logged: dict[str, Any] = {"ref": ref, "answer": text[:20000], "items": items}
        if given != text:
            logged["as_given"] = given[:20000]
        if gate == "final":
            # answers to the file-size questions: the structure fix run applies them (split,
            # or leave as is) before the final gates
            tag = f"[{STRUCTURE_WHO}] "
            shape = [
                f"{shown[it['n'] - 1].removeprefix(tag)}\n  The human's answer: {it['a']}"
                for it in parse_answer_items(text)
                if 0 < it["n"] <= len(shown) and shown[it["n"] - 1].startswith(tag) and it["a"]
            ]
            if shape:
                prog["structure_findings"] = [*(prog.get("structure_findings") or []), *shape]
                # answered once: not asked again in a later round
                prog["size_answered"] = [
                    *(prog.get("size_answered") or []),
                    *(x.split(" ", 1)[0] for x in shape),
                ]
        if gate == "tests":
            self.archive_questions(feature, "tests gate", open_q, reply)
            log_event(self.cfg, feature, "answer", gate="tests", **logged)
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
            if not parked and not prov_item:
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
                    **logged,
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
                        # a file-size answer needs the structure run even without a spec change
                        "unchanged": {"phase": "REVIEW", "sub": "final"}
                        if prog.get("structure_findings")
                        else {"phase": "PR", "sub": None},
                        "feedback": "",
                        "amend": True,
                    },
                )
                save_progress(self.cfg, feature, prog)
                self.echo(
                    f"Answers recorded ({ref}); the next run writes them into the spec, the test "
                    "writer applies the ones that need a test change; if the spec changed, the "
                    "reviewer checks the code against it again (changed tests: the implementer "
                    "makes them pass); then the final gate again."
                )
                self.echo(f"Next: agentflow run {feature}")
                return EXIT_OK
            questions = "\n".join(
                [f"- [{p['label']}] {p.get('question') or p.get('reason')}" for p in parked]
                + ([f"- {prov_item}"] if prov_item else [])
            )
            self.archive_questions(feature, "final gate: parked", questions + "\n" + open_q, reply)
            log_event(
                self.cfg,
                feature,
                "answer",
                gate="final",
                parked=[p["label"] for p in parked],
                **logged,
            )
            steps = [p for p in parked if p.get("kind") == "step"]
            # provisional steps: IMPLEMENT settles them first (contested tests pass now, or the
            # provisional code is reverted and the step redone)
            then = (
                {"phase": "IMPLEMENT", "sub": None}
                if steps or prov_item
                else {"phase": "REVIEW", "sub": "fix"}
            )
            prog.update(
                waiting=None,
                redo=parked,
                parked=[],
                redo_mode=True,
                final_repair=[],  # a new final-gate cycle: one repair per owner again
                attempt=0,
                phase="SPEC_UPDATE",
                sub=None,
                spec_update={
                    "gate": "final",
                    "ref": ref,
                    "then": then,
                    "feedback": "",
                    "amend": True,
                },
            )
            save_progress(self.cfg, feature, prog)
            redone = [p["label"] for p in parked]
            self.echo(
                f"Answers recorded ({ref}); the next run writes them into the spec, the test "
                "writer applies the ones that need a test change"
                + (
                    " (the approved test fixes of the provisional steps), the contested tests "
                    "must then pass (else that step's code is reverted and redone)"
                    if prov_item
                    else ""
                )
                + (f", then only the parked items are redone ({', '.join(redone)})" * bool(redone))
                + ", then the final gate again."
            )
        self.echo(f"Next: agentflow run {feature}")
        return EXIT_OK

    def keep(self, feature: str, picks: list[str]) -> int:
        """The human's pick of the retro's proposals -> the retro backlog (tracked, committed):
        picked ones open, the others skipped; the retro's text is kept beside it."""
        try:
            kept = backlog.keep(self.cfg, feature, picks)
        except backlog.BacklogError as e:
            raise WorkflowError(str(e)) from e
        self.commit_backlog(
            f"{feature}: retro backlog ({', '.join(i.id for i in kept.opened) or 'none kept'})"
        )
        log_event(
            self.cfg,
            feature,
            "retro_kept",
            kept=[i.id for i in kept.opened],
            skipped=[i.id for i in kept.skipped],
            folded=kept.folded,
        )
        rel = self.rel(backlog.path(self.cfg))
        for i in kept.opened:
            self.echo(f"Kept: {i.line()}")
        if kept.skipped:
            ids = ", ".join(i.id for i in kept.skipped)
            self.echo(f"Not kept (counted if they come back): {ids}")
        self.echo(f"Backlog: {rel} (agentflow backlog; applied one: agentflow backlog done B<n>)")
        return EXIT_OK

    def commit_backlog(self, message: str) -> None:
        """Commit the backlog file and the kept retro texts, nothing else."""
        paths = [self.rel(backlog.path(self.cfg))]
        retros = backlog.retro_copy(self.cfg, "x").parent
        if retros.is_dir():
            paths.append(self.rel(retros))
        git(self.cfg, "add", "--", *paths)
        if git(self.cfg, "diff", "--cached", "--name-only", "--", *paths).strip():
            git(self.cfg, "commit", "-q", "-m", message, "--", *paths)

    def backlog_cmd(self, action: str, ids: list[str], note: str, every: bool, scope: str) -> int:
        """`agentflow backlog [list | show | done | drop | reopen]`."""
        try:
            if action == "list":
                lines = backlog.listing(self.cfg, every, scope or None)
                rel = self.rel(backlog.path(self.cfg))
                what = "proposal(s)" if every else "open proposal(s)"
                self.echo(f"{len(lines)} {what} in {rel}" + (":" if lines else "."))
                for line in lines:
                    self.echo(f"- {line}")
            elif action == "show":
                for item_id in ids or [""]:
                    self.echo(backlog.show(self.cfg, item_id))
            else:
                status = {"done": "applied", "drop": "dropped", "reopen": "open"}[action]
                changed = backlog.set_status(self.cfg, ids, status, note)
                names = ", ".join(i.id for i in changed)
                self.commit_backlog(f"retro backlog: {names} {status}")
                self.echo(f"{names}: {status}" + (f" ({note})" if note else ""))
        except backlog.BacklogError as e:
            raise WorkflowError(str(e)) from e
        return EXIT_OK

    def learn(self, feature: str, picks: list[str]) -> int:
        """Append the retro's project-knowledge proposals the human accepted (`K1 K3`, or
        `all`) to the knowledge file, commit it and tag the commit knowledge-update/<f> (the
        frame diff allows exactly that change; agents cannot edit the file)."""
        prop = self.fdir(feature) / "retro_proposals.md"
        items = knowledge_items(prop.read_text(encoding="utf-8") if prop.is_file() else "")
        if not items:
            raise WorkflowError(
                f"No project-knowledge proposals (K1, ...) in {self.rel(prop)}: run the retro."
            )
        wanted = {p.strip().upper().lstrip("K") for x in picks for p in x.split(",") if p.strip()}
        chosen = items if "ALL" in wanted else [i for i in items if str(i[0]) in wanted]
        unknown = sorted(wanted - {str(i[0]) for i in items} - {"ALL"})
        if unknown or not chosen:
            have = ", ".join(f"K{n}" for n, _, _ in items)
            raise WorkflowError(f"Pick from {have} (or all); unknown: {', '.join(unknown) or '-'}")
        path = self.cfg.root / self.cfg.knowledge
        text = (
            path.read_text(encoding="utf-8")
            if path.is_file()
            else package_text("templates", "project.md")
        )
        for _, section, fact in chosen:
            text = add_knowledge(text, section, f"- {fact} ({feature})")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        names = ", ".join(f"K{n}" for n, _, _ in chosen)
        git(self.cfg, "add", "--", self.cfg.knowledge)
        git(
            self.cfg,
            "commit",
            "-q",
            "-m",
            f"{feature}: project knowledge from the retro ({names})",
            "--",
            self.cfg.knowledge,
        )
        git(self.cfg, "tag", "-f", knowledge_tag(feature))
        log_event(self.cfg, feature, "knowledge_added", items=[list(i) for i in chosen])
        self.echo(
            f"Added {names} to {self.cfg.knowledge} (committed, tagged {knowledge_tag(feature)})."
        )
        return EXIT_OK

    def finish(self, feature: str) -> int:
        """FINAL gate -> DONE without the retro (the metrics row is still written)."""
        prog = self._require_gate(feature, ("final",))
        self.refuse_open_provisional(feature, prog)
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
