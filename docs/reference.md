# agentflow reference

Everything in detail: the phases, the chat flow, project knowledge, the CLI, gates, retro,
hooks and the language adapters. The short version is the [README](../README.md).

## All commands

In the chat you only use the four skills; they call the CLI for you. The CLI works the same from
a terminal.

| Command | What it means | When |
|---|---|---|
| `/agentflow:setup` | set up agentflow in this repo (draft config + project facts, questions, doctor) | once per repo |
| `/agentflow:feature <f>` | start or continue feature `<f>`: spec dialogue, run, gates, wrap-up | each feature |
| `/agentflow:resume [f]` | reattach the chat to a running or waiting feature | after a restart |
| `/agentflow:status [f]` | where features stand; tag cleanup after the PR is merged | any time |
| `agentflow init` | write the starter files (config, spec templates, project facts, .gitignore lines) | once per repo |
| `agentflow doctor [--full]` | check the setup without agents; `--full` also runs the whole test suite | after setup or config changes |
| `agentflow spec <f>` | create the branch `feat/<f>` and open the spec phase | start of a feature |
| `agentflow approve-spec <f>` | freeze the spec ("spec OK"); restarts the feature if the spec changed later | gate 1 |
| `agentflow run <f>` | run the agents until the next human gate or DONE | after every decision |
| `agentflow watch <f>` | follow the progress lines of a run | while it runs |
| `agentflow status [f] [--brief]` | state of one or all features | any time |
| `agentflow gate-summary <f>` | the digest of the waiting gate | at a gate |
| `agentflow approve <f>` | accept the gate: final gate -> retro, then DONE (tests gate -> implement) | at a gate |
| `agentflow answer <f>` | apply your answers in `out/<f>/answers.md` (spec, tests, redo of parked work) | at a gate |
| `agentflow reject <f> --reason "..."` | send the tests back to the test writer (only with `[gates] tests = "human"`) | tests gate |
| `agentflow finish <f> --no-retro` | accept the final gate and skip the retro | final gate |
| `agentflow learn <f> K1 [K3 ...\|all]` | keep the project facts the retro proposed (into `.agentflow/project.md`) | after the retro |
| `agentflow cleanup <f> [--tags]` | delete the run files; `--tags` also the feature's git tags (after the merge) | after DONE |
| `agentflow gates <f> --scope step\|final` | run the checks by hand (also used in CI) | debugging, CI |
| `agentflow spec-brief <f>` | the project's spec conventions (the feature skill reads it) | internal |
| `agentflow protocol` | the chat run protocol (the skills read it) | internal |
| `agentflow unlock [--force]` | clear a lock left by a killed run | after a crash |
| `agentflow hook ...` | the Claude Code hooks (called by the plugin) | internal |

Exit codes: 0 done, 1 error, 10 waiting at a gate, 130 interrupted (run again to resume).

## Phases

Two human gates, forward only: **SPEC -> FINAL** (the test reviewer checks the tests against the
spec; its open points and all questions come to the final gate; `[gates] tests = "human"` adds
a stop at the tests gate). Everything between them runs as
fresh headless `claude -p` runs inside a frame of deterministic checks (red-against-stubs,
traceability, test smells, frozen tests via a tag-based frame diff, lint/types, diff coverage,
mutation score) and never stops to ask.

| Phase | Agent(s) | Ends with |
|---|---|---|
| SPEC | spec dialogue in the chat (`/agentflow:feature <f>`) | **gate 1**: `agentflow approve-spec` -> tag `spec-approved/<f>` |
| TESTS | test writer -> mechanical checks -> **test reviewer** (`roles/test_review.md`); objections go back to the writer, at most `[agents] test_review_rounds` (2) times | **gate 2**: `out/<f>/tests_gate.md` (ID -> tests -> what it checks, check results, reviewer verdict + remaining objections, all open questions batched) -> approve (tag `tests-approved/<f>`) / reject / answer (answers go into the spec first: SPEC_UPDATE). `[gates] tests = "auto"` passes it without a stop when everything is clean |
| IMPLEMENT | implementer, one fresh run per plan step | step gates green -> commit. Ambiguity: assume from the spec, log it in `steps.md`. No reasonable assumption, or `max_attempts` failed attempts: the step is **parked** (its code set aside as `out/<f>/parked/*.diff`), the run goes on |
| REVIEW | adversarial reviewer -> **triage** by the test reviewer -> fixes | valid findings fixed (or parked), invalid ones removed with a reason; final gates |
| PR | summary agent (`roles/pr.md`) | **gate 3**: `out/<f>/final_summary.md` -> approve (retro) / `finish --no-retro` / answer the parked questions (into the spec, then redo only those) |
| RETRO | retro agent | `retro_proposals.md` + metrics row -> DONE |

