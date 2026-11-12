# agentflow reference

Everything in detail: the phases, the chat flow, project knowledge, the CLI, gates, retro,
hooks and the language adapters. The short version is the [README](../README.md).

## All commands

In the chat you only use the four skills; they call the CLI for you. The CLI works the same from
a terminal.

| Command | What it means | When |
|---|---|---|
| `/agentflow:setup` | set up agentflow in this repo (draft config, project facts and code map; a short brief that asks only the real decisions; doctor) | once per repo |
| `/agentflow:feature <f>` | start or continue feature `<f>`: spec dialogue, run, gates, wrap-up | each feature |
| `/agentflow:bugfix <f>` | a bug fix: bug report dialogue, tests that fail on today's code, the fix, gates | each bug |
| `/agentflow:refactor <f>` | a refactor: scope dialogue, tests that pin today's behavior, small steps, gates | each refactor |
| `/agentflow:resume [f]` | reattach the chat to a running or waiting feature | after a restart |
| `/agentflow:status [f]` | where features stand; tag cleanup after the PR is merged | any time |
| `agentflow init` | write the starter files (config, spec templates, project facts, .gitignore lines) | once per repo |
| `agentflow doctor [--full] [--brief]` | check the setup without agents; ends with a verdict and only what needs action (`--brief`: only that); `--full` also runs the whole test suite | after setup or config changes |
| `agentflow spec <f> [--mode feature\|bugfix\|refactor]` | create the branch `feat/<f>` and open the spec phase with the mode's templates | start of a feature |
| `agentflow spec-check <f>` | mechanical checks of the spec tables (below); exit 1 on a problem | internal |
| `agentflow approve-spec <f> [--force]` | spec-check, then freeze the spec ("spec OK"); restarts the feature if the spec changed later | gate 1 |
| `agentflow run <f>` | run the agents until the next human gate or DONE | after every decision |
| `agentflow watch <f>` | follow the progress lines of a run | while it runs |
| `agentflow status [f] [--brief]` | state of one or all features | any time |
| `agentflow gate-summary <f>` | the brief of the waiting gate (decision and default first, plain words) | at a gate |
| `agentflow approve <f>` | accept the gate: final gate -> retro, then DONE (tests gate -> implement) | at a gate |
| `agentflow answer <f>` | apply your answers in `out/<f>/answers.md` (spec, tests, redo of parked work) | at a gate |
| `agentflow reject <f> --reason "..."` | send the tests back to the test writer (only with `[gates] tests = "human"`) | tests gate |
| `agentflow finish <f> --no-retro` | accept the final gate and skip the retro | final gate |
| `agentflow learn <f> K1 [K3 ...\|all]` | keep the project facts the retro proposed (into `.agentflow/project.md`) | after the retro |
| `agentflow keep <f> 1 3 [...\|take\|all\|none]` | record which retro proposals you accept in the retro backlog | after the retro |
| `agentflow backlog [show B3 \| done\|drop\|reopen B3 ...] [--all] [--scope ...]` | the open proposals; one in full; mark it applied / dropped / open again | any time |
| `agentflow stats [f] [--json]` | cost, agent and check time, final-gate runs per feature; with `<f>`: per phase and per final run | any time |
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
fresh headless `claude -p` runs inside a frame of deterministic checks (red-against-stubs; for
a bug fix or a refactor the first proof differs, see "Modes" below;
traceability, test smells, frozen tests via a tag-based frame diff, lint/types, diff coverage,
mutation score) and never stops to ask.

