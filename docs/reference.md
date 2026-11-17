# framegate reference

Everything in detail: the phases, the chat flow, project knowledge, the CLI, gates, retro,
hooks and the language adapters. The short version is the [README](../README.md).

## All commands

In the chat you only use the four skills; they call the CLI for you. The CLI works the same from
a terminal.

| Command | What it means | When |
|---|---|---|
| `/framegate:setup` | set up framegate in this repo (probe the project, one table of its facts to confirm, then config, project facts and code map; doctor) | once per repo |
| `/framegate:feature <f>` | start or continue feature `<f>`: spec dialogue, run, gates, wrap-up | each feature |
| `/framegate:bugfix <f>` | a bug fix: bug report dialogue, tests that fail on today's code, the fix, gates | each bug |
| `/framegate:refactor <f>` | a refactor: scope dialogue, tests that pin today's behavior, small steps, gates | each refactor |
| `/framegate:pipeline <f>` | the pipeline task: make the deploy / CI changes a task left as its pipeline follow-up (`<f>` = that task), or one of their own (`<f>` = a new name): the list, the edits in the chat, one check, one brief to approve, the publish ("The pipeline task" below) | after a task with a pipeline follow-up; any time for a change of its own |
| `/framegate:resume [f]` | reattach the chat to a running or waiting feature | after a restart |
| `/framegate:status [f]` | where features stand; tag cleanup after the PR is merged | any time |
| `framegate init` | write the starter files (config, spec templates, project facts, .gitignore lines) | once per repo |
| `framegate install-tools [--dry-run] [--update]` | install what the workflow needs into the project's environment (python: the framegate pytest plugin, pytest, pytest-cov, hypothesis, diff-cover, and with a framegate.toml the lint / format / type tools its gate commands name); `--update` also brings the framegate pytest plugin there to the command-line tool's version; reports versions it changed; works before `init` | first step of `/framegate:setup`, after config changes |
| `framegate probe [--json]` | the project facts every run depends on, from instant checks only (no test run, no linter over the code, no agent): language, the interpreter the run uses, the test / lint / format / type commands with where each comes from and whether the tool answers `--version` there, source and test folders, base branch, remote and host, deploy / CI paths locked or to lock, editable install, references; works before `init` (proposals from the project's own files) and with a framegate.toml (the configured values); always exit 0 | step 1 of `/framegate:setup` |
| `framegate update [--plugin-version X]` | bring this machine in line with the installed plugin at any time: prints the install command for the command-line tool (it cannot replace itself while it runs), then updates the project's workflow tools; `/framegate:update` does both in the chat. Tasks between "spec OK" and DONE are the only thing the start check leaves alone; `update` does not, except while a run is active (it says so and updates nothing that run uses) |
| `framegate preflight [<f>] [--plugin-version X] [--check]` | the start check: the command-line tool at the plugin's version (prints the install command for the chat), the project environment's workflow tools at the tool's version (updated here), a session with outdated skills told to restart, a short setup check; one line when all is in line; always exit 0. Nothing is updated while a task is in progress, with `--check` or without a feature: the update waits (`KEPT:`), "The start check and updates" below | first step of `/framegate:feature`, `:bugfix`, `:refactor`, `:pipeline`, `:resume` |
| `framegate doctor [--full] [--tree] [--brief]` | check the setup without agents; ends with a verdict and only what needs action (`--brief`: only that); `--full` also runs the whole test suite; `--tree` runs it too and says what is red on the code as it is ("The tree at setup" below) | after setup or config changes; `--tree` once per repo, in `/framegate:setup` |
| `framegate commit-setup [--dry-run] [PATH ...]` | the files the setup wrote, in one commit of their own ("The tree at setup" below); `--dry-run` only lists them | last step of `/framegate:setup` |
| `framegate spec <f> [--mode feature\|bugfix\|refactor]` | create the branch `feat/<f>` and open the spec phase with the mode's templates | start of a feature |
| `framegate spec-check <f>` | mechanical checks of the spec tables, the stubs and the files the plan steps name (below); lists the setup files the approval commits and the documentation files the plan makes writable; exit 1 on a problem | internal |
| `framegate port-stubs <f> [--force]` | a port: write the stubs of its code files from the reference (text kept, function bodies replaced; python); lists imports that will not resolve here ("References and ports") | internal (spec dialogue of a port) |
| `framegate approve-spec <f> [--force]` | spec-check, `[env] refresh`, then freeze the spec and the setup files ("spec OK"); restarts the feature if the spec changed later | gate 1 |
| `framegate run <f>` | run the agents until the next human gate or DONE | after every decision |
| `framegate watch <f>` | follow the progress lines of a run | while it runs |
| `framegate wake <f>` | a run that waits for the Claude usage limit tries again now ("Usage limit" below) | the limit was refilled before the time the run waits for |
| `framegate status [f] [--brief]` | state of one or all features | any time |
| `framegate gate-summary <f>` | the brief of the waiting gate (decision and default first, plain words) | at a gate |
| `framegate approve <f>` | accept the gate: final gate -> retro, then DONE (tests gate -> implement) | at a gate |
| `framegate answer <f>` | apply your answers in `out/<f>/answers.md` (spec, tests, redo of parked work) | at a gate |
| `framegate reject <f> --reason "..."` | send the tests back to the test writer (only with `[gates] tests = "human"`) | tests gate |
| `framegate finish <f> --no-retro` | accept the final gate and skip the retro | final gate |
| `framegate learn <f> K1 [K3 ...\|all]` | keep the project facts the retro proposed (into `.framegate/project.md`) | after the retro |
| `framegate keep <f> 1 3 [...\|take\|all\|none]` | record which retro proposals you accept in the retro backlog | after the retro |
| `framegate backlog [show B3 \| done\|drop\|reopen B3 ...] [--all] [--scope ...]` | the open proposals; one in full; mark it applied / dropped / open again | any time |
| `framegate stats [f] [--json]` | cost, agent and check time, final-gate runs per feature; with `<f>`: per phase and per final run | any time |
| `framegate cleanup <f> [--tags]` | delete the run files; `--tags` also the feature's git tags (after the merge) | after DONE |
| `framegate publish <f> [--dry-run] [--pr] [--title "..."]` | with `[git] state_branch`: framegate's files -> the state branch, the product -> the branch for the pull request, both pushed; `--pr` also creates the pull request, with `specs/<f>/pr_description.md` as its text; `--dry-run` only says what would go where ("Product branch and state branch" below); works after the cleanup too | after DONE, on your yes |
| `framegate pr-description <f> [--pipeline]` | writes the pull request's description again from what is there now and commits it; prints the file ("Product branch and state branch" below, the pull request's description) | when a pull request is created without `publish` |
| `framegate state [init\|save\|guard] [--push]` | the state branch: where it is; `init` creates it from this checkout's framegate files; `save` sends a work branch's framegate files to it; `guard` (re)installs the pre-push hook that keeps framegate's files off every other branch | once per repo; a change outside a feature |
| `framegate gates <f> --scope step\|final` | run the checks by hand (also used in CI) | debugging, CI |
| `framegate spec-brief <f>` | the project's spec conventions (the feature skill reads it) | internal |
| `framegate protocol` | the chat run protocol (the skills read it) | internal |
| `framegate unlock [--force]` | clear a lock left by a killed run | after a crash |
| `framegate hook ...` | the Claude Code hooks (called by the plugin) | internal |

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
| SPEC | spec dialogue in the chat (`/framegate:feature <f>`; a bug: `/framegate:bugfix <f>`; a refactor: `/framegate:refactor <f>`) | **gate 1**: the spec brief (`out/<f>/spec_brief.md`), then `framegate approve-spec` -> tag `spec-approved/<f>` |
| TESTS | test writer -> mechanical checks -> **test reviewer** (`roles/test_review.md`); objections go back to the writer, at most `[agents] test_review_rounds` (2) times | **gate 2**: `out/<f>/tests_gate.md` (ID -> tests -> what it checks, check results, reviewer verdict + remaining objections, all open questions batched) -> approve (tag `tests-approved/<f>`) / reject / answer (answers go into the spec first: SPEC_UPDATE). `[gates] tests = "auto"` passes it without a stop when everything is clean |
| IMPLEMENT | implementer, one fresh run per plan step | step gates green -> commit. Ambiguity: assume from the spec, log it in `steps.md`. No reasonable assumption, or `max_attempts` failed attempts: the step is **parked** (its code set aside as `out/<f>/parked/*.diff`), the run goes on |
| REVIEW | adversarial reviewer (bugs as failing tests, structure findings without tests) -> **triage** by the test reviewer -> fixes -> structure fixes | valid findings fixed (or parked), invalid ones removed with a reason, the code restructured with every test green; final gates + structure report |
| SUMMARY | summary agent (`roles/summary.md`): `final_summary.md` (detail), `final_brief.md` (the gate brief), the code map update (committed) | **gate 3**: the final brief -> approve (retro) / `finish --no-retro` / answer the numbered items (into the spec, then redo only those) |
| RETRO | retro agent (reads the retro backlog; a problem seen again is counted there) | `retro_proposals.md` + metrics row -> DONE; then `framegate keep` records your pick in the backlog |

## Parts

| Part | Where | Docs |
|---|---|---|
| Python package + CLI `framegate` | `src/framegate/` | this file, docs/adapters.md, docs/claude-cli-notes.md |
| Claude Code plugin (`/framegate:setup`, `:feature`, `:resume`, `:status`, hooks) | `plugin/` | docs/plugin.md |

## Daily use: from the Claude Code chat