## Parts

| Part | Where | Docs |
|---|---|---|
| Python package + CLI `agentflow` | `src/agentflow/` | this file, docs/adapters.md, docs/claude-cli-notes.md |
| Claude Code plugin (`/agentflow:setup`, `:feature`, `:resume`, `:status`, hooks) | `plugin/` | docs/plugin.md |

## Daily use: from the Claude Code chat

Once per repository: `/agentflow:setup` in the chat (it reads the repo, drafts agentflow.toml and
`.agentflow/project.md`, asks one batch of questions, runs `agentflow init` and `agentflow
doctor`, and offers a small smoke feature), or by hand `agentflow init [--language
python|dotnet|generic]` (agentflow.toml, spec templates, `.agentflow/project.md`, .gitignore
lines; commit them) and `agentflow doctor` (checks config, git, the claude CLI, the tools, the
gate commands and the test selection by spec ID, without agents; `--full` also times the
whole suite). Any language without a built-in adapter uses the generic adapter: test, coverage,
mutation and format commands with placeholders in `[generic]` (docs/adapters.md), no agentflow
code. Also the plugin installed (docs/plugin.md), and an allow rule so the
skills can run the CLI without a prompt after the first turn (an `allowed-tools` grant of a skill
only lasts for the turn it was invoked in): `"Bash(agentflow *)"` and `"PowerShell(agentflow *)"`
under `permissions.allow` in `.claude/settings.local.json` (or answer the first prompt with
"always allow").

Then you only type requirements, answers and gate decisions:

