# agentflow

Spec-driven, test-first workflow orchestrator for Claude Code (package + plugin). Design:
docs/AGENTIC_WORKFLOW_SETUP.md (Part 8 = target architecture).

Three human gates, forward only: **SPEC -> TESTS -> FINAL**. Everything between them runs as
fresh headless `claude -p` runs inside a frame of deterministic checks (red-against-stubs,
traceability, test smells, frozen tests via a tag-based frame diff, lint/types, diff coverage,
mutation score) and never stops to ask.

| Phase | Agent(s) | Ends with |
|---|---|---|
| SPEC | interactive `/spec` session | **gate 1**: `agentflow approve-spec` -> tag `spec-approved/<f>` |
| TESTS | test writer -> mechanical checks -> **test reviewer** (`roles/test_review.md`); objections go back to the writer, at most `[agents] test_review_rounds` (2) times | **gate 2**: `out/<f>/tests_gate.md` (ID -> tests -> what it checks, check results, reviewer verdict + remaining objections, all open questions batched) -> approve (tag `tests-approved/<f>`) / reject / answer. `[gates] tests = "auto"` passes it without a stop when everything is clean |
| IMPLEMENT | implementer, one fresh run per plan step | step gates green -> commit. Ambiguity: assume from the spec, log it in `steps.md`. No reasonable assumption, or `max_attempts` failed attempts: the step is **parked** (its code set aside as `out/<f>/parked/*.diff`), the run goes on |
| REVIEW | adversarial reviewer -> **triage** by the test reviewer -> fixes | valid findings fixed (or parked), invalid ones removed with a reason; final gates |
| PR | summary agent (`roles/pr.md`) | **gate 3**: `out/<f>/final_summary.md` -> approve (retro) / `finish --no-retro` / answer the parked questions (redo only those) |
| RETRO | retro agent | `retro_proposals.md` + metrics row -> DONE |

## Parts

| Part | Where | Docs |
|---|---|---|
| Python package + CLI `agentflow` | `src/agentflow/` | this file, docs/adapters.md, docs/claude-cli-notes.md |
| Claude Code plugin (`/spec`, hooks) | `plugin/` | docs/plugin.md |

## Daily use

```
agentflow init [--language python|dotnet]   # agentflow.toml, specs/_template/, .gitignore lines
git checkout -b feature/<f>
agentflow spec <f>                          # then in claude: /spec <f>
agentflow approve-spec <f>                  # tag spec-approved/<f>
agentflow run <f>                           # runs to the next gate (tests, final) or DONE
agentflow status <f>                        # which gate is waiting, and what to do
agentflow approve <f>                       # tests gate -> implement; final gate -> retro
agentflow reject <f> --reason "..."         # tests gate: writer + test reviewer re-run with it
agentflow answer <f>                        # after writing out/<f>/answers.md: tests gate ->
                                            # writer + reviewer re-run; final gate -> the next
                                            # run redoes only the parked items
agentflow finish <f> --no-retro             # final gate -> DONE without the retro
agentflow gates <f> --scope step|final      # the frame checks (also used in CI)
agentflow spec-brief <f>                    # conventions for the SPEC session (used by /spec)
agentflow unlock [--force]                  # clear a stale .phase/.reqs left by a killed run
```

`run` never waits on the keyboard. It advances TESTS -> IMPLEMENT (per plan step) -> REVIEW ->
PR -> FINAL -> RETRO -> DONE and stops only at the tests gate and the final gate, writing
`out/<f>/progress.json` (`waiting: {kind: gate, gate: tests|final}`) and printing what to read.
Exit codes: 0 done/ok, 1 error, 10 human gate (tests or final; the output and `status` name it),
130 interrupted. Run `agentflow run <f>` again after `approve` / `reject` / `answer`; it also
resumes after an interruption. The spec itself must change: edit it, `agentflow approve-spec <f>`
(resets the progress to TESTS), `run`.