| Phase | Agent(s) | Ends with |
|---|---|---|
| SPEC | spec dialogue in the chat (`/agentflow:feature <f>`; a bug: `/agentflow:bugfix <f>`; a refactor: `/agentflow:refactor <f>`) | **gate 1**: the spec brief (`out/<f>/spec_brief.md`), then `agentflow approve-spec` -> tag `spec-approved/<f>` |
| TESTS | test writer -> mechanical checks -> **test reviewer** (`roles/test_review.md`); objections go back to the writer, at most `[agents] test_review_rounds` (2) times | **gate 2**: `out/<f>/tests_gate.md` (ID -> tests -> what it checks, check results, reviewer verdict + remaining objections, all open questions batched) -> approve (tag `tests-approved/<f>`) / reject / answer (answers go into the spec first: SPEC_UPDATE). `[gates] tests = "auto"` passes it without a stop when everything is clean |
| IMPLEMENT | implementer, one fresh run per plan step | step gates green -> commit. Ambiguity: assume from the spec, log it in `steps.md`. No reasonable assumption, or `max_attempts` failed attempts: the step is **parked** (its code set aside as `out/<f>/parked/*.diff`), the run goes on |
| REVIEW | adversarial reviewer (bugs as failing tests, structure findings without tests) -> **triage** by the test reviewer -> fixes -> structure fixes | valid findings fixed (or parked), invalid ones removed with a reason, the code restructured with every test green; final gates + structure report |
| PR | summary agent (`roles/pr.md`): `final_summary.md` (detail), `final_brief.md` (the gate brief), the code map update (committed) | **gate 3**: the final brief -> approve (retro) / `finish --no-retro` / answer the numbered items (into the spec, then redo only those) |
| RETRO | retro agent (reads the retro backlog; a problem seen again is counted there) | `retro_proposals.md` + metrics row -> DONE; then `agentflow keep` records your pick in the backlog |

## Parts

| Part | Where | Docs |
|---|---|---|
| Python package + CLI `agentflow` | `src/agentflow/` | this file, docs/adapters.md, docs/claude-cli-notes.md |
| Claude Code plugin (`/agentflow:setup`, `:feature`, `:resume`, `:status`, hooks) | `plugin/` | docs/plugin.md |

## Daily use: from the Claude Code chat

Once per repository: `/agentflow:setup` in the chat (it reads the repo, drafts agentflow.toml,
`.agentflow/project.md` and the code map, shows a short brief that asks only the real decisions, runs
`agentflow init` and `agentflow doctor --brief`, and offers a small smoke feature in one line), or by hand `agentflow init [--language
python|dotnet|generic]` (agentflow.toml, spec templates, `.agentflow/project.md`, .gitignore
lines; commit them) and `agentflow doctor` (checks config, git, that agentflow.toml is on the base
branch, the claude CLI, the chat's allow rules, the tools, the gate commands and the test selection by spec ID, without agents; `--full` also times the
whole suite). Any language without a built-in adapter uses the generic adapter: test, coverage,
mutation and format commands with placeholders in `[generic]` (docs/adapters.md), no agentflow
code. Also the plugin installed (docs/plugin.md), and an allow rule so the
skills can run the CLI without a prompt after the first turn (an `allowed-tools` grant of a skill
only lasts for the turn it was invoked in): `"Bash(agentflow *)"` and `"PowerShell(agentflow *)"`
under `permissions.allow` in `.claude/settings.local.json` (or answer the first prompt with
"always allow").

Then you only type requirements, answers and gate decisions:

A bug fix starts with `/agentflow:bugfix <name>` and a refactor with `/agentflow:refactor
<name>` instead of step 1: the same flow with another first proof ("Modes" below).

