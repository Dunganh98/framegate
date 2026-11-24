# framegate reference

Everything in detail: the phases, the chat flow, project knowledge, the CLI, gates, retro,
hooks and the language adapters. The short version is the [README](../README.md).

## All commands

In the chat you only use the skills; they call the CLI for you. The CLI works the same from
a terminal.

| Command | What it means | When |
|---|---|---|
| `/framegate:setup` | set up framegate in this repo with two commands: `framegate setup --plan` (one table of the project's facts to confirm) and `framegate setup --apply` (config, templates, the setup check); then the chat writes the project facts and the code map ("Setup: two commands" below) | once per repo |
| `framegate setup --plan [--survey]` | installs the workflow tools, maps the folders (survey, once; `--survey` again), probes, and prints the confirmation table with the decisions it needs; keeps the plan in `out/setup_plan.json` | first command of `/framegate:setup` |
| `framegate setup --apply [--set ROW=VALUE ...] [--red "TEST=REASON" ...]` | writes the confirmed plan (`framegate init` if needed, the framegate.toml keys that differ from the defaults), installs the tools the gate commands name, runs `doctor --tree --brief`, creates the state branch once the tree is settled, prints "Left to do"; `--set`: a corrected row; `--red`: a red test the user agreed to list; can run again; exit 1 while the setup is not ready | second command of `/framegate:setup` |
| `/framegate:feature <f>` | start or continue feature `<f>`: spec dialogue, run, gates, wrap-up | each feature |
| `/framegate:bugfix <f>` | a bug fix: bug report dialogue, tests that fail on today's code, the fix, gates | each bug |
| `/framegate:refactor <f>` | a refactor: scope dialogue, tests that pin today's behavior, small steps, gates | each refactor |
| `/framegate:pipeline <f>` | the pipeline task: make the deploy / CI changes a task left as its pipeline follow-up (`<f>` = that task), or one of their own (`<f>` = a new name): the list, the edits in the chat, one check, one brief to approve, the publish ("The pipeline task" below) | after a task with a pipeline follow-up; any time for a change of its own |
| `/framegate:resume [f]` | reattach the chat to a running or waiting feature | after a restart |
| `/framegate:status [f]` | where features stand; tag cleanup after the PR is merged | any time |
| `framegate init` | write the starter files (config, spec templates, project facts, .gitignore lines) | once per repo |
| `framegate install-tools [--dry-run] [--update]` | install what the workflow needs into the project's environment (python: the framegate pytest plugin, pytest, pytest-cov, hypothesis, diff-cover, and with a framegate.toml the lint / format / type tools its gate commands name); `--update` also brings the framegate pytest plugin there to the command-line tool's version; reports versions it changed; works before `init` | run by `framegate setup`; after config changes |
| `framegate survey` | an agent (read-only, Sonnet) maps the source folders, the test folder and what is not the project's code from the code and the build and test files (comments are no evidence); every code file must end up in one of them, the agent is asked once more about the rest, what is left is a `PROBLEM` line; writes out/survey.json, which the probe uses for its folder lines before a framegate.toml exists | run by `framegate setup --plan` |
| `framegate probe [--json]` | the project facts every run depends on, from instant checks only (no test run, no linter over the code, no agent): language, the interpreter the run uses, the test / lint / format / type commands with where each comes from and whether the tool answers `--version` there, source and test folders, base branch, remote and host, deploy / CI paths locked or to lock, editable install, references; works before `init` (proposals from the project's own files) and with a framegate.toml (the configured values); always exit 0 | run by `framegate setup --plan` |
| `framegate update [--plugin-version X]` | bring this machine in line with the installed plugin at any time: prints the install command for the command-line tool (it cannot replace itself while it runs), then updates the project's workflow tools; `/framegate:update` does both in the chat. Tasks between "spec OK" and DONE are the only thing the start check leaves alone; `update` does not, except while a run is active (it says so and updates nothing that run uses) |
| `framegate preflight [<f>] [--plugin-version X] [--check]` | the start check: the command-line tool at the plugin's version (prints the install command for the chat), the project environment's workflow tools at the tool's version (updated here), a session with outdated skills told to restart, a short setup check; one line when all is in line; always exit 0. Nothing is updated while a task is in progress, with `--check` or without a feature: the update waits (`KEPT:`), "The start check and updates" below | first step of `/framegate:feature`, `:bugfix`, `:refactor`, `:pipeline`, `:resume` |
| `framegate doctor [--full] [--tree] [--brief]` | check the setup without agents; ends with a verdict and only what needs action (`--brief`: only that); `--full` also runs the whole test suite; `--tree` runs it too and says what is red on the code as it is ("The tree at setup" below) | after setup or config changes; `--tree` once per repo, run by `framegate setup --apply` |
| `framegate commit-setup [--dry-run] [PATH ...]` | the files the setup wrote, in one commit of their own ("The tree at setup" below); `--dry-run` only lists them | last step of `/framegate:setup` |
| `framegate spec <f> [--mode feature\|bugfix\|refactor]` | create the branch `feat/<f>` and open the spec phase with the mode's templates | start of a feature |
| `framegate spec-check <f>` | mechanical checks of the spec tables and the stubs (below); lists the setup files the approval commits, the deferred work on the plan's files and the plan's files over the size limit; exit 1 on a problem | internal |
| `framegate port-stubs <f> [--force]` | a port: write the stubs of its code files from the reference (text kept, function bodies replaced; python); lists imports that will not resolve here ("References and ports") | internal (spec dialogue of a port) |
| `framegate approve-spec <f> [--force]` | spec-check, `[env] refresh`, then freeze the spec and the setup files ("spec OK"); refuses a spec of another kind than the task was started as; restarts the feature if the spec changed later | gate 1 |
| `framegate run <f>` | run the agents until the next human gate or DONE | after every decision |
| `framegate watch <f>` | follow the progress lines of a run | while it runs |
| `framegate wake <f>` | a run that waits for the Claude usage limit tries again now ("Usage limit" below) | the limit was refilled before the time the run waits for |
| `framegate status [f] [--brief]` | state of one or all features | any time |
| `framegate gate-summary <f>` | the brief of the waiting gate (decision and default first, plain words) | at a gate |
| `framegate approve <f>` | accept the gate: final gate -> retro, then DONE (tests gate -> implement) | at a gate |
| `framegate answer <f>` | apply your answers in `out/<f>/answers.md` (spec, tests, redo of parked work); answers that only take defaults approve the gate as it stands | at a gate |
| `framegate reject <f> --reason "..."` | send the tests back to the test writer (only with `[gates] tests = "human"`) | tests gate |
| `framegate finish <f> --no-retro` | accept the final gate and skip the retro | final gate |
| `framegate learn <f> K1 [K3 ...\|all]` | keep the project facts the retro proposed (into `.framegate/project.md`) | after the retro |
| `framegate keep <f> 1 3 [...\|take\|all\|none]` | record which retro proposals you accept in the retro backlog | after the retro |
| `framegate backlog [show B3 \| done\|drop\|reopen B3 ...] [--all] [--scope ...]` | the open proposals; one in full; mark it applied / dropped / open again | any time |
| `framegate stats [f] [--json] [--risk]` | cost, agent and check time, final-gate runs, escaped defects per feature; with `<f>`: per phase and per final run; `--risk`: the risk rating next to what you decided at each final gate | any time |
| `framegate defect <f> --what TEXT [--found-by TEXT] [--severity low\|medium\|high] [--fix <f or commit>] [--file PATH ...] [--id SPEC-ID ...] [--force]` | record a bug found after task `<f>` was accepted ("Escaped defects" below); `framegate defect --close X3 [--fix ...]` marks one fixed; `framegate defect [--all]` lists the open (all) ones | when a bug of an accepted task shows up |
| `framegate deferred [--file <path>] [--later <file> --feature <f>]` | the deferred-work list (`.framegate/deferred.md`): the open entries, or only those on one file; `--later` adds "split this over-limit file later" ("Deferred work" below) | any time; the spec dialogue |
| `framegate uses <symbol> [--file <path>]` | every place in the source (not the tests) that reads a function or a variable, as `file:line: line`; the lines that define or assign it are listed apart, comment lines are left out | the spec dialogue, when a requirement changes what an input may be |
| `framegate cleanup <f> [--tags]` | delete the run files; `--tags` also the feature's git tags (after the merge) | after DONE |
| `framegate publish <f> [--dry-run] [--pr] [--title "..."]` | with `[git] state_branch`: framegate's files -> the state branch, the product -> the branch for the pull request, both pushed; `--pr` also creates the pull request, with `specs/<f>/pr_description.md` as its text; `--dry-run` only says what would go where ("Product branch and state branch" below); works after the cleanup too | after DONE, on your yes |
| `framegate pr-description <f> [--pipeline]` | writes the pull request's description again from what is there now and commits it; prints the file ("Product branch and state branch" below, the pull request's description) | when a pull request is created without `publish` |
| `framegate state [init\|save\|guard] [--push]` | the state branch: where it is; `init` creates it from this checkout's framegate files; `save` sends a work branch's framegate files to it; `guard` (re)installs the pre-push hook that keeps framegate's files off every other branch | once per repo; a change outside a feature |
| `framegate gates <f> --scope step\|final` | run the checks by hand (also used in CI) | debugging, CI |
| `framegate spec-brief <f>` | the project's spec conventions (the feature skill reads it) | internal |
| `framegate protocol` | the chat run protocol (the skills read it) | internal |
| `framegate unlock [--force]` | clear a lock left by a killed run | after a crash |
| `framegate replay <candidates.md>` | real commits as tasks, each delivered in a sandbox cut before the commit and scored with the commit's own tests ([replay.md](replay.md)) | measuring framegate |
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
| TESTS | test writer -> mechanical checks (a failure goes back to the writer before any review, at most `[agents] test_check_rounds` (2) times) -> **test reviewer** (`roles/test_review.md`); objections and failed checks go back to the writer, at most `[agents] test_review_rounds` (2) times; a later review round is a new agent that gets the objections and the test diff | **gate 2**: `out/<f>/tests_gate.md` (ID -> tests -> what it checks, check results, reviewer verdict + remaining objections, all open questions batched) -> approve (tag `tests-approved/<f>`) / reject / answer (answers go into the spec first: SPEC_UPDATE). `[gates] tests = "auto"` passes it without a stop when everything is clean |
| IMPLEMENT | implementer, one fresh run per plan step | step gates green -> commit. Ambiguity: assume from the spec, log it in `steps.md`. No reasonable assumption, or `max_attempts` failed attempts: the step is **parked** (its code set aside as `out/<f>/parked/*.diff`), the run goes on |
| SIMPLIFY | simplifier (`roles/simplify.md`; Sonnet, 40 turns, 1.0 USD), one run after the last plan step and before the review ("Simplify pass") | the task's diff smaller with the behaviour unchanged, as its own commit `<f>: simplify`; dropped when a check fails after it; never stops, never asks |
| REVIEW | adversarial reviewer (bugs as failing tests, structure findings without tests) -> **triage** by the test reviewer -> fixes -> structure fixes | valid findings fixed (or parked), invalid ones removed with a reason, the code restructured with every test green; final gates + structure report; a final repair that changed source files is looked at by a new reviewer agent ("Final-gate repair") |
| SUMMARY | summary agent (`roles/summary.md`): `final_summary.md` (detail), `final_brief.md` (the gate brief), the code map update (committed) | **gate 3**: the final brief -> approve (retro) / `finish --no-retro` / answer the numbered items (only defaults: approved as it stands; else into the spec, then redo only those) |
| DOC_CHECK | doc checker (`roles/doc_check.md`; Opus, 30 turns, 1.0 USD; Read, Glob, Grep, Write; writes `out/` only): in the SUMMARY phase before an important doc proposal is asked, and before the docs step applies text the owner gave with an approval ("Sources and doc updates") | a text that states something the code does not do is corrected, or asked about; a run without a result changes nothing |
| DOCS | docs writer (`roles/docs.md`; Sonnet, 30 turns, 1.0 USD; Read, Glob, Grep, Write, Edit, MultiEdit; writes documentation files and `out/`), once after the approved final gate ("Sources and doc updates") | the doc proposals applied, commit `<f>: docs`; a failed run is reverted and never blocks the task |
| RETRO | retro agent (reads the retro backlog; a problem seen again is counted there) | `retro_proposals.md` + metrics row -> DONE; then `framegate keep` records your pick in the backlog |

## Parts

| Part | Where | Docs |
|---|---|---|
| Python package + CLI `framegate` | `src/framegate/` | this file, docs/adapters.md, docs/claude-cli-notes.md |
| Claude Code plugin (`/framegate:setup`, `:feature`, `:bugfix`, `:refactor`, `:pipeline`, `:resume`, `:status`, `:update`, hooks) | `plugin/` | docs/plugin.md |

## Daily use: from the Claude Code chat

Once per repository: `/framegate:setup` in the chat. It runs `framegate setup --plan` and
shows its table of the project's facts as it is, to confirm with "ok" or "5: use black"; then
`framegate setup --apply` (each correction as `--set <row>=<value>`), which writes
framegate.toml and the templates and runs the setup check with one look at the tree as it is
("Setup: two commands" and "The tree at setup" below). The chat then writes
`.framegate/project.md` and the code map, offers to commit the setup's files (`framegate
commit-setup`) and a small smoke feature in one line. A few minutes plus one run of the whole
suite. Or by hand `framegate init [--language
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

### Setup: two commands

`src/framegate/setup_cmd.py`. Nothing here decides for the user: a row is a proposal from the
project's own files, and only "ok" or a `--set` makes it a line of framegate.toml.

`framegate setup --plan` installs the workflow tools into the project's environment, maps the
source and test folders with the survey agent (once, while there is no framegate.toml;
`--survey` maps them again), probes, picks the adapter and prints finished text the chat shows
unchanged. In a clone whose framegate files live on a state branch it only says that the
repository is set up already. The table, one row each with its value, where it comes from and
whether the tool answers:

| # | Row | # | Row |
|---|---|---|---|
| 1 | Language / adapter | 8 | Base branch |
| 2 | Interpreter | 9 | Shared repository: framegate's files on their own branch |
| 3 | Test command | 10 | Locked deploy / CI paths |
| 4 | Lint | 11 | Reinstall when a spec changes packaging (`[env] refresh`) |
| 5 | Format | 12 | Reference projects |
| 6 | Types | 13 | Project rule checks ("Project rule checks" below) |
| 7 | Source / test folders | 14 | Mutation testing |

Below the table: the machine files framegate ignores, the paths that are never approved
without a person (`[risk] never_auto`), one `Environment` line per framework that is declared
but not installed, one line saying everything else uses the defaults, and "Decisions the
repository cannot answer" (a `Blocker:` such as an undetected language; mutation testing that
cannot run here).

`framegate setup --apply` starts from the kept plan (`out/setup_plan.json`). `--set
<row>=<value>` corrects a row by its number or name (`--set "5=python -m black --check app
tests"`, `--set 6=off`, `--set 9=no`); more keys without a row: `rules_text`, `builtin_off`,
`machine_files`, `project_folders`, `metrics`. `--red "<test file[::test]>=<reason>"` lists a
red test the user agreed to (`[gates] unrunnable_tests`). A correction that cannot be applied
is refused before anything is written. Then, in this order: `framegate init` when there is no
framegate.toml; the keys that differ from the defaults (an invalid file is not written); with
a state branch the `.gitignore` lines of `init` are taken out again; the tools the gate
commands name are installed; `framegate doctor --tree --brief` runs; the state branch is
created once the tree is settled (nothing is pushed); "Left to do" names what is open. The
command can run again with more `--set` / `--red`; earlier ones are kept. Exit 1 while the
setup is not ready (the verdict, a `PROBLEM:` or a `MUTATION:` line).

What stays with the chat ("Left to do"): the knowledge file and the code map, written from
the code; with a state branch, the `req` marker line in a Python project's pytest config; the
allow rules for the CLI in `.claude/settings.local.json`; the commit of the setup's files
(`framegate commit-setup`); the smoke run offer.

### The tree at setup

A feature and a refactor start on a green tree. Seen in three of three replay runs on real
repositories: the untouched tree was already red (failing tests, lint, format) and the gates
blamed the task; the setup ended with uncommitted files and the first `framegate spec` was
refused. So the setup looks at the tree once per project, and closes with a commit. (A bug
fix records its own start: `[gates] regression_baseline`, under "Markers, commits, known
failures".)

`framegate doctor --tree` (`src/framegate/tree_check.py`) runs the whole suite once (the
final gate's regression run) and uses doctor's own lint / format / typecheck / imports
results and tool list; nothing runs twice. The setup ends on a green tree: a failing test
that is not listed is a FAIL (verdict "not ready") until it is made to run or listed with
its reason. After the verdict it prints plain lines:

| Line | Meaning | What the setup chat does |
|---|---|---|
| `TESTS: 412 run, 3 fail (...)` | the count | |
| `LISTED: <test>: <reason>` | an entry of `[gates] unrunnable_tests`, every time; "(matches no test of the suite)" when it is stale | names them once; for the debt entries (a `->` line counts them) it suggests one bug-fix or refactor task |
| `PROBLEM: N test(s) fail on the untouched tree and are not listed:` then one line per test with the first line of its failure | red tests nobody decided about (doctor FAILs) | per test: makes it run (a missing package), or proposes an entry, the user confirms, the chat passes it as `setup --apply --red "<test>=<reason>"`. Two kinds of reason: this machine cannot run it (a database, a service, another operating system), or `red before any task: ...` (the project's own debt) |
| `PROBLEM: the whole suite did not run to the end` / `gave no per-test results` | nothing is known about the tests (doctor FAILs) | fixes the test command or `[gates] regression_timeout` first |
| `DEBT: <check>: N line(s) in M file(s) (most in ...)` | a lint / format / typecheck / imports check or a project rule check fails on the code as it is; its size is the output lines that name a file | proposes ONE clean-up task (`/framegate:refactor <name>`) as a follow-up the user accepts or skips; no question per file |
| `MUTATION: <tool> cannot run on this machine: <why>` | e.g. WSL has no `python3` (doctor's tool check; for mutmut in WSL it now asks WSL instead of waiting for the first mutation run) | makes it run, or asks once and switches it off (`setup --apply --set 14=off`, which writes `[gates] mutation_tool = "none"`) |
| `Tree: green.` / `Tree: not green (see the lines above).` | the last line | |

`[gates] unrunnable_tests` (`src/framegate/unrunnable.py`): the tests that are red before
any task, each with its reason: this machine cannot run them, or they are the project's own
debt (the reason then starts with "red before any task"):

```toml
[gates]
unrunnable_tests = [
  { test = "tests/test_db.py", reason = "needs a PostgreSQL server" },
  { test = "tests/test_tty.py::test_resize", reason = "Linux only" },
  { test = "tests/test_todo.py", reason = "red before any task: placeholder tests" },
]
```

- `test` names the file: a whole test file (`tests/test_db.py` covers
  `tests.test_db::test_connect[1]`) or one test in it (`tests/test_db.py::test_connect`). A
  bare test name (`test_connect`) is a config error, as is an entry without a `reason`.
- The engine cannot tell "this machine lacks a database" from "really broken". The chat
  proposes, the user confirms, then the entry is written. Nothing is listed by itself.
- A listed test that failed on this machine at the setup (`doctor --tree`) is not run here
  again (out/known_red.json); on a machine where the database exists it runs and counts.
  The final summary offers one follow-up task that makes them run or pass (default: skip).
- A whole-suite result is reused for the same code: the key is the tree's content without
  framegate's own files, the test command and the installed packages (out/suite_run.json).
  A **failure** of a listed test is left out, in every mode (feature, bug fix, refactor):
  at the step gates (the step's tests, the whole suite, the review tests), in the final
  regression check and in the record `[gates] regression_baseline` takes. The check's result
  line says "listed in unrunnable_tests, left out: N". Any other failing test still fails.
- Named every time: the final gate report starts with "Listed tests", and final_facts.md
  has "Not checked" with each test and its reason, for one line in the summary. A green
  result says nothing about these tests.
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
   decides what the docs, the code map, the code and `.framegate/project.md` settle, asks the
   rest in one compact round (see "Which questions are asked" below), writes
   requirements/design/plan and the interface stubs, and shows the **spec brief** instead of the
   spec files (see "Manager-style briefs" below).
2. "Spec OK": the skill approves the spec, starts `framegate run` in the background and shows the
   live view: one line per phase start/end, check result, failed attempt, assumption, park and
   gate, each with the cost so far (~10-30 lines per feature). The chat stays usable.
3. At the final gate (and the tests gate, if `[gates] tests = "human"`) the chat shows the
   gate brief (`framegate gate-summary`), Windows shows a toast, and Claude Code waits for you.
   Reply in plain words: "ok" (with numbered items: take every default, which changes
   nothing; an item that says "No default: choose." still needs your choice; without items:
   approve, retro, then DONE), answers by number, "approve as is", "no retro", "reject: <why>" (tests gate
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
| Spec gate (`out/<f>/spec_brief.md`, written by the chat) | Task (2-4 lines) / Plan (one line per step, with its main file) / How it is proven (the 3 most important checks) / Not proven (the 3 most important things to check by using it) / Decisions I made for you (one line each; only points with no observable effect on callers or stored data) / Project details that matter / Setup files changed (config, packaging and tool files the spec phase changed; approved and frozen with the spec) / Questions (the real decisions, by impact) |
| Tests gate (`[gates] tests = "human"`/`"auto"`) | what the tests check in plain words (the test reviewer's `## What the tests check` in test_review.md), objections left, one line of mechanical checks, questions with defaults |
| Final gate (`framegate gate-summary`, from `final_brief.md` + run records; ~20 lines when nothing went wrong) | Steps (the spec brief's plan again: one line per step with how it went, as planned / changed / parked, what happened inside it and its main file) / Struggles (only what happened outside the steps; one line when none) / Decisions I made / Outcomes with numbers (gates, diff coverage, mutation, failed attempts, parks, cost, agent and check time, structure) / Proven and Not proven (the 3 most important points each, the rest on request) / Code map (one line; a WARN when new public types are missing) / Questions numbered 1..k by impact (the problem in one line, then `A: ... (default) / B: ...`) / Decisions I made / how many gate items were left out |
| Setup | one fixed table, printed by `framegate setup --plan` and shown as it is, each row with its value, where it comes from (a file or the probe) and, for a tool, whether it answered: language / adapter, interpreter, test command, lint, format, types, source / test folders, base branch, shared repository (state branch yes / no), locked deploy / CI paths, reinstall command (`[env] refresh`), reference projects, project rule checks, mutation testing. Reply "ok" or the row and the change ("5: use black"). Below it: the machine files, the paths never approved without a person, environment gaps, one line saying the rest uses the defaults. No detail questions (slow tests, thresholds, risk limits); a "Blocker:" line only for what the repository cannot answer (no test framework, no way to tag tests, no project interpreter) |
| `framegate doctor --brief` | one verdict line (ready / not ready), then only the FAILs with the fix and the WARNs that need a decision |
| Retro (`## Brief` in retro_proposals.md) | numbered proposals, problem -> fix, recommend take / skip / fold into N (and `repeats B3` for a problem the backlog already holds); the knowledge facts; answered with numbers (`framegate keep`, `framegate learn`) |
| Smoke run offer, DONE wrap-up, status, resume, errors, interruptions | one or two lines: the state and the one decision needed, with a default |

The default of every gate question is the option that changes nothing
(`src/framegate/defaults.py`): the code, the tests and the spec as they are, parked work left
parked, a proposed change left for later. At a gate with numbered questions, "ok" takes every
default (the chat writes them to answers.md); "approve as is" approves and leaves them open.
Answers that only take defaults, or only approve doc updates, start no run: `framegate answer`
approves the gate as it stands (`human_gate` event with `from_answers`). At the final gate the
next run does the retro; at the tests gate the tests are approved and tagged. Any other answer
starts the usual run.

An answer names an option: its letter first (`B`, `B: split it`), its number, or the option's
own words. The words after it are a note and decide nothing. An answer that names none of its
question's options and is not plainly the default ("ok", "leave it", "Default: ...") is refused
once (`answer_unclear` event): the chat asks the user to choose. Left without a letter after
that, it is taken as free text. A question that says "No default: choose." has no default: "ok"
does not answer it, and it always needs a letter. Two kinds of item say so: a parked item when
no step of the task is built (A: leave it parked, the task ends with nothing built / B: redo
it), and a reviewer's objection to a final repair that stands after its retry ("Final-gate
repair").
One line per bullet; a section where nothing happened is one line; no sentence repeats a heading
or another section.

### Which questions are asked

The gate briefs have no count cap. The spec dialogue asks one compact round of about 8
points at most, the most consequential first, option A being the recommended default (the
setup asks nothing but its table and blockers, see above). A point is a question
only when it is a real decision: two or more reasonable options with different outcomes, and the
spec, the code, `.framegate/project.md` and earlier answers don't settle it. Otherwise it is not
a question: only one sensible option -> decided, one line under "Decisions I made"; already
answered, already resolved (e.g. by a scan) or a note -> left out; the same question from two
agents -> one item. Questions are ordered by impact (what changes most if answered differently
first).

The spec dialogue always asks, unless the task text, the code or the docs already state it:
every new or changed public name, signature, parameter type, default value, error type or
message, stored or wire format (an existing pattern in the code that it follows states it);
what must not change; where the tests go when existing test files cover the area; which docs
the task may edit; for a bug fix the design of the fix when there is more than one. Only a
point with no observable effect on callers or stored data is a decision the chat makes itself.
design.md, "Public interfaces", has one row per new or changed public member with where its
shape comes from (`task`, `code: <pattern>`, `docs: <file>`, `asked`); `spec-check` fails a
row without a source and a public name of the stubs without a row. A reply that describes the
task instead of answering the questions is not "ok" and takes no default: it is the
requirement, and the questions it leaves open are asked again.

At the final gate the summary agent sorts every numbered gate item (parked work, the provisional
steps, the open questions) into `## Questions`, `## Decisions I made` or `## Left out` of
final_brief.md, naming it `[N]`. Parked work and the provisional steps are always asked; their default
leaves them as they are (parked work stays parked, the provisional code stays with its
contested tests red; both go to the deferred-work list). An item the brief places nowhere is
asked as it is. The human sees the questions
numbered 1..k; `framegate answer` maps answers.md back to the gate items and rewrites it
(`# Answers (final gate, numbered by gate item)`): a decided item gets `Default: <decision>`, a
repeated one the answer of the item it repeats, any other left-out item "No answer needed"; the
chat's own file is kept in the `answer` event (`as_given`). After the summary agent's run framegate
checks the brief (not blocking): questions without `[N]` or with one option, duplicates, closed
items or notes asked as questions, parked work left out, gate items placed nowhere -> the summary
agent rewrites final_brief.md once (event `brief_check`).

## Structure and the code map

The human owns what the classes are for and how they work together, not every line.
- The implementer writes for that (role): one concept per file, domain names, no dead
  defensive branches, no fragmentation into one-method helpers. The implementer and the
  reviewer also get the coding guidelines (`templates/coding_guidelines.md`, appended to both
  roles): the smallest diff that meets the spec, nothing the spec does not need today,
  comments of at most 2 lines and only for what the code cannot say, never a change of
  behaviour to satisfy a tool, the style of the surrounding code. A breach in the feature's
  diff is a review finding.
- The adversarial reviewer checks the same list and writes `## Structure findings` in
  review.md (`- <file>: <problem> -> <change>`; no test needed). Before the final gates the
  implementer applies them without changing behavior, every test green (`structure fixes`;
  failing it parks like a review fix).
- The final gates end with a structure report (not blocking): the size of every source file
  the feature changed (and before it), the longest methods. A file over `[gates]
  max_file_lines` (400; 0 = off) that this feature made longer is a note, never a final-gate
  question: final_facts.md lists it ("Files taken over the size limit", with the step whose
  gate saw it, or "Files over the size limit made longer"), the summary says each in one
  line, and its split becomes one entry of the deferred-work list (`size_noted` event; once
  per file, and not for a file answered at spec time or listed already). It still counts
  against the risk rating. A file over the limit that the feature changed without making it
  longer is listed only and does not count. Two questions remain: the spec-time question for
  the files the plan names (below), and the final-gate question when the reviewer proposes
  to split a file that was over the limit before the feature (A: leave it for a later
  cleanup, the default / B: split it now, which goes to the structure run). Sizes "before"
  are counted on the start commit formatted as the gates format, so a rewrap is no growth. The
  target files of a port (the spec's `## Ported from` table) keep the reference's file
  layout: the report lists their size ("ported: the reference's file layout is kept"), and
  they raise no gate item, no `GREW` line at a step gate and no "already over the limit"
  note in the implementer task.
- Before that, so the code is written in the right place: the spec brief and every
  implementer task name the source files that are already over the limit (the 12 longest),
  `spec-check` lists the files the plan names that are over it (the chat asks once per file:
  split it in this task with a plan step of its own, or later, `framegate deferred --later
  <file> --feature <f>`), the plan template asks a step that would add to one of them to name the new file, and
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
| Spec (requirements.md) | requirements, edge cases, stubs | bug report: reproduction rows (R), must-not-change rows (I), suspected causes, each tied to a row or ruled out | scope, behavior to preserve (R, I), structure targets |
| Tests | new, from the spec | new, from the bug report | existing tests first: a test that already asserts a row gets the row's ID as a tag; new tests only for the gaps |
| First proof (`test_checks.md`) | every test FAILS against the stubs | reproduction tests FAIL on today's code (each listed with why, for the test reviewer); must-not-change tests PASS | every test PASSES on today's code; the check lists how many tagged tests existed before and how many are new |
| Steps | per plan step, batched | usually one; the step note names `ROOT CAUSE` and `RULED OUT` lines (shown in final_facts.md and the brief) | one move per run; every step is checked with ALL the tests; a step that changes no source file fails |
| Review | against the spec | also: the same bug through another input or caller | a finding is a difference from the code before (`git show tests-approved/<f>:<path>`) |
| Runs after the review (review fixes, structure fixes, final repair) | checked with the feature's tests and the review tests (the whole suite runs once, in the final gates) | the same | the same |
| Risk rating | every changed line counts | every changed line counts | moved lines are not counted (`moved_lines` in the `risk` event) |
| Final-gate repair | code, then tests | code, then tests | code only: tests written after the refactor would pin the changed code, so weak tests go to the human |

Templates: `specs/_template_bugfix/`, `specs/_template_refactor/` (framegate's own unless the
project has a copy). The events `spec_started` / `spec_approved` carry `mode`; `framegate
stats` shows it. An unknown `Mode:` word is refused by `approve-spec`. So is a spec of
another kind than the task was started as (the last `spec_started` event): it is rewritten in
the layout of that kind, or the task is started again with `framegate spec <f> --mode <kind>`.

Suspected causes (bug fix, `src/framegate/checks/suspected_causes.py`). requirements.md,
`## Suspected causes`: one bullet per cause, `- H1: <what, the suspect expression in
backticks> (<file>: <function>) -> <ID>` (the row whose test runs through that code) or
`... -> ruled out: <evidence>`. `spec-check` fails a cause with neither and a cause that
names an ID the report does not define. A case that is green before the fix is an I row,
never an R row. After the last step gate a check lists every cause that is not ruled out and
whose code no test of the task runs (the lines that hold the backticked expression, else the
body of the named function): `out/<f>/causes_unreached.md`, a section of final_facts.md and a
line in the reviewer's task. A note, never blocking.

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
that is not among the feature's tests is a note. A bullet may name a spec ID instead of a
test: it stands for every test tagged with that ID, and the ID is then green by design as a
whole. `spec-check` refuses a bullet that is not `- <test>: <reason>`. When the red check fails
only on tests that pass before the code and the test writer asked about each of them
(questions.md, by test name), the check is not sent back to the writer: only the owner can
say whether such a test is to be listed, so it goes to the reviewer and the gate. The section is general because a normal feature can have such a test
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
questions, assumptions, risk level and escaped defects, then the averages of the finished
features, the escaped-defect total with one line per task that has one, and one line that
compares the risk rating with your final-gate decisions. `framegate stats <f>` adds the agent
runs per phase (runs, cost, time, output tokens), every final-gate run with its failed checks
and three slowest checks, the repair runs, how the simplify pass ended and the task's escaped
defects. `--json` prints the numbers. `framegate stats [f] --risk` prints only the comparison
("Risk rating" below).

## Escaped defects

A bug found after a task was accepted is logged against the task that caused it
(`src/framegate/defects.py`): `[paths] metrics`/`escaped_defects.md`, one table row per defect
with a stable id (`X3`), status (`open` / `fixed`), date, task, severity, who found it, what
is wrong, files, spec IDs and the fix. The command commits that file alone and logs an
`escaped_defect` / `escaped_defect_fixed` event.

- `framegate defect <f> --what TEXT [--found-by TEXT] [--severity low|medium|high] [--file
  PATH ...] [--id SPEC-ID ...]` records one (severity `medium` unless given). `--fix <task or
  commit>` records it as fixed at once. A task that was never accepted here (no metrics row,
  no approved final gate in the events) is refused; `--force` records it anyway.
- `framegate defect --close X3 [--fix <task or commit>]` marks it fixed.
- `framegate defect` lists the open ones, `--all` every one.
- `framegate stats` counts them per accepted task; `stats --risk` counts them per rating of
  the task.
- The retro input of a later task lists the open defects that name one of the files it
  changed (the file, or a folder above it) or one of its spec IDs, and those logged against
  the task itself.
- Escaped defects on tasks that were rated low block the automatic approval ("Risk
  rating").

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

## Sources and doc updates

Every agent of a run gets the ranking with its role: the code is the current state; the task
and the approved spec are the requirement; docs are context only (never a requirement, never
tested); a comment or docstring is no source. Program output (an error message, a log line) is
behaviour: a spec may quote it, a test may match it.

No agent of the loop writes a doc; it proposes: in the spec under `## Proposed doc updates`,
in a run as a line in `out/<f>/doc_proposals.md` (`src/framegate/doc_proposals.py`). One
proposal per file and place (the first wording; a contradiction wins over a plain one), none
for a file framegate writes itself (specs, out, metrics, tests, the code map, the knowledge
file) or an agent's memory file (`CLAUDE.md`, `AGENTS.md`). The kinds:

- important (`.md` / `.rst` under a `docs/` or `doc/` folder; also a line that names no
  file): `- <file>, <where>: today: <what it says now> -> proposed: <the new text> (<why>)`.
  Each is a final-gate question: A: apply it after the task (the docs step) / B: later (the
  default). Without a letter, an answer that starts with approve / yes / apply (or now / do
  it) approves it.
- important, where the task's code contradicts the doc: `- <file>, <where>: today: <what it
  says now, quoted> -> code now: <what the code does>`. The question is about the code: A:
  approve the code, the docs step updates the doc / B: approve the code, leave the doc as it
  is (later; the default) / C: reject the code, with the owner's solution, which goes into
  the spec like any other answer.
- minor (README, CHANGELOG, HANDOVER outside those folders): `- <file>: <one brief line>`. No
  question; the brief lists them under "Doc updates that will be applied after approval".
- code text: a docstring or comment in a source file is no doc. One that the task's code
  makes wrong is reworded by the plan step that changes the file (plan.md names the lines:
  `Reword: <file>:<lines>`). In a file no step changes it is one line, `- code text: <source
  file>:<lines>: today: <quoted> -> code now: <what the code does>`, listed in the final
  summary ("Stale docstrings and comments in source files"): never a question, never part of
  the docs step. `spec-check` refuses a requirement row whose subject is docstring or comment
  text.

A proposal states what the task built. In the SUMMARY phase, before an important proposal is
asked, one doc checker run (phase `DOC_CHECK`, role `doc_check.md`, `[phases.DOC_CHECK]`)
compares its text with the code as built. A text that states something the code does not do
is replaced by the corrected line (`out/<f>/doc_corrections.md`, `doc_check` event). It runs
once per commit and set of proposals; without a result the proposals are shown as proposed.

The owner may approve with a text of their own (the words after the letter: `A: <text>`).
Before the docs step applies it, a doc checker run compares that text with the code
(`doc_text_check` event). A text that contradicts the code is not applied, and the final gate
comes once more with that item ("your text" / "code now"): A: apply your text as written / B:
apply it corrected to what the code does / C: leave the doc as it is (later; the default). In
`finish --no-retro` nothing is asked: such a text is kept as not applied. Without a result
the text is applied unchecked.

After the final gate is approved and the final checks had passed (nothing parked), one docs
step (phase `DOCS`, role `docs.md`, `[phases.DOCS]`; before the retro, or in `finish
--no-retro`) applies the minor and the approved important proposals and commits them as
`<f>: docs`. If the run fails, changes nothing, or changes anything but docs (`out/` aside),
it is reverted and nothing is applied; the task finishes either way (`docs_step` event).
`specs/<f>/doc_proposals.md` keeps every proposal marked applied or not applied (with the
reason: later, the code was rejected, code text left as it is); an important one that was not
applied also becomes an entry of the deferred-work list.

## Deferred work

`.framegate/deferred.md` (`src/framegate/deferred.py`) keeps what a task left for later, so
later tasks see it. One line per item, with a stable id:

```
- D7 [refactor] src/app/state.py: split it into one file per concept (623 lines, limit 400). From released-lock, 2026-11-18: answered "later".
```

Kinds: `refactor`, `bugfix`, `feature`, `docs`, `pipeline`, `tests`. At the end of a task
framegate adds: the "later" answers to questions that proposed a change, the important doc
proposals that were not applied, parked work and provisional steps left as they were, the split of
each file over the size limit that the task made longer, the
final checks that had failed when the task was approved, the clean-up of the known-red tests
(`[gates] unrunnable_tests`) and a pointer to each follow-up list with open items. The same
kind, files and text again updates the entry's "From" part; a second entry about the same
file split or the same doc place is not added. The file is committed with the task's final
commit (`deferred` event).

- `framegate deferred` lists the open entries, `--file <path>` those on one file.
  `framegate spec-brief` says how many are open; `spec-check` prints the entries on the files
  the plan names.
- A spec that does an entry says `Resolves: D7` in requirements.md. `spec-check` refuses an
  id that is no open entry; the end of the task removes the entry.
- `framegate deferred --later <file> --feature <f>` records the user's "later" for a file over
  the size limit (the spec dialogue asks once per file). No gate asks about that file again.
- The owner may delete lines.

## Frameworks declared but not installed

`framegate probe` (its `Environment: ...` lines) and `framegate spec` compare what the project
declares with what its environment has (`src/framegate/env_gaps.py`; python: the
dependencies, optional extras and dependency groups of pyproject.toml, the requirements
files, and modules its tests guard with `importorskip`). `framegate spec` prints one line per
gap and writes them to `out/<f>/env_facts.md` (`declared but not installed: <name>
(<where>)`; `env_gaps` event). Before a requirement or a test needs such a framework, the
spec dialogue asks the user: drop those tests or requirements, allow installing it, or
another way; the answer goes into requirements.md. An adapter without this check reports no
gap.

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

1. *Once per repository:* `/framegate:setup` writes framegate's files into the checkout, and
   `framegate setup --apply` creates the state branch from them once the tree is settled (by
   hand: `framegate state init`; committed or not; nothing is pushed without `--push`). The base never gets a framegate file; `framegate doctor` warns
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
has a state branch only says that it is set up already.

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
framegate setup --plan [--survey]           # the setup's first command: tools, survey, probe,
                                            # one confirmation table (out/setup_plan.json)
framegate setup --apply [--set ROW=VALUE ...] [--red "TEST=REASON" ...]
                                            # its second: framegate.toml, templates, the gate
                                            # commands' tools, doctor --tree --brief, the state
                                            # branch, "Left to do"; can run again
framegate commit-setup [--dry-run] [PATH ...]  # the setup's files in one commit of their own
framegate survey                            # an agent maps source / test folders and what is
                                            # not project code from the code and build files;
                                            # every code file must be accounted for
                                            # (out/survey.json, read by probe); a minute or two
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
                                            # setup files and stray protected files, deferred
                                            # work on the plan's files, files over the limit
framegate port-stubs <f> [--force]          # a port: the stubs of its code files from the
                                            # reference (text kept, bodies replaced; python)
framegate approve-spec <f> [--force]        # spec-check, [env] refresh, commit (specs, stubs,
                                            # setup files), then tag spec-approved/<f>
framegate run <f>                           # runs to the next gate (tests, final) or DONE;
                                            # progress lines on stdout + out/<f>/progress.log;
                                            # FRAMEGATE_DETACH_RUN=1: starts itself detached
                                            # (output: out/<f>/run.log) and returns
framegate watch <f>                         # live view: new progress lines until the run ends
framegate wake <f>                          # a run waiting for the usage limit tries again now
framegate status [<f>] [--brief]            # all features / one; --brief = short, for the chat
framegate status --all                      # every open task on this machine, any repository
framegate retro <f>                         # the retro of a run that stopped before DONE
framegate note <f> <text>                   # one line of the chat, for the retro
framegate gate-summary <f>                  # chat digest of the waiting gate
framegate approve <f>                       # tests gate -> implement; final gate -> retro
framegate reject <f> --reason "..."         # tests gate: writer + test reviewer re-run with it
framegate answer <f>                        # after writing out/<f>/answers.md: only defaults
                                            # -> the gate is approved as it stands; else the
                                            # next run writes them into the spec (SPEC_UPDATE), then
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
framegate stats [<f>] [--json] [--risk]     # cost, agent / check / waiting time, final-gate
                                            # runs, escaped defects per feature; <f>: per phase,
                                            # per final run; --risk: the rating next to your
                                            # final-gate decisions
framegate defect <f> --what TEXT [--found-by TEXT] [--severity low|medium|high]
                 [--fix TEXT] [--file PATH ...] [--id SPEC-ID ...] [--force]
                                            # a bug found after <f> was accepted
                                            # (metrics/escaped_defects.md, committed)
framegate defect --close X3 [--fix TEXT]    # the fix is in
framegate defect [--all]                    # the open ones (--all: every one)
framegate deferred [--file PATH]            # the deferred-work list (.framegate/deferred.md)
framegate deferred --later FILE --feature <f>  # "split this over-limit file later"
framegate uses <symbol> [--file PATH]       # where the source (not the tests) reads a
                                            # function or a variable, as file:line: line
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

**Markers, commits, known failures.** `spec-approved/<f>`, `tests-approved/<f>`, `spec-update/<f>`, `tests-update/<f>`, `knowledge-update/<f>` and `config-update/<f>` are private refs (`refs/<name>`) by default: `git diff tests-approved/<f>` resolves them like a tag, but no `git push`, `--tags` or `--all` publishes them to a shared remote. `[git] markers = "tags"` stores git tags instead (needed for a CI job that runs `framegate gates`); tags left by an older framegate are still read and replaced on the next write. A workflow commit stages only the workflow's paths (`[paths] src`, `tests`, `specs`, `metrics`, the knowledge file, the deferred-work list, the code map), never the whole working tree: your notes and unrelated edits stay out. Lint, format, typecheck and imports, in every mode (Python): the checks run on the whole tree, and a finding counts only in a file the task changed since the tests were approved, or since the spec was approved (the tests the task wrote). Findings in other files are left out of the verdict and of the output ("in files the task did not change (left out): N line(s)"). Limits: a type error the task causes in a file it did not change is not reported (the tests have to find it), and a failure that names no file (an import contract, a crashed tool) always counts. `[gates] regression_baseline` is about tests only: `approve-spec` runs the whole suite once and records the tests that already fail (`out/<f>/regression_baseline.json`; never a test tagged with one of the feature's own spec IDs; kept on a second approval). The step gates and the final regression check (with the default `coverage_scope = "feature"`) leave them out. Not set, it is on for a bug fix (the bug can have a failing test already) and off for a feature and a refactor, which start on a green tree; `true` / `false` in framegate.toml sets it for every mode. Formatting debt, in every mode: the format fix formats a changed file whole, so debt from before the task in a file the task touches is fixed with it, without a question. The lines the formatter rewrote outside the task's own lines are counted (`format_debt` event) and listed in final_facts.md ("Formatted beyond the task's lines"), for one line in the summary. They are committed apart, before the task's commit, as `<f>: format <files>` (`src/framegate/format_split.py`, `format_commit` event), so the task's commit shows the task's lines only; a file whose task lines and formatted lines touch stays whole in the task's commit. The reviewer is told which files were restyled: never a finding. A statement whose suppression comment the formatter would move off its line (python: `# noqa`, `# type: ignore`, `pragma: no cover`) stays unformatted; the "Format fix" line of the gate names it and the format check leaves it out. Files the task does not touch are left as they are.

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
  shared spec ID and 80% of the shorter one's words; `questions` event: `repeats`). Among the
  open questions a question is its asker and the spec IDs it names: a new one with the same
  asker and IDs takes the open one's place, whatever its words (`question_superseded`
  event). A question the owner answered earlier in the task is not asked again: a file split
  by its file, a doc proposal by its file and place, any other by its words (`asked_again`
  event; final_facts.md and the summary list it under "Answered before"). An open question
  that names a test check which failed in an earlier round and passes now is closed
  (`question_resolved` event, "resolved by the writer" in `questions_archive.md`). The
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
- Spec-named tests changed (a test check, blocking; `checks/named_tests.py`). An existing test
  that the spec's "Test data and fixtures" names as `tests/<file>::<Class>` or
  `tests/<file>::<Class>::<test>` must have a changed line inside it since the spec approval;
  one that is gone (removed or renamed) fails too. `(unchanged)` after the name: not checked.
  A test that did not exist at the approval: not checked. Python checks the lines of the class
  or test; other languages check that the file changed. A test file the spec names for a spec
  ID must be touched too (`NOT TOUCHED` otherwise). Only an explicit placement counts: a line
  `<ID>[, <ID>]: in tests/<file>`, or a table column headed "Test file" / "Test in"; a path in
  prose says nothing, and `(unchanged)` after the file takes it out.
- The red check (feature; `checks/red_stubs.py`) wants every test red for the right reason:
  on the stub, on an assertion about the new behaviour, or inside the code under test. A test
  that stops on another error before that (in the test itself, a fixture, a helper) is
  listed as `FAILS FOR ANOTHER REASON THAN THE STUB` and fails the check. The verdict covers
  the tests the feature added or changed. A test file it did not touch that cannot load is a
  `NOTE:` when it was broken before the task: listed in `[gates] unrunnable_tests`, recorded
  in the regression baseline, or not loadable at the spec-approved commit either (asked once
  per set of files, in a scratch worktree; `out/<f>/red_before.json`). One that loads there
  stays an error: the task broke it. A red test stops at its first failing line, so
  test_checks.md lists "Asserts not reached at red" (a note): per red test where it stopped
  and the assertion lines after it that no run has executed; the test reviewer walks each by
  hand.
- A test tagged with spec IDs of more than one plan step runs at the gate of the latest of
  those steps. The step gates before it and the Stop hook leave it out
  (`out/later_tests.txt`; "left out, they also need a later step: N" in the check's result
  line), and the implementer's task names it. test_checks.md and tests_gate.md list them
  under "Tests that span plan steps".
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
  nothing: it writes the root cause, the fix owner (`src` / `tests` / `spec` / `config` /
  `setup`) and one proposed fix (`out/<f>/diagnoses/<item>.md`, `diagnosis` event). `src`: the proposal
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
  It is never offered for approval: the diagnosis names it with `Fix owner: setup`, and a
  `config` proposal is checked when the diagnosis is read (the `[section]` and the TOML
  line of its "Proposed fix"). The parked item's question is then "This needs the setup:
  <key: old -> new> ... Run /framegate:setup ..., then continue this task"; its redo runs
  once the gate inputs changed, or on "redo anyway". An approved change that is refused
  all the same parks the work again with that question; nothing is redone.
- A redo that cannot end differently is not run. Work that parked on something no
  implementer run can change ("unfixable in IMPLEMENT", or a diagnosis with owner `tests`
  / `spec` / `config`) keeps a digest of what the gates read outside the source folders:
  every tracked file outside `[paths] src`, the review tests, the out folder and the code
  map (the spec, the frozen tests, framegate.toml, packaging and tool config), uncommitted
  changes to such files, and the two records of what was red at the spec approval. When
  the answer only approves (`B: apply the proposed fix as written.`, `B: approved`) and the
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
  fixes as one numbered item (`approve` / `finish` refuse while it is open): A: leave them,
  the code stays and its contested tests stay red (the default; the deferred-work list) / B:
  apply all the proposed test fixes / C: say which fix is wrong. After answer B,
  the test writer applies the approved fixes (TESTS_AMEND); then the contested tests must pass.
  If they still fail (the fix was not approved), the provisional commit is reverted (its diff
  kept in `out/<f>/parked/`) and the step is redone against the tests
  (`provisional_settled` event). Goal: every wrong test surfaces in the same round.
- A parked item's default leaves it parked (the deferred-work list); answer B (`B: apply the
  proposed fix as written.`, or `B: <your words>`) redoes it. Parked work answered with its
  default, or not named among numbered answers, stays as it is; a step that only waits for a
  redone one is redone with it. When no step of the task is built, leaving the item parked
  would end the task empty: the item says "No default: choose." and needs A or B.
- Final gates with parked steps or provisional steps: the whole-suite regression run counts
  the parked steps' tests (by spec ID) and the contested tests as expected red ("expected red
  (parked): N" in the report) and fails only on any other failure (listed as UNEXPECTED);
  coverage, diff coverage and mutation are SKIPPED with the reason until the redo.
- Review findings (failing reviewer tests) are triaged against the spec by the test reviewer:
  VALID stays and goes to the fix loop, INVALID is deleted with a one-line reason
  (`triage.md`, `triage` event). Fixes that fail `max_attempts` times are parked. The reviewer
  is told the parked steps' IDs; a valid finding whose test carries only such IDs is DEFERRED:
  no review-fix run builds the parked step, its redo makes the test pass. A new review test
  file whose tests all pass backs no finding: it is moved to `out/<f>/review_probes/`
  (`review_probes` event); one with passing and failing tests stays.
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
the interactive session); `framegate unlock` clears it. The orchestrator logs once why it
stops (`orchestrator_exit` event: a signal, an error, a gate, the end); a run found gone
quotes that record, or says there is none (a hard kill).

Every check is timed (`check` event: name, phase, seconds, status). A check that runs longer
than a minute writes a progress line every minute (`still running <check> (N s)`).

With `FRAMEGATE_DETACH_RUN=1` (the replay sets it) `framegate run` starts itself detached,
returns once that run holds the run lock and prints `runs detached` with the pid and the log
(`out/<f>/run.log`); its end shows in `watch` and `status`. If the detached run ends first (a
gate, an error), its output and exit code are passed on.

Turn and cost caps per agent run: `--max-turns` = `[phases.<P>] max_turns`, else
`[agents] max_turns`, else TESTS 50, TEST_REVIEW 30, IMPLEMENT 80, SIMPLIFY 40, REVIEW 40, TRIAGE 30, SUMMARY 30,
DOC_CHECK 30, DOCS 30, RETRO 30. A turn is one model round trip (one assistant message, however many parallel tool calls
it makes); the CLI's `num_turns` counts messages (prompt + every tool result) and runs about 2x
higher, so framegate logs both (`turns` = round trips from the message stream, `messages` =
`num_turns`; docs/claude-cli-notes.md). `--max-budget-usd` = `[phases.<P>] max_budget_usd`, else
`[agents] max_budget_usd`, else TESTS 2.0, TEST_REVIEW 1.5, IMPLEMENT 1.5, SIMPLIFY 1.0, REVIEW 1.5, TRIAGE 1.5,
SUMMARY 1.5, DOC_CHECK 1.0, DOCS 1.0, RETRO 3.0 USD (~3x the live FCSim cost per run; `0` = no cap). A run that stops at
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
  after DONE updates. At DONE, `framegate run` and `status --brief` name a waiting update
  in one line (`/framegate:update`).
- A task without activity for 14 days (its newest event; without one, the time its
  `progress.json` was written) no longer holds an update back. It stays in the list.
- `framegate status --all` shows the list as one table: repository, task, mode, phase,
  last activity, `stale`. Works in any folder.
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
A check's output in a check file (test_checks.md) or in a task sent back to an agent is cut
after 60 lines; the rest is in the same log file, named in the last line.

Models and speed:
- `--model` per phase: `[phases.<P>] model`, else `[agents] model`, else Sonnet for the writers
  that follow the approved spec and the frozen tests (TESTS, IMPLEMENT incl. review fixes,
  SIMPLIFY, SPEC_UPDATE, SUMMARY, DOCS) and Opus for the checkers and the retro (TEST_REVIEW, REVIEW, TRIAGE,
  DOC_CHECK, RETRO). `--effort` likewise (`effort`, default: Claude Code's). Each `agent_run` event records
  the model, so cost and failed attempts per model can be compared across features.
- Small plans run in one implementer run: consecutive steps share a run while their spec IDs add
  up to at most `[agents] step_batch_ids` (15; 0 = one run per step). The batch is checked
  and committed as one unit (`steps 1, 2, 3`). A batch that cannot pass as a whole is judged
  step by step: the most of its first steps whose own step gate passes are kept (one commit;
  as provisional when tests are contested), and only the steps left are parked and redone
  (`batch_split` event). A question that contests no test stops the kept part at the first
  step whose spec IDs it names; when it names none, nothing is kept.
- A step gate judges only what belongs to the task: the tests of the step's IDs and the
  earlier steps'. The whole suite runs once, at the final gate (seen live: run at every step,
  it failed each one on tests that were red before the task). `[gates] step_tests = "suite"`
  runs it at every step gate too, leaving out this feature's later steps, contested tests
  and tests that already failed when the spec was approved.
  Without that setting, a step gate and the gates of the runs after the review also run the
  existing tests of the changed code (check `existing tests of the changed code`): the test
  files that were there when the task started, that the task did not change, that hold no
  test of the feature and that mention a function or class the task changed; at most 10
  files, those that mention most of the changed names first, run by file. Contested tests,
  listed tests and tests recorded as failing at the spec approval are left out; the files
  beyond 10 are left to the final regression.
  The runs after the review (review fixes, structure fixes, a final-gate repair) are checked
  with the feature's tests (its spec IDs; with parked steps the done steps' IDs) and the
  review tests folder, not with the whole suite: the final gates run it right after (seen
  live: 4.5 minutes per gate, twice for two small fix runs, directly before the final
  regression). What such a run breaks elsewhere fails the final regression and goes to the
  one repair run, and the repair of a whole-suite failure is checked with the whole suite.
  Before checking, the gates format the changed source files
  and the changed review tests, and the test writer's files are formatted after each of its
  runs (`[gates] format_fix`, default on, only with the adapter's own format check), so a
  formatting slip costs no attempt and never sits in a file the implementer may not edit.
  The same files first get the linter's safe fixes (import order, an unused import):
  `ruff check --fix` when the lint command is ruff, else `[gates.commands] lint_fix` (with
  `{files}`; `[]` = off; `[gates] format_fix = false` turns both fixes off). For
  .NET the format / lint checks report only findings on changed lines, and the format fix keeps
  only its changes there: no churn in code the feature did not touch. For Python the lint /
  format / typecheck / imports checks count only findings in files the task changed.
- `[references]` (name = path, or `{ path, note }`): other projects the agents may read, see
  "References and ports".
- `[gates.extra]` (name = command): project checks after the final gates, e.g. a balance
  measurement. Shown at the final gate with exit code and output (full output in
  `out/<f>/extra_<name>.log`); never part of the verdict.

Simplify pass (`src/framegate/simplify.py`, phase `SIMPLIFY`, role `simplify.md`, `[simplify]
enabled`, default on): after the last plan step and before the review, one agent run whose
only job is to make the task's diff smaller with the behaviour unchanged, once per task. It
does not run in a refactor (its diff is the structure the plan asked for), in a port (the
code follows its reference), while parked or provisional work waits for an answer, or when
the task added fewer source lines than `[simplify] min_lines` (10). The frame: tests and spec
are frozen as for the implementer, and only source files the task already changed may be
touched. Afterwards the checks of a run after the review run (lint, the project rule checks,
the frame check, the task's tests, the existing tests of the changed code) plus coverage and
diff coverage of the lines the pass wrote. All green: the pass is its own commit (`<f>:
simplify`). Anything else (a red check, a file outside the list, an interrupted run): its
changes are dropped and the run goes on with the code as it was. It never parks and never
asks. Logged as a `simplify` event (status `kept` / `dropped` / `unchanged` / `skipped`, the
task's source diff lines before and after); final_facts.md gets "Simplify pass" with those
numbers or why it was dropped, and `framegate stats <f>` shows the line.

Project rule checks (`src/framegate/rules.py`, `[checks]`): the project's own CI rules as
gate checks.

```toml
[checks]
rules = ["python ci/check_rules.py src tests"]     # one command per string
rules_text = "python ci/check_rules.py --rules"    # prints the rules as text
```

- Every step gate, the gates of the runs after the review and the final gate run each
  command as a check (`project rule: <command>`). A failure goes back to the implementer
  like lint output; at the final gate it belongs to the implementer's repair.
- `{files}` as an argument is the task's changed source and test files (no changed file:
  PASS without a run); without it the command runs as written.
- Debt is not the task's. `approve-spec` records once per task what each command prints on
  a failure on the code as it is (`out/<f>/rules_baseline.json`); an output line that was
  printed before is left out, and a finding that names a file counts only on a line the task
  wrote. A command that cannot start or times out is a tool failure, not a verdict.
- Every task that writes source (implementer, review and final fixes, structure fixes, the
  simplifier) gets one generated block "Project rules": the commands, the linter's active
  rule selection (read for ruff from its config) and what `rules_text` prints (60 lines at
  most). No handwritten guideline text.
- The setup proposes both in row 13: a script named like a rules check under a CI or scripts
  folder, as the line of a CI file that calls it, else run plainly; `rules_text` when the
  script knows `--rules`.
- `framegate doctor` runs the commands on the whole tree; `doctor --tree` reports a failing
  one as a `DEBT:` line.

Final-gate repair (`[gates] final_repair`, default on): a failed final gate gets one repair
run per owner before the human sees it. Failures the code owns (lint, format, typecheck, a red
test in the regression or coverage run, removed suppressions and catch-alls, a project rule, a diff-coverage
miss on lines only the structure fixes wrote) go to the implementer (`FIX_FINAL` task, the whole
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

Repair review: after a repair run that changed source files, a new reviewer agent judges only
that diff against the spec's "Must not change" (`out/<f>/repair_review.md`, `repair_review`
event): does the repair change behavior the spec keeps, remove a check, a suppression or an
error handler the code needs, or go beyond the failed checks? Objections go back to the
implementer once, then the review runs again. An objection that still stands is a final-gate
item without a default: A: keep the repair as it is / B: the implementer changes the repair
as the reviewer says (one more run, the final checks again). "No default: choose." The retro
gets both review files.

Removed suppressions and catch-alls (final gates; `checks/removed_guards.py`): per source
file the task changed, each suppression or catch-all handler the adapter knows (python:
`# noqa`, `# type: ignore`, `pragma: no cover`, `except Exception`, a bare `except:`; .NET:
`#pragma warning disable`, `SuppressMessage`, `ExcludeFromCodeCoverage`, `catch (Exception)`,
a bare `catch`) that the base has and the file no longer has fails the check, unless a line
of steps.md names the file, the guard and a spec ID (the implementer's step note: `Removed
<guard> in <file>: <spec ID>, <why>`). A guard counts by its text per file against the
formatted base, so a moved or rewrapped one is not gone. SKIPPED for an adapter without guard
patterns.

The structure fixes' own gate also runs coverage and diff coverage on the lines they wrote,
so a line no feature test reaches is found there and not at the final gate.

Tool failures: a mutation check that fails without a score (the tool crashed, could not build
its own copy, was cut short) is a tool failure, not a statement about the tests: it is shown
as `[NOT MEASURED]`, never as FAIL, with `TOOL FAILURE: ...` under the check. The
`final_gates` event lists it in `tool_failures` and `not_measured`, and no repair run is
started for it. The final gate asks instead (an item the
brief always shows): A: finish without this check (approve as is; the default) / B: run the
final checks again. The .NET adapter starts a failed Stryker run a second time first (with `--diag` when
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
...)`). Mutation off on purpose (`[gates] mutation_tool = "none"`, written by the setup
after the user confirmed) is SKIPPED: no risk reason, no stop.

Spec check (`framegate spec-check <f>`, also the first step of `approve-spec`, which refuses
while it finds something; `--force` approves anyway and logs `spec_check` with `forced`). It
reads columns of the spec templates' tables; a spec without them is not checked:
- `Spec ID` (design.md, "Error handling" and "Command-line surface"): every filled row names
  a spec ID of requirements.md. A rule that is only written in design.md gets no test.
- "Green against stubs" (requirements.md): every bullet is `- <test name or node ID>:
  <reason>` ("References and ports").
- "Public interfaces" (design.md): every row names where the member's shape comes from
  (`task`, `code: <pattern>`, `docs: <file>` or `asked`), and every public name the stubs add
  has a row ("Which questions are asked").
- Requirement rows (requirements, edge cases, invariants, reproduction, must not change,
  behavior to preserve): a row whose subject is docstring or comment text is refused; quoted
  program output (an error message, a log line) is behaviour and passes.
- "Suspected causes" (bug fix): every cause ends with `-> <ID>` of a row of the report or
  `-> ruled out: <evidence>` ("Modes").
- `Resolves:` (requirements.md): every id is an open entry of the deferred-work list.
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
- Files a plan step names (`checks/plan_files.py`): a step may mention any file. An existing
  code file outside the source and test folders is a problem (add the folder to `[paths]
  src`). Nothing a step names becomes writable.
`spec-check` also prints `Setup files changed (committed with the spec): ...` and reports
uncommitted protected files that are not setup files (`approve-spec` refuses on those even
with `--force`). Not blocking: a `WARNING` when the spec adds a test file while a CI file
lists test files by path and "Pipeline follow-up" does not name it; the deferred-work entries
on the files the plan names; the files the plan names that are over the size limit.
`framegate doctor` warns when a project's copy of the templates (`specs/_template/`) lacks a
section or a table column of framegate's templates.

Risk rating (`src/framegate/risk.py`, `[risk]`): at every final gate the engine rates the
feature `low` or `elevated` from its own records, never from an agent's opinion. The
principle: strict by default, and one value is loosened at a time with the evidence of
`framegate stats --risk`. The switch `[gates] final` is `"human"` by default: every final
gate is yours, and the rating is only shown and logged. A feature is `low` only when none of
these blockers holds; each one that holds is a reason on the brief.

| Blocker (the feature is `elevated`) | Strict default | The setting that loosens it |
|---|---|---|
| the final gates did not run, or failed | always | none |
| diff coverage or the mutation score was not measured, or is NOT MEASURED (named with its fix) | always | none; mutation switched off at the setup (`[gates] mutation_tool = "none"`) is no reason |
| parked work, provisional steps, open items to decide | always | none |
| in the run: an answer round, a park, a provisional step, a cap hit twice, a rejected gate | always | none |
| the size of the change could not be measured | always | none |
| a final check was SKIPPED | any | `allow_skipped_checks = true` |
| the final gates needed a repair run | any | `allow_repair = true` |
| a review finding was judged valid | any | `allow_valid_findings = true` |
| a doc update was proposed | any | `allow_doc_proposals = true` |
| logged assumptions | more than 1 | `max_assumptions` (0 = not tested) |
| changed source lines (added + removed) | more than 80 | `max_changed_lines` (0 = not tested) |
| changed source files | more than 4 | `max_changed_files` (0 = not tested) |
| removed lines in source files that existed before | more than 10 | `max_removed_lines` (0 = not tested) |
| a changed path that is never automatic | the default list below | `never_auto` (a list replaces the defaults; `[]` = none) |
| a file over `[gates] max_file_lines` that the feature made longer | any | `[gates] max_file_lines` |
| finished features in the metrics file | fewer than 3 | `min_features` (0 = not tested) |

Lines are measured against the feature's base formatted as the gates format, so a rewrap is
no change; in a refactor moved lines (removed in one place and added unchanged, but for the
indent, in another) are not counted.

`never_auto`, when the project sets none: the deploy / CI paths (the built-in names and
`[protected] extra`), schema and migration files (`**/*.sql`, `**/*.prisma`, `*migrations/*`,
`*alembic/*`) and docs (`docs/`, `doc/`, `**/README*`, `**/CHANGELOG*`). With these defaults
framegate's own files (the specs, the code map) do not count: every task changes them.

Track record: three more rows never change the rating, but while one holds `[gates] final =
"auto"` passes no gate of the project. They are read from the past final gates of other
tasks and from the escaped-defect log:

| Row | Strict default | Setting |
|---|---|---|
| past final gates rated low that a person approved as they stood | fewer than 5 | `min_agreed` (0 = not tested) |
| past final gates rated low that a person answered with changes | more than 0 | `max_low_overruled` (-1 = not counted) |
| escaped defects on tasks rated low | more than 0 | `max_low_defects` (-1 = not counted) |

The brief shows `Risk: low (...)`, `Risk: low (...); never automatic yet: <the rows that
hold>` or `Risk: elevated: <the reasons>`. The `risk` event (level, reasons, `blocked`, the
numbers) and the `human_gate` event (`risk`) record it next to your decision.

`framegate stats --risk` (`src/framegate/risk_review.py`; read-only, from the events) is the
evidence: how many final gates were rated low and elevated and how each was decided
(approved as it stood, answered with changes, approved by the engine), how often "low"
disagreed with you and on which tasks, the escaped defects per rating, and one line per
decided gate with its first reasons. `framegate stats <f> --risk` shows one task.

With `[gates] final = "auto"` a feature rated low, with every check measured and no
track-record row holding, passes the final gate without a stop. It writes: a first line
`> APPROVED AUTOMATICALLY on <date>: no person approved this final gate. ...` in
final_brief.md and final_summary.md, a `human_gate` event with `auto: true`, `by: "engine"`,
`person: false`, `risk: low` and the brief's path, a progress line and a toast. Then the
retro and DONE; nothing is pushed, the pull request stays yours. Everything else stops as
before.
A configured check that is NOT MEASURED (the tool cannot run here, or gave no result) is a
reason for `elevated`, with its fix: `Risk: elevated: mutation score NOT MEASURED: <reason>.
Fix: <fix>`. A score below the threshold is a reason as before; `mutation_tool = "none"` (off
on purpose, a setup decision) is not.

A final check whose tool gave no result is asked at the final gate: finish without it (the
default: no change), or run the final checks again. With the same result a second time,
option B becomes "repair the tool with `/framegate:setup`, then run the final checks again".

The task's own lines: lint, typecheck and imports findings and diff coverage count only the
lines the task wrote. The format fix formats a touched file whole, so the comparison base is
the approved commit with those files run through the same fixes (`refs/framegate/task-base/`,
`task_base.py`): a line only the formatter rewrote is the same on both sides. The structure
report's sizes "before" and the risk rating's line counts use the same kind of base, built
from the feature's start commit.

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

Mutation, what counts (python, mutmut): only mutants on lines the task changed, measured
against the formatted base. A mutant on other lines of a changed function, or only inside a
logging call (`logger.info(...)`, `self.log.debug(...)`, `print(...)`), is left out of the
score; the score line says how many of each. No mutant on the task's lines is a PASS. The
runner switches off hypothesis's "differing executors" health check for its own test runs
(mutmut repeats the tests in one process). For every adapter: a scored run is kept with a
hash of what it ran on (the source and test files, the changed files, the test scope, the
threshold; `out/<f>/mutation_cache.json`), and while none of that changed the check gives
the result again without a run ("result reused, not run again"). A failing run's survivors
stay in `mutation_survivors.md` under `## Earlier run <n>` (the newest first, at most 5), for
the retro.

Unreached assertions (`[gates] unreached_assertions`, default on; Python and .NET): the
coverage run also records the lines of the test files the feature added or changed (not the
review tests; diff coverage still counts source files only). A test that ran, with an
assertion whose line has no hit, fails the check `unreached assertions (feature tests)`; the
final-gate repair sends it to the test writer like a weak diff coverage. Seen live: `if the
shot was saved: Assert...` on a match without a saved shot passed every check. Left out:
tests that were there before the feature, tests that did not run, assertions that always fail
(`pytest.fail`, `Assert.Fail`, `assert False`: guards), and a line marked `unreached: <why>`
in a comment on it or above it. Without a coverage tool the check is SKIPPED.
A bug fix and a refactor also run it in the TESTS phase, for the tests that pass before the
code (must-not-change tests, a refactor's tests): one run of the feature's tests that records
the test files only (python). A red test says nothing there; a feature's tests are judged at
the final gate.

Questions, assumptions, parked items and failed retries feed the retro. Every step-gate run is
logged in `out/events.jsonl` (`gate`: phase, step, attempt, `failed` check names, trimmed
`excerpt`, seconds), as are questions (phase, who, text), test reviews (`test_review`: round, verdict,
objections, `follow_up: true` for a follow-up round), test checks sent
back before a review (`test_checks`: `round`, `failed_checks`, `sent_back`), the auto tests gate (`tests_gate_auto`: passed, reasons), answers (gate, text; also
appended to `questions_archive.md` under the questions), parked items (`parked`: step/finding,
reason, question, failing checks, attempts, diff), redo results, review findings and their triage,
final gates (`failed`, `skipped`, seconds per check), permission denials (`permission_denials`:
phase, step, tool + command/path of every call the permission check denied, from the `claude -p`
JSON), turn-cap hits (`max_turns`, and `escalation` when the re-run hit it too) and
interruptions. Also logged: `check` (every check with its seconds), `format_commit`,
`batch_split`, `question_superseded`, `question_resolved`, `asked_again`, `answer_unclear`,
`repair_review`, `structure_fixed`, `review_probes`, `doc_check`, `doc_text_check`,
`deferred`, `env_gaps`, `simplify`, `size_noted`, `escaped_defect`,
`escaped_defect_fixed` and `orchestrator_exit`. Before the RETRO agent runs, framegate writes `out/<f>/retro_input.md`: counts,
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

retro_input.md also holds, as context without IDs, the five slowest checks, the
orchestrator's exit records, what the reviewer wrote about a final repair, and the open
escaped defects on the task's files or spec IDs ("Escaped defects").

Two more sections of retro_input.md carry no IDs:
- `## Chat`: the chat's notes. The engine does not see the chat, so the skills record one
  line per question to the user outside a gate brief (with the reply), per thing done by
  hand and per refusal: `framegate note <f> <text>` (a `chat_note` event, 300 characters;
  the retro gets the newest 40).
- `## Run state: stopped before DONE`: only with `framegate retro <f>`, the retro of a run
  that was given up, hit a cap or ended in an error. It works in any phase after "spec OK"
  and before the final gate is passed, names the phase reached, why the run stopped and the
  last progress lines, and leaves the task as it is: no metrics row, no commit,
  `progress.json` unchanged. `keep` and `learn` work on its proposals as usual.

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
can't stop while the step's tests fail, contested tests of provisional steps and tests that
also need a later plan step left out, unless it parked: an `out/*/park.md` exists). Both read
the hook JSON on stdin, exit 0 silently without `framegate.toml` or `.phase`, and block with exit
2. The write protection holds for every session while a phase is set, the chat included; in the
SPEC phase the chat may also write the setup files and `out/<f>/spec_brief.md` ("The spec phase
covers the setup" above); a deploy / CI file (the built-in names, `[protected] extra`) is
refused in every phase but the pipeline task's. A documentation file is refused in every
phase but `DOCS` (the docs step after the final gate): the block tells the agent to add a
line to `out/<f>/doc_proposals.md` (in the spec phase: under `## Proposed doc updates` of
requirements.md) and carry on. A doc inside a phase's own write folders (a README in a source
folder) is not refused; the pipeline task has its own list. In `DOCS` only documentation files
and `out/` are writable ("Sources and doc updates"). A path outside the repository is
blocked only for the agents of a run
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

Checks first. After the test writer, the test checks run. If one fails, the tests go back to
the test writer with the check output and no test reviewer starts (event `test_checks`).
After `[agents] test_check_rounds` send-backs, or when the checks fail exactly as before, the
reviewer runs and the failure goes on as below.

Follow-up review. Round 1 of the test review is a new agent that reads the spec and all
tests. A later round of the same gate cycle is a new agent with a short task: the objections
of the round before, the diff of the test files since then and the check results. It judges
whether each objection is resolved and whether the change broke anything else, and writes
`test_review.md` in full. After a reject or an answer at the tests gate the review is full
again. The review ends with a "Spec conformance" table: one row per spec ID (test file,
fixture and expected value as the spec says); every row that departs from the spec is an
objection.

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
`[agents] test_review_rounds` (2) bounds the writer <-> reviewer loop; `[agents]
test_check_rounds = 2`: how often failed test checks go back to the test writer before the
test reviewer runs (per gate cycle), 0 = the reviewer always runs; `[gates] tests = "human"`
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