Between the gates nothing stops:
- Questions of the test writer and the test reviewer (`questions.md`) are collected in
  `open_questions.md` and shown together at the tests gate.
- The implementer assumes from the spec and logs each assumption in `steps.md`
  (`- ASSUMPTION [<spec ID>]: ...`). Only when no reasonable assumption is possible it writes
  `park.md` and stops (the Stop hook lets it); a step that fails `max_attempts` times is parked
  too (reason, failing checks, attempts). The parked step's uncommitted source changes are saved
  as `out/<f>/parked/<step>.diff` and the sources restored to the last green commit; later steps
  continue (their check leaves the parked step's IDs out; a declared `Depends on: Step N` in
  plan.md is noted in the final summary).
- Review findings (failing reviewer tests) are triaged against the spec by the test reviewer:
  VALID stays and goes to the fix loop, INVALID is deleted with a one-line reason
  (`triage.md`, `triage` event). Fixes that fail `max_attempts` times are parked.
- The final summary (`final_summary.md`) is written from `final_facts.md` (generated from the
  run records); agentflow appends the cost / time / turns table per phase.

Stopping a run: Ctrl+C, Ctrl+Break or SIGTERM kill the running agent's whole process tree (each
agent runs in its own process group; on Windows also in a kill-on-close job object, so even a
`taskkill /F` of agentflow leaves no orphan `claude`), clear `.phase`/`.reqs`, record an
`interrupted` event (+ `progress.json` `interrupted`; the attempt is not counted) and exit 130.
`out/.run.lock` (pid, feature, start) marks a running orchestrator: a second `run` is refused,
a lock whose pid is gone is taken over (and the interruption recorded). `agentflow status` warns
about a phase lock without a running orchestrator (`SPEC` excepted: `agentflow spec` sets it for
the interactive session); `agentflow unlock` clears it.

Turn caps: `--max-turns` per phase = `[phases.<P>] max_turns`, else `[agents] max_turns`, else
TESTS 50, TEST_REVIEW 30, IMPLEMENT 80, REVIEW 40, TRIAGE 30, PR 30, RETRO 30 (from the FCSim
smoke run: RETRO used 48 turns, mostly denied shell probing). A run that stops at the cap is
logged (`max_turns` event). IMPLEMENT: the step gate decides (a failed gate is a normal failed
attempt whose feedback names the cap); other phases are re-run once with a note; if that run
stops at the cap too, it is logged (`escalation` event, retro E item) and the run continues with
what the agent wrote.

Final gates (`--scope final`): coverage + diff coverage and mutation testing run only on the
feature's tests (tagged with any spec ID of the feature, or under `[paths] review_tests`); the
whole suite runs once more without coverage instrumentation (`regression` check). Coverage over a
large suite is too slow per feature (FCSim: > 30 min vs 142 s feature-scoped). `final_gates.md`
starts with the scope of each check. `[gates] coverage_scope` / `mutation_scope = "suite"` bring
back the whole-suite runs. A run cut short (timeout `[gates] regression_timeout` 3600 s /
`coverage_timeout` 1800 s / `mutation_timeout` 3600 s, crashed or aborted test host, killed,
missing report or coverage file) is `FAIL` with `ERROR: ... incomplete (<reason>)`, never a
coverage or mutation figure. Diff coverage and mutation score lines show the figures under
PASS/FAIL.