1. `/agentflow:feature <name>`: creates the branch `feat/<name>` from `[git] base` (`main`) in
   this checkout (refused with uncommitted changes to tracked files; an existing branch is used
   only if it is this feature's), sets the SPEC phase and starts the spec dialogue. Claude
   decides what the docs, the code map, the code and `.agentflow/project.md` settle, asks only
   only the real decisions (see "Which questions are asked" below), writes
   requirements/design/plan and the interface stubs, and shows the **spec brief** instead of the
   spec files (see "Manager-style briefs" below).
2. "Spec OK": the skill approves the spec, starts `agentflow run` in the background and shows the
   live view: one line per phase start/end, check result, failed attempt, assumption, park and
   gate, each with the cost so far (~10-30 lines per feature). The chat stays usable.
3. At the final gate (and the tests gate, if `[gates] tests = "human"`) the chat shows the
   gate brief (`agentflow gate-summary`), Windows shows a toast, and Claude Code waits for you.
   Reply in plain words: "ok" (with numbered items: take every default; without: approve, retro,
   then DONE), answers by number, "approve as is", "no retro", "reject: <why>" (tests gate
   only). The skill runs the matching command and continues the run in the background.
4. DONE: the skill shows the retro's numbered proposals (problem -> fix, with a recommendation)
   and its knowledge facts, you answer with numbers; it offers the final summary as the PR
   description (`gh pr create`, only on your yes; nothing is pushed without it) and cleans up the
   run files (`agentflow cleanup`; `specs/<name>/` and the metrics row stay). After the PR is
   merged, `/agentflow:status <name>` offers removing the feature's tags.

After a session restart: `/agentflow:resume [name]` reattaches (live view, the waiting gate or the
wrap-up). `/agentflow:status` lists the features and where they stand.

## Manager-style briefs

The human is the manager of a feature: clear input, outcomes checked. The spec files stay
precise and are written for the agents; wherever the human reads or decides, the chat shows a
short brief instead: the decision and its default first, about one screen in plain words, no
spec ID without its meaning, the detail files only on request ("more detail on step 2"). There
is no mode or switch for this.

| Where | The brief |
|---|---|
| Spec gate (`out/<f>/spec_brief.md`, written by the chat) | Task (2-4 lines) / Plan (one line per step) / How it is proven (the 3 most important checks) / Not proven (the 3 most important things to check by using it) / Decisions I made for you (one line each, with what it rules out) / Project details that matter / Questions (the real decisions, by impact) |
| Tests gate (`[gates] tests = "human"`/`"auto"`) | what the tests check in plain words (the test reviewer's `## What the tests check` in test_review.md), objections left, one line of mechanical checks, questions with defaults |
| Final gate (`agentflow gate-summary`, from `final_brief.md` + run records; ~20 lines when nothing went wrong) | Plan followed? (one line when all steps went as planned, else one per changed / parked step) / Outcomes with numbers (gates, diff coverage, mutation, failed attempts, parks, cost, agent and check time, structure) / Struggles (one line when none) / Proven and Not proven (the 3 most important points each, the rest on request) / Code map (one line; a WARN when new public types are missing) / Questions numbered 1..k by impact (the problem in one line, then `A: ... (default) / B: ...`) / Decisions I made / how many gate items were left out |
| Setup | what I found, the settings that differ from the defaults (with why), the knowledge facts in short lines, the code map's modules, the real decisions as questions with defaults |
| `agentflow doctor --brief` | one verdict line (ready / not ready), then only the FAILs with the fix and the WARNs that need a decision |
| Retro (`## Brief` in retro_proposals.md) | numbered proposals, problem -> fix, recommend take / skip / fold into N (and `repeats B3` for a problem the backlog already holds); the knowledge facts; answered with numbers (`agentflow keep`, `agentflow learn`) |
| Smoke run offer, DONE wrap-up, status, resume, errors, interruptions | one or two lines: the state and the one decision needed, with a default |

At a gate with numbered questions, "ok" takes every default (the chat writes them to
answers.md); answers by number work as before; "approve as is" approves and leaves them open.
One line per bullet; a section where nothing happened is one line; no sentence repeats a heading
or another section.

### Which questions are asked

No count cap anywhere (spec dialogue, spec / final gate briefs, setup). A point is a question
only when it is a real decision: two or more reasonable options with different outcomes, and the
spec, the code, `.agentflow/project.md` and earlier answers don't settle it. Otherwise it is not
a question: only one sensible option -> decided, one line under "Decisions I made"; already
answered, already resolved (e.g. by a scan) or a note -> left out; the same question from two
agents -> one item. Questions are ordered by impact (what changes most if answered differently
first).

At the final gate the PR agent sorts every numbered gate item (parked work, the provisional
steps, the open questions) into `## Questions`, `## Decisions I made` or `## Left out` of
final_brief.md, naming it `[N]`. Parked work and the provisional steps are always asked (they
block the run); an item the brief places nowhere is asked as it is. The human sees the questions
numbered 1..k; `agentflow answer` maps answers.md back to the gate items and rewrites it
(`# Answers (final gate, numbered by gate item)`): a decided item gets `Default: <decision>`, a
repeated one the answer of the item it repeats, any other left-out item "No answer needed"; the
chat's own file is kept in the `answer` event (`as_given`). After the PR agent's run agentflow
checks the brief (not blocking): questions without `[N]` or with one option, duplicates, closed
items or notes asked as questions, parked work left out, gate items placed nowhere -> the PR
agent rewrites final_brief.md once (event `brief_check`).

## Structure and the code map

The human owns what the classes are for and how they work together, not every line.
- The implementer writes for that (role): one concept per file, domain names, a one-line doc
  comment on every public type and method, no single-use interfaces or speculative
  abstraction, no dead defensive branches, no fragmentation into one-method helpers.
- The adversarial reviewer checks the same list and writes `## Structure findings` in
  review.md (`- <file>: <problem> -> <change>`; no test needed). Before the final gates the
  implementer applies them without changing behavior, every test green (`structure fixes`;
  failing it parks like a review fix).
- The final gates end with a structure report (not blocking): the size of every source file
  the feature changed (and before it), the longest methods. A file over `[gates]
  max_file_lines` (400; 0 = off) becomes a numbered item at the final gate with a default:
  split it now if this feature took it over the limit, else leave it for later. The answer
  goes to the structure run (split, or leave it), and the file is not asked about again.
  Seen live: FCSim M5-freekick-review grew SetPieceShotModel.cs to 694 lines, mixing the kick
  model with the review calculators.
- The code map (`[docs] code_map`, default `docs/code-map.md`): a living Markdown doc, per
  module its purpose, its key classes (one line each), how they work together (short flow,
  mermaid optional), "where to look for X"; under ~300 lines. `/agentflow:setup` drafts it
  from the code; the PR phase updates the sections the feature touched (agentflow commits it
  as `<f>: code map`); a new public type in the feature's diff that the map does not name is a
  WARN on the final brief's code map line. `agentflow doctor` WARNs when it is missing.

## Modes: feature, bug fix, refactor

What a task is decides the first proof of its tests. The mode is the `Mode:` line of the
spec's requirements.md, written by `agentflow spec <f> --mode bugfix|refactor` (the chat:
`/agentflow:bugfix <f>`, `/agentflow:refactor <f>`); no line means feature. Everything after
the tests (steps, step gates, review, final gates, the two human gates) is the same flow.

| | feature | bug fix | refactor |
|---|---|---|---|
| Spec (requirements.md) | requirements, edge cases, stubs | bug report: reproduction rows (R), must-not-change rows (I), suspected causes | scope, behavior to preserve (R, I), structure targets |
| Tests | new, from the spec | new, from the bug report | existing tests first: a test that already asserts a row gets the row's ID as a tag; new tests only for the gaps |
| First proof (`test_checks.md`) | every test FAILS against the stubs | reproduction tests FAIL on today's code (each listed with why, for the test reviewer); must-not-change tests PASS | every test PASSES on today's code; the check lists how many tagged tests existed before and how many are new |
| Steps | per plan step, batched | usually one; the step note names `ROOT CAUSE` and `RULED OUT` lines (shown in final_facts.md and the brief) | one move per run; every step is checked with ALL the tests; a step that changes no source file fails |
| Review | against the spec | also: the same bug through another input or caller | a finding is a difference from the code before (`git show tests-approved/<f>:<path>`) |
| Structure fixes after the review | checked with the whole suite | checked with the whole suite | checked with the feature's tests, like a step (the whole suite runs once, in the final gates) |
| Risk rating | every changed line counts | every changed line counts | moved lines are not counted (`moved_lines` in the `risk` event) |
| Final-gate repair | code, then tests | code, then tests | code only: tests written after the refactor would pin the changed code, so weak tests go to the human |

Templates: `specs/_template_bugfix/`, `specs/_template_refactor/` (agentflow's own unless the
project has a copy). The events `spec_started` / `spec_approved` carry `mode`; `agentflow
stats` shows it. An unknown `Mode:` word is refused by `approve-spec`.

Existing tests first (refactor). The first live refactor (FCSim, M5-setpiece-model-split)
wrote 835 lines of new tests beside 789 lines of existing ones for a change with 114 lines
that were not moves, and the mutation check ran only the new ones. Now the spec lists the
tests that exercise the code in scope (`## Existing tests of the code in scope`), and the
test writer tags an existing test that asserts a row exactly with the row's ID: a tag line,
nothing else in that test. Every check selects tests by ID, so the step checks, the
coverage run and the mutation check then run the old and the new tests alike. The check
"Green on today's code" reports the tagged existing tests, the rows only they pin, and an
existing test file with removed or changed lines (a note for the test reviewer). The
test-smell check counts only tests the feature added, also in a file it touched.
Measured on that run: the six older test classes that name the moved code kill 320 of 349
mutants of the change without any new test (the new tests: 329), but took 34 min in the
mutation run against 8, because they include match tests.
`[gates] mutation_scope = "suite"` is no way around this on a large suite: on FCSim (2,040
tests) Stryker's coverage capture did not end within the hour.

Scope coverage (refactor, `src/agentflow/checks/scope_coverage.py`). Before any code moves,
the tests checks run the tagged tests once more with coverage of the files in scope: the
paths in backticks in the first column of the spec's `## Scope` table that are source files
today. test_checks.md gets `### Scope coverage (note, not blocking)`: per file the share of
lines run and the line ranges no tagged test runs. The test reviewer reads each range; moved
or merged behavior that no test pins is an objection, so the gap is closed while the tests
can still change (a refactor has no test repair at the final gate). It never blocks by
itself: a line that is run is not yet asserted (the mutation check measures that at the
end), and it is skipped with its reason when the Scope table names no existing file or the
coverage tool is missing (python: pytest-cov; .NET: coverlet.collector; the generic adapter
does not measure it). On FCSim the run took 37 s for one file in scope.

## The retro backlog

`agentflow keep <f> <numbers | take | all | none>` records the human's pick of a retro's
proposals in `metrics/retro_backlog.md` (tracked, committed): one row per proposal with a
status (`open` = accepted, not applied yet; `applied`; `dropped`; `skipped` = not accepted),
its scope (framework / project), the features whose retro raised it, and a pointer into
`metrics/retro/<f>.md`, a copy of that retro's full text (`agentflow cleanup` deletes the run
folder). A proposal the retro folded into another gets no row.