Once per repository: `/framegate:setup` in the chat (it runs `framegate probe`, reads a little,
shows one table of the project's facts to confirm with "ok" or "5: use black", then writes
framegate.toml, `.framegate/project.md` and the code map, runs `framegate init` and
`framegate doctor --tree --brief` (the setup check and one look at the tree as it is: "The
tree at setup" below), offers to commit the setup's files (`framegate commit-setup`) and a
small smoke feature in one line; a few minutes plus one run of the whole suite), or by hand `framegate init [--language
python|dotnet|generic]` (framegate.toml, spec templates, `.framegate/project.md`, .gitignore
lines), `framegate doctor --tree` (checks config, git, that framegate.toml is on the base
branch, the claude CLI, the chat's allow rules, the tools, the gate commands and the test selection by spec ID, without agents; `--full` only times the
whole suite, `--tree` also says what is red) and `framegate commit-setup`. Any language without a built-in adapter uses the generic adapter: test, coverage,
mutation and format commands with placeholders in `[generic]` (docs/adapters.md), no framegate
code. Also the plugin installed (docs/plugin.md), and an allow rule so the
skills can run the CLI without a prompt after the first turn (an `allowed-tools` grant of a skill
only lasts for the turn it was invoked in): `"Bash(framegate *)"` and `"PowerShell(framegate *)"`
under `permissions.allow` in `.claude/settings.local.json` (or answer the first prompt with
"always allow").

### The tree at setup

A feature and a refactor start on a green tree. Seen in three of three replay runs on real
repositories: the untouched tree was already red (failing tests, lint, format) and the gates
blamed the task; the setup ended with uncommitted files and the first `framegate spec` was
refused. So the setup looks at the tree once per project, and closes with a commit. (A bug
fix records its own start: `[gates] regression_baseline`, under "Markers, commits, known
failures".)

`framegate doctor --tree` (`src/framegate/tree_check.py`) runs the whole suite once (the
final gate's regression run) and uses doctor's own lint / format / typecheck / imports
results and tool list; nothing runs twice. Failing tests are a WARN that needs a decision,
not a FAIL: the setup works, the tree does not. After the verdict it prints plain lines:

| Line | Meaning | What the setup chat does |
|---|---|---|
| `TESTS: 412 run, 3 fail (...)` | the count | |
| `LISTED: <test>: <reason>` | an entry of `[gates] unrunnable_tests`, every time; "(matches no test of the suite)" when it is stale | names them once |
| `PROBLEM: N test(s) fail on the untouched tree and are not listed:` then one line per test with the first line of its failure | red tests nobody decided about | per test: this machine cannot run it (it needs a database, a service, another operating system) -> proposes the entry, the user confirms, the chat writes it; else it is really red -> recommends a bug fix first (`/framegate:bugfix <name>`), before the first feature |
| `PROBLEM: the whole suite did not run to the end` / `gave no per-test results` | nothing is known about the tests (doctor FAILs) | fixes the test command or `[gates] regression_timeout` first |
| `DEBT: <check>: N line(s) in M file(s) (most in ...)` | a lint / format / typecheck / imports check fails on the code as it is; its size is the output lines that name a file | proposes ONE clean-up task (`/framegate:refactor <name>`) as a follow-up the user accepts or skips; no question per file |
| `MUTATION: <tool> cannot run on this machine: <why>` | e.g. WSL has no `python3` (doctor's tool check; for mutmut in WSL it now asks WSL instead of waiting for the first mutation run) | says so in one line |
| `Tree: green.` / `Tree: not green (see the lines above).` | the last line | |

`[gates] unrunnable_tests` (`src/framegate/unrunnable.py`): tests this machine cannot run,
each with its reason:

```toml
[gates]
unrunnable_tests = [
  { test = "tests/test_db.py", reason = "needs a PostgreSQL server" },
  { test = "tests/test_tty.py::test_resize", reason = "Linux only" },
]
```

- `test` is a name as the test report prints it, a pytest node ID, a test file, or a folder
  of tests; its name parts must appear in a test's name in a row (`tests/test_db.py` covers
  `tests.test_db::test_connect[1]`; `Shop.Tests.DbTests` covers every method of that class).
  `test` and `reason` are both required; anything else is a config error.
- The engine cannot tell "this machine lacks a database" from "really broken". The chat
  proposes, the user confirms, then the entry is written. Nothing is listed by itself.
- The listed tests still run with the suite: where the database exists they pass and count.
  A **failure** of a listed test is left out, in every mode (feature, bug fix, refactor):
  at the step gates (the step's tests, the whole suite, the review tests), in the final
  regression check and in the record `[gates] regression_baseline` takes. The check's result
  line says "cannot run on this machine, left out: N". Any other failing test still fails.
- Named every time: the final gate report starts with "Tests this machine cannot run", and
  final_facts.md has "Not checked on this machine" with each test and its reason, for one
  line in the summary. A green result says nothing about these tests.
- Limits: a run without per-test results cannot be told apart (it fails as before). A test
  file that cannot even be collected (an import that is missing here) stops the whole run:
  take it out of the test command (`[python] pytest_args = ["--ignore=..."]`). With
  `coverage_scope = "suite"` the final coverage run is the regression run and fails on any
  red test, as it does for the regression baseline.

Format and lint debt during a task needs no setting: the format fix formats a file the task
touches whole and counts those lines for the summary; files the task does not touch are left
alone ("Markers, commits, known failures").

`framegate commit-setup [--dry-run] [PATH ...]` commits what the setup wrote, in one commit
of its own ("framegate: setup") on the current branch: framegate.toml, the knowledge file,
the code map, the spec template folders, `.gitignore`, the packaging / test / tool-config
files that changed (python: the `req` marker line in the pytest config), and each `PATH`
(a tag helper the setup added). Only these paths are committed: other changes, staged or
not, stay as they are; ignored files are never added. With `[git] state_branch` framegate's
own files are left out (they live on the state branch: `framegate state init`), so only the
product-side files are committed. `--dry-run` lists the files; the setup chat shows the list
and asks once (default: yes).

Then you only type requirements, answers and gate decisions:

A bug fix starts with `/framegate:bugfix <name>` and a refactor with `/framegate:refactor
<name>` instead of step 1: the same flow with another first proof ("Modes" below).

1. `/framegate:feature <name>`: creates the branch `feat/<name>` from `[git] base` (`main`) in
   this checkout (refused with uncommitted changes to tracked files: the refusal lists them
   and the one `git commit` command that commits them as they are; an existing branch is used
   only if it is this feature's), sets the SPEC phase and starts the spec dialogue. Claude
   decides what the docs, the code map, the code and `.framegate/project.md` settle, asks only
   only the real decisions (see "Which questions are asked" below), writes
   requirements/design/plan and the interface stubs, and shows the **spec brief** instead of the
   spec files (see "Manager-style briefs" below).
2. "Spec OK": the skill approves the spec, starts `framegate run` in the background and shows the
   live view: one line per phase start/end, check result, failed attempt, assumption, park and
   gate, each with the cost so far (~10-30 lines per feature). The chat stays usable.
3. At the final gate (and the tests gate, if `[gates] tests = "human"`) the chat shows the
   gate brief (`framegate gate-summary`), Windows shows a toast, and Claude Code waits for you.
   Reply in plain words: "ok" (with numbered items: take every default; without: approve, retro,
   then DONE), answers by number, "approve as is", "no retro", "reject: <why>" (tests gate
   only). The skill runs the matching command and continues the run in the background.
4. DONE: the skill shows the retro's numbered proposals (problem -> fix, with a recommendation)
   and its knowledge facts, you answer with numbers; it asks one question, "publish and
   create the pull request?", whose default is yes (`gh pr create`; with `[git]
   state_branch`: `framegate publish --pr`, which pushes a branch with the product only;
   "publish without a pull request" and "no" are answers too, and nothing is pushed before
   your reply). The pull request's text is `specs/<name>/pr_description.md`, written by
   framegate. Then it cleans up the
   run files (`framegate cleanup`; `specs/<name>/` and the metrics row stay). After the PR is
   merged, `/framegate:status <name>` offers removing the feature's tags.

After a session restart: `/framegate:resume [name]` reattaches (live view, the waiting gate or the
wrap-up). `/framegate:status` lists the features and where they stand.

## Manager-style briefs

The human is the manager of a feature: clear input, outcomes checked. The spec files stay
precise and are written for the agents; wherever the human reads or decides, the chat shows a
short brief instead: the decision and its default first, about one screen in plain words, no
spec ID without its meaning, the detail files only on request ("more detail on step 2"). There
is no mode or switch for this.

| Where | The brief |
|---|---|
| Spec gate (`out/<f>/spec_brief.md`, written by the chat) | Task (2-4 lines) / Plan (one line per step, with its main file) / How it is proven (the 3 most important checks) / Not proven (the 3 most important things to check by using it) / Decisions I made for you (one line each, with what it rules out) / Project details that matter / Setup files changed (config, packaging and tool files the spec phase changed; approved and frozen with the spec) / Questions (the real decisions, by impact) |
| Tests gate (`[gates] tests = "human"`/`"auto"`) | what the tests check in plain words (the test reviewer's `## What the tests check` in test_review.md), objections left, one line of mechanical checks, questions with defaults |
| Final gate (`framegate gate-summary`, from `final_brief.md` + run records; ~20 lines when nothing went wrong) | Steps (the spec brief's plan again: one line per step with how it went, as planned / changed / parked, what happened inside it and its main file) / Struggles (only what happened outside the steps; one line when none) / Decisions I made / Outcomes with numbers (gates, diff coverage, mutation, failed attempts, parks, cost, agent and check time, structure) / Proven and Not proven (the 3 most important points each, the rest on request) / Code map (one line; a WARN when new public types are missing) / Questions numbered 1..k by impact (the problem in one line, then `A: ... (default) / B: ...`) / Decisions I made / how many gate items were left out |
| Setup | one fixed table, shown every time, each row with its value, where it comes from (a file or `framegate probe`) and, for a tool, whether it answered: language / adapter, interpreter, test command, lint, format, types, source / test folders, base branch, shared repository (state branch yes / no), locked deploy / CI paths, reinstall command (`[env] refresh`), reference projects. Reply "ok" or the row and the change ("5: use black"). Below it: the knowledge facts in short lines, the code map's modules in one line, one line saying the rest uses the defaults. No detail questions (slow tests, thresholds, risk limits); a "Blocker:" line only for what the repository cannot answer (no test framework, no way to tag tests, no project interpreter) |
| `framegate doctor --brief` | one verdict line (ready / not ready), then only the FAILs with the fix and the WARNs that need a decision |
| Retro (`## Brief` in retro_proposals.md) | numbered proposals, problem -> fix, recommend take / skip / fold into N (and `repeats B3` for a problem the backlog already holds); the knowledge facts; answered with numbers (`framegate keep`, `framegate learn`) |
| Smoke run offer, DONE wrap-up, status, resume, errors, interruptions | one or two lines: the state and the one decision needed, with a default |

At a gate with numbered questions, "ok" takes every default (the chat writes them to
answers.md); answers by number work as before; "approve as is" approves and leaves them open.
Final-gate answers that only take the defaults or keep things as they are ("Default: ...",
"accept", "leave it") and then change nothing (the spec update and the test writer commit
nothing, no new question, nothing to restructure, the final checks had passed with every tool
giving a result) approve the feature with the checks you saw: the retro runs next, with no
second round of checks, summary and final gate (`human_gate` event with `from_answers`). Seen
live: such answers restarted everything, 26 minutes, for the same result. Any change puts the
normal round back.
One line per bullet; a section where nothing happened is one line; no sentence repeats a heading
or another section.

### Which questions are asked

No count cap anywhere (spec dialogue, spec / final gate briefs; the setup asks nothing but its
table and blockers, see above). A point is a question
only when it is a real decision: two or more reasonable options with different outcomes, and the
spec, the code, `.framegate/project.md` and earlier answers don't settle it. Otherwise it is not
a question: only one sensible option -> decided, one line under "Decisions I made"; already
answered, already resolved (e.g. by a scan) or a note -> left out; the same question from two
agents -> one item. Questions are ordered by impact (what changes most if answered differently
first).

At the final gate the summary agent sorts every numbered gate item (parked work, the provisional
steps, the open questions) into `## Questions`, `## Decisions I made` or `## Left out` of
final_brief.md, naming it `[N]`. Parked work and the provisional steps are always asked (they
block the run); an item the brief places nowhere is asked as it is. The human sees the questions
numbered 1..k; `framegate answer` maps answers.md back to the gate items and rewrites it
(`# Answers (final gate, numbered by gate item)`): a decided item gets `Default: <decision>`, a
repeated one the answer of the item it repeats, any other left-out item "No answer needed"; the
chat's own file is kept in the `answer` event (`as_given`). After the summary agent's run framegate
checks the brief (not blocking): questions without `[N]` or with one option, duplicates, closed
items or notes asked as questions, parked work left out, gate items placed nowhere -> the summary
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
  max_file_lines` (400; 0 = off) that this feature made longer becomes a numbered item at the
  final gate with a default: split it now if this feature took it over the limit, else leave
  it for later. A file over the limit that the feature changed without making it longer is
  listed, not asked about, and does not count against the risk rating. "Split it" goes to
  the structure run; "leave it" starts no run. The file is not asked about again. The
  target files of a port (the spec's `## Ported from` table) keep the reference's file
  layout: the report lists their size ("ported: the reference's file layout is kept"), and
  they raise no gate item, no `GREW` line at a step gate and no "already over the limit"
  note in the implementer task.
- Before that, so the code is written in the right place: the spec brief and every
  implementer task name the source files that are already over the limit (the 12 longest),
  the plan template asks a step that would add to one of them to name the new file, and
  every step gate prints the changed files over the limit with `before -> after` and `GREW`
  (not blocking; the `gate` event lists them in `grown`). Seen live: two steps added to files
  of 1,093 and 835 lines, both step gates passed, and the reviewer's finding cost a fix run.
  Seen live: FCSim M5-freekick-review grew SetPieceShotModel.cs to 694 lines, mixing the kick
  model with the review calculators.
- The code map (`[docs] code_map`, default `docs/code-map.md`): a living Markdown doc, per
  module its purpose, its key classes (one line each), how they work together (short flow,
  mermaid optional), "where to look for X"; under ~300 lines. `/framegate:setup` drafts it
  from the code; the SUMMARY phase updates the sections the feature touched (framegate commits it
  as `<f>: code map`); a new public type in the feature's diff that the map does not name is a
  WARN on the final brief's code map line. `framegate doctor` WARNs when it is missing.

## Modes: feature, bug fix, refactor

What a task is decides the first proof of its tests. The mode is the `Mode:` line of the
spec's requirements.md, written by `framegate spec <f> --mode bugfix|refactor` (the chat:
`/framegate:bugfix <f>`, `/framegate:refactor <f>`); no line means feature. Everything after
the tests (steps, step gates, review, final gates, the two human gates) is the same flow.

| | feature | bug fix | refactor |
|---|---|---|---|
| Spec (requirements.md) | requirements, edge cases, stubs | bug report: reproduction rows (R), must-not-change rows (I), suspected causes | scope, behavior to preserve (R, I), structure targets |
| Tests | new, from the spec | new, from the bug report | existing tests first: a test that already asserts a row gets the row's ID as a tag; new tests only for the gaps |
| First proof (`test_checks.md`) | every test FAILS against the stubs | reproduction tests FAIL on today's code (each listed with why, for the test reviewer); must-not-change tests PASS | every test PASSES on today's code; the check lists how many tagged tests existed before and how many are new |
| Steps | per plan step, batched | usually one; the step note names `ROOT CAUSE` and `RULED OUT` lines (shown in final_facts.md and the brief) | one move per run; every step is checked with ALL the tests; a step that changes no source file fails |
| Review | against the spec | also: the same bug through another input or caller | a finding is a difference from the code before (`git show tests-approved/<f>:<path>`) |
| Runs after the review (review fixes, structure fixes, final repair) | checked with the feature's tests and the review tests (the whole suite runs once, in the final gates) | the same | the same |
| Risk rating | every changed line counts | every changed line counts | moved lines are not counted (`moved_lines` in the `risk` event) |
| Final-gate repair | code, then tests | code, then tests | code only: tests written after the refactor would pin the changed code, so weak tests go to the human |

Templates: `specs/_template_bugfix/`, `specs/_template_refactor/` (framegate's own unless the
project has a copy). The events `spec_started` / `spec_approved` carry `mode`; `framegate
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

Scope coverage (refactor, `src/framegate/checks/scope_coverage.py`). Before any code moves,
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

## References and ports

**Reference folders.** Another project on this machine (a sibling repository) that the agents
may read and never write. Declared once in framegate.toml, chosen per feature in the spec:

```toml
[references]
otto = { path = "../da-otto", note = "the runner this repository ports" }
core = "../da-core"            # path only; relative to the repository root, or absolute
```

- `framegate doctor` lists each one with its commit; a folder that is missing is a WARN
  (paths differ per machine). `framegate spec-brief` shows them to the spec dialogue.
- The spec names the ones the feature uses: requirements.md, `## References` (name, files /
  folders that matter, what for; all three templates have the section). Only those reach the
  agents. A name that `[references]` does not declare fails `framegate spec-check`. The chat
  lists them on the spec brief ("Project details that matter").
- `approve-spec` pins them: each one's git commit and whether it had uncommitted changes go
  to `progress.json` (`references`) and a `references_pinned` event, and are printed. Every
  `framegate run` compares: another commit, uncommitted changes or a missing folder is a
  `reference_changed` event and a `WARNING:` progress line, never a stop.
- Every agent run of the feature gets the folders as `claude --add-dir <folder>...` and a
  deny rule for edits there (`--disallowedTools "Edit(//<folder>/**)"`), and its task's
  orientation block names them: name, absolute path, note, pinned commit, read-only. The
  write guard (PreToolUse hook) blocks every agent write outside the repository as before.
  What was checked live: docs/claude-cli-notes.md, "Reference folders".
- A reference folder that is missing on this machine is left out of the run (the orientation
  says so).

**Ports.** A feature that copies code of a reference ("file for file, same names, so a diff
between the two repositories shows only the additions"). No mode of its own yet: a mode is
built after two port runs showed what repeats. A feature is a port when its requirements.md
fills `## Ported from` (feature template; "None." otherwise):

```markdown
## Ported from
Reference: otto
Commit: 1a2b3c4d5e

| Source (path in the reference) | Target (path in this repository) | Kind (code / tests) |
|---|---|---|
| runner/controller.py | runner/controller.py | code |
| tests/test_controller.py | tests/runner/test_controller.py | tests |

What may differ:
- import names: `otto.` becomes `argos.`
- `Controller.report()` exists only here

Kept quirks:
- runner/common.py: is_name(): the regex `$` also accepts a trailing newline
```

Ticket / plan / ADR numbers and repository names inside the reference's docstrings and
comments stay the reference's, word for word, unless "What may differ" lists them: they say
where the code comes from, and every reworded line is a difference someone has to explain.

| Who | What changes for a port (added task text, `references.port_note`) |
|---|---|
| Spec dialogue | the IDs describe what the reference does today; questions only about what differs. It asks for the reference's quirks ("Kept quirks", or a fix here plus a "Reference follow-up" line) and for carried tests that cannot fail before the implementation ("Green against stubs"). The stubs of the `code` rows are not written by hand: `framegate port-stubs <f>` (below). plan.md: one step per target file, leaf modules first; a step covers the IDs its file serves |
| Test checks | test_checks.md, "Reference tests carried over" (not blocking): per `tests` row the reference file's test functions and the ones whose name is not in the target file ("NOT CARRIED OVER: <name>"); python, other languages `SKIPPED`. One line in the final brief's outcomes ("reference tests carried: 14 of 60; not carried: see ...") |
| Test writer | carries over the listed reference tests: same names, order, structure and assertions, adapted only for imports and "What may differ", tagged with the spec IDs. A new test only for an ID no reference test covers, listed in test_summary.md under `## New tests (not in the reference)`; a test that cannot be carried over under `## Reference tests not carried over` |
| Test writer, later runs | answers at the final gate (TESTS_AMEND): a test change follows the reference (carry over the reference test that covers it, else the smallest change to the carried-over test). Strengthening after weak final gates (TESTS_STRENGTHEN): first a reference test that was not carried over; a test of its own only where no reference test exists, listed under `## New tests (not in the reference)` |
| Test reviewer | reads each reference test file next to its target: a missing, renamed, rewritten or weakened reference test is an objection, and so is a reference test not carried over (the report above) without a reason tied to "What may differ" or to code that is not ported yet |
| Implementer | copies the reference file and changes only what "What may differ" lists: no rewording, no restructuring, comments and docstrings kept; any other change is logged as an assumption. Kept quirks stay. A bug of the reference fixed here gets a `REFERENCE FOLLOW-UP` line in the step note |
| Step gates | **fidelity at every step and fix gate**, not blocking: each ported target file the run touched against its reference file, with the final report's numbers and the rule "every hunk must match a 'What may differ' bullet; restore the rest". The lines are part of the gate report, so a failed attempt's feedback holds them; the next step's task lists what the earlier steps left different; the `gate` event has `fidelity` |
| Reviewer | a difference from the reference that the spec does not list is a finding: a failing test when behavior differs, else a `## Structure findings` bullet ("restore the reference's text"), applied by the structure-fixes run. The readability checklist does not apply to ported files. No finding about a kept quirk. A finding about behaviour the reference has too stays a finding when the spec promises otherwise, with a `REFERENCE FOLLOW-UP` line in review.md |
| Triage | a finding about a kept quirk is INVALID, with the "Kept quirks" line as the reason; "the reference does the same" is no reason to dismiss; a VALID finding the reference shares gets a `REFERENCE FOLLOW-UP` line in triage.md |
| Final gates | **fidelity report**, not blocking: per source -> target row the changed / added / removed line counts against the reference file (at the pinned commit; the working file when the reference was not clean or is no git repository), in final_gates.md; the diffs in `out/<f>/fidelity/<target>.diff`; one line in the final brief's outcomes. `SKIPPED` with the reason when the reference is not on this machine |

`approve-spec` warns when the section's `Commit:` is not the commit the reference is at.
The first proof stays the feature mode's: the carried-over tests must fail against the stubs,
except the ones the spec lists under `## Green against stubs` (below).

**Stubs of a port: `framegate port-stubs <f> [--force]`.** For every `code` row the target
file is written as the reference file's text (at the spec's `Commit:`; after the approval at
the pinned commit; "as it is now" when that commit does not have the file) with the bodies
of functions and methods replaced by `raise NotImplementedError`. Module, class and function
docstrings, signatures, annotations, decorators, constants, imports and all other
module-level code stay byte for byte; no spec IDs are added (the tests' tags carry the
traceability). So a file the implementer leaves alone is identical to the reference, and
its wording is the reference's from the start. Rules:
- a function whose body already does nothing (`...`, `pass`, `raise NotImplementedError`:
  an overload, a protocol or abstract method) stays as it is;
- a function that runs while the module is imported (called at module or class level, used
  as a decorator, or called by such a function; not the `if __name__ == "__main__":` block)
  keeps its body, and the command says so ("kept whole"): with the body replaced the stub
  could not be imported. A test of such a function passes before the implementation: list
  it under "Green against stubs";
- a `code` row that is no source file of the language (data, config) is copied as it is;
- imports are the reference's. Each one that will not resolve here is printed as `IMPORT:
  <file>:<line>: ...` (a module ported under another name, a module that is not ported, a
  package the project's interpreter does not have): fix those lines in the stub by hand
  (the red check needs tests that can be collected) and list the kind of change under
  "What may differ";
- an existing target that is not the generated stub is kept (`KEPT:`; `--force` overwrites);
  after the spec approval the command refuses without `--force`;
- python only. Another language prints `NOT SUPPORTED` (exit 0) and the spec dialogue
  writes those stubs by hand, with the reference's names, signatures and doc comments.

**Green against stubs (any feature, not only a port).** requirements.md, `## Green against
stubs`: the tests that cannot fail before the implementation, by design, one bullet each,
`- <test name or node ID>: <reason>` (a scan "no analytic name appears in the code", a
negative check, a structure test; in a port a carried-over test of that kind). "Red against
stubs" then accepts exactly those when they pass ("passes by design (listed ...)"); a listed
test that fails against the stubs fails the check ("LISTED AS GREEN BY DESIGN BUT RED"), any
other passing test fails it as before, and so does a spec ID whose every tagged test is green
by design ("ONLY TESTS THAT PASS BY DESIGN PROVE <ID>"; from the tag scan). A listed test
that is not among the feature's tests is a note. `spec-check` refuses a bullet that is not
`- <test>: <reason>`. The section is general because a normal feature can have such a test
too (it cost nothing more); bug fixes and refactors have no red check and no such section.

**Reference follow-up.** What the reference should change because of the feature (a bug
fixed here that the reference shares, a deliberate improvement) has the life cycle of the
pipeline follow-up ("Pipeline follow-up" below; `followup.py` holds both lists):
requirements.md `## Reference follow-up` (all three templates, `- <reference file>: <what
should change> (<why>)`), agent lines `- REFERENCE FOLLOW-UP [<reference file>]: <what and
why>` (implementer step note, review.md, triage.md; the tasks of a feature that uses a
reference say so), `out/<f>/reference_followup.md`, a section of final_facts.md and
final_summary.md, the final brief ("Reference follow-up (for otto; not part of this
feature): N; nothing to answer"), and after DONE `specs/<f>/reference_followup.md` plus a
`reference_followup` event. The reference itself is never changed.

## The retro backlog

`framegate keep <f> <numbers | take | all | none>` records the human's pick of a retro's
proposals in `metrics/retro_backlog.md` (tracked, committed): one row per proposal with a
status (`open` = accepted, not applied yet; `applied`; `dropped`; `skipped` = not accepted),
its scope (framework / project), the features whose retro raised it, and a pointer into
`metrics/retro/<f>.md`, a copy of that retro's full text (`framegate cleanup` deletes the run
folder). A proposal the retro folded into another gets no row.

The next retro gets the rows at the end of retro_input.md (`## Backlog`). A problem a row
already describes is not proposed again: the retro's brief line names the row (`repeats B3`),
and framegate adds the feature to that row's "Seen" when the retro ends. Picking such a
proposal reopens a skipped, dropped or applied row. A first-time problem that looks like a
one-off is recommended `skip`; it becomes a `skipped` row and is counted if it comes back.

`framegate backlog` lists the open rows (most often seen first; `--all`, `--scope
framework|project`), `backlog show B3` prints one with the retro's section, `backlog done |
drop | reopen B3 [--note "PR #12"]` changes its status. Framework-scope rows are applied in the
framegate repository; mark them `done` here with a note.

## Stats

`framegate stats` reads `out/events.jsonl` (no agents): one line per feature with its cost,
the time agents and gate checks ran, the time a gate waited for you, the final-gate runs,
the spec time (from `framegate spec` to the approval: the dialogue in the chat; the chat's
own cost and the talk before the command are not in the log), failed step gates,
questions, assumptions and risk level, then the averages of the finished
features. `framegate stats <f>` adds the agent runs per phase (runs, cost, time, output tokens),
every final-gate run with its failed checks and three slowest checks, and the repair runs.
`--json` prints the numbers.

## Project knowledge

`.framegate/project.md` (`[project] knowledge`; `framegate init` creates it) holds what no
single spec repeats: architecture rules, what must never change (hashes, save formats,
public APIs), test-harness facts (slow tests, how fixtures and seeds are made, scan
commands), tools, off-limits. The spec dialogue gets it through `framegate spec-brief`;
the test writer, test reviewer, implementer, reviewer, spec updater, diagnosis and retro
get it in their instructions (up to 300 lines; keep it short). Agents cannot edit it. The
retro ends with a "Project knowledge" section (`- K1 [<section>]: <fact>`); at DONE the
chat shows them and `framegate learn <f> K1 K3` (or `all`) appends the ones you pick under
their section, commits and tags `knowledge-update/<f>` (the frame diff allows that
change). The engine stays generic; each project's knowledge grows with its runs.

## Product branch and state branch

For a repository a team shares: a pull request must carry the product and nothing of
framegate. Off by default (`[git] state_branch = ""`: framegate's files are committed on the
feature branch and come along in its pull request, as in a repository of your own). On:

```toml
[git]
base = "dev"                         # or "origin/dev"
branch_prefix = "feature/"
state_branch = "framegate/{base}"    # -> framegate/dev
```

**Three branches, two kinds of files.**

| | holds | pushed | merged into the base |
|---|---|---|---|
| state branch `framegate/<base>` | only framegate's files | yes (`publish`, `state save --push`) | never: its history shares no commit with the base, so git and the hosts refuse |
| work branch `<work_prefix><f>` (`framegate-work/<f>`), local | the base + the state branch merged in + the feature's commits: one working tree with both kinds of files, as the agents and the gates need it | never | no |
| product branch `<branch_prefix><f>` on the remote | the base commit the feature started from + one commit with the feature's product files | yes (`publish`) | yes, by the pull request |

*framegate's files* (one function, `publish.workflow_paths`, used everywhere): `framegate.toml`,
`.framegate/`, `[paths] specs` (templates and every `specs/<f>/`, the pipeline follow-up
included), `[paths] metrics` (metrics rows, the retro backlog), the knowledge file, `[roles]
extra_dir`, and the code map (`[docs] code_map`; `[git] publish_code_map = true` makes it
product documentation that goes to the pull request). *The product* is everything else the
feature changed: source, tests (review tests included), the project's own setup and packaging
files. `out/` and the lock files are ignored as ever, but through this clone's
`.git/info/exclude` (`state init` and `spec` write it): `.gitignore` is a product file, and
the lines `framegate init` adds to it are not needed (`git checkout -- .gitignore`).

**Life cycle.**

1. *Once per repository:* `/framegate:setup` writes framegate's files into the checkout, then
   `framegate state init` creates the state branch from them (committed or not; nothing is
   pushed without `--push`). The base never gets a framegate file; `framegate doctor` warns
   when it holds one under framegate's paths (a project with its own `metrics/` folder sets
   `[paths] metrics = ".framegate/metrics"`).
2. *A feature starts:* `framegate spec <f>` fetches the state branch (creates the local one
   from the remote's, moves a local one that is behind forward), creates the work branch from
   `[git] base` and merges the state branch into it. The trees do not overlap, so this merge
   cannot conflict, however far the base has moved. Untracked copies of framegate's files in
   the checkout (left by the setup) are removed when they equal the state branch's; one that
   differs refuses. The command works in a checkout without `framegate.toml` (the base branch,
   a fresh clone): the config is read from the state branch (found by its default name,
   `framegate/<current branch>`, else the only `framegate/*` branch; a state branch under
   another name is found only from a branch that has `framegate.toml`).
3. *During the run* nothing differs: one working tree, the same commits (they stage the
   source and test folders and framegate's paths), the same private refs.
4. *After DONE, on your OK:* `framegate publish <f> [--pr] [--title "..."]`, or first
   `--dry-run`, which prints what would go where and changes nothing.
   - *State:* the work branch's framegate files become one commit on top of the state
     branch's newest commit, and the branch is pushed. If the state branch moved meanwhile
     (another feature was published), it is merged into the work branch first, by git, with
     the right base; a real conflict (two features changed the same framegate file) is undone
     and reported with the command to resolve it by hand. The metrics file merges by union
     (each feature adds a row).
   - *Product:* one commit whose tree is the work branch's tree without framegate's files and
     whose parent is the base commit the feature started from (`git merge-base` with `[git]
     base`): a normal feature branch, which the pull request merges, with the conflicts a
     moved base may bring, on the host as for any branch. Two checks on the commit itself,
     apart from how it was built, refuse the publish: it holds no file under framegate's
     paths, and it differs from the work branch in nothing but those. It is pushed as
     `<branch_prefix><f>`: the team sees the usual branch name. What is lost: the feature's
     single commits (tests approved, steps, review fixes) stay on the local work branch.
   - *Again:* a second `publish` finds nothing to do. After more work (review feedback), the
     new product commit goes on top of the published one: a fast-forward, never a rewrite.
   - *Pull request (`--pr`; the chat's publish question has it in its default, yes):*
     GitHub: `gh pr create`; Azure DevOps (`dev.azure.com`, `*.visualstudio.com`, their
     ssh forms): `az repos pr create`, when `az` and its `azure-devops` extension are
     there; the description is `specs/<f>/pr_description.md` (below). Without the tool,
     on another host, when creating fails or without `--pr`: one line that says why it
     was not created, the address that opens the pull request by hand and where its
     description is. Only the line `Pull request ... created: <address>` means it exists.
   - *The pull request's description* (`description.py`): written by framegate itself, no
     agent, for a reviewer who has not seen the run. Sections: What (the spec brief's
     Task; a bug fix: Bug / Right result; a refactor: Goal / What moves), To do by hand
     outside the repository (the unticked checklist), Changes (the steps as the final
     brief lists them, how each went, its main file), How it is proven (the brief's
     Proven lines and one line of numbers: final checks, tests, diff coverage, mutation),
     Not proven / check by hand, Decisions, Deploy / CI changes (the done items of the
     pipeline list: "the second commit of this pull request"; open ones and ones closed as
     not needed are named as such), and a last line `Built with framegate <version>; N
     tests`. No cost figure, no path into `out/`. It always fits Azure DevOps' 4000
     characters: when too long, the least important sections lose their extra lines first
     (decisions, not proven, deploy / CI, proven; the steps and the checklist keep every
     line), no section is cut in the middle, and the last line says `(shortened)` once.
     Written at DONE with the metrics row, again by `pipeline-approve` and by `publish`
     (committed on the work branch when it changed: `<f>: pull request description`). It
     is one of framegate's files: on the state branch, never on the product branch, and
     `cleanup` keeps it, so the order of publish and cleanup does not matter and a
     cleaned-up task still gets its pull request (`publish` needs the spec folder and
     git, never `out/`). Run files gone: the text stays and only the two lists are read
     again. A task finished by an older framegate (no description, no run files): built
     from requirements.md, plan.md and the list, and `publish` says in one line that it
     is short. A deploy / CI change with a pull request of its own has its own text,
     `specs/<f>/pipeline_pr_description.md`: the approved brief, else the list, the
     checklist on top. `final_summary.md` stays the run's internal report.
5. *After the merge:* `framegate cleanup <f> --tags` removes the feature's private refs and
   the product ref; delete the local work branch yourself. The next feature starts from the
   base again.

**What it refuses, and never does.** It never force-pushes, never commits on or pushes the
base, never rewrites the work branch (the private refs `spec-approved/<f>`, ... keep pointing
into it, and the frame check reads them there as before). It refuses: uncommitted changes,
and untracked files in the source, test or framegate folders (they would stay out without a
word); a feature that is not DONE (`--unfinished` overrides); a base commit that holds files
under framegate's paths; a state branch that has diverged (this clone and the remote both have
commits the other lacks: it prints the three git commands); a state branch that holds foreign
files; a remote branch `<branch_prefix><f>` with other content (someone pushed there):
`--force-with-lease` replaces it, and only if it still is what was seen.

**The work branch is never pushed: two safeguards.**

- *Its own name.* The work branch is `<work_prefix><f>` (`[git] work_prefix`, default
  `framegate-work/`), never the name the team sees: a `git push` out of habit cannot land on
  `<branch_prefix><f>`. `spec`, `status`, resume, `cleanup` and the chat use the work name; the
  product commit is the only thing ever pushed as `<branch_prefix><f>`. A local branch still
  under the product branch's name that holds `framegate.toml` (a feature started the old way,
  or before this) is renamed to the work name by `state init`, `spec` and `publish` (not by a
  dry run); not when the remote has a branch of that name that is neither this commit nor the
  product this clone published: then it refuses and prints the `git branch -m` line.
- *A push guard.* `state init`, `spec`, `state save` and `publish` install a `pre-push` hook
  in this clone (`framegate state guard` does only that; `framegate doctor` checks it is in
  place and current). It refuses every push whose commit holds a file under framegate's
  paths unless the remote ref is the state branch: the work branch under its own name, under
  any other name, by `HEAD:...`, within `--all`. `publish`'s own pushes pass (the product
  commit holds none; the state branch is the exception). It is plain sh and git with the
  paths and the state branch written into it, so it needs no `framegate` on `PATH` and runs
  under Git for Windows; after a change of framegate's paths doctor reports it out of date
  until `framegate state guard` rewrites it. An existing `pre-push` hook of yours is kept: it
  is moved to `pre-push.user`, and the guard runs it first, with the same arguments and
  input, and stops if it fails. Nothing is installed, and it says so (doctor WARNs), when
  `core.hooksPath` is set (hooks the repository manages itself, e.g. husky: add the check
  there if you want it) or when `pre-push` and `pre-push.user` are both hooks of yours.

`git push --no-verify` skips every hook, this one included. The second line stays: a
remote `<branch_prefix><f>` that holds anything but the product makes `publish` refuse
(other content), and `publish --force-with-lease` replaces it with the product.

**The tests in the pull request and framegate's marker.** Python tests carry
`@pytest.mark.req("XXX-R-001")`. The marker comes from framegate's pytest plugin; in the
team's CI or a colleague's environment without it, pytest warns about an unknown marker and
fails with `--strict-markers`. So the project registers the marker itself, once: `req(*ids):
spec IDs this test proves` under `markers` in its pytest config (`pytest.ini`, or
`[tool.pytest.ini_options]` in `pyproject.toml`). `framegate doctor` checks it by asking
pytest with the plugin switched off (`team: pytest without framegate knows the req marker`);
`/framegate:setup` adds the line. It is a setup file: the first approved spec commits it,
and it reaches the base with that feature's pull request. `--req` (the selection by spec ID)
stays framegate's: the team's runs do not need it. .NET needs nothing: `[Trait("Req", ...)]`
is xUnit's own attribute. A test that imports a package the project does not declare (e.g.
`hypothesis`) fails in the team's CI like any missing dependency: declare it at spec time.

**A second machine, a colleague.** With the plugin installed there: `git clone <url>` (or
`git fetch`), then `/framegate:feature <name>` (= `framegate spec <name>`): it fetches the
state branch, creates the work branch with framegate's files, and the start check installs
the workflow tools into the project's environment. `/framegate:setup` on a repository that
has a state branch only installs the tools.

**A change outside a feature** (a config edit, kept retro proposals, a knowledge fact):
commit it on a work branch, then `framegate state save [--push]` (the state half of
`publish`). `framegate state` shows where the state branch is and whether the push guard is
in place.

**Moving a project that was set up the old way** (framegate's files committed on the feature
branch `<branch_prefix><f>`, nothing pushed), on that branch:

```
# framegate.toml, [git]: state_branch = "framegate/{base}"
git commit -m "framegate: state branch" framegate.toml
framegate state init                 # renames the branch to framegate-work/<f>, creates the
                                     # state branch from its framegate files, installs the guard
framegate publish <f> --dry-run      # what would go where; look at the product file list
framegate publish <f> --pr --title "<f>: <one line>"
```

Product files the old setup changed (the lines `framegate init` added to `.gitignore`) are in
the dry run's list; to keep one out of the pull request, put the base's version back on the
work branch first (`git checkout <base> -- .gitignore`, commit). From then on features
start from the base (`git switch <base>`, `/framegate:feature <name>`).

Not possible together: a CI job that runs `framegate gates` on the pull request (`[git]
markers = "tags"`): the product branch has no `framegate.toml` and no specs.

## CLI (the engine; manual fallback)

The skills only call these commands; everything works the same from a terminal.

```
framegate init [--language python|dotnet|generic]  # framegate.toml, specs/_template/,
                                            # .framegate/project.md, .gitignore lines
framegate doctor [--full] [--tree] [--brief]  # check the setup without agents; ends with a
                                            # verdict + what needs action (--brief: only that);
                                            # --tree: the whole suite once, and what is red on
                                            # the code as it is ("The tree at setup")
framegate commit-setup [--dry-run] [PATH ...]  # the setup's files in one commit of their own
framegate probe [--json]                    # the project facts in seconds: interpreter, test /
                                            # lint / format / type commands (+ does each tool
                                            # answer --version), folders, base branch, remote,
                                            # paths to lock, editable install; nothing is run
                                            # on the code; works before init
framegate update [--plugin-version X]       # tool and project tools at the plugin's version
framegate preflight [<f>] [--plugin-version X] [--check]
                                            # the skills' start check: tool at the plugin's
                                            # version (prints the install command), project
                                            # tools at the tool's version; a task in progress
                                            # or --check: nothing is updated, the update
                                            # waits; restart notice; off: [update] check =
                                            # false, FRAMEGATE_UPDATE_CHECK=0
framegate spec <f> [--mode feature|bugfix|refactor]
                                            # branch feat/<f> from [git] base + SPEC phase,
                                            # with the mode's spec templates
framegate spec-check <f>                    # rules without a spec ID, copied code not in the
                                            # file, forbidden markers in the stubs; lists the
                                            # setup files and stray protected files
framegate port-stubs <f> [--force]          # a port: the stubs of its code files from the
                                            # reference (text kept, bodies replaced; python)
framegate approve-spec <f> [--force]        # spec-check, [env] refresh, commit (specs, stubs,
                                            # setup files), then tag spec-approved/<f>
framegate run <f>                           # runs to the next gate (tests, final) or DONE;
                                            # progress lines on stdout + out/<f>/progress.log
framegate watch <f>                         # live view: new progress lines until the run ends
framegate wake <f>                          # a run waiting for the usage limit tries again now
framegate status [<f>] [--brief]            # all features / one; --brief = short, for the chat
framegate gate-summary <f>                  # chat digest of the waiting gate
framegate approve <f>                       # tests gate -> implement; final gate -> retro
framegate reject <f> --reason "..."         # tests gate: writer + test reviewer re-run with it
framegate answer <f>                        # after writing out/<f>/answers.md: the next run
                                            # writes them into the spec (SPEC_UPDATE), then
                                            # tests gate -> writer + reviewer re-run; final
                                            # gate -> the test writer applies answers that need
                                            # a test change, then only the parked items redo
framegate finish <f> --no-retro             # final gate -> DONE without the retro
framegate learn <f> K1 [K3 ...|all]         # add accepted retro knowledge to the
                                            # project knowledge file (committed)
framegate keep <f> 1 3 [...|take|all|none]  # the retro proposals you accept -> the retro
                                            # backlog (metrics/retro_backlog.md, committed)
framegate backlog [--all] [--scope framework|project]
                                            # the open proposals (--all: every row)
framegate backlog show B3                   # one row with the retro's full section
framegate backlog done|drop|reopen B3 [B5 ...] [--note TEXT]
framegate stats [<f>] [--json]              # cost, agent / check / waiting time, final-gate
                                            # runs per feature; <f>: per phase, per final run
framegate cleanup <f> [--tags] [--force]    # DONE: delete out/<f>/ + its run logs; --tags: also
                                            # spec-approved/tests-approved/spec-update/
                                            # tests-update tags
framegate publish <f> [--dry-run] [--pr] [--title TEXT] [--force-with-lease] [--unfinished]
                                            # [git] state_branch: framegate's files -> the
                                            # state branch, the product -> one commit on the
                                            # base, pushed as <branch_prefix><f>; --pr: the
                                            # pull request (gh / az), else where to open it;
                                            # works after the cleanup
framegate pr-description <f> [--pipeline]   # write specs/<f>/pr_description.md again and
                                            # commit it; --pipeline: the text of a deploy /
                                            # CI change's own pull request
framegate state [status|init|save|guard] [--push]
                                            # the state branch: where it is / create it from
                                            # this checkout's framegate files / this work
                                            # branch's framegate files -> the state branch /
                                            # (re)install the pre-push guard
framegate pipeline <name>                   # the pipeline task (deploy / CI files): start it,
framegate pipeline-check <name>             # its mechanical checks,
framegate pipeline-approve <name>           # tick + commit after the user's OK
framegate gates <f> --scope step|final      # the frame checks (also used in CI)
framegate spec-brief <f>                    # conventions for the spec dialogue (skill injects it)
framegate protocol                          # the chat run protocol (skills inject it)
framegate unlock [--force]                  # clear a stale .phase/.reqs left by a killed run
```

Branch per feature (`[git]`): `spec` creates `<branch_prefix><f>` (`feat/`) from `base` (`main`)
and marks it (`git config branch.<b>.framegateFeature`); it switches to an existing branch only
if it carries that mark or the feature's spec folder, refuses with uncommitted changes to tracked
files (untracked files are fine; the refusal lists the files and prints the one command that
commits them as they are: `git commit -m "Changes before <f>" -- <files>`) and while a run is active, and only notes an already approved
spec (no SPEC phase). `[git] branch = false` stays on the current branch. A checkout whose
framegate.toml is not on `main` yet sets `base` to the branch that has it. In a repository a
team shares, `[git] state_branch` keeps framegate's files off the base and out of the pull
request: "Product branch and state branch" above.

Progress lines (`HH:MM:SS <what> [$cost since the spec approval]`, max 160 chars) go to stdout
and `out/<f>/progress.log`; `watch` prints the new ones (it remembers its position in
`progress.cursor`, so a re-armed watch neither repeats nor skips) and exits once no run of the
feature holds `out/.run.lock` (or none appears within 30 s). Notifications (`[notify] toast =
true`, Windows only; env `FRAMEGATE_TOAST=0` turns them off): a toast at each gate, at DONE and
on an error, through PowerShell 5.1's WinRT ToastNotificationManager (no modules); fire and
forget, silent if it fails. `cleanup` keeps `specs/<f>/`, the metrics row and the shared
`out/events.jsonl`, refuses while the feature's run is active or before DONE (`--force`), and
prints the `git push origin --delete` line for tags that were pushed (`[git] markers = "tags"`).

**Markers, commits, known failures.** `spec-approved/<f>`, `tests-approved/<f>`, `spec-update/<f>`, `tests-update/<f>`, `knowledge-update/<f>` and `config-update/<f>` are private refs (`refs/<name>`) by default: `git diff tests-approved/<f>` resolves them like a tag, but no `git push`, `--tags` or `--all` publishes them to a shared remote. `[git] markers = "tags"` stores git tags instead (needed for a CI job that runs `framegate gates`); tags left by an older framegate are still read and replaced on the next write. A workflow commit stages only the workflow's paths (`[paths] src`, `tests`, `specs`, `metrics`, the knowledge file, the code map), never the whole working tree: your notes and unrelated edits stay out. Lint, format, typecheck and imports, in every mode (Python): the checks run on the whole tree, and a finding counts only in a file the task changed since the tests were approved, or since the spec was approved (the tests the task wrote). Findings in other files are left out of the verdict and of the output ("in files the task did not change (left out): N line(s)"). Limits: a type error the task causes in a file it did not change is not reported (the tests have to find it), and a failure that names no file (an import contract, a crashed tool) always counts. `[gates] regression_baseline` is about tests only: `approve-spec` runs the whole suite once and records the tests that already fail (`out/<f>/regression_baseline.json`; never a test tagged with one of the feature's own spec IDs; kept on a second approval). The step gates and the final regression check (with the default `coverage_scope = "feature"`) leave them out. Not set, it is on for a bug fix (the bug can have a failing test already) and off for a feature and a refactor, which start on a green tree; `true` / `false` in framegate.toml sets it for every mode. Formatting debt, in every mode: the format fix formats a changed file whole, so debt from before the task in a file the task touches is fixed with it, without a question. The lines the formatter rewrote outside the task's own lines are counted (`format_debt` event) and listed in final_facts.md ("Formatted beyond the task's lines"), for one line in the summary. Files the task does not touch are left as they are.

**The spec phase covers the setup.** Everything the human adds or changes happens before
"spec OK"; after it the frame is locked and no agent (and no later hand edit) may change a
protected path. So in the SPEC phase the chat may write, besides `specs/` and the source
folders (stubs):
- the **setup files**: `framegate.toml` and the language's packaging / test / tool-config
  files, i.e. the adapter's own list (`setup_paths()`; python: `pyproject.toml`, `setup.cfg`,
  `setup.py`, `requirements*`, `uv.lock` / `poetry.lock` / `Pipfile`, `pytest.ini`,
  `tox.ini`, `mypy.ini`, `ruff.toml` / `.ruff.toml`, `.importlinter`, the root `conftest.py`;
  dotnet: `*.csproj`, `*.sln` / `*.slnx`, `Directory.Build.props` / `.targets`,
  `Directory.Packages.props`, `global.json`, `NuGet.config`, `.editorconfig`,
  `*.runsettings`, `.config/dotnet-tools.json`, `stryker-config.json`, outside the tests
  folder; generic: `[generic] protected`);
- `out/<f>/spec_brief.md` (and `answers.md`).

Spec-locked, also for the chat: `tests/`, `.claude/`, `.framegate/`, `CLAUDE.md`, the
built-in deploy / CI paths (below), `[roles] extra_dir` and **every `[protected] extra`
path** (deploy, CI, generated files such as `ci/`, `platform/deploy/`, `*_pb2.py`), also
where it lies inside a source folder. Deploy and CI files are never a feature's to change,
not even at spec time ("Pipeline follow-up" below). A path listed in `[protected] extra`
that is also one of the language's setup files (`pyproject.toml`, `pytest.ini`,
`requirements.txt`, `ruff.toml`, ...) is a setup file all the same.

**Deploy / CI paths.** One definition (`frame_diff.is_pipeline_file`) for the frame check,
the write guard in every phase, the stray-file check of `approve-spec`, the pipeline task
and the two commits of `publish`:

| Part | What | Where it is set |
|---|---|---|
| built-in folders (at the root) | `.github/`, `.circleci/`, `.azure-pipelines/` | nothing to write |
| built-in file names (at any depth, case does not matter) | `Dockerfile`, `Dockerfile.*`, `*.dockerfile`, `.dockerignore`, `docker-compose*.yml`, `docker-compose*.yaml`, `compose*.yml`, `compose*.yaml`, `Jenkinsfile`, `bitbucket-pipelines.yml`, `azure-pipelines*.yml`, `.gitlab-ci*.yml`, `.travis.yml` | nothing to write |
| the project's own | folders and files whose name can mean something else: `deploy/`, `ci/`, `k8s/`, `helm/`, `charts/`, Terraform folders, scripts CI calls, generated files | `[protected] extra` (`framegate probe` proposes them; `x/` = a folder, a glob such as `*_pb2.py`, `**/<name>` = a file name at any depth) |
| taken off | a built-in name that is no deploy file in this project | `[protected] builtin_off = ["Dockerfile"]`: the name as written above; anything else is a config error |

A built-in name under the test folder, the spec folder, the out folder, `.claude/` or
`.framegate/` is no deploy file (a `tests/data/compose.yml` is test data, locked as a
test). A setup file is never a deploy file, and neither is a source file of the
project's language that a built-in name happens to match (`dockerfile.py` and
`Dockerfile.*`), unless `[protected] extra` lists it. For a feature, a bug fix or a refactor these
files are locked in every phase, also inside a source folder: the write guard refuses the
write and names the pipeline follow-up line to write instead, and the frame check reports
`PROTECTED PATH CHANGED`. Only the pipeline task may write them. `[protected] exceptions`
is another thing: paths the frame check leaves out (the review tests).

`framegate probe` names the exact files and folders of this repository. "Locked deploy / CI
paths": the built-in ones that exist (a folder as `name/`, a file by its path) and the
`[protected] extra` entries. "Lock candidates", each with why: top-level folders with a
deploy / CI name (`deploy/`, `ci/`, `k8s/`, `helm/`, `charts/`, `infra/`, `terraform/`,
`pipelines/`, ...), a folder that holds a Dockerfile, a Helm `Chart.yaml`, Terraform `*.tf`
files or Kubernetes manifests (yml with `apiVersion:` and `kind:`), scripts (`.sh`, `.ps1`,
`.cmd`, `.bat`) a CI file names, `Procfile` / `databricks.yml` / `cloudbuild.yml`, generated
files. A source, test or spec folder (or one that holds them) is never proposed as a whole:
there the single file is. The budget: the tracked file names (`git ls-files`) and at most
40 files read, the first 8 KB of each; no deeper look.

**Machine files: framegate never cares about them.** Tool state of this machine is not
project context. One definition (`config.is_machine_file`), used by every step:

| Part | What | Where it is set |
|---|---|---|
| the rule | a path inside a top-level folder whose name starts with a dot: `.serena/`, `.copilot/`, `.vscode/`, `.idea/`, `.cursor/`, `.venv/`, `.aws/`, `.mypy_cache/`, ... | nothing to write |
| not machine files | the dot folders `.github/`, `.circleci/`, `.azure-pipelines/`, `.gitlab/`, `.azuredevops/`, `.buildkite/` (deploy / CI), `.framegate/`, `.claude/`, `.devcontainer/`; a path the config names (`[paths]` src / tests / specs / out / metrics, the knowledge file, the code map, `[roles] extra_dir`, plain entries of `[protected] extra` / `paths` and `[generic] protected`; dotnet: `.config/dotnet-tools.json`); every dot *file* at the root (`.gitignore`, `.dockerignore`, `.gitlab-ci.yml`, `.pre-commit-config.yaml`, `.env.example`, `.editorconfig`) | nothing to write |
| more of them | a tool's file elsewhere | `[git] machine_files = ["local.settings.json", "tools/cache/"]` (a file, a folder `x/`, a glob, `**/<name>` = a file name at any depth; an entry that would cover `framegate.toml`, `.framegate/`, `.claude/` or a configured path is a config error) |
| taken back | a dot folder that is project context here | `[git] project_folders = [".config/"]` (top-level dot folders only) |

What "never cares" means, tracked or not, changed before or during a task:
- **No refusal.** `framegate spec` / `pipeline` switch the branch with a machine file
  uncommitted (git carries it over); `publish` and `state save` do not count it as an
  uncommitted change. Only when the other branch holds another version of that file does
  git itself refuse the switch: one line names the file and the fix (`git restore <file>`,
  or commit it).
- **No check sees it.** The frame check, `approve-spec`'s stray-file check, doctor's
  "uncommitted protected files", `pipeline-check` (also the "changed before this task"
  list), the changed-file lists behind diff coverage, structure, mutation, smells and
  formatting, and the risk paths (`never_auto`) leave machine files out. A built-in deploy /
  CI name inside one (`.venv/.../Dockerfile`) is no deploy file.
- **No agent writes it.** The write guard refuses a machine file in every phase, the spec
  phase and the pipeline task included ("it is a machine file ..."). Reads are not limited.
- **Never in a pull request.** framegate's commits never hold one (they commit the workflow's
  paths only). A change someone committed on the work branch by hand is left out by
  `publish`: the product commit keeps the base's version of that file, and one line says so
  ("Left out of the pull request: N machine file(s) ..."). It stays on the local work branch.
- **Setup.** `framegate probe` never proposes a machine folder as a lock candidate and reads
  no file in one; its line "Machine files (tracked, ignored by framegate): ..." names the
  tracked ones (a folder with more than 3 as `name/ (N files)`), and the setup shows it
  under its table.

Not used: `git update-index --skip-worktree` / `--assume-unchanged` and
`.git/info/exclude` for tracked files (hidden git state in your repository). `git status`
shows these files as ever. `.claude/settings.local.json` is handled apart, as before: the
frame check and the approval leave it out. A step that still refuses over uncommitted
changes names the three ways out: commit the file, restore it, or add it to
`[git] machine_files`.

`approve-spec` then, in this order: (1) spec check; (2) refuses while a protected file that is
neither a setup file nor in a path the workflow commits has uncommitted changes or is untracked
(e.g. an edited `CLAUDE.md`, a changed CI file), and likewise for a changed `[protected] extra`
path wherever it lies, naming the files: commit or revert them, since the approval must not
commit them and every frame check of the run would fail on them;
(3) runs `[env] refresh` if set (e.g. `["python", "-m", "pip", "install", "-e", ".",
"--no-deps"]`; `python` = the project interpreter; a non-zero exit refuses the approval, with
the last output lines); (4) commits the specs, the stubs and the changed setup files in one
commit (`<f>: spec approved`), prints `Setup files committed with the spec: ...` and logs them
(`spec_approved` event, `setup`). The spec brief lists them under "Setup files changed"
(`spec-check` prints the list beforehand), so the user approves them knowingly. After this
commit the frame check has a clean baseline: a `PROTECTED PATH CHANGED` at a step gate can only
come from the run itself. `framegate doctor` warns about uncommitted protected files.

`.claude/settings.local.json` is left out of all of this: it is Claude Code's per-machine
settings file (the allow rules of this checkout, which Claude Code itself rewrites when a
permission prompt is answered with "don't ask again"), so it is neither a stray file at the
approval nor a `PROTECTED PATH CHANGED` in the frame check, tracked or not, ignored or not.
The agents still cannot write it (no phase's write paths hold `.claude/`); the rest of
`.claude/` (`settings.json`, skills, hooks) stays protected.

**Pipeline follow-up.** When a feature means a deploy file (Dockerfile, compose, k8s
manifests) or a CI file (yml, scripts) must change, that change is a separate step after the
feature, never part of it; framegate only makes sure it is not lost. (1) The spec:
requirements.md has `## Pipeline follow-up` (all three templates; one bullet per change,
`- <file>: <what must change> (<why>)`, "None." by default), filled by the spec dialogue (a
new package folder the Dockerfile must COPY, a new dependency the image must install, a new
test selection for CI) and shown on the spec brief. (2) The run: an agent that finds such a
need does not attempt the change; the implementer writes `- PIPELINE FOLLOW-UP [<file>]:
<what must change and why>` in its step note (-> steps.md, like an assumption), the reviewer
the same line in review.md. (3) The final gate: framegate itself lists every item (the
spec's and the run's, each once) in `out/<f>/pipeline_followup.md`, in final_facts.md, in a
generated section of final_summary.md and in the final brief
(`gate-summary`: "Pipeline follow-up (a separate step after this feature; not part of it):
N; nothing to answer"). It is no gate item: not a question, not blocking, and the summary
agent cannot place it under "Left out". (4) After DONE: the list is written to
`specs/<f>/pipeline_followup.md` (a checklist, committed with the metrics row; `cleanup`
keeps the spec folder) and logged as a `pipeline_followup` event. The line is only for
deploy / CI files (the agents are told so: a note about anything else, also a remark about
packaging or the environment, is an assumption or a park request). An agent's line whose
file is no deploy / CI file of the project (`is_pipeline_file`) is not put on the list:
the pipeline task could never do it. It is shown once on the final brief ("Not a deploy /
CI matter (an agent's note; not on the pipeline list): N") and in final_facts.md. The
spec's own section is the user's and stays as written. What a reference project should change has its own list with the same life
cycle: "Reference follow-up" under "References and ports".

**Items outside the repository.** Some of what a task makes necessary is no file here: a
variable in the CI's variable group, a secret in the vault, a migration of the real
database, a switch of an environment. It is part of the pipeline follow-up, in one fixed
form everywhere (the spec section, an agent's note, the kept list):

    - OUTSIDE [<where>]: <what must be done> (<why>)
    - [ ] OUTSIDE [CI variable group argos-dev]: add RUNNER_TOKEN (the new job reads it)

Nothing in framegate ever does such an item; it only keeps it in sight:

| Where | What is shown |
|---|---|
| spec brief (the chat) | under "Pipeline follow-up", as "outside the repository: by hand: <where>: <what>" |
| final brief (`gate-summary`) | its own line under the pipeline follow-up: "Pipeline follow-up, outside the repository: by hand: N", not counted with the repository's items |
| final_summary.md, final_facts.md | a generated section "Outside the repository, by hand (framegate does not do them): N" with an unticked checklist (`- [ ] <where>: <what>`) |
| `framegate pipeline`, `pipeline-check`, `pipeline-approve` | "Outside the repository: by hand, not this task: N to do" and the items; never numbered, never open work, never a problem (a list with only such items passes the check) |
| pipeline brief (the chat) | its own section "Outside the repository: by hand" |
| pull request (`framegate publish`) | the unticked checklist near the top of the description (`specs/<f>/pr_description.md`, section "To do by hand outside the repository: N"), and on top of a pipeline-only one (`specs/<f>/pipeline_pr_description.md`); also when the pull request is opened by hand |
| `framegate status` | counted apart: "...; outside the repository: N item(s) to do by hand (specs/<f>/pipeline_followup.md)" |

The user ticks such a line in `specs/<f>/pipeline_followup.md` by hand when it is done;
a ticked one is no longer counted or put into a pull request.

**The pipeline task** (`/framegate:pipeline <name>`; `src/framegate/pipeline.py`) is that
separate step: the only place where deploy / CI files are changed. It starts three ways with
one skill: after a task of any mode whose kept list has open items (`<name>` = the task; the
list is the input, no new dialogue), the same after the task's pull request is merged, or on
its own (`<name>` is new; the chat writes the same kind of list, `- [ ] <file>: <what>
(<why>)`, and shows it for an OK before any edit). There is no spec, no test-first and no
agent run: a yml change has no unit test. The chat makes the edits itself.

| Step | Command | What it does |
|---|---|---|
| start | `framegate pipeline <name>` | switches to the branch, sets `.phase` to `PIPELINE <name>`, remembers the start commit (`refs/framegate/pipeline/<name>`), prints the list |
| check | `framegate pipeline-check <name>` | no agents; exit 1 on a problem: a changed file that is not a deploy / CI file or not on the list (so: nothing else changed since the start), a yml / json / toml file that does not parse, an open item whose file did not change and that has neither `(left open: <reason>)` nor `(not needed: <reason>)`. Notes, not blocking: repo paths the added lines name that do not exist (e.g. a test file added to a CI list) |
| approve | `framegate pipeline-approve <name>` | after the user's OK on the brief (`out/<name>/pipeline_brief.md`: what was asked, what changed per item with its file, checks, not proven: "the pipeline itself has not run: the pull request's CI run is the proof", questions): the check again, the done items ticked (`- [x]`) and the ones closed as not needed too, the pull request's description written again, the files, the list and the description committed, the phase cleared |

What may be written in the `PIPELINE` phase (the write guard, `hooks/protect_paths.py`):
deploy / CI files ("Deploy / CI paths" above: the built-in names and `[protected] extra`
without the setup files), and of those only the files the list names (the user adds one by
saying so: its line goes on the list first); plus the list itself and the brief. Source,
tests, setup files, specs and framegate's own files are locked. A Dockerfile, a compose file
or a CI yml counts by its name; a `k8s/` or `deploy/` folder only when `[protected] extra`
names it. In every other phase these files stay locked. YAML is parsed with PyYAML (a
dependency of framegate): every document of a file, and tags of the file's own dialect
(`!reference`, `!Ref`) are accepted. A yml file that is a template (Helm, Jinja: `{{- }}`,
`{% %}`) and does not parse as plain YAML is a note, not a problem.

How the change lands depends on the task before it, read from git alone (never from the
host): the task counts as merged when every product file it changed has the same content
in the fetched base, or merging its product into the base changes nothing.

| The task's product | With `[git] state_branch` (`framegate publish <name>`) | Without it |
|---|---|---|
| not merged into the base | a second commit on the same product branch and pull request: commit 1 the product (everything that is not a deploy / CI file), commit 2 the deploy / CI files, both built by path from the work branch; published before: commit 2 is added on top (never rewritten); the lease / force rules are the product branch's | one more commit on the task's feature branch (`pipeline-approve` makes it; the chat pushes the branch) |
| merged, or there is no task | its own branch `<pipeline_prefix><name>` (`[git] pipeline_prefix`, default `pipeline/`): one commit with the deploy / CI files on the base commit the work branch started from, its own pull request with the brief as its text (ref `refs/framegate/pipeline-product/<name>`) | the work is done on a local branch `<pipeline_prefix><name>` created from the base at the start; the chat pushes it and opens its pull request |

With a state branch the work happens on the task's local work branch (a new
`framegate-work/<name>` for a change of its own), the ticked list goes to the state branch,
and neither the product nor the pipeline branch ever holds a framegate file (checked on the
commit; the push guard stays in force). `framegate status` shows every task with open
pipeline items, also after its cleanup; the wrap-up after DONE offers the pipeline task
before the publish question, so both commits go out in the first push.

**An idle plan step.** A plan step (feature, bug fix) whose implementer run changed nothing
is neither a failure nor a full gate: when HEAD is still the commit the last green step gate
left and the workflow's paths are clean, the gate runs only the step's own tests (scope
"idle" in the `gate` event) and the progress line says that lint, the frame check and the
whole suite are that gate's results. Red tests there fail the attempt as usual. A refactor
is different: its tests were green all along, so an idle refactor step fails.

`run` never waits on the keyboard. It advances TESTS -> IMPLEMENT (per plan step) -> REVIEW ->
SUMMARY -> FINAL -> RETRO -> DONE and stops only at the final gate (and the tests gate with
`[gates] tests = "human"`, or `"auto"` when it isn't clean), writing
`out/<f>/progress.json` (`waiting: {kind: gate, gate: tests|final}`) and printing what to read.
Exit codes: 0 done/ok, 1 error, 10 human gate (tests or final; the output and `status` name it),
130 interrupted. Run `framegate run <f>` again after `approve` / `reject` / `answer`; it also
resumes after an interruption. The spec itself must change: edit it, `framegate approve-spec <f>`
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
  the Write tool to `out/<f>/notes/<step>.md`; framegate appends it to `steps.md`:
  `- ASSUMPTION [<spec ID>]: ...`). Only when no reasonable assumption is possible it writes
  `park.md` and stops (the Stop hook lets it); a step that fails `max_attempts` times is parked
  too (reason, failing checks, attempts). The parked step's uncommitted source changes are saved
  as `out/<f>/parked/<step>.diff` and the sources restored to the last green commit; later steps
  continue (their check leaves the parked step's IDs out, and their task says not to build
  them). A step with `Depends on: Step N` (plan.md) on a parked step waits: parked without a
  run, redone after it. A step gate whose only failures are lint / format findings in files the
  implementer may not edit (frozen tests) parks at once ("unfixable in IMPLEMENT") instead of
  using up its attempts. The same for a step gate whose frame check reports nothing but
  `PROTECTED PATH CHANGED` on paths the implementer may not write: it can neither have caused
  nor fix that, so it parks after that attempt, without a diagnosis run, and the reason names
  the files (a `SUPPRESSION ADDED` is its own line to remove: normal retry). The frame
  check counts a suppression by its text, per file: a line that the formatter re-wrapped,
  or whose comment was shortened, removes one and adds the same one, which is no new
  suppression; more of the same than before are still reported.
- When every plan step is parked (none done, none provisional) there is nothing to review:
  the reviewer, the final checks and the summary run are skipped (`review_skipped` event,
  counted by `framegate stats <f>`), and the final gate comes at once with the parked items;
  its brief is generated, `final_gates.md` and `final_summary.md` say why they are empty.
  The first round that builds something goes through the whole review.
- When the same checks fail `[agents] diagnose_after` attempts in a row (default 2), a
  diagnosis run (Opus, `roles/diagnosis.md`) reads everything, runs the tests and changes
  nothing: it writes the root cause, the fix owner (`src` / `tests` / `spec` / `config`) and
  one proposed fix (`out/<f>/diagnoses/<item>.md`, `diagnosis` event). `src`: the proposal
  goes to the implementer's next attempt (it always gets that attempt). `tests` / `spec` /
  `config`: the item parks at once with the proposal as its question; at the final gate one
  reply approves it, and the redo sends it to the phase that owns the file (spec updater,
  test writer, implementer).
- `Fix owner: config` is a change to framegate.toml that keeps every check as strict as it
  is: a timeout, a tool path, a command that runs the same check, agent settings. No agent
  may write that file. When the answer approves the proposal, the spec updater writes the
  whole new file to `out/<f>/config_update.toml`; framegate checks it, puts it in place,
  commits it alone (`<f>: framegate.toml updated from answers ANS-n`), tags it
  `config-update/<f>` and goes on with the new config. The `config_update` event holds each
  change as `key: old -> new`, and final_facts.md lists them under "framegate.toml changed
  during the task". The frame check accepts framegate.toml while it is exactly the tagged
  file. Not applied: a file that does not load, and a change that weakens a check
  (`src/framegate/config_change.py`). That is a lower `diff_coverage` / `mutation_score`,
  a new `unrunnable_tests` entry, a command that is emptied or gets an argument that leaves
  something out (`--exclude`, `--ignore`, `-k` ...), and every key that is not known to be
  harmless (`mutation_tool`, `regression_baseline`, `[paths]`, `[protected]`, `[frame]`,
  `[risk]` ...). Such a change belongs to the setup (`/framegate:setup`), outside a task.
- A redo that cannot end differently is not run. Work that parked on something no
  implementer run can change ("unfixable in IMPLEMENT", or a diagnosis with owner `tests`
  / `spec` / `config`) keeps a digest of what the gates read outside the source folders:
  every tracked file outside `[paths] src`, the review tests, the out folder and the code
  map (the spec, the frozen tests, framegate.toml, packaging and tool config), uncommitted
  changes to such files, and the two records of what was red at the spec approval. When
  the answer only approves ("ok", "Approved: apply the proposed fix as written") and the
  digest is still the same after the spec updater and the test writer ran, the approved
  change was not made: the item parks again at once ("not redone: nothing changed since it
  parked", `redo_result` with `unchanged`), without an implementer or a diagnosis run, and
  its question says what is missing. An answer that says more than an approval is new
  information and its redo always runs; so does "redo anyway" (for a change framegate
  cannot see: an installed package, a service). Seen live in three of three replay runs:
  an approved framegate.toml change nobody could apply, redone with the same failed
  attempts and a second diagnosis that found the same thing.
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
- The final summary (`final_summary.md`, the detail; the pull request has its own text,
  `specs/<f>/pr_description.md`, written by framegate) and the final brief
  (`final_brief.md`, what `gate-summary` shows) are written from `final_facts.md` (generated
  from the run records, with the final gate's numbered items, so the brief rewords each one in
  plain words under the same number) and `spec_brief.md`; framegate appends the cost / time /
  turns table per phase to the summary and adds the numbers to the brief itself.

Answers go into the spec (the spec stays the source of truth): `framegate answer` gives the answers
a reference `ANS-<n>` (in `questions_archive.md` and the `answer` event), and the next `run` starts
with one SPEC_UPDATE agent run (`roles/spec_update.md`) that writes them into
`specs/<f>/requirements.md` / `design.md` (and a new ID into the `Covers:` line of `plan.md`),
each change marked `(answer ANS-<n>)`. framegate then undoes edits to any other file under the
specs folder, reverts the whole update if an ID defined in requirements.md disappeared (IDs are
never renumbered; a new case gets a new ID, so traceability asks for its tests), and commits it
(`<f>: spec updated from answers ANS-<n>`, `spec_update` event: files, new IDs, lost IDs, undone
paths). The diff since `spec-approved/<f>` is shown in `tests_gate.md` (section "Spec changes from
your answers") or appended to `final_summary.md`; approving that gate (or `finish --no-retro`)
moves the tag `spec-approved/<f>` to it (`spec_reapproved` event). `[gates] tests = "auto"` never
passes a gate with such a diff. At the final gate the update comes after the tests were approved:
framegate tags it `spec-update/<f>`, and the frame diff allows exactly the spec files that commit
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
`taskkill /F` of framegate leaves no orphan `claude`), clear `.phase`/`.reqs`, record an
`interrupted` event (+ `progress.json` `interrupted`; the attempt is not counted) and exit 130.
`out/.run.lock` (pid, feature, start) marks a running orchestrator: a second `run` is refused,
a lock whose pid is gone is taken over (and the interruption recorded). `framegate status` warns
about a phase lock without a running orchestrator (`SPEC` excepted: `framegate spec` sets it for
the interactive session); `framegate unlock` clears it.

Turn and cost caps per agent run: `--max-turns` = `[phases.<P>] max_turns`, else
`[agents] max_turns`, else TESTS 50, TEST_REVIEW 30, IMPLEMENT 80, REVIEW 40, TRIAGE 30, SUMMARY 30,
RETRO 30. A turn is one model round trip (one assistant message, however many parallel tool calls
it makes); the CLI's `num_turns` counts messages (prompt + every tool result) and runs about 2x
higher, so framegate logs both (`turns` = round trips from the message stream, `messages` =
`num_turns`; docs/claude-cli-notes.md). `--max-budget-usd` = `[phases.<P>] max_budget_usd`, else
`[agents] max_budget_usd`, else TESTS 2.0, TEST_REVIEW 1.5, IMPLEMENT 1.5, REVIEW 1.5, TRIAGE 1.5,
SUMMARY 1.5, RETRO 3.0 USD (~3x the live FCSim cost per run; `0` = no cap). A run that stops at
either cap is logged (`max_turns` / `max_budget` event). IMPLEMENT: the step gate decides (a
failed gate is a normal failed attempt whose feedback names the cap); other phases are re-run
once with a note; if that run stops at a cap too, it is logged (`escalation` event with
`cap_kind`, retro E item) and the run continues with what the agent wrote.

Usage limit: a run that the Claude subscription's usage limit refuses (a rejected
`rate_limit_event` in the stream, or an error result such as "You've hit your limit · resets
4pm") is not a failed attempt. framegate logs a `usage_limit` event, waits until the reset plus
one minute (15 minutes when claude names no time; progress line and toast), then runs the same
task again. A reset further away than `[agents] limit_wait_max_hours` (default 6; `0` = never
wait), e.g. a weekly limit, or a limit still there after 4 waits, stops the run like Ctrl+C
(exit 130, `interrupted` with the reset time); `framegate run <f>` after the reset goes on.

The time claude names is the latest end of the wait, not the only one (seen live: a run slept
1 h 40 min after the limit had been refilled). Two things end it early:

- **A try that gets through.** Every `[agents] limit_probe_minutes` (default 5; `0` = no
  tries) framegate starts the refused run again. Claude refuses a run at the limit before
  any model call, so a refused try costs nothing; it leaves no `agent_run` event and no run
  files. The try that gets through is the run itself (the same task, the same model): no
  extra call is paid for, and the run goes on from there.
- **`framegate wake <f>`.** The waiting run tries again within 5 seconds (in the chat: tell
  it the limit is back). While a run waits, `out/<f>/limit_wait.json` exists; the command
  deletes it, and so may you. A run that is not waiting: exit 1, nothing happens. If the
  limit is still there, the run is refused again and a new wait starts (it counts as one of
  the 4).

Ctrl+C / a stop during the wait behaves as before (exit 130, state kept). The end of a wait is
a `usage_limit_over` event (`how`: `reset`, `probe` or `wake`; `waited_s`; `probes`).

**The start check and updates.** Three pieces run at one version: the plugin (Claude Code
updates it), the command-line tool (one per machine) and the project environment's workflow
tools (one per project). The start check (`framegate preflight`) brings the last two to the
plugin's version, but never under a task in progress:

- "spec OK" (`approve-spec`) writes the framegate version into the task's `progress.json`
  (`framegate`) and the task into a list for this machine (`<claude config>/framegate/tasks/`,
  one small file per task; `framegate run` adds a task an older version approved). A task
  leaves the list when it is DONE or its run files are gone (`cleanup`).
- While any task on this machine is in the list, or a task in this checkout is between
  "spec OK" and DONE, no start check prints the install command for the command-line tool:
  also not the start check of a new task, here or in another repository. The workflow
  tools of a project are kept while a task of that project is in progress. The check says
  so in one `KEPT:` line (an update is waiting, and for which task); the next start check
  after DONE updates.
- `framegate update` / `/framegate:update` updates at once, tasks in progress or not. It
  refuses only while a run is active (`PROBLEM: a framegate run is active ...`): replacing
  the files of a running tool breaks that run.
- Off altogether: `[update] check = false` in framegate.toml (this project) or env
  `FRAMEGATE_UPDATE_CHECK=0` (this machine or session; it wins over the file). Then
  `preflight` and `status` compare nothing and update nothing (`Start check: ok (...; the
  update check is off)`); a missing framegate part in the project environment and a missing
  claude CLI are still named. `framegate update` still works. For a pinned setup or a
  replay of old tasks.

Seen on a replay over three repositories (0.12.0): tasks went to 0.12.1 and 0.12.2 during
their runs and a `status` failed on an import. The check kept the versions of the feature it
was called for only, so the start check of the next task replaced the one tool of the
machine under the tasks still running.

API / connection error: a run of any phase that did no work because the API could not be
reached (an error result whose text starts with "API Error:", e.g. "API Error: Can't reach
the API server ... (ENOTFOUND)", or an error result with no cost and at most one turn) is not
a failed attempt either, and no gate runs on it. framegate logs an `api_error` event, waits
30 s, then 2 min, then 5 min (progress line each time), and starts the same task again. Still
unreachable after the third wait: the run stops like Ctrl+C (exit 130, nothing parked);
`framegate run <f>` goes on with the same run.

The summary phase was called `PR` before 0.9.1 (it writes the final summary, the final brief
and the code map; it never created a pull request). Still read: a `[phases.PR]` section and a
`pr.md` in `[roles] extra_dir` (`framegate doctor` WARNs: rename them to `[phases.SUMMARY]` /
`summary.md`), a progress.json that says `PR`, and old events (counted as SUMMARY in
`framegate stats`).

Check output in the gate report and the fix prompt: a failing check's output of more than 40
lines is filtered to the lines that look like failures (failure words, pytest `E` lines, file
locations such as `src/x.py:12:` or `Foo.cs(12,5)`) with two lines of context, plus the last 8
lines; the whole output is saved to `out/<f>/check_logs/<check>.log` and named in the report.
Decisions on the output (e.g. "lint fails only in frozen files") read the whole output.

Models and speed:
- `--model` per phase: `[phases.<P>] model`, else `[agents] model`, else Sonnet for the writers
  that follow the approved spec and the frozen tests (TESTS, IMPLEMENT incl. review fixes,
  SPEC_UPDATE, SUMMARY) and Opus for the checkers and the retro (TEST_REVIEW, REVIEW, TRIAGE,
  RETRO). `--effort` likewise (`effort`, default: Claude Code's). Each `agent_run` event records
  the model, so cost and failed attempts per model can be compared across features.
- Small plans run in one implementer run: consecutive steps share a run while their spec IDs add
  up to at most `[agents] step_batch_ids` (15; 0 = one run per step). The batch is checked,
  committed, parked and redone as one unit (`steps 1, 2, 3`).
- The implementer checks only with the step command (its steps' tests and the earlier ones); the
  step gate runs everything else: after the step's tests it runs the whole suite (the
  adapter's everyday selection: `[dotnet] base_filter`, `[python] pytest_args`), and fails on
  any red test that is not a test of this feature's later or parked steps, a contested test,
  or a test that already failed when the spec was approved (`regression_baseline`). Seen
  live: a step made helpers public and broke another feature's frozen public-surface test;
  both step gates passed and the break showed in a later fix run. `[gates] step_tests =
  "feature"` keeps the step gate to the step's own tests (a suite too slow to run per step).
  The runs after the review (review fixes, structure fixes, a final-gate repair) are checked
  with the feature's tests (its spec IDs; with parked steps the done steps' IDs) and the
  review tests folder, not with the whole suite: the final gates run it right after (seen
  live: 4.5 minutes per gate, twice for two small fix runs, directly before the final
  regression). What such a run breaks elsewhere fails the final regression and goes to the
  one repair run, and the repair of a whole-suite failure is checked with the whole suite.
  Before checking, the gates format the changed source files
  and the changed review tests, and the test writer's files are formatted after each of its
  runs (`[gates] format_fix`, default on, only with the adapter's own format check), so a
  formatting slip costs no attempt and never sits in a file the implementer may not edit. For
  .NET the format / lint checks report only findings on changed lines, and the format fix keeps
  only its changes there: no churn in code the feature did not touch. For Python the lint /
  format / typecheck / imports checks count only findings in files the task changed.
- `[references]` (name = path, or `{ path, note }`): other projects the agents may read, see
  "References and ports".
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

Not measured: a mutation check whose tool cannot run on this machine (Python on Windows: no
WSL, or no WSL distribution with `python3`, or the venv for mutmut cannot be built; `native`
runner: mutmut is not installed, or the machine is Windows; .NET: Stryker.NET is not installed)
is `NOT MEASURED`, not SKIPPED and not FAIL. The gate report shows `[NOT MEASURED] mutation
score` with the reason and the one-line fix (`... Fix: ...`), its last line says `ALL GATES
PASSED (NOT MEASURED, the tool cannot run here: ...)`, and the `final_gates` event lists the
check in `not_measured` (its `results` entry has the reason and the fix). No repair run and no
question. The brief shows it with the scores and on the risk line; a pull request's
description shows it with the reason. `framegate stats` marks the run (`PASS (not measured:
...)`). A check the config leaves out (`mutation_tool = "none"`) stays SKIPPED.

Spec check (`framegate spec-check <f>`, also the first step of `approve-spec`, which refuses
while it finds something; `--force` approves anyway and logs `spec_check` with `forced`). It
reads columns of the spec templates' tables; a spec without them is not checked:
- `Spec ID` (design.md, "Error handling" and "Command-line surface"): every filled row names
  a spec ID of requirements.md. A rule that is only written in design.md gets no test.
- "Green against stubs" (requirements.md): every bullet is `- <test name or node ID>:
  <reason>` ("References and ports").
- `Assigned as (code, copied)` (requirements.md, "Existing data this spec reads"): every
  backticked piece of code in the cell is in the file the "Set where" cell names (a path, or
  a file name looked up in the source folders; spacing is ignored). `n/a: <why>` skips a row.
- `ID` and `Data` (requirements.md, "Test data and fixtures"), once a row there pins data:
  every edge-case ID (`-E-`) of the "Edge cases" table is in the ID cell of a row. The row
  names the data that shows the case, or says `unit-only: <which test builds the case itself>:
  <why>`. One cell may list several IDs (`XXX-E-001, E-002`, `XXX-E-003..E-005`). A spec whose
  fixture table is empty (every test builds its own inputs) is not checked.
- Stubs: a line added to a source file since the branch left `[git] base` (untracked files
  whole) that matches one of the frame check's forbidden patterns (`[frame]
  forbidden_patterns`, else the adapter's: `noqa`, `type: ignore`, `#pragma warning disable`,
  ...) is reported with file and line. The frame check would reject it at every step gate.
- Files a plan step names (`checks/plan_files.py`): every path-like word of a
  `## Step N:` section of plan.md that the implementer cannot write. Seen live (a replay
  pilot, 2 of 3 runs): a step said "update docs/xyz.md", the write guard blocked the
  implementer and the step parked.
  - A **documentation file** becomes writable for the implementer while it runs that step:
    a `README` / `CHANGELOG` (`.md`, `.rst`, `.txt`) or a `.md` / `.rst` file under a folder
    called `docs` or `doc`. Never: a path in a dot folder (`.claude/`, `.github/`,
    `.framegate/`), the tests, the specs, the out and metrics folders, the code map, the
    knowledge file, `CLAUDE.md` / `AGENTS.md` anywhere, a machine file, a setup file, a
    deploy / CI file, and every path the frame check protects (`[protected] extra` /
    `paths`). `spec-check` prints them: `Documentation files the plan makes writable
    (frozen with the spec): step 2: docs/guide.md`.
  - Anything else the implementer cannot write is a problem, per step: `plan.md, Step 2:
    names tests/test_x.py, which the implementer cannot write ...`, with what to do (tests:
    the test writer's; a deploy / CI file: "Pipeline follow-up"; a setup file: change it in
    the spec phase; else one line under `## To do by hand` at the end of plan.md, which is
    no step and for the user). A file the step only reads: `(read only)` after its name. A
    `README` / `CHANGELOG` without its file name is a problem too (write the path).
  - Counted as a file: a word with a letter suffix that exists in the repository, lies in
    an existing top folder, or has a documentation or deploy / CI name. Paths in the spec
    folder (the step's sources), web addresses and names such as `e.g` or `Parser.parse`
    are left out. Not seen: a folder ("the docs"), a glob, a file without a suffix.
  - The list is frozen with the approval: the run reads plan.md at the
    `spec-approved/<f>` marker, so a spec update or an edit of plan.md makes nothing new
    writable. For an implementer run of plan steps the orchestrator puts that step's list
    into the agent's environment (`FRAMEGATE_STEP_DOCS`, a JSON list) and into its task;
    the write guard allows a listed file in the `IMPLEMENT` phase only, for an agent of
    the run only, and checks the rule again. Review-fix runs and every other role get no
    list. The files are committed with the step's code and set aside with it when the
    step parks (they are in the parked diff). The frame check needs no rule for them: a
    file it protects is never a documentation file here.
`spec-check` also prints `Setup files changed (committed with the spec): ...` and reports
uncommitted protected files that are not setup files (`approve-spec` refuses on those even
with `--force`).
`framegate doctor` warns when a project's copy of the templates (`specs/_template/`) lacks a
section or a table column of framegate's templates.

Risk rating (`src/framegate/risk.py`, `[risk]`): at every final gate the engine rates the
feature `low` or `elevated` from its own records, never from an agent's opinion. Low needs all
of: final gates passed with diff coverage and mutation measured (a project without them is
never low risk); nothing parked, provisional or open; no answer round, no park and no cap hit
twice in the run; at most `max_assumptions` (3) assumptions; at most `max_changed_lines` (200)
changed source lines in `max_changed_files` (8) files and `max_removed_lines` (30) removed
lines in existing code, measured against the feature's base (a refactor: without moved
lines, that is lines removed in one place and added unchanged, but for the indent, in
another; live: 876 changed lines were 114 without the moves); no changed path under
`never_auto`; no file over `[gates] max_file_lines` that the feature made longer; at least `min_features` (3)
finished features in the metrics file. The brief shows `Risk: low` or `Risk: elevated: <the
tests that failed>`; the `risk` event and the `human_gate` event (`risk`) record it next to
your decision, so you can compare before switching on `[gates] final = "auto"`: then a low-risk
feature passes the final gate without a stop (`human_gate` with `auto: true`, retro, DONE;
nothing is pushed, the PR stays yours). Everything else stops as before.
A check that is NOT MEASURED (the tool cannot run on this machine, see "Not measured") is no
reason for `elevated`: it says nothing about the feature. It is no proof either. The brief
shows `Risk: low for what was measured (...); mutation score NOT MEASURED: <reason>. Fix:
<fix>` (or, next to real reasons, `Risk: elevated: <reasons>. Not a reason: ...`), the `risk`
event has it in `not_measured`, and `[gates] final = "auto"` does not pass the gate without a
stop. A score below the threshold and a check the config switched off are reasons as before.

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

Unreached assertions (`[gates] unreached_assertions`, default on; Python and .NET): the
coverage run also records the lines of the test files the feature added or changed (not the
review tests; diff coverage still counts source files only). A test that ran, with an
assertion whose line has no hit, fails the check `unreached assertions (feature tests)`; the
final-gate repair sends it to the test writer like a weak diff coverage. Seen live: `if the
shot was saved: Assert...` on a match without a saved shot passed every check. Left out:
tests that were there before the feature, tests that did not run, assertions that always fail
(`pytest.fail`, `Assert.Fail`, `assert False`: guards), and a line marked `unreached: <why>`
in a comment on it or above it. Without a coverage tool the check is SKIPPED.

Questions, assumptions, parked items and failed retries feed the retro. Every step-gate run is
logged in `out/events.jsonl` (`gate`: phase, step, attempt, `failed` check names, trimmed
`excerpt`, seconds), as are questions (phase, who, text), test reviews (round, verdict,
objections), the auto tests gate (`tests_gate_auto`: passed, reasons), answers (gate, text; also
appended to `questions_archive.md` under the questions), parked items (`parked`: step/finding,
reason, question, failing checks, attempts, diff), redo results, review findings and their triage,
final gates (`failed`, `skipped`, seconds per check), permission denials (`permission_denials`:
phase, step, tool + command/path of every call the permission check denied, from the `claude -p`
JSON), turn-cap hits (`max_turns`, and `escalation` when the re-run hit it too) and
interruptions. Before the RETRO agent runs, framegate writes `out/<f>/retro_input.md`: counts,
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

Hooks (called by the plugin): `framegate hook pre-tool-use` (phase-based write protection, and
the command form of Bash / PowerShell calls during a run) and `framegate hook stop` (IMPLEMENT
can't stop while the step's tests fail, contested tests of provisional steps left out, unless
it parked: an `out/*/park.md` exists). Both read
the hook JSON on stdin, exit 0 silently without `framegate.toml` or `.phase`, and block with exit
2. The write protection holds for every session while a phase is set, the chat included; in the
SPEC phase the chat may also write the setup files and `out/<f>/spec_brief.md` ("The spec phase
covers the setup" above); a deploy / CI file (the built-in names, `[protected] extra`) is
refused in every phase but the pipeline task's. The implementer of a plan step may also write
the documentation files the approved plan names for that step ("Files a plan step names"
under the spec check). A path outside the repository is blocked only for the agents of a run
(`FRAMEGATE_AGENT` set): a chat session keeps writing its own files there (memory, scratch);
a `[references]` folder is read-only for both. Shell calls: only plain single commands are pre-approved, so a pipe (`dotnet test ... |
Select-String`), a `;` / `&&` / `||` chain, several lines, or a `cd` / env-variable prefix would
be denied by the permission check without a reason. During a run (any phase but SPEC) the hook
blocks them first and tells the agent why and what to do instead: one command per call, and the
command's own options (`--filter`, `--logger "console;verbosity=minimal"`, `-k`, `-q`) or the
result file it writes to see less output. The task text says the same (`COMMAND_FORM`).

The lint / format / type commands a task names are the project's own: the effective
`[gates.commands]` (else the adapter's defaults), with `{src}` as every source folder, a
disabled command (`[]`) left out, and the format command in its fix form
(`[gates.commands] format_fix` on `<files>`, e.g. `python -m black <files>`). The tool rules
of a run are built from the same list (`Bash(python -m black *)`), so a run is never told to
use a command it is denied; a run without lint rules (reviewer, triage, test reviewer,
diagnosis) is told that the gates check them. `framegate doctor` WARNs when a
`[phases.<P>] tools` list denies a command that phase's task names.

Test writer <-> test reviewer rounds: failed test checks or objections send the tests back,
at most `[agents] test_review_rounds` times. A check that fails exactly as in the round
before (the same problem lines) while the reviewer has no objections ends the loop at once
(the writer had its round; seen live: a carried-over test that passes by design); the
failure goes to the gate. A question the same asker already has open is not collected again:
at least 80% of the shorter one's words shared, or the same tests / files and spec IDs named
and half of the other words shared.

The environment of an agent run (`src/framegate/agent_env.py`; the `claude -p` process and
every shell it starts): `FRAMEGATE_AGENT=<role>`; the adapter's `agent_env()`: for Python
the project interpreter's folder first on `PATH` (plus `VIRTUAL_ENV` for a venv), so the
`python -m pytest -q --req <IDs>` an agent is told to run uses the interpreter the gates use
(on a real run `python` was the machine's global one, without the framegate pytest plugin:
`unrecognized arguments: --req`); and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, so an agent does
not load or write Claude Code's per-project auto-memory of the user's chats (a retro agent had
quoted notes of an earlier chat). The agents work from the spec, the code and the run files.
The project's `CLAUDE.md` is still loaded, and so is the user's global `~/.claude/CLAUDE.md`
(no switch was found that drops it and keeps a user-scope plugin's hooks;
docs/claude-cli-notes.md). `framegate doctor` checks the agents' `python` (`tool: agents'
python`: `--req` in its `pytest --help`).

Configuration: `framegate.toml` (all keys optional; see the commented template written by
`init`). `[agents] claude` or env `FRAMEGATE_CLAUDE` selects the claude executable;
`[agents] test_review_rounds` (2) bounds the writer <-> reviewer loop; `[gates] tests = "human"`
(default) or `"auto"` (no stop when all checks pass, the reviewer has no objections and no
question is open; otherwise it stops as in human mode).

Python projects: add framegate (pinned) to the dev dependencies; its pytest plugin provides the
`req` marker and `--req ID,ID` selection. mutmut needs fork, so on Windows the mutation gate runs
through WSL (`[gates] mutation_runner = "auto"`, default) in a WSL-side venv with the project's
dependencies. `[env] wsl_distro` picks the distribution; without it the default one is used,
or, when that one has no `python3` (Docker Desktop's `docker-desktop`), the first other one
that has. Install once inside WSL: `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `sudo
apt install python3-venv`). Without a usable WSL the check is NOT MEASURED with the reason and
the fix, and `framegate doctor` warns (`tool: mutmut (mutation)`). Details: docs/adapters.md.

.NET projects (xUnit, `language = "dotnet"`, detected from a root `*.sln`/`*.slnx`/`*.csproj`):
install framegate with the `dotnet` extra (`pip install "framegate[dotnet]"`, brings diff-cover)
and Stryker.NET (`dotnet tool install -g dotnet-stryker`). Tag tests with
`[Trait("Req", "XXX-R-001")]`; selection uses `dotnet test --filter "(Req=A|Req=B)&(<base_filter>)"`.
Put `[paths] review_tests` inside a test project folder and set the `[dotnet]` keys
(`base_filter`, `test_projects`, `configuration = "Release"`, ...). Details and the FCSim notes:
docs/adapters.md.