Questions, assumptions, parked items and failed retries feed the retro. Every step-gate run is
logged in `out/events.jsonl` (`gate`: phase, step, attempt, `failed` check names, trimmed
`excerpt`, seconds), as are questions (phase, who, text), test reviews (round, verdict,
objections), the auto tests gate (`tests_gate_auto`: passed, reasons), answers (gate, text; also
appended to `questions_archive.md` under the questions), parked items (`parked`: step/finding,
reason, question, failing checks, attempts, diff), redo results, review findings and their triage,
final gates (`failed`, `skipped`, seconds per check), permission denials (`permission_denials`:
phase, step, tool + command/path of every call the permission check denied, from the `claude -p`
JSON), turn-cap hits (`max_turns`, and `escalation` when the re-run hit it too) and
interruptions. Before the RETRO agent runs, agentflow writes `out/<f>/retro_input.md`: counts,
then every question (Q1..), failed attempt (F1..), escalation (E1..), human rejection (R1..),
review finding with its triage verdict (V1..), final-gate failure (G1..), permission denial
(D1..), assumption (A1..) and parked item (P1..).
The retro must mention every ID in `retro_proposals.md` (a proposal, or "Not acted on" with a
one-line reason); if IDs are missing it is re-run once with them as feedback, then a warning is
printed and logged (`retro_coverage` event).

`metrics/workflow_metrics.csv` (one row per finished feature):

| Column | Meaning |
|---|---|
| `human_rejections` | `reject` at the tests gate |
| `escalations` | agent runs that stopped at the turn cap twice |
| `questions` | question rounds (`questions.md` files; one round can hold several questions) |
| `gate_failures` | failed step-gate runs = failed implementation attempts (incl. review fixes; parking doesn't count). This is the "failed attempts" count; there is no separate column |
| `cost_usd` | sum of `total_cost_usd` of all agent runs |
| `parked` | parked steps / review fixes (each park, incl. re-parks after a redo) |
| `assumptions` | `- ASSUMPTION [...]` lines in `steps.md` |

Hooks (called by the plugin): `agentflow hook pre-tool-use` (phase-based write protection) and
`agentflow hook stop` (IMPLEMENT can't stop while the step's tests fail, unless it parked: an
`out/*/park.md` exists). Both read the hook
JSON on stdin, exit 0 silently without `agentflow.toml` or `.phase`, and block with exit 2.

Configuration: `agentflow.toml` (all keys optional; see the commented template written by
`init`). `[agents] claude` or env `AGENTFLOW_CLAUDE` selects the claude executable;
`[agents] test_review_rounds` (2) bounds the writer <-> reviewer loop; `[gates] tests = "human"`
(default) or `"auto"` (no stop when all checks pass, the reviewer has no objections and no
question is open; otherwise it stops as in human mode).

Python projects: add agentflow (pinned) to the dev dependencies; its pytest plugin provides the
`req` marker and `--req ID,ID` selection. mutmut needs fork, so on Windows the mutation gate runs
through WSL (`[gates] mutation_runner = "auto"`, default; `[env] wsl_distro` picks the
distribution) in a WSL-side venv with the project's dependencies. Install once inside WSL:
`curl -LsSf https://astral.sh/uv/install.sh | sh` (or `sudo apt install python3-venv`). Without
WSL the gate is SKIPPED with the reason. Details: docs/adapters.md.

.NET projects (xUnit, `language = "dotnet"`, detected from a root `*.sln`/`*.slnx`/`*.csproj`):
install agentflow with the `dotnet` extra (`pip install "agentflow[dotnet]"`, brings diff-cover)
and Stryker.NET (`dotnet tool install -g dotnet-stryker`). Tag tests with
`[Trait("Req", "XXX-R-001")]`; selection uses `dotnet test --filter "(Req=A|Req=B)&(<base_filter>)"`.
Put `[paths] review_tests` inside a test project folder and set the `[dotnet]` keys
(`base_filter`, `test_projects`, `configuration = "Release"`, ...). Details and the FCSim notes:
docs/adapters.md.

## Development

```
uv sync
uv run pytest          # unit + end-to-end tests (fake claude, no API calls), ~3 min
uv run pytest -m slow  # real toolchains: .NET e2e (dotnet SDK) ~1.5 min, mutmut via WSL ~15 s
uv run pytest -m "slow or not slow"   # everything
uv run ruff check src tests ; uv run ruff format --check src tests ; uv run mypy
```