The next retro gets the rows at the end of retro_input.md (`## Backlog`). A problem a row
already describes is not proposed again: the retro's brief line names the row (`repeats B3`),
and agentflow adds the feature to that row's "Seen" when the retro ends. Picking such a
proposal reopens a skipped, dropped or applied row. A first-time problem that looks like a
one-off is recommended `skip`; it becomes a `skipped` row and is counted if it comes back.

`agentflow backlog` lists the open rows (most often seen first; `--all`, `--scope
framework|project`), `backlog show B3` prints one with the retro's section, `backlog done |
drop | reopen B3 [--note "PR #12"]` changes its status. Framework-scope rows are applied in the
agentflow repository; mark them `done` here with a note.

## Stats

`agentflow stats` reads `out/events.jsonl` (no agents): one line per feature with its cost,
the time agents and gate checks ran, the time a gate waited for you, the final-gate runs,
the spec time (from `agentflow spec` to the approval: the dialogue in the chat; the chat's
own cost and the talk before the command are not in the log), failed step gates,
questions, assumptions and risk level, then the averages of the finished
features. `agentflow stats <f>` adds the agent runs per phase (runs, cost, time, output tokens),
every final-gate run with its failed checks and three slowest checks, and the repair runs.
`--json` prints the numbers.

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
agentflow doctor [--full] [--brief]         # check the setup without agents; ends with a
                                            # verdict + what needs action (--brief: only that)
agentflow spec <f> [--mode feature|bugfix|refactor]
                                            # branch feat/<f> from [git] base + SPEC phase,
                                            # with the mode's spec templates
agentflow spec-check <f>                    # rules without a spec ID, copied code not in the file
agentflow approve-spec <f> [--force]        # spec-check, then tag spec-approved/<f>
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
agentflow keep <f> 1 3 [...|take|all|none]  # the retro proposals you accept -> the retro
                                            # backlog (metrics/retro_backlog.md, committed)
agentflow backlog [--all] [--scope framework|project]
                                            # the open proposals (--all: every row)
agentflow backlog show B3                   # one row with the retro's full section
agentflow backlog done|drop|reopen B3 [B5 ...] [--note TEXT]
agentflow stats [<f>] [--json]              # cost, agent / check / waiting time, final-gate
                                            # runs per feature; <f>: per phase, per final run
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
prints the `git push origin --delete` line for tags that were pushed (`[git] markers = "tags"`).

**Markers, commits, known failures.** `spec-approved/<f>`, `tests-approved/<f>`, `spec-update/<f>`, `tests-update/<f>` and `knowledge-update/<f>` are private refs (`refs/<name>`) by default: `git diff tests-approved/<f>` resolves them like a tag, but no `git push`, `--tags` or `--all` publishes them to a shared remote. `[git] markers = "tags"` stores git tags instead (needed for a CI job that runs `agentflow gates`); tags left by an older agentflow are still read and replaced on the next write. A workflow commit stages only the workflow's paths (`[paths] src`, `tests`, `specs`, `metrics`, the knowledge file, the code map), never the whole working tree: your notes and unrelated edits stay out. `[gates] regression_baseline = true`: `approve-spec` runs the whole suite once and records the tests that already fail (`out/<f>/regression_baseline.json`, kept on a second approval); the final regression check then fails only on other failures (with the default `coverage_scope = "feature"`).

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
  test reviewer's objections still open and failed test checks). A question the same agent
  asked before is dropped (the same words after its own label `Q1:` / `Q1 (repeat):`, or a
  shared spec ID and 80% of the shorter one's words; `questions` event: `repeats`). The
  `answer` event keeps for every item the question as it was asked (`asked`), so the retro
  finds the answer although the chat rewords the question. Answers there go into the spec
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
- `test_checks.md` also lists "Existing members asserted" (python, dotnet; a note, never
  blocking): members that the feature's changed test files read inside an assertion, declared
  only in code that existed before the feature (declaration line outside the diff against the
  merge base with `[git] base`, which holds the spec dialogue's stubs) and asserted by no
  fixture precondition test. The red run cannot check such a claim (the stub fails first); the
  test reviewer reads where each member is set and objects or asks.
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
- Provisional steps: when only named frozen tests are wrong (park.md starts with `Park kind:
  tests-wrong` and `Contested tests: A, B`, or a diagnosis says `Fix owner: tests` with a
  `Contested tests:` line) and the step's code passes every other check of its step gate, the
  code is committed as provisional (`<f>: <step> (PROVISIONAL: tests contested: ...)`,
  `provisional` event, `progress.json` `provisional`) instead of being parked. Dependent steps
  build on it; the contested tests are left out of the step gates and the Stop hook
  (`out/contested_tests.txt`); later tasks name them. The final gate lists all proposed test
  fixes as one numbered item (`approve` / `finish` refuse while it is open). After the answer,
  the test writer applies the approved fixes (TESTS_AMEND); then the contested tests must pass.
  If they still fail (the fix was not approved), the provisional commit is reverted (its diff
  kept in `out/<f>/parked/`) and the step is redone against the tests
  (`provisional_settled` event). Goal: every wrong test surfaces in the same round.
- Final gates with parked steps or provisional steps: the whole-suite regression run counts
  the parked steps' tests (by spec ID) and the contested tests as expected red ("expected red
  (parked): N" in the report) and fails only on any other failure (listed as UNEXPECTED);
  coverage, diff coverage and mutation are SKIPPED with the reason until the redo.
- Review findings (failing reviewer tests) are triaged against the spec by the test reviewer:
  VALID stays and goes to the fix loop, INVALID is deleted with a one-line reason
  (`triage.md`, `triage` event). Fixes that fail `max_attempts` times are parked. The reviewer
  is told the parked steps' IDs; a valid finding whose test carries only such IDs is DEFERRED:
  no review-fix run builds the parked step, its redo makes the test pass.
- Every agent task starts with a short rule (look around with Read / Glob / Grep, write with
  Write / Edit, the shell only for the named commands) and an orientation block: the files in
  `specs/<f>/` and `out/<f>/` and `git log -3 --stat`, which agents otherwise fetched with
  chained shell calls that the run denies.
- The final summary (`final_summary.md`, the detail and the PR description) and the final brief
  (`final_brief.md`, what `gate-summary` shows) are written from `final_facts.md` (generated
  from the run records, with the final gate's numbered items, so the brief rewords each one in
  plain words under the same number) and `spec_brief.md`; agentflow appends the cost / time /
  turns table per phase to the summary and adds the numbers to the brief itself.

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

Usage limit: a run that the Claude subscription's usage limit refuses (a rejected
`rate_limit_event` in the stream, or an error result such as "You've hit your limit · resets
4pm") is not a failed attempt. agentflow logs a `usage_limit` event, waits until the reset plus
one minute (15 minutes when claude names no time; progress line and toast), then runs the same
task again. A reset further away than `[agents] limit_wait_max_hours` (default 6; `0` = never
wait), e.g. a weekly limit, or a limit still there after 4 waits, stops the run like Ctrl+C
(exit 130, `interrupted` with the reset time); `agentflow run <f>` after the reset goes on.

Check output in the gate report and the fix prompt: a failing check's output of more than 40
lines is filtered to the lines that look like failures (failure words, pytest `E` lines, file
locations such as `src/x.py:12:` or `Foo.cs(12,5)`) with two lines of context, plus the last 8
lines; the whole output is saved to `out/<f>/check_logs/<check>.log` and named in the report.
Decisions on the output (e.g. "lint fails only in frozen files") read the whole output.

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

Final-gate repair (`[gates] final_repair`, default on): a failed final gate gets one repair
run per owner before the human sees it. Failures the code owns (lint, format, typecheck, a red
test in the regression or coverage run) go to the implementer (`FIX_FINAL` task, the whole
dev-loop suite as its step check; a failure parks like a review fix). When only the diff
coverage or the mutation score is too low, the test writer adds tests (`TESTS_STRENGTHEN`:
from the spec, never from the code; what the spec does not cover is listed under "Not covered
on purpose" in test_summary.md); the new tests get one test-reviewer pass (objections go to the
final gate), are committed (`<f>: tests strengthened (final gates)`) and tagged
`tests-update/<f>`; a new test that fails goes to the implementer. Then the final gates run
again; at most three runs per cycle (first, after the code repair, after the tests repair). A
repair that changes nothing ends it. No repair while parked or provisional work waits (those
failures are expected). Events: `final_repair` (owner, failed, changed). The mutation check is
not started when the typecheck, the regression or the coverage run failed before it (SKIPPED
with that reason): it needs a green build and green tests.

Tool failures: a mutation check that fails without a score (the tool crashed, could not build
its own copy, was cut short) is a tool failure, not a statement about the tests. The gate
report says so under the check (`TOOL FAILURE: ...`), the `final_gates` event lists it in
`tool_failures`, and no repair run is started for it. The final gate asks instead (an item the
brief always shows): run the final checks again (any answer does; the default), or approve as
is. The .NET adapter starts a failed Stryker run a second time first (with `--diag` when
Stryker's output offers it; the first output is kept in `out/<f>/stryker/<project>.first.log`).

Spec check (`agentflow spec-check <f>`, also the first step of `approve-spec`, which refuses
while it finds something; `--force` approves anyway and logs `spec_check` with `forced`). It
reads two columns of the spec templates' tables; a spec without them is not checked:
- `Spec ID` (design.md, "Error handling" and "Command-line surface"): every filled row names
  a spec ID of requirements.md. A rule that is only written in design.md gets no test.
- `Assigned as (code, copied)` (requirements.md, "Existing data this spec reads"): every
  backticked piece of code in the cell is in the file the "Set where" cell names (a path, or
  a file name looked up in the source folders; spacing is ignored). `n/a: <why>` skips a row.
`agentflow doctor` warns when a project's copy of the templates (`specs/_template/`) lacks a
section or a table column of agentflow's templates.

Risk rating (`src/agentflow/risk.py`, `[risk]`): at every final gate the engine rates the
feature `low` or `elevated` from its own records, never from an agent's opinion. Low needs all
of: final gates passed with diff coverage and mutation measured (a project without them is
never low risk); nothing parked, provisional or open; no answer round, no park and no cap hit
twice in the run; at most `max_assumptions` (3) assumptions; at most `max_changed_lines` (200)
changed source lines in `max_changed_files` (8) files and `max_removed_lines` (30) removed
lines in existing code, measured against the feature's base (a refactor: without moved
lines, that is lines removed in one place and added unchanged, but for the indent, in
another; live: 876 changed lines were 114 without the moves); no changed path under
`never_auto`; no changed file over `[gates] max_file_lines`; at least `min_features` (3)
finished features in the metrics file. The brief shows `Risk: low` or `Risk: elevated: <the
tests that failed>`; the `risk` event and the `human_gate` event (`risk`) record it next to
your decision, so you can compare before switching on `[gates] final = "auto"`: then a low-risk
feature passes the final gate without a stop (`human_gate` with `auto: true`, retro, DONE;
nothing is pushed, the PR stays yours). Everything else stops as before.

Several source folders: `[paths] src` takes one folder or a list (`["core", "autoload",
"scenes"]`). The implementer may write all of them; the frame diff, the forbidden patterns,
the format fix, the structure report, the park revert and the Python lint / type / coverage
commands cover all of them; in a configured command an argument that is exactly `{src}`
becomes one argument per folder.

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
review finding with its triage verdict (V1..), structure finding of the reviewer with what
its fix cost (S1..; implementer runs, gate runs, seconds), final-gate failure (G1..), permission denial
(D1..), assumption (A1..) and parked or provisional item (P1..). Each question is paired with
its answer from any later answer round (the `answer` events, then every `### Answer ANS-n`
round of `questions_archive.md`): by its first words, else by shared spec IDs and words (the
gate often rewords a question). No match although answer rounds and a spec update followed:
"answer lookup failed", not "not answered".
retro_input.md ends with the retro backlog (`## Backlog`: the proposals of earlier retros
with their status), so the retro names a problem it sees again instead of proposing it anew
("The retro backlog" above).
The retro must mention every ID in `retro_proposals.md` (a proposal, or "Not acted on" with a
one-line reason); if IDs are missing it is re-run once with them as feedback, then a warning is
printed and logged (`retro_coverage` event).