1. `/agentflow:feature <name>`: creates the branch `feat/<name>` from `[git] base` (`main`) in
   this checkout (refused with uncommitted changes to tracked files; an existing branch is used
   only if it is this feature's), sets the SPEC phase and starts the spec dialogue: clarifying
   questions, requirements/design/plan, interface stubs.
2. "Spec OK": the skill approves the spec, starts `agentflow run` in the background and shows the
   live view: one line per phase start/end, check result, failed attempt, assumption, park and
   gate, each with the cost so far (~10-30 lines per feature). The chat stays usable.
3. At the final gate (and the tests gate, if `[gates] tests = "human"`) the chat shows a digest
   (`agentflow gate-summary`: checks, reviewer verdict, spec IDs -> tests, numbered questions /
   parked items, spec diff, costs), Windows shows a toast, and Claude Code waits for you. Reply
   in plain words: "ok" / "done" (approve: retro, then DONE), "no retro", answers to the
   numbered items, "reject: <why>" (tests gate only). The skill runs the matching command and
   continues the run in the background.
4. DONE: the skill lists the retro proposals, offers the final summary as the PR description
   (`gh pr create`, only on your yes; nothing is pushed without it) and cleans up the run files
   (`agentflow cleanup`; `specs/<name>/` and the metrics row stay). After the PR is merged,
   `/agentflow:status <name>` offers removing the feature's tags.

After a session restart: `/agentflow:resume [name]` reattaches (live view, the waiting gate or the
wrap-up). `/agentflow:status` lists the features and where they stand.

## Project knowledge

`.agentflow/project.md` (`[project] knowledge`; `agentflow init` creates it) holds what no
single spec repeats: architecture rules, what must never change (hashes, save formats,
public APIs), test-harness facts (slow tests, how fixtures and seeds are made, scan
commands), tools, off-limits. The spec dialogue gets it through `agentflow spec-brief`;
the test writer, test reviewer, implementer, reviewer, spec updater, diagnosis and retro
get it in their instructions (up to 300 lines; keep it short). Agents cannot edit it. The
retro ends with a "Project knowledge" section (`- K1 [<section>]: <fact>`); at DONE the
chat shows them and `agentflow learn <f> K1 K3` (or `all`) appends the ones you pick under
their section, commits and tags `knowledge-update/<f>` (the frame diff allows that
change). The engine stays generic; each project's knowledge grows with its runs.

## CLI (the engine; manual fallback)

The skills only call these commands; everything works the same from a terminal.

```
agentflow init [--language python|dotnet|generic]  # agentflow.toml, specs/_template/,
                                            # .agentflow/project.md, .gitignore lines
agentflow doctor [--full]                   # check the setup without agents
agentflow spec <f>                          # branch feat/<f> from [git] base + SPEC phase
agentflow approve-spec <f>                  # tag spec-approved/<f>
agentflow run <f>                           # runs to the next gate (tests, final) or DONE;
                                            # progress lines on stdout + out/<f>/progress.log
agentflow watch <f>                         # live view: new progress lines until the run ends
agentflow status [<f>] [--brief]            # all features / one; --brief = short, for the chat
agentflow gate-summary <f>                  # chat digest of the waiting gate
agentflow approve <f>                       # tests gate -> implement; final gate -> retro
agentflow reject <f> --reason "..."         # tests gate: writer + test reviewer re-run with it
agentflow answer <f>                        # after writing out/<f>/answers.md: the next run
                                            # writes them into the spec (SPEC_UPDATE), then
                                            # tests gate -> writer + reviewer re-run; final
                                            # gate -> the test writer applies answers that need
                                            # a test change, then only the parked items redo
agentflow finish <f> --no-retro             # final gate -> DONE without the retro
agentflow learn <f> K1 [K3 ...|all]         # add accepted retro knowledge to the
                                            # project knowledge file (committed)
agentflow cleanup <f> [--tags] [--force]    # DONE: delete out/<f>/ + its run logs; --tags: also
                                            # spec-approved/tests-approved/spec-update/
                                            # tests-update tags
agentflow gates <f> --scope step|final      # the frame checks (also used in CI)
agentflow spec-brief <f>                    # conventions for the spec dialogue (skill injects it)
agentflow protocol                          # the chat run protocol (skills inject it)
agentflow unlock [--force]                  # clear a stale .phase/.reqs left by a killed run
```

Branch per feature (`[git]`): `spec` creates `<branch_prefix><f>` (`feat/`) from `base` (`main`)
and marks it (`git config branch.<b>.agentflowFeature`); it switches to an existing branch only
if it carries that mark or the feature's spec folder, refuses with uncommitted changes to tracked
files (untracked files are fine) and while a run is active, and only notes an already approved
spec (no SPEC phase). `[git] branch = false` stays on the current branch. A checkout whose
agentflow.toml is not on `main` yet sets `base` to the branch that has it.

Progress lines (`HH:MM:SS <what> [$cost since the spec approval]`, max 160 chars) go to stdout
and `out/<f>/progress.log`; `watch` prints the new ones (it remembers its position in
`progress.cursor`, so a re-armed watch neither repeats nor skips) and exits once no run of the
feature holds `out/.run.lock` (or none appears within 30 s). Notifications (`[notify] toast =
true`, Windows only; env `AGENTFLOW_TOAST=0` turns them off): a toast at each gate, at DONE and
on an error, through PowerShell 5.1's WinRT ToastNotificationManager (no modules); fire and
forget, silent if it fails. `cleanup` keeps `specs/<f>/`, the metrics row and the shared
`out/events.jsonl`, refuses while the feature's run is active or before DONE (`--force`), and
prints the `git push origin --delete` line for tags that were pushed.

`run` never waits on the keyboard. It advances TESTS -> IMPLEMENT (per plan step) -> REVIEW ->
PR -> FINAL -> RETRO -> DONE and stops only at the final gate (and the tests gate with
`[gates] tests = "human"`, or `"auto"` when it isn't clean), writing
`out/<f>/progress.json` (`waiting: {kind: gate, gate: tests|final}`) and printing what to read.
Exit codes: 0 done/ok, 1 error, 10 human gate (tests or final; the output and `status` name it),
130 interrupted. Run `agentflow run <f>` again after `approve` / `reject` / `answer`; it also
resumes after an interruption. The spec itself must change: edit it, `agentflow approve-spec <f>`
(resets the progress to TESTS), `run`.

Between the gates nothing stops:
- Questions of the test writer and the test reviewer (`questions.md`) are collected in
  `open_questions.md` and shown together at the next gate (by default the final gate, with the
  test reviewer's objections still open and failed test checks). Answers there go into the spec
  and, where they need it, into the tests (below); if the spec changes, the reviewer checks the
  code against it again, then the final gate comes again.
- Pinned test data (a seed, a recording) gets a fixture precondition test (`test_fixture_*` /
  `Fixture_*`) that shows, with code that exists before the feature, that the data has the
  situation the tests need. The red check expects these to PASS against the stubs (all other
  tests must fail), so data picked wrong shows up before any implementation. Checks over cases
  picked from data must assert at least one case: the smell scan notes loops / `all()` over
  filtered cases without a visible non-empty check (C# `.Where(...)`, Python comprehensions with
  `if` / `filter()`). These are NOTEs for the test reviewer, not failures (a heuristic: most
  hits in an existing suite are fine), and every smell counts only in the test files the
  feature changed.
- The implementer assumes from the spec and logs each assumption in its step note (written with
  the Write tool to `out/<f>/notes/<step>.md`; agentflow appends it to `steps.md`:
  `- ASSUMPTION [<spec ID>]: ...`). Only when no reasonable assumption is possible it writes
  `park.md` and stops (the Stop hook lets it); a step that fails `max_attempts` times is parked
  too (reason, failing checks, attempts). The parked step's uncommitted source changes are saved
  as `out/<f>/parked/<step>.diff` and the sources restored to the last green commit; later steps
  continue (their check leaves the parked step's IDs out, and their task says not to build
  them). A step with `Depends on: Step N` (plan.md) on a parked step waits: parked without a
  run, redone after it. A step gate whose only failures are lint / format findings in files the
  implementer may not edit (frozen tests) parks at once ("unfixable in IMPLEMENT") instead of
  using up its attempts.
- When the same checks fail `[agents] diagnose_after` attempts in a row (default 2), a
  diagnosis run (Opus, `roles/diagnosis.md`) reads everything, runs the tests and changes
  nothing: it writes the root cause, the fix owner (`src` / `tests` / `spec`) and one
  proposed fix (`out/<f>/diagnoses/<item>.md`, `diagnosis` event). `src`: the proposal goes
  to the implementer's next attempt (it always gets that attempt). `tests` / `spec`: the item
  parks at once with the proposal as its question; at the final gate one reply approves it,
  and the redo sends it to the phase that owns the file (spec updater, test writer,
  implementer).
- Review findings (failing reviewer tests) are triaged against the spec by the test reviewer:
  VALID stays and goes to the fix loop, INVALID is deleted with a one-line reason
  (`triage.md`, `triage` event). Fixes that fail `max_attempts` times are parked. The reviewer
  is told the parked steps' IDs; a valid finding whose test carries only such IDs is DEFERRED:
  no review-fix run builds the parked step, its redo makes the test pass.
- Every agent task starts with a short rule (look around with Read / Glob / Grep, write with
  Write / Edit, the shell only for the named commands) and an orientation block: the files in
  `specs/<f>/` and `out/<f>/` and `git log -3 --stat`, which agents otherwise fetched with
  chained shell calls that the run denies.
- The final summary (`final_summary.md`) is written from `final_facts.md` (generated from the
  run records); agentflow appends the cost / time / turns table per phase.

Answers go into the spec (the spec stays the source of truth): `agentflow answer` gives the answers
a reference `ANS-<n>` (in `questions_archive.md` and the `answer` event), and the next `run` starts
with one SPEC_UPDATE agent run (`roles/spec_update.md`) that writes them into
`specs/<f>/requirements.md` / `design.md` (and a new ID into the `Covers:` line of `plan.md`),
each change marked `(answer ANS-<n>)`. agentflow then undoes edits to any other file under the
specs folder, reverts the whole update if an ID defined in requirements.md disappeared (IDs are
never renumbered; a new case gets a new ID, so traceability asks for its tests), and commits it
(`<f>: spec updated from answers ANS-<n>`, `spec_update` event: files, new IDs, lost IDs, undone
paths). The diff since `spec-approved/<f>` is shown in `tests_gate.md` (section "Spec changes from
your answers") or appended to `final_summary.md`; approving that gate (or `finish --no-retro`)
moves the tag `spec-approved/<f>` to it (`spec_reapproved` event). `[gates] tests = "auto"` never
passes a gate with such a diff. At the final gate the update comes after the tests were approved:
agentflow tags it `spec-update/<f>`, and the frame diff allows exactly the spec files that commit
changed, as long as their content equals the tagged one (any further spec edit fails the frame
again). Then the test writer runs once more (`TESTS_AMEND` task): it applies the answers that
need a test change (fixtures, seeds, expected values, tests for new IDs, a review test an answer
names) and changes nothing else; the implementer may not touch tests, so without this such an
answer could never be applied. Changed tests get one test-reviewer pass (objections go to the
final gate), are committed (`<f>: tests updated from answers ANS-<n>`, `tests_amended` event)
and tagged `tests-update/<f>`, which the frame diff allows like `spec-update/<f>`. If they fail,
the review fixes make them pass. `approve-spec` drops both tags. The PreToolUse hook lets
SPEC_UPDATE write only `specs/` and `out/`.

Stopping a run: Ctrl+C, Ctrl+Break or SIGTERM kill the running agent's whole process tree (each
agent runs in its own process group; on Windows also in a kill-on-close job object, so even a
`taskkill /F` of agentflow leaves no orphan `claude`), clear `.phase`/`.reqs`, record an
`interrupted` event (+ `progress.json` `interrupted`; the attempt is not counted) and exit 130.
`out/.run.lock` (pid, feature, start) marks a running orchestrator: a second `run` is refused,
a lock whose pid is gone is taken over (and the interruption recorded). `agentflow status` warns
about a phase lock without a running orchestrator (`SPEC` excepted: `agentflow spec` sets it for
the interactive session); `agentflow unlock` clears it.

Turn and cost caps per agent run: `--max-turns` = `[phases.<P>] max_turns`, else
`[agents] max_turns`, else TESTS 50, TEST_REVIEW 30, IMPLEMENT 80, REVIEW 40, TRIAGE 30, PR 30,
RETRO 30. A turn is one model round trip (one assistant message, however many parallel tool calls
it makes); the CLI's `num_turns` counts messages (prompt + every tool result) and runs about 2x
higher, so agentflow logs both (`turns` = round trips from the message stream, `messages` =
`num_turns`; docs/claude-cli-notes.md). `--max-budget-usd` = `[phases.<P>] max_budget_usd`, else
`[agents] max_budget_usd`, else TESTS 2.0, TEST_REVIEW 1.5, IMPLEMENT 1.5, REVIEW 1.5, TRIAGE 1.5,
PR 1.5, RETRO 3.0 USD (~3x the live FCSim cost per run; `0` = no cap). A run that stops at
either cap is logged (`max_turns` / `max_budget` event). IMPLEMENT: the step gate decides (a
failed gate is a normal failed attempt whose feedback names the cap); other phases are re-run
once with a note; if that run stops at a cap too, it is logged (`escalation` event with
`cap_kind`, retro E item) and the run continues with what the agent wrote.

Models and speed:
- `--model` per phase: `[phases.<P>] model`, else `[agents] model`, else Sonnet for the writers
  that follow the approved spec and the frozen tests (TESTS, IMPLEMENT incl. review fixes,
  SPEC_UPDATE, PR summary) and Opus for the checkers and the retro (TEST_REVIEW, REVIEW, TRIAGE,
  RETRO). `--effort` likewise (`effort`, default: Claude Code's). Each `agent_run` event records
  the model, so cost and failed attempts per model can be compared across features.
- Small plans run in one implementer run: consecutive steps share a run while their spec IDs add
  up to at most `[agents] step_batch_ids` (15; 0 = one run per step). The batch is checked,
  committed, parked and redone as one unit (`steps 1, 2, 3`).
- The implementer checks only with the step command (its steps' tests and the earlier ones); the
  step gate runs everything else. Before checking, the gates format the changed source files
  and the changed review tests, and the test writer's files are formatted after each of its
  runs (`[gates] format_fix`, default on, only with the adapter's own format check), so a
  formatting slip costs no attempt and never sits in a file the implementer may not edit. For
  .NET the format / lint checks report only findings on changed lines, and the format fix keeps
  only its changes there: no churn in code the feature did not touch.
- `[gates.extra]` (name = command): project checks after the final gates, e.g. a balance
  measurement. Shown at the final gate with exit code and output (full output in
  `out/<f>/extra_<name>.log`); never part of the verdict.

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

Hooks (called by the plugin): `agentflow hook pre-tool-use` (phase-based write protection, and
the command form of Bash / PowerShell calls during a run) and `agentflow hook stop` (IMPLEMENT
can't stop while the step's tests fail, unless it parked: an `out/*/park.md` exists). Both read
the hook JSON on stdin, exit 0 silently without `agentflow.toml` or `.phase`, and block with exit
2. Shell calls: only plain single commands are pre-approved, so a pipe (`dotnet test ... |
Select-String`), a `;` / `&&` / `||` chain, several lines, or a `cd` / env-variable prefix would
be denied by the permission check without a reason. During a run (any phase but SPEC) the hook
blocks them first and tells the agent why and what to do instead: one command per call, and the
command's own options (`--filter`, `--logger "console;verbosity=minimal"`, `-k`, `-q`) or the
result file it writes to see less output. The task text says the same (`COMMAND_FORM`).

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
