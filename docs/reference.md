# framegate reference

What each part of framegate does and why it is there, in the order a task flows. Each entry names
its module (under `src/framegate/`) or its command. Options, file names, event names and message
texts are not repeated here: read them in the code, in `framegate --help` and in the commented
`framegate.toml` that `framegate init` writes.

Purpose, principles and roadmap: [plan.md](plan.md). Standing decisions:
[decisions.md](decisions.md). First steps and the command overview: [README](../README.md). `<f>` is
the task's name; a task is a feature, a bug fix or a refactor.

## Contents

1. [Setup](#1-setup)
2. [Spec](#2-spec)
3. [Tests](#3-tests)
4. [Implementation and step gates](#4-implementation-and-step-gates)
5. [Simplify pass](#5-simplify-pass)
6. [Review](#6-review)
7. [Final checks and repair](#7-final-checks-and-repair)
8. [Docs](#8-docs)
9. [Final gate and summary](#9-final-gate-and-summary)
10. [After the task](#10-after-the-task)
11. [Agent runs](#11-agent-runs)
12. [State, branches and locked paths](#12-state-branches-and-locked-paths)
13. [Usage limits, errors and stops](#13-usage-limits-errors-and-stops)
14. [Adapters, config, plugin, replay](#14-adapters-config-plugin-replay)

## 1. Setup

Once per repository: `/framegate:setup` in the chat.

**Two commands** (`setup_cmd.py`). `framegate setup --plan` installs the workflow tools into the
project's environment, maps the folders, probes the project and prints one table of facts: language,
interpreter, test / lint / format / type commands, folders, base branch, shared repository, locked
paths, references, project rule checks, mutation testing. The user replies "ok" or corrects a row.
`framegate setup --apply` writes the confirmed plan (only the keys that differ from the defaults),
installs the tools the gate commands name, runs the setup check on the tree and says what is left to
do. It can run again; earlier corrections are kept. The chat still writes the project knowledge file
and the code map.
Why: setup as model turns was slow and reworded the facts on the way. A row is a proposal from the
project's own files; nothing becomes config without the user's "ok" or correction.

**Survey and probe** (`survey.py`, `probe.py`). The survey is one read-only agent run that sorts
every code file into source, tests or not the project's code; a file it leaves out is reported. The
probe uses instant checks only: it reads files, asks git, and asks each tool for its version in the
project's interpreter. It runs no test and no linter.
Why: a command found in a file is no proof that the tool answers in the environment the gates use,
and folder names alone do not say where the code is.

**The tree at setup** (`tree_check.py`, `framegate doctor --tree`). The whole suite runs once on the
untouched code. A failing test that nobody decided about makes the setup "not ready". Lint, format,
type and project-rule findings on the existing code are reported as debt with their size, and the
chat proposes one clean-up task for them. A mutation tool that cannot run on this machine is made to
run or switched off once by the user (`[gates] mutation_tool = "none"`).
Why: a feature and a refactor start on a green tree. Debt found once at setup is not blamed on the
first task, and is not asked about file by file. Mutation switched off is a decision; a tool that
silently cannot run would make every later task "not measured".

**Listed tests** (`unrunnable.py`, `known_red.py`; `[gates] unrunnable_tests`). A test that is red
before any task is listed with its reason: this machine cannot run it, or it is the project's own
debt. The chat proposes the entry and the user confirms it. A failure of a listed test is left out
of every gate in every mode. One that failed here at setup is not run here again. The final summary
names the listed tests each time. `setup --apply --install <requirement>` (`env_install.py`)
installs a package the tests lack; when that fails, the environment is put back and the tests that
lack a module are listed with that reason.
Why: the engine cannot tell "this machine lacks a database" from "really broken". Naming them every
time keeps "all green" from being read as proof for tests that never ran, and a setup must not end
in repairing the machine.

**Frameworks declared but not installed** (`env_gaps.py`). The probe and `framegate spec` compare
what the project declares (dependencies, extras, modules its tests skip without) with what the
environment has. The spec dialogue asks about a gap before a requirement or a test needs it.
Why: otherwise the tests phase finds out, after the spec is frozen.

**Project knowledge** (`[project] knowledge`, default `.framegate/project.md`). What no single spec
repeats: architecture rules, what must never change, test-harness facts, tools. The spec dialogue
and the agents get it; no agent may edit it. It grows only by the facts a retro proposes and the
user picks (`framegate learn`).
Why: templates and roles stay general; a project's facts live in the project.

**Code map** (`[docs] code_map`; `checks/structure.py`). A short living document: per module its
purpose, its key classes and how they work together. The setup drafts it; the summary phase updates
the parts a task touched. A new public type the map does not name is a warning on the final brief.
Why: the human owns the structure, not every line. The map is how they see it.

**Setup check and commit** (`doctor.py`; `framegate doctor`, `framegate commit-setup`). The doctor
checks config, git, the tools, the gate commands and the test selection by spec ID, without agents.
`commit-setup` puts the files the setup wrote into one commit of their own, because `framegate spec`
refuses uncommitted changes to tracked files.

## 2. Spec

**Starting a task** (`framegate spec <f> --mode ...`; `/framegate:feature`, `:bugfix`, `:refactor`).
Creates the task's branch from `[git] base`, opens the spec phase with the mode's templates and
lists the environment gaps. It refuses while tracked files have uncommitted changes and uses an
existing branch only if it is this task's.
Why: one branch per task in the current checkout; a dirty tree would mix the user's edits into the
task's commits.

**Modes** (`specs.py`, `checks/baseline.py`). The mode is the `Mode:` line of requirements.md. It
decides the first proof of the tests; the rest of the flow is shared. A spec of another kind than
the task was started as is refused at approval.

| | feature | bug fix | refactor |
|---|---|---|---|
| Spec | requirements, edge cases, stubs | reproduction rows, must-not-change rows, suspected causes | scope, behaviour to preserve, structure targets |
| First proof | every test fails against the stubs | reproduction tests fail on today's code, must-not-change tests pass | every test passes on today's code |
| Tests | new | new | existing tests that assert a row are tagged with its ID; new ones only for the gaps |
| Steps | batched | usually one; the step note names the root cause | one move per run, checked with all the task's tests; a step that changes no source fails |
| Review finding | against the spec | also the same bug through another input | a difference from the code before |
| Start tree | green | may be red | green |
| Repair of weak tests at the end | yes | yes | no: the weak tests go to the human |

Why no test repair in a refactor: tests written after the move would pin the changed code, not the
behaviour before it.

**What the spec dialogue asks** (the skills; `framegate spec-brief`). One compact round, the most
consequential first, each with a default. A point is a question only when two or more reasonable
options lead to different outcomes and the task, the code, the docs and the project knowledge do not
settle it. Always asked unless already stated: every new or changed public name, signature, type,
default, error type or message and stored format; what must not change; where the tests go; which
docs may change. A reply that describes the task instead of answering is the requirement, not "ok":
open questions are asked again. `framegate uses <symbol>` (`uses.py`) lists every place that reads a
function or variable, for a requirement that changes what an input may be.
Why: runs fail on decisions nobody asked about. A point with no visible effect on callers or stored
data is decided by the chat and listed, not asked.

**Spec check** (`framegate spec-check <f>`; `checks/spec_lint.py`, `checks/spec_rows.py`).
Mechanical checks of the spec tables and the stubs; the approval refuses while one fails. It reads
the templates' columns, so a spec written without them is not checked.

- Every rule in design.md's error handling and command-line tables names a spec ID. Why: a rule
  without an ID gets no test.
- Every public member has a row that says where its shape comes from, and every public name in the
  stubs has a row.
- Every error rule names its source: the task, existing code, a doc, or the user's answer
  (`checks/spec_rows.py`). Why: an error type or message nobody decided is a question to the user,
  not an agent's invention.
- A row with an ID whose own text says the case is untested or undefined is refused. Why: every ID
  gets a test; a case left open has no ID.
- "Test data and fixtures" names one existing test per line, each with its file. Why: the
  `(unchanged)` mark after a test must belong to that test only.
- Code copied into "Existing data this spec reads" is really in the file the row names, and a spec
  that pins test data names the data for every edge case. Why: both make the spec writer read the
  code and the data instead of trusting a name.
- A requirement row about docstring or comment text is refused. Quoted program output passes. Why:
  comments are no source; output is behaviour.
- Bug fix: every suspected cause is tied to a row or ruled out with evidence
  (`checks/suspected_causes.py`).
- Bug fix: every existing test that reaches the function named under "Where the bug is" is named as
  staying or changing (`checks/bug_tests.py`; Python). Why: otherwise the fix turns an old test red
  and nobody decided whether that test or the fix is right.
- The stubs hold no suppression the frame check would reject at every step gate; a plan step names
  no existing code file outside the source and test folders; `Resolves:` names an open deferred
  entry; a named reference is declared.

Listed without blocking: the setup files the approval will commit, deferred work on the plan's
files, and the plan's files over the size limit (asked once per file: split now or later).

**Stubs** (the adapter's conventions brief; `/framegate:feature`). A stub is a full signature whose
body only raises or throws not-implemented: no docstring, no doc comment, no spec ID. The design's
"Public interfaces" lists the IDs each member serves and the tests carry them as tags; port stubs
keep the reference's text. Why: the step gate refuses new docstrings and IDs in source, and text in
a stub is checked by no gate.

**Approval** (`framegate approve-spec <f>`). Before "spec OK" the chat may change, next to the specs
and the stubs, the config file and the language's packaging, test and tool-config files (the
adapter's `setup_paths()`); the brief lists them. The approval runs the spec check, refuses stray
changes to other protected files, runs `[env] refresh` when set, commits specs, stubs and setup
files, and sets the `spec-approved` marker. Then it pins the references, records what the project
rule checks print today and, for a bug fix, which tests already fail.
Why: everything the human adds or changes happens at spec time, and one commit is the baseline every
later check compares with. After it a failure on a protected path can only come from the run.
Approving again after an edit restarts the task from the tests.

**References** (`references.py`; `[references]`). Other projects on this machine that the agents may
read and never write. The spec names the ones the task uses; the approval pins their commits and
every run warns when one moved.
Why: a task that follows another repository needs its real text, and a moved reference silently
changes what "the same as there" means.

**Ports** (`references.py`, `ported.py`). A task whose spec fills "Ported from" copies code of a
reference file for file. `framegate port-stubs <f>` writes the stubs from the reference's text with
the bodies removed (Python). The test writer carries the reference's tests over; the implementer
changes only what "What may differ" lists; kept quirks are listed and get no finding. Step gates and
the final checks report how far each target file is from its reference, never blocking. The size
limit, the docstring check and the simplify pass leave ported files alone.
Why: a diff between the two repositories should show only the listed differences. Every reworded
line is one someone has to explain later.

**Follow-ups** (`followup.py`). What a task makes necessary outside itself: a deploy or CI change
(pipeline follow-up), a change the reference project should make (reference follow-up), and items
outside the repository such as a CI variable or a migration. The spec has a section for each; an
agent that finds one writes a marked line instead of attempting it. They are listed at the final
gate without a question, kept in the spec folder after the task, and the outside items are an
unticked checklist in the pull request text.
Why: deploy and CI files are never a task's to change, and the need must not get lost.

## 3. Tests

**Writer, checks, reviewer** (`orchestrator.py`, `phase_tests`). The test writer writes tests from
the spec and the stubs. Mechanical checks run first; a failure goes back to the writer before any
review. Then the test reviewer, a new agent on the stronger model, checks the tests against the
spec; objections go back to the writer. Both loops are bounded (`[agents] test_check_rounds`,
`test_review_rounds`). A later review round judges only the earlier objections and the test diff. A
check that fails exactly as before ends the loop.
Why: the verifier is independent of the implementer, and a cheap check should fail before an
expensive review starts.

**First proof** (`checks/baseline.py`, `checks/red_stubs.py`). Per mode, see the table in section 2.
For a feature each test must be red for the right reason: on the stub, on an assertion about the new
behaviour, or inside the code under test. A test that stops earlier on another error fails the
check. A red test stops at its first failing line, so the assertions after it are listed for the
reviewer to walk by hand.
Why: a test that passes against a stub asserts nothing, and one that is red for the wrong reason
says nothing about the feature.

**Green against stubs** (`checks/red_stubs.py`). The spec may list tests that cannot fail before the
code by design (a scan, a negative check, a structure test), each with a reason. Exactly those may
pass. A spec ID that only such tests prove fails the check. Pinned data (a seed, a recording) gets a
precondition test that shows, with code that exists already, that the data has the situation the
tests need; these must pass in every mode.
Why: such tests exist, above all in ports. Without the list the red check would send them back to a
writer who may not change them. Wrongly picked data otherwise shows only at the end.

**Existing tests the spec names** (`checks/named_tests.py`). An existing test the spec names under
"Test data and fixtures" must change, unless marked `(unchanged)`; a test file the spec names for an
ID must be touched. Only an explicit placement counts, not a path in prose.
Why: a writer that puts everything into a new file leaves the old tests asserting the old behaviour.

**Other test checks.** Traceability (`checks/traceability.py`): every spec ID has a test and every
tag names a real ID. Test smells (`checks/test_smells.py`): tests without assertions or skipped,
counted only for tests the task added. Lint on the tests' own lines (`checks/test_lint.py`), because
the implementer may not edit tests and could never fix a finding there. Leftover files
(`checks/leftovers.py`, section 4).
**Notes for the test reviewer, never blocking.** Existing members asserted
(`checks/existing_members.py`): members the new tests assert that are declared only in code from
before the task. The stub's failure hides whether that claim holds, so the reviewer reads where each
member is set. Refactor: scope coverage (`checks/scope_coverage.py`), the lines of the files in
scope that no tagged test runs.
Why scope coverage: a refactor has no test repair at the end, so a gap must be closed while the
tests can still change. The limit: a line that is run is not yet asserted.

**Questions never stop the run.** Questions of the test writer and the reviewer, open objections and
failed test checks are collected and shown at the next human gate. A repeat from the same asker is
dropped, and a new question on the same spec IDs replaces the open one (`answered.py`).
**Tests gate** (`[gates] tests`). By default there is no stop: the tests are approved, tagged and
frozen, and the leftovers go to the final gate. `"human"` always stops; `"auto"` stops only when
something is open. Know the setting exists if you want to read tests before any code is written.

## 4. Implementation and step gates

**Steps and batches** (`phase_implement`). Each plan step is a fresh implementer run. Consecutive
small steps share a run while their spec IDs stay under `[agents] step_batch_ids`. A batch that
cannot pass as a whole is judged step by step: the first steps that pass are kept, the rest are
parked. A refactor always runs one step per run.
Why: no context accumulates between steps; small plans should not pay a run per step.

**The frame** (`hooks/protect_paths.py`, `hooks/require_green.py`, `checks/frame_diff.py`). A write
hook allows each phase only its own folders; the implementer cannot touch tests, specs or config. A
stop hook keeps the implementer from ending while its step's tests fail, unless it parked. The frame
check then compares the tree with the `tests-approved` marker: a changed protected path or a newly
added suppression fails the gate.
Why: hooks give early feedback and are not airtight; a write hidden in a shell command gets past
them. The diff against the marker is the guarantee. A suppression counts by its text per file, so a
rewrapped one is not new.

**What a step gate runs** (`gates.py`, scope `step`). It judges only what the task changed:

- Lint, format, types and imports: a finding counts only on a line the task wrote (`red_tree.py`,
  `task_base.py`). Why: old debt blamed on the task parks every step. The limit: a type error the
  task causes in a file it did not change is left to the tests.
- The tests of the step's IDs and the earlier steps'. The whole suite runs once, at the final
  checks; `[gates] step_tests = "suite"` runs it at every step. A test tagged with IDs of several
  steps runs at the latest of them. A step whose run changed nothing is checked with its own tests
  only; in a refactor it fails.
- Existing tests of the changed code: the test files from before the task that mention a function or
  class the task changed, at most ten, run by file. Why: a broken old test shows at the step that
  broke it, not first at the final regression. The files beyond ten wait for the regression.
- Project rule checks (below).
- No new docstring and no spec ID in the lines added to source (`checks/added_docs.py`; `[gates]
  docstring_check`; Python and .NET). An existing docstring may be reworded, moved or deleted. Why:
  comments are no source and spec IDs belong in the tests' tags.
- Step notes against the spec (`checks/steps_log.py`). Fails when a logged assumption only repeats a
  spec row, or when a bug fix's root-cause or ruled-out line uses a cause number the spec does not
  have or describes another cause's code. Why: the retro and the brief count assumptions; a restated
  requirement is noise, and a wrong cause number makes the bug report unreadable.
- Reword check (`checks/plan_rewords.py`). A plan step may name lines of a docstring or comment its
  change makes wrong (`Reword: <file>:<lines>`, numbered as at the approval). The gate fails while
  the step's diff leaves them untouched. The new wording is not judged. Why: stale code text is
  nobody's question, so the step that causes it fixes it.
- Leftover files (`checks/leftovers.py`; `[gates] test_leftovers`). A test run that leaves a new
  untracked file in the tree fails its check; the file is listed and removed. Build and cache
  folders, ignored paths and the out folder do not count.

**Project rule checks** (`rules.py`; `[checks] rules`, `rules_text`). The project's own CI rule
commands run as checks at every step gate, after each fix run and at the final checks. A failure
goes back to the implementer like lint output. What a command printed on the code before the task is
recorded at the approval and left out. Every agent that writes source gets one generated block: the
commands, the linter's active rules and the rules as text.
Why: a rule that can be checked is a check, not guideline text, and the implementer learns the
project's CI rules before the pull request does.

**Format and lint fixes before checking** (`[gates] format_fix`; `format_split.py`). The gates apply
the formatter and the linter's safe fixes to the changed files first. A file is formatted whole;
lines the formatter rewrote outside the task's own lines go into a commit of their own and are
counted for the summary.
Why: a formatting slip costs no attempt, and the task's commit shows the task's lines only.
The limit: a file whose task lines and formatted lines touch stays whole in the task's commit.

**Step gate notes** (`checks/step_notes.py`). A green step gate adds notes for the next runs and the
reviewer: lines of the changed functions that no test of the task runs (Python), and untouched
comments that mention existing data the spec names. Never a failure.
Why: both are hints a later agent can act on while it is cheap; neither is proof of a defect.

**Assumptions and parking.** An implementer that meets a gap assumes from the spec and logs the
assumption with its spec ID. Only when no reasonable assumption exists it parks the step. A step
that fails `[agents] max_attempts` times parks too. A parked step's changes are saved as a diff and
the source goes back to the last green commit; later steps go on without it, and a step that depends
on it waits. A gate that fails only on files the implementer may not write parks at once.
Why: between the gates the run never stops. Parked work is answered at the final gate.

**Diagnosis** (`roles/diagnosis.md`; `[agents] diagnose_after`). When the same checks fail twice in
a row, a diagnosis run on the stronger model reads everything, changes nothing and names the cause,
its owner and one fix. A code cause goes to the implementer's next attempt. A test, spec or config
cause parks the item with the proposal as its question.
Why: retrying against a wrong test or spec only spends attempts.

**Provisional steps.** When only named frozen tests are wrong and the code passes every other check,
the code is committed as provisional and later steps build on it. The contested tests are left out
of the gates until the final gate, where all proposed test fixes are one item.
Why: every wrong test surfaces in the same round instead of one park per round.

**Config changes during a task** (`config_change.py`, `redo.py`). A diagnosis may propose a
`framegate.toml` change. After the user's approval at the final gate it is applied only when no
check gets weaker: a timeout, a tool path, a command that runs the same check. A lower threshold, a
new listed test or an excluding argument belongs to the setup and is refused.
Why: a running task must not loosen its own frame.

**A redo that cannot end differently is not run** (`redo.py`). Work parked on something no
implementer run can change keeps a digest of what the gates read outside the source folders. If an
approving answer left that digest unchanged, the item parks again at once and says what is missing.
An answer with new information, or "redo anyway", always runs.
Why: an approved fix nobody could apply would repeat the same failed attempts at full cost.

## 5. Simplify pass

`simplify.py`, `roles/simplify.md`, `[simplify]`. After the last plan step and before the review,
one agent run tries to make the task's diff smaller with the behaviour unchanged. It may touch only
source files the task already changed; tests and spec stay frozen. Then the checks of a fix run plus
coverage of the lines it wrote must pass. If so, the pass is its own commit. Anything else (a red
check, a file outside the list, an interrupted run) drops its changes. It never parks and never
asks. It does not run in a refactor, in a port, while parked or provisional work waits, or when the
task added fewer source lines than `[simplify] min_lines`.
Why: agent diffs are often larger than a person's for the same change, and a smaller diff is less to
review and less to break. The pass is kept only when every check stays green, so it cannot cost
correctness. The limit: it can only shrink what was written; it does not make the implementer write
less.

## 6. Review

**Reviewer** (`roles/review.md`, `phase_review`). An adversarial reviewer on the stronger model
reads the task's diff against the spec. A bug is written as a failing test in the review tests
folder; a structure problem is a finding without a test. Its diff base is the approved code
formatted as the gates format, so lines only the formatter rewrote are not part of it.
Why: a finding as a failing test can be checked. An opinion cannot.

**Triage** (`roles/test_review.md`, triage task). The test reviewer judges each failing review test
against the spec: valid findings go to the fix loop, invalid ones are deleted with a reason. A
review test file whose tests all pass backs no finding and is moved out of the suite. A valid
finding about a parked step waits for that step's redo.
Why: the reviewer is rewarded for finding things; someone else must check that a finding is a
requirement.

**Fix runs and structure fixes.** The implementer fixes valid findings, then applies the structure
findings without changing behaviour. These runs are checked with the task's tests, the review tests
and the existing tests of the changed code, not the whole suite; a fix that fails its attempts is
parked. The structure fixes' gate also measures coverage of the lines they wrote. When every plan
step is parked there is nothing to review: review, final checks and summary run are skipped and the
final gate comes at once.
Why: the final checks run the whole suite right after; running it per fix run only repeats it.

## 7. Final checks and repair

**What runs** (`gates.py`, scope `final`; also `framegate gates <f> --scope final` for CI). Lint and
project rules, the frame check, the whole suite once without coverage, coverage and diff coverage,
removed guards, traceability, test smells, unreached assertions, the mutation score, then the
structure report and the project's own commands (`[gates.extra]`), which are shown with exit code
and output and are never part of the verdict.
**Scoped to the task** (`task_base.py`, `checks/mutation.py`). Coverage and mutation run only on the
task's tests (its spec IDs and the review tests); `[gates] coverage_scope` and `mutation_scope`
bring back whole-suite runs. Diff coverage and the mutation score count only lines the task wrote,
against the formatted base. A scored mutation run is reused while nothing it ran on changed, and the
check does not start when the build or the tests are red.
Why: instrumented runs over a large suite take longer than the task. A run cut short is a failure,
never a figure.

**Assertions that never ran** (`checks/unreached.py`; `[gates] unreached_assertions`). The coverage
run also records the task's test files. A test that ran with an assertion line that was never
executed fails the check. A line marked `unreached: <why>` is left out. A bug fix and a refactor
also run it in the tests phase, for the tests that pass before the code.
Why: an assertion behind a condition the test data never meets passes every other check.

**Removed suppressions and catch-alls** (`checks/removed_guards.py`). A suppression or a catch-all
handler the task removed fails the check unless the step note names the file, the guard and a spec
ID.
Why: a lint finding can be made to vanish by deleting the handler around it.

**Structure report and the size limit** (`checks/structure.py`; `[gates] max_file_lines`). Not
blocking: the size of every changed source file, before and after, and the longest methods.
Implementer tasks name the files already over the limit and every step gate prints the ones that
grew, so code is written in the right place instead of split after a finding. A file over the limit
that the task made longer is a note in the summary and one deferred entry, never a question; it
still counts against the risk rating. The reviewer's proposal to split a file that was over the
limit before the task is a question (default: later).
Why: the split is a refactor of its own, and a default "later" must not start rework.

**Not measured is not a pass.** A mutation tool that cannot run here, or that crashed without a
score, is shown as not measured with its fix: never as failed, never as passed. No repair run starts
for it. A crashed tool is also a final-gate question (default: finish without it). Mutation switched
off at setup is skipped and is no risk reason.

**Final repair** (`[gates] final_repair`; `repair_final`). A failed final check gets one repair run
per owner before the human sees it. Code failures go to the implementer. A weak diff coverage or
mutation score goes to the test writer, who adds tests from the spec, never from the code; a
refactor has no such repair. Then the checks run again. While parked or provisional work waits there
is no repair: their tests count as expected red, and coverage and mutation are skipped until the
redo.
Why: most final failures are small and mechanical; the human should see what could not be fixed.

**Repair review.** After a repair that changed source, a new reviewer judges only that diff: does it
change behaviour the spec keeps, remove a guard, or go beyond the failed checks? An objection goes
back once. One that still stands is a final-gate item without a default.
Why: a repair runs under pressure to turn a check green and after the main review.

**Newly red tests** (`newly_red.py`). The final regression records the existing tests that passed
when the spec was approved and fail now. If the repair leaves one red, the final gate asks without a
default: finish with it red, or one more repair. A test that was red before already is not the
task's: one line in the summary and a deferred bug-fix entry.
Why: whether the old test or the new behaviour is right is the owner's call.
The limit: it needs the record of what was red at the approval (`[gates] regression_baseline`: on
for a bug fix when not set). Without it a red test fails the regression check and goes to the repair
like any failure.

## 8. Docs

**Sources have a rank.** Every agent is told: the code is what exists, the task and the approved
spec are the requirement, docs are context only, a comment or docstring is no source. Program output
is behaviour and may be specified and tested.
**No agent of the loop writes a doc; it proposes** (`doc_proposals.py`). The write hook refuses
documentation files in every phase but the docs step. A proposal is a line in the spec or in the
run's proposal file. Kinds:

- Important (files under a docs folder): a final-gate question, default "later".
- Important, where the task's code now contradicts the doc: the question is about the code. Approve
  it and update the doc, approve it and leave the doc, or reject the code.
- Minor (README, CHANGELOG): applied after approval without a question.
- Code text (a stale docstring or comment): never a question and never part of the docs step. The
  plan step that changes the file rewords it (Reword check, section 4); elsewhere it is one line in
  the summary.

Why: docs are the owner's plan. A proposal is their overview of how the system moved away from it.

**Merged, and asked once.** One proposal per file and place; two lines with the same file, section
and text are one; a contradiction wins over a plain proposal. Files framegate writes itself and
agent memory files get none. A proposal the user already answered in the spec dialogue (marked
`[answered: later]` or `[answered: apply]`) is no gate item: the answer stands and is shown under
"Answered before".
Why: several agents notice the same stale paragraph, and an answered question is not asked again.

**Doc check** (`roles/doc_check.md`). Before an important proposal is asked, one run on the stronger
model compares its text with the code as built and corrects what the code does not do. Text the
owner gives with an approval is checked the same way before it is applied; a text that contradicts
the code comes back as one more question.
Why: a proposal states what was built. A doc updated from a wrong sentence is worse than a stale
one.

**Docs step** (`roles/docs.md`). Once, after an approved final gate whose final checks had passed:
the minor and the approved proposals are applied as one commit. A run that fails, changes nothing or
touches anything but docs is reverted. The task finishes either way. Every proposal is kept in the
spec folder as applied or not; an important one not applied becomes a deferred entry.
Why: docs never block a task, and never mix into its code commits.

## 9. Final gate and summary

**Summary** (`roles/summary.md`, `phase_summary`, `report.py`). framegate generates the facts from
the run's records, with the gate's numbered items. The summary agent turns them into the detailed
summary and the final brief and updates the code map. Cost, time and the other numbers are added by
framegate.
Why: no model computes a number the human decides on.

**Briefs** (`chat.py`, `framegate gate-summary <f>`). Wherever the human reads or decides they get
about one screen in plain words, the decision and its default first, detail on request. The spec
brief, written by the chat: the task, the plan with one line per step, how it is proven, what is
not, decisions made for them, setup files changed, questions. The final brief: the same steps and
how each went, struggles, decisions, numbers, the most important proven and not-proven points, the
risk line, questions.
Why: the spec files are precise and written for the agents. The human checks outcomes.

**Only real decisions are asked** (`brief.py`). The summary agent sorts every gate item into a
question, a decision it made (only one sensible option) or left out (answered, resolved, a repeat).
Parked work and provisional steps are always asked; an item placed nowhere is asked as it is. Two
items may be asked as one only when both have the same options and the same default; an item without
a default is never merged with one that has one. framegate checks the brief once and has it
rewritten when it breaks these rules.
Why: a merged question must mean one answer; a wrong merge would apply "ok" to a choice that has no
safe default.

**Defaults** (`defaults.py`, `answered.py`). The default of every question changes nothing: code,
tests and spec as they are, parked work left parked, a proposed change left for later. "ok" takes
every default, and answers that only take defaults start no run: the gate is approved as it stands.
An answer names an option by letter, number or its own words; the words after it are a note. One
that names no option and is not plainly the default is asked once more. Three items have no default
and must be chosen: a parked item when nothing of the task is built, a repair objection that still
stands, and a newly red test. A question answered earlier in the task is not asked again; the
summary lists it with the answer that stands.
Why: taking defaults must never start rework. Where every option has a cost, the owner chooses.

**Answers go into the spec** (`framegate answer <f>`; `roles/spec_update.md`). Answers get a
reference; one spec-update run writes them into the spec, where IDs are never renumbered or lost.
Then the test writer applies the answers that need a test change, with one reviewer pass. Both
commits are marked, and the frame check allows exactly those files while their content is the marked
one. Approving the gate moves the spec marker. Only the affected work is redone: a parked item
answered with anything but its default, the steps that waited for it, and what a changed spec makes
the reviewer check again. Then the final gate comes again.
Why: the spec stays the one source of truth, and the implementer may not touch tests, so without
this step an answer about a test could never be applied.

**Risk rating** (`risk.py`; `[risk]`). At every final gate the engine rates the task `low` or
`elevated` from its own records, with the reasons on the brief and in the events next to the human's
decision. Always elevated: final checks failed or not run, coverage or mutation not measured,
anything parked, provisional or open, an answer round, a rejected gate, a change whose size is
unknown. Elevated by a strict default that one setting each can loosen: a skipped check, a repair
run, a valid review finding, a doc proposal, more than one assumption, a change over the line and
file limits, a path on the never-automatic list (deploy and CI, schema and migrations, docs), a file
grown over the size limit, too few finished tasks. In a refactor moved lines are not counted.
Why: strict first. A setting is loosened one at a time, with `framegate stats --risk` as the
evidence.

**Automatic approval** (`[gates] final`). `"human"` by default: every final gate is the owner's and
the rating is only shown. With `"auto"` a task rated low, with every check measured, passes without
a stop, but only once the project's track record allows it: enough past low-rated gates a person
approved as they stood, none answered with changes, no escaped defect on a low-rated task. The brief
and the summary then say in their first line that no person approved. Nothing is pushed.
Why: the rating has to agree with the owner's own decisions before it replaces them. The spec gate
is never automated.

## 10. After the task

**Retro** (`retro.py`, `roles/retro.md`). framegate builds the input from the events: every
question, failed attempt, cap hit, rejection, review finding, final failure, permission denial,
assumption and parked item, each with an ID, plus the chat's notes (`framegate note`), the slowest
checks, open escaped defects on the task's files and the backlog. The retro must address every ID
and marks each proposal as the project's or framegate's. A proposal that changes a template or a
role must state a rule that holds for any project. `framegate retro <f>` runs it for a task that
stopped before the end.
Why: every human correction becomes data. A project's gap goes to its knowledge or a project check,
not into the general templates.

**Backlog** (`backlog.py`; `framegate keep`, `framegate backlog`). The user's pick is recorded with
a status per proposal; the retro's text is kept beside it. A later retro that sees the same problem
names the row instead of proposing it again, and the row counts the repeat. A first-time oddity is
recommended "skip" and still counted if it returns.
Why: one odd task must not become a permanent rule, and an accepted proposal must not be lost with
the run files.

**Escaped defects** (`defects.py`; `framegate defect`). A bug found after a task was accepted is
logged against that task with severity, files and spec IDs, and closed when the fix is in. A task
never accepted here is refused unless forced. `framegate stats` counts them per task and per risk
rating; later retros see the open ones on their files or IDs; defects on low-rated tasks block
automatic approval.
Why: escaped defects are the half of the metric that matters most, and the only evidence that the
gates miss something.

**Deferred work** (`deferred.py`; `framegate deferred`). `.framegate/deferred.md` keeps what a task
left for later: "later" answers, important doc proposals not applied, work left parked, files grown
over the limit, final checks that had failed at approval, tests red before the task, and a pointer
to open follow-up lists. Later specs see the entries on their files; a spec that does one says
`Resolves:` and the task's end removes it.
Why: every "later" stays visible, and no gate asks the same thing in the next task.

**Stats** (`stats.py`, `risk_review.py`; `framegate stats [<f>] [--risk]`). Read from the event log,
no agents: per task cost, agent and check time, final-gate rounds, questions, assumptions, risk and
escaped defects; for one task also the runs per phase. `--risk` sets the rating next to what the
human decided at each final gate.

**Pull request text** (`description.py`; `framegate pr-description <f>`). Written by framegate, no
agent, for a reviewer who has not seen the run: what changed, what to do by hand, how it is proven
and what is not. No cost and no path into the run files. It is kept in the spec folder, so it
survives the cleanup.
**Publish** (`publish.py`, `hosts.py`; `framegate publish <f> [--pr]`). Only on the user's OK.
Without a state branch the chat pushes the task branch and opens the pull request. With one, see
section 12. Pull requests are created on GitHub and Azure DevOps; elsewhere the command prints where
to open one. `framegate cleanup <f>` then deletes the run files; the spec folder, the metrics row
and the event log stay.

**Pipeline task** (`pipeline.py`; `/framegate:pipeline`, `framegate pipeline`, `pipeline-check`,
`pipeline-approve`). The only place deploy and CI files change: the follow-up list of a task, or a
list of its own. No spec, no tests, no agent: the chat edits the files inside a guard phase that
allows only the listed deploy and CI files. The check verifies that nothing else changed, that the
files parse and that every item is done or closed with a reason. The user approves one brief. The
change lands as a second commit of the task's pull request while that is unmerged, else as its own
branch.
Why: a pipeline change has no unit test, so its proof is the pull request's CI run, and it must stay
apart from the product change.

## 11. Agent runs

**Fresh process, model by role** (`config.py`, `DEFAULT_MODELS`; `[phases.<P>] model`). Each phase
and each step is a new headless `claude -p` run; state is in files, and the orchestrator, not a
model, decides what runs next. Writers that follow the approved spec and the frozen tests run on the
cheaper model; checkers (test reviewer, reviewer, triage, diagnosis, doc check) and the retro on the
stronger one. Every run has a turn cap and a cost cap: an implementer run that hits one is judged by
its step gate, other phases are run once more.
Why: a weak checker defeats the frame.

**What a run is given** (`agent_commands.py`, `agent_env.py`, `hooks/shell_form.py`,
`failure_filter.py`). An orientation block (the spec and run files, recent commits, references) and
the project's own lint, format and type commands; the tool rules are built from the same list. The
project's interpreter comes first on the path, and Claude Code's auto-memory of the user's chats is
off. The hook blocks pipes, chains and multi-line shell commands and says what to do instead. A long
failing check output is cut down to the lines that look like failures; the whole output stays in a
log file.
Why: an agent told a command it is denied loses turns, one told the wrong formatter restyles the
project, and one that tests in another interpreter than the gates proves nothing. Only plain single
commands are pre-approved, so anything else would be denied without a reason.

## 12. State, branches and locked paths

**Run state** (`state.py`). Per task a progress file and the run's files in the out folder; one
shared event log; a phase file that the hooks read. `framegate status` reads them, and `framegate
run <f>` resumes from them at any point. The exit code says whether a run ended, waits at a gate,
failed or was interrupted. The approvals are private git refs that no push publishes (`[git] markers
= "tags"` stores real tags, for a CI job that runs `framegate gates`). A workflow commit stages only
the workflow's paths, so the user's unrelated edits stay out.
**Deploy and CI paths** (`frame_diff.is_pipeline_file`; `[protected]`). Built-in names (CI folders,
Dockerfiles, compose and pipeline files) plus the project's own in `[protected] extra`;
`builtin_off` takes a name off. They are locked in every phase, at spec time too. Only the pipeline
task writes them. A setup file or test data with such a name is not one.

**Machine files** (`config.is_machine_file`; `[git] machine_files`, `project_folders`). Tool state
of this machine, by rule everything in a top-level dot folder that is not CI, framegate's or
Claude's. framegate ignores them everywhere: no check sees them, no step refuses over them, no agent
writes them, no pull request carries them.
Why: an editor's or a tool's state must neither block a task nor leak into it.

**State branch** (`publish.py`; `[git] state_branch`, off by default). For a repository a team
shares: the pull request carries the product and nothing of framegate.

| Branch | Holds | Pushed | Merged into the base |
|---|---|---|---|
| state branch | only framegate's files (config, specs, metrics, knowledge, code map) | yes | never; it shares no history with the base |
| work branch, local | the base, the state branch merged in, the task's commits | never | no |
| product branch | the base commit the task started from plus one commit with the product | yes, by `publish` | yes, by the pull request |

`framegate spec` builds the work branch; the two trees do not overlap, so that merge cannot
conflict. `framegate publish` writes the task's framegate files to the state branch and builds the
product commit, and checks both before pushing: the product commit holds no framegate file and
differs from the work branch in nothing else. It never force-pushes and never touches the base.
`framegate state` creates, saves and shows the state branch.
The limits: the task's single commits stay local, and a CI job that runs `framegate gates` on the
pull request is not possible, because the product branch has no specs.
**The work branch is never pushed.** It has its own name, so a push out of habit cannot land on the
team's branch name. A pre-push hook (`framegate state guard`) refuses any push that carries
framegate's files to anything but the state branch. An existing hook of the user is kept and run
first. Python tests carry framegate's `req` marker, so the project registers it in its own pytest
config once; the team's runs need nothing else from framegate.

## 13. Usage limits, errors and stops

**Usage limit** (`usage_limit.py`; `[agents] limit_wait_max_hours`, `limit_probe_minutes`). A run
the subscription's limit refuses is not a failed attempt. framegate waits for the reset and runs the
same task again. During the wait it retries at intervals, since a refused try costs nothing, and
`framegate wake <f>` retries at once. A reset too far away, or a limit still there after several
waits, stops the run like an interruption; `framegate run` resumes it. A run that did no work
because the API could not be reached (`api_error.py`) is likewise repeated after short waits without
counting an attempt.
Why: the reset time claude names is the latest end of the wait, not the earliest.

**Stopping** (`procs.py`). An interrupt kills the agent's whole process tree, clears the phase,
records the interruption and does not count the attempt. A run lock refuses a second run of the same
task; `framegate unlock` clears a lock a killed run left. `framegate watch <f>` follows a run's
progress lines; on Windows a toast marks each gate, the end and an error (`notify.py`).

**Start check and updates** (`preflight.py`; `framegate preflight`, `framegate update`). The plugin,
the command-line tool and the project's workflow tools run at one version. The start check of every
skill brings them in line, but never while a task on this machine is between "spec OK" and its end;
the update then waits and says so. A task without activity for two weeks no longer holds an update
back. `framegate update` updates at once, except while a run is active.
Why: replacing the tool under a running task breaks that task.

## 14. Adapters, config, plugin, replay

**Adapters** (`adapters/`): how a language proves things: test selection by spec ID, coverage,
mutation, stubs, setup files, guard patterns. Python, .NET, and a generic one driven by commands in
the config. Some checks exist only where the adapter supports them and are skipped with a reason
elsewhere. Detail and timings: [adapters.md](adapters.md).

**Config and plugin** (`config.py`, `plugin/`). Every `framegate.toml` key is optional; the template
`framegate init` writes documents each with its default, and an unknown key is a config error. The
chat skills and the two hooks: [plugin.md](plugin.md).

**Replay** (`replay/`; `framegate replay`): real commits as measured tasks in a sandbox, scored with
the commit's own hidden tests. A driver that dies on the usage limit is counted as not run, not as
bad, and the candidate is run again after the reset. Everything else: [replay.md](replay.md) and
[replay-candidates.md](replay-candidates.md).

**The repository's own tools** (`tools/`, `ci/`). `release.py` releases one branch in a fixed order
(version on the branch, full suite green, pull request, merge, full suite green on main, tag, branch
deleted) and stops at the first failure. `affected_tests.py` picks the test files that reach the
changed code. `check_on_runs.py` tries the checks on recorded replay runs. `ci/check_rules.py` with
`ci/allowlist.txt` freezes framegate's own counts of long files, comment blocks, suppressions and
docstrings; a count may only go down. Why: the whole suite runs on CI only; a new check is tried on
real runs before it can fail a step; framegate keeps the rules it asks of a project.