Each `agent_run` event also has `role`, `step`, `attempt`, `session` and the token counts
(`tokens_in`, `tokens_out`, `cache_read`, `cache_write`), so cost per step and attempt can be
read from `out/events.jsonl`. `progress.json` is written to a temp file and moved into place;
a line of `events.jsonl` cut short by a killed run is skipped when reading.

`metrics/workflow_metrics.csv` (one row per finished feature):

| Column | Meaning |
|---|---|
| `human_rejections` | `reject` at the tests gate |
| `escalations` | agent runs that stopped at the turn cap twice |
| `questions` | single questions asked of the human: each question of the agents' `questions.md` files, plus each park and provisional step that asks one (rows before 2026-11-11 counted rounds and no parks) |
| `gate_failures` | failed step-gate runs (failed implementation attempts, incl. review fixes; parking doesn't count) plus failed final-gate runs (rows before 2026-11-11: step gates only) |
| `cost_usd` | sum of `total_cost_usd` of all agent runs |
| `parked` | parked steps / review fixes (each park, incl. re-parks after a redo) |
| `assumptions` | `- ASSUMPTION [...]` lines in `steps.md` |

Hooks (called by the plugin): `agentflow hook pre-tool-use` (phase-based write protection, and
the command form of Bash / PowerShell calls during a run) and `agentflow hook stop` (IMPLEMENT
can't stop while the step's tests fail, contested tests of provisional steps left out, unless
it parked: an `out/*/park.md` exists). Both read
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
