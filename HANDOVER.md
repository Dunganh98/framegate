# HANDOVER — framegate

**Renamed from agentflow on 2026-11-12 (0.6.0):** command, chat commands, `framegate.toml`,
`.framegate/`, `FRAMEGATE_*`, package, repository. Older entries below were written under the
old name and read with the new one; names of things outside this repository (FCSim's
worktree and branch, old clone paths) are as they were. Moving a machine or a repository:
docs/migrate-from-agentflow.md.

**What:** spec-driven, test-first orchestrator for Claude Code = Python package + CLI `framegate`
(`src/framegate/`, the deterministic engine) + Claude Code plugin (`plugin/`: skills + hooks).
Design: docs/AGENTIC_WORKFLOW_SETUP.md; usage: README.md; plugin facts: docs/plugin.md;
`claude -p` facts: docs/claude-cli-notes.md; language adapters (python, dotnet): docs/adapters.md.

## State (2026-11-11, main; this work on feat/brief-questions)
- **Gates:** SPEC -> FINAL (tests gate only with `[gates] tests = "human"|"auto"`), forward only.
  Between them fresh `claude -p` runs in a frame of checks (red stubs, traceability, smells,
  tag-based frame diff, lint/types, feature-scoped diff coverage + mutation, whole-suite
  regression, then `[gates.extra]` project checks, not blocking). Exit codes 0/1/10 (gate)/130.
- **First FCSim feature (M5-slide-gamble, 2026-11-10):** 4h42 wall, ~3h agent+check time,
  $12.35 for ~60 lines. ~2h of it were framegate bugs, fixed the same day: the chat's Stop hook
  ran tests next to the agent (hooks now act only with `FRAMEGATE_AGENT`, set on agent runs);
  Stryker instrumented all of FCSim.Core (now a temp worktree where only the changed hunks are
  mutable, via `// Stryker disable/restore all`); "done" mapped to finish without retro.
- **Speed/cost defaults since then:** writers (TESTS, IMPLEMENT, SPEC_UPDATE, PR) on Sonnet,
  checkers + retro on Opus (`[agents] model`, `[phases.<P>] model`/`effort`); small plans in one
  implementer run (`step_batch_ids` 15); implementer runs only its step check; gates format the
  changed files first (`format_fix`). **Not yet measured on a real feature** — compare the
  `agent_run` events (model, cost) and failed attempts of the next FCSim feature with the table
  above.
- **Second FCSim feature (M5-action-reviews, 2026-11-10):** 67 min agent time, $9.76; its retro
  (FCSim `docs/history/agentflow-retro-M5-action-reviews.md`) led to: answers at the final gate
  reach the tests (TESTS_AMEND run, tag `tests-update/<f>`); a step-gate failure only in files
  the implementer may not edit parks at once; the test writer's and the reviewer's files are
  formatted; .NET format/lint checks only changed lines; fixture precondition tests
  (`test_fixture_*` / `Fixture_*`) must pass on the stubs; a .NET smell for loops over picked
  cases without a non-empty check; `Depends on` steps wait for a parked step; findings only on
  parked IDs are deferred; every task has a look-around rule and an orientation block; step
  notes are written with the Write tool; retro_input pairs each question with its own answer;
  new spec template sections (overlaps / sources of truth, fixture table, input domain and copy
  semantics, serialized-data tests) and spec-dialogue questions. FCSim's own `specs/_template/`
  is a copy and does not have these sections until it is updated.
- **Project knowledge + diagnosis (branch feat/knowledge-diagnosis, on top of PR #2):**
  `.framegate/project.md` read by the spec dialogue and the agents, grown by
  `framegate learn` from the retro's K items (user picks; never automatic); a diagnosis
  run after the same checks fail twice (src fix -> next attempt; tests/spec fix ->
  parked, approved at the final gate in one reply). Also on the branch: `/framegate:setup`,
  `framegate doctor` (setup check without agents; on FCSim 40 s, 0 failures), the generic
  adapter (any language via `[generic]` commands; e2e-tested by driving pytest through it),
  the Python zero-case smell; zero-case smells are NOTEs (not blocking: 15 hits in FCSim's
  existing tests were mostly fine filters) and smells count only in the feature's test
  files. Per-path thresholds: not built; decide after the next FCSim feature.
- **Check output + usage limit (2026-11-11, from harnessOrch):** a failing check's output in the
  gate report and fix prompt keeps the failure lines (+ context, file:line locations, tail), not
  the last 30 lines; the whole output goes to `out/<f>/check_logs/`. A run refused by the
  Claude usage limit is not a failed attempt: framegate waits until the reset (+1 min; 15 min
  if unknown) and repeats it, or stops like Ctrl+C (exit 130) when the reset is more than
  `[agents] limit_wait_max_hours` (6) away. Detection is tested only with the fake `claude`;
  **the first real limit confirms the message format**.
- **Third FCSim feature (M5-freekick-review, 2026-11-11):** all final gates passed with no hand
  fixes, $10.55, but two parks caused by wrong frozen tests cost two extra rounds (~1 h, ~$4.5).
  The parks came one after the other: step 1's code was right, only its tests were wrong, yet
  steps 2-3 waited, so their own wrong test (`KeyMomentRecord.Goal` read as "the moment's goal
  event") showed only in round 2. Fixes (branch `feat/freekick-retro`):
  - **Provisional steps:** a park request with `Park kind: tests-wrong` + `Contested tests:`
    (or a diagnosis `Fix owner: tests` with that line) whose code passes every other step check
    is committed as provisional; dependents build on it; contested tests are left out of the
    step gates and the Stop hook (`out/contested_tests.txt`). The final gate lists all proposed
    test fixes as one item (approve / finish refuse while open); after the answer TESTS_AMEND
    applies them and the contested tests must pass, else the provisional commit is reverted and
    the step redone. **Not yet seen on a real feature** (an implementer must use the new park
    format; the role says how).
  - **Final gates with parked / provisional work:** regression counts their tests as expected
    red and fails only on other failures; coverage, diff coverage, mutation SKIPPED with the
    reason. New adapter method `regression_run()` (per-test results).
  - **Existing-member asserts** (python + dotnet, note only): members of pre-feature code
    asserted by the feature's changed tests and by no fixture test, listed in test_checks.md for
    the test reviewer. On FCSim's frozen M5 tests it lists `KeyMomentRecord.Goal` among 9 others
    (most real but harmless; tune if it gets noisy).
  - Templates: requirements "Existing data this spec reads", edge-case comment line (own
    clamping, outputs on "no result", error precedence), design "Mirrored code"; the spec
    dialogue asks for them. A project's own `specs/_template/` (written by init) wins over
    these, so `framegate doctor` now WARNs about packaged `## ` sections the copy lacks. FCSim's
    copy lacks "Existing data this spec reads" and "Mirrored code": add them or delete the copy.
  - retro_input pairs reworded questions with answers (spec IDs + words), searches every answer
    round incl. questions_archive.md, says "answer lookup failed" when a spec update followed.
  - Metrics: `questions` counts single questions + asking parks / provisional steps,
    `gate_failures` includes failed final-gate runs. FCSim's M5-freekick-review row
    (questions 1, gate_failures 0) should read 6 and 2; not rewritten.
- **Manager-style briefs (2026-11-11, branch feat/manager-briefs; no mode, no switch):** the
  human gives input and checks outcomes; specs stay precise for the agents. Wherever the human
  reads or decides, the chat shows a short brief: decision + default first, ~one screen, plain
  words, no spec ID without its meaning, detail files on request (docs/reference.md "Manager-style
  briefs"). Spec gate: the chat drafts from docs / code map / code / project.md, asks at most 5
  questions per round with defaults, writes `out/<f>/spec_brief.md` (Task, Plan, How it is
  proven, Not proven, Decisions I made for you, Project details, Questions). Final gate:
  `gate-summary` builds it from the PR agent's `final_brief.md` (plan followed?, struggles,
  proven / not proven, code map, numbered items reworded with options + default) plus generated
  outcomes (gates, coverage / mutation scores, attempts, parks, cost, agent + check time,
  structure); `final_facts.md` carries the numbered items so the numbers match. Tests gate: the
  test reviewer's `## What the tests check`. "ok" with numbered items = take every default;
  "approve as is" approves. `doctor --brief` (verdict + FAILs + WARNs needing a decision), retro
  `## Brief` (numbered, take / skip / fold), one-line states elsewhere.
  - **Structure:** implementer and reviewer roles carry the readability checklist; the reviewer's
    `## Structure findings` (no test) go to a `structure fixes` implementer run before the final
    gates (parks like a review fix); the final gates end with a structure report (changed files'
    sizes before/after, longest methods; not blocking); a file over `[gates] max_file_lines`
    (400) becomes a final-gate item (default: split now if the feature took it over, else
    later); its answer goes to the structure run; asked once (`size_answered`).
  - **Code map:** `[docs] code_map` (docs/code-map.md); setup drafts it; the PR phase updates it
    (PR may write that file; framegate commits `<f>: code map`); new public types missing from
    it are a WARN on the brief; doctor WARNs when missing.
  - **Not yet seen on a real feature:** how well Sonnet (PR phase) writes the brief and the code
    map, the brace heuristic for C# method lengths on FCSim, whether "ok = take the defaults"
    reads naturally at the gate. FCSim has no code map yet: run the setup's code-map step (or
    write it by hand) before the next feature.
- **Stryker on untested projects (PR #9, 2026-11-11):** a changed .NET project no test project
  references (ProjectReferences followed transitively) is not mutated but listed; all of them
  untested -> SKIPPED; a failed Stryker run keeps the finished projects' counts. The PR role
  quotes a failing gate's reason from final_gates.md. FCSim M5-moment-scan's mutation gate is
  still a real FAIL (Core 77.5% < 80).
- **Questions filtered by rule, shorter briefs (branch feat/brief-questions, plugin 0.4.1):** no
  count cap on questions or decisions anywhere (spec dialogue, spec / final gate briefs, setup).
  A question only when it is a real decision (two or more reasonable options with different
  outcomes, not settled by spec / code / project.md / earlier answers); one sensible option ->
  "Decisions I made"; answered / resolved / note -> left out; duplicates -> one; ordered by
  impact. Briefs: same sections, one line per bullet, empty sections one line, Proven / Not
  proven the top 3, a question = one line + `A: ... (default) / B: ...`; final brief ~20 lines.
  - **Final gate mechanics** (`src/framegate/brief.py`): the PR agent places every gate item
    under `## Questions` / `## Decisions I made` / `## Left out` of final_brief.md by `[N]`;
    parked work and the provisional steps are always asked; unplaced items are asked as they
    are. The human answers questions 1..k; `framegate answer` rewrites answers.md by gate item
    (decided -> `Default: <decision>`, same-as -> the other's answer, else "No answer needed";
    the chat's file in the `answer` event's `as_given`). A brief check after the PR run
    (one-option questions, untagged, duplicates, closed / note items, parked work left out,
    unplaced items) re-runs the PR agent once (event `brief_check`; not blocking).
  - Tests gate (optional) unchanged: its questions still come straight from the agents.
  - **Not yet seen on a real feature:** whether Sonnet tags the items reliably and how often
    the rewrite fires; check the `brief_check` events of the next FCSim feature.
- **Repair, risk, source folders (branch feat/repair-risk-roots, 2026-11-11, from a review of
  the five FCSim runs: every feature needed 2-3 final-gate runs of 20-25 min, and a failed
  final gate always went to the human):**
  - Final gates: one repair run per owner before the human sees a failure (`[gates]
    final_repair`; code failures -> implementer, weak diff coverage / mutation -> test writer +
    reviewer pass, tag `tests-update/<f>`); mutation is not started after a red typecheck /
    regression / coverage run. `src/framegate/orchestrator.py` `repair_final`.
  - Risk rating by the engine (`src/framegate/risk.py`, `[risk]`): on every final brief and in
    the `risk` / `human_gate` events (shadow mode); `[gates] final = "auto"` lets a low-risk
    feature pass the final gate without a stop. Default stays `"human"`.
  - `[paths] src` as a list (CausalCollapse: Godot, sources in core/, autoload/, scenes/).
  - `agent_run` events carry role, step, attempt, session, tokens; progress.json is written
    atomically; a cut-short events line is skipped.
  - `framegate doctor`: the chat's allow rules, framegate.toml on `[git] base`.
  - **Not yet seen on a real feature:** how well Sonnet strengthens tests from the survivors
    file without copying the code's behavior; whether any FCSim feature rates low (they logged
    10-12 assumptions, the limit is 3); the token fields against a real `claude -p` result.
  - Deferred: session resume for mechanical retries (the last two features had 0 failed step
    gates), retro backlog with applied-tracking (design doc 9.7), the orchestrator split
    (before epics), `git add -A` limited to the phase's paths.
- **From the M5-moment-scan retro (branch feat/retro-moment-scan, 2026-11-12, plugin 0.4.3;
  the first run with the repair: the test writer took the mutation score from 77.5% to 89.5%
  without the human; risk rated elevated):**
  - Tool failures (retro #9): a mutation FAIL without a score is a tool failure: no repair
    run, the final gate asks "run the checks again?" (always shown). Stryker gets a second
    try. Before this, the repair would have sent a Stryker build failure to the test writer.
  - Questions (#10): a repeated question is dropped at collection; `answer` items keep the
    question as asked, so the retro finds answers to reworded questions. The retro's own
    diagnosis (label collision) was wrong: the chat rewords questions, the lookup ignored the
    item number.
  - Spec check (#3, #4): `framegate spec-check`, run by `approve-spec` (`checks/spec_lint.py`).
  - Templates (#1, #2, #5, #6): columns "Measured with", "Decided by", "Assigned as"; design.md
    "Error handling" as a table with a Spec ID column, new "Command-line surface"; one literal
    example per text output. Test writer: every count gets a non-zero case; implementer: an
    assumption never goes against design.md. `framegate doctor` now also names missing columns
    of a project's template copy (FCSim has a copy: update or delete it).
  - Not done: #8 (mutation pre-check after IMPLEMENT: 11-16 min on every feature to save ~34
    min when the score falls short; one clear case in five features; no parallel runner);
    #7 (FCSim: tests reference FCSim.Runner).
  - Was open, done since (next entry): the slow coverage run; a home for accepted retro
    proposals.
- **Stats, retro backlog, modes, coverage of changed files (branch feat/stats-backlog-modes,
  2026-11-12, plugin 0.5.0):**
  - `framegate stats [f]` (`src/framegate/stats.py`): cost, agent / check / waiting time,
    final-gate runs per feature, from events.jsonl (design doc 9.1, the report part).
  - Retro backlog (`src/framegate/backlog.py`, design doc 9.7 without the reviewer panel):
    `framegate keep` after the retro, `metrics/retro_backlog.md` + `metrics/retro/<f>.md`,
    the retro sees the rows and names repeats, `framegate backlog done|drop|reopen`.
  - Modes (design doc 9.5): `framegate spec --mode bugfix|refactor`, skills
    `/framegate:bugfix` and `/framegate:refactor`, templates `_template_bugfix` /
    `_template_refactor`, first proof by mode (`checks/baseline.py`), mode notes on the
    tasks (`MODE_NOTES`), root-cause lines in final_facts.md, refactor: one step per run,
    all tests per step, an idle step fails, no test repair at the final gate.
    **Not yet run with real agents.** Not built: a scope-coverage check before a refactor
    moves code; `hypothesis` rows as a mechanical requirement (the cause lines are reported,
    not enforced).
  - `[dotnet] coverage_files = "changed"` (default): measured on FCSim M5-moment-scan: 54 s
    instead of 624-732 s, the same diff coverage (98%, 222/225).
- **Adapters:** python (pytest, mutmut via WSL on Windows), dotnet (xUnit, Stryker opt-in level).
- **Chat flow (plugin 0.5.0):** `/framegate:setup` (new repo: scan, questions, toml +
  project.md, `framegate doctor`, optional smoke run); `/framegate:feature <f>` (branch `feat/<f>` from `[git] base`,
  spec dialogue + spec brief, approve, background `framegate run` + Monitor on `framegate watch`,
  gate briefs via `framegate gate-summary`, plain-word replies mapped to approve/reject/answer/finish,
  wrap-up: retro proposals, PR offer, `framegate cleanup`), `/framegate:resume [f]`,
  `/framegate:status [f]`. Run protocol text: `src/framegate/templates/chat_protocol.md`
  (`framegate protocol`, injected; plugin files can't be read from injections).
- **Notifications:** Windows toast at each gate, DONE, error (`[notify] toast`, env
  `FRAMEGATE_TOAST=0`). Fired once manually on 2026-11-10 (exit 0) — **user to confirm it showed**.
- **Verified:** plugin validates `--strict`; headless Haiku check ($0.05): skill loads, injections
  run, background run + Monitor live view + gate digest work (docs/plugin.md). Not yet run in an
  interactive session.
- Tests: `uv run pytest` (fast), `-m slow` (dotnet e2e, WSL mutmut), node hook tests; ruff + mypy.

- **First live refactor (FCSim M5-setpiece-model-split, 2026-11-12):** clean, $5.96, 41 min;
  its retro and the user's feedback led to (branch feat/refactor-run-feedback):
  structure findings are retro items with the cost of their fix (S1..; the 73-line
  `FreeKick` cost 10 of the 41 minutes and the retro did not see it); `framegate stats`
  shows the spec time; a refactor's structure fix is checked with the feature's tests,
  not the whole suite; the risk rating of a refactor leaves moved lines out (876 -> 114
  changed lines; that run stays elevated by 36 removed lines against the limit of 30);
  refactors use existing tests first (the test writer tags an existing test that pins a
  row; `Green on today's code` lists them; smells count only for tests the feature
  added). Measured there: `mutation_scope = "suite"` does not finish on FCSim (2,040
  tests, over an hour). Also built: scope coverage, a note in test_checks.md with the lines
  of the files in scope that no tagged test runs, before any code moves (37 s on FCSim).
  Measured for "existing tests first": the six older test classes that name the moved
  code kill 320/349 mutants of the change (91.7%) without any new test; the 835 new lines
  reached 329/349 (94.3%). The older classes took 34 min in Stryker against 8 min (they
  include match tests): tagging heavy tests makes the mutation check slower.
  Not built, on purpose until the next real refactor shows what is left: a
  cheaper path for small tasks (about $3 of tests and test reviews and about 20 min of
  checks are fixed costs). The retro's proposals 1 (shared-helper table) and 2 (wider
  mutation filter) were skipped: 2 is covered by tagging; its example mutants were
  equivalent ones.

- **Shared target repos + install-tools (2026-11-12, branches fix/python-target-repos and
  feat/install-tools, plugin 0.5.2; from preparing da-argos, a Python service repo on Azure
  DevOps whose full suite needs InfluxDB / Kafka / Spark):** `[python] pytest_args` (every
  pytest run + the agents' test command); `[gates] regression_baseline` (approve-spec records
  the tests that already fail, the final regression fails only on others; off by default);
  workflow commits stage only the workflow's paths (src, tests, specs, metrics, knowledge file,
  code map), no longer `git add -A`; markers (`spec-approved/<f>` ...) are private refs
  (`refs/<name>`) that no push publishes, `[git] markers = "tags"` keeps tags for a CI job, old
  tags are read and replaced; `[gates.commands] format_fix` (the project's own formatter, e.g.
  black); `framegate install-tools` (python: framegate's pytest plugin, pytest, pytest-cov,
  hypothesis, diff-cover, later the gate commands' tools; reports versions it changed), run by
  `/framegate:setup` as step 0 and again before doctor; doctor reports a missing plugin.
  **Not yet seen live:** all of it (unit and fake-claude tests only; the slow tests were not
  run after these changes; a dry run of install-tools on da-argos named the right interpreter
  and three missing packages). FCSim: the two changed defaults apply there too (set `[git]
  markers = "tags"` if its CI gate job is used; commit framegate.toml edits yourself).
  The earlier out-of-repo prototype (a `.framegate` junction to `projects/<name>/` in this
  repo, nothing tracked in the target) is the local-only branch `legacy`; not continued.

- **From the M5-reach-by-save-chance retro (FCSim, 2026-11-12: $9.27; branch
  feat/retro-keeper-reach, 2026-11-13; docs/findings/2026-11-13-M5-reach-by-save-chance-retro.md;
  FCSim backlog rows B1-B5, to be closed there with `framegate backlog done`):**
  - #1 Edge cases without pinned data (five questions): `framegate spec-check` fails an
    edge-case ID without a row in "Test data and fixtures" once that table pins data (the
    data, or `unit-only: <why>`; `checks/spec_lint.py: edge_case_fixture_problems`); template
    text, a spec-dialogue point. "Every outcome a rule names in words" is template text only
    (not mechanical).
  - #2 An assertion behind `if saved` never ran: the coverage run records the feature's own
    test files (Python `--cov=<tests>`, .NET coverlet `IncludeTestAssembly`, unchanged test
    files excluded; diff coverage still on source files only), and the final check
    `unreached assertions (feature tests)` fails on an assertion line without a hit in a test
    that ran (`checks/unreached.py`; guards and lines marked `unreached: <why>` are left out;
    the test writer repairs it; `[gates] unreached_assertions = false` turns it off).
  - #3 Answers that only took defaults restarted everything (26 min): such answers
    (`take_defaults`) that change nothing (`answers_changed_nothing`: no commit, no new
    question, nothing to restructure, final checks passed) approve the feature without a
    second round. The spec update and the test amend still run (two short agent runs): only
    they can tell that a default needs no change. A "leave it" answer to a file-size
    question starts no implementer run.
  - #4 "File too long" only at the review: the spec brief and every implementer task name
    the files already over the limit, the plan template asks for the new file, every step
    gate prints changed files over the limit (`GREW`), and the final gate asks only about
    files the feature made longer (risk rating the same).
  - #5 A step broke another feature's frozen test, both step gates passed: the step gate
    runs the whole everyday suite after the step's tests, leaving out this feature's later
    steps, contested tests and the regression baseline (`gates.step_suite`; `[gates]
    step_tests = "feature"` turns it off). **This reverses** "a refactor's structure fix is
    checked with the feature's tests, not the whole suite": that fix now also runs the suite
    once per attempt (FCSim: about 1 min).
  - Not done: #6 (FCSim: one boundary test). The mutation score that moved 99.1 % -> 97.4 %
    on the same code is not looked into.
  - **Not yet seen live:** all of it. The .NET part of #2 is checked by the slow e2e test
    with a real coverlet run; the others by unit and fake-claude tests.

## User decisions so far
1. Two human gates, forward only: spec and final (2026-11-10, after the first FCSim feature;
   was three). The test reviewer checks the tests against the spec; its questions, objections
   left and failed test checks go to the final gate (`[gates] tests = "defer"`, the default;
   `"human"` / `"auto"` still exist).
2. Between gates never stop: assume (logged) or park; parked work is answered at the final gate.
3. Test reviewer checks the tests (writer <-> reviewer rounds) and triages review findings.
4. Answers go into the spec (SPEC_UPDATE, `ANS-n` marks, re-approval moves the tag).
5. mutmut through WSL on Windows; coverage/mutation feature-scoped (suite once without coverage).
6. Driven entirely from the chat: the user types requirements, answers, gate decisions only.
7. One branch per feature (`feat/<f>` from main, in the current checkout; dirty tracked files ->
   refuse).
8. Cleanup at the end: keep `specs/<f>/` + metrics row; delete `out/<f>/` + its run logs; tags
   only after the PR merge (`cleanup --tags`); PR only on the user's explicit OK.
9. Notifications: chat + Claude Code's input alert + Windows toast.
10. Publish (private GitHub repo + user-wide install) only after one real FCSim feature ran clean.
11. Later (2026-11-10): **epics**. One big task, split by Opus into sub-features. Two
    approvals: "split OK" first, then all sub-specs in one "spec OK". One agent per
    sub-feature in parallel (own worktree), models as today (Sonnet writes, Opus checks), one
    final gate with a summary for all. Planned in design doc §9.8, with what to port from
    harnessOrch (a friend's repo, no license file; the author agreed to share it on 2026-11-10;
    credit ported files). Starts after
    single features run clean.
12. Manager-style gates by default, no mode or config switch (2026-11-11): the human is the
    manager (clear input, outcomes checked), not the reader of specs; the old chat flow that
    asked to review the spec files and showed full gate summaries is gone; the files stay for
    the agents and for "more detail". The user owns the structure (what classes are for, how
    they work together), not every line or test.
13. Questions filtered by rule, not capped (2026-11-11): only real decisions are asked, by
    impact; single-option points are decided and listed; the final brief fits one screen.

14. Scope (2026-11-12): **one lean core + adapters, grown by modes; the rest in the project's
    setup.** In this repo: the orchestrator, the language adapters (how tests are selected by
    spec ID, coverage, mutation, stubs: python, dotnet, generic) with their proof tools (mutmut,
    Stryker), and the hosting templates (CI gate job per host). Not in this repo: a project's
    stack (Databricks, Kafka, Postgres, Azure services, ...): those are the project's
    dependencies; framegate only learns what they mean for a run through framegate.toml
    (`pytest_args`, baseline, protected paths, `[gates.extra]`) and `.framegate/project.md`.
    `install-tools` stays limited to what the workflow itself needs. Other kinds of work
    (pipelines / infrastructure, experiments / model quality) come later as **modes** with their
    own first proof, like bugfix and refactor, not as separate packages. Presets (shared
    settings for projects with the same stack) only once two set-up projects show what repeats.
    Design doc §9.11.
15. Renamed to **framegate** (2026-11-12): everything, no compatibility layer (nothing reads
    the old names); one version for the CLI and the plugin, released as git tag `v<version>`;
    install = two plugin commands from the GitHub repository, `/framegate:setup` installs the
    CLI (uv, from the tag) and the project's tools. Machines and repositories that used
    agentflow are moved once with docs/migrate-from-agentflow.md (commands + a prompt).
    The name is free on PyPI and npm (checked 2026-11-12); not published there.

## FCSim integration
- Worktree `D:\Me\Projects\FCSim-agentflow`, branch `feat/agentflow-setup` (framegate.toml,
  roles extras, dotnet keys) — **not merged** into FCSim main.
- Because framegate.toml is not on FCSim `main` yet, set `[git] base = "feat/agentflow-setup"`
  in that worktree's framegate.toml (else `feat/<f>` would start from a main without it).
- Add `"Bash(framegate *)"`, `"PowerShell(framegate *)"` to `permissions.allow` in the
  worktree's `.claude/settings.local.json` (skill grants last one turn only).

## Next steps
Each step waits for the run before it; stop and fix what a step shows before the next.
0. Rename the GitHub repository to `framegate` (Settings; the old URL redirects), then on
   each machine part A of docs/migrate-from-agentflow.md (this laptop: nothing is installed,
   on purpose, to test the install from GitHub: first live test of `marketplace add
   Dunganh98/framegate` and of the CLI install from the tag). FCSim and the other PC: parts
   A and B.
1. Here, no cost: `uv run pytest -m slow` (real .NET builds, mutmut through WSL): they were not
   run after the 2026-11-12 changes.
2. da-argos, no agents: `/framegate:setup` until `framegate doctor` says ready. Look at:
   install-tools' `CHANGED versions` line (pinned packages), the settings (source folders
   model / data / orchestrator / utils / platform/services, base `origin/dev`, prefix
   `feature/`, pytest_args for integration / spark / slow, regression_baseline, black as
   format + format_fix, typecheck off, mutation off, ci/ + configs/ + wsic_pb2 protected).
3. da-argos smoke run (the setup's clamp feature, about USD 2-4, 15-30 min): the first live
   proof of the hooks, the baseline, the scoped commits and the private refs. Check by hand:
   the commits hold only workflow paths, `git tag` lists nothing new, the baseline count is
   plausible, black's format fix ran.
4. First small real da-argos feature: pure logic in model/ or data/, no service. Read the retro,
   fix what it shows here.
5. After one clean feature there: mutation for da-argos (the WSL environment has to install its
   TensorFlow / pyspark pins: measure the build time first); an Azure Pipelines gate template
   if the team wants the frame check in CI (then `[git] markers = "tags"`).
6. da-core: needs a way to set `PYTHONPATH=src` for the runs (no config key yet) and uses the
   global interpreter (no .venv); then setup + smoke. da-otto after it (no importable packages:
   decide its source folders first).
7. With two of the three set up: extract a preset from what was identical. After that the
   larger items: epics (decision 11), new modes for experiments and pipelines (decision 14).

Small known gaps: doctor does not check hypothesis in the project interpreter; doctor's
.gitignore hint still says the workflow commits with `git add -A`.

FCSim (unchanged, from before):
1. Install the plugin locally in the FCSim worktree (`claude plugin marketplace add
   D:\Me\Projects\agentflow` + `claude plugin install framegate@framegate --scope local`), set the
   two items above, confirm the toast.
2. First real feature: `/framegate:feature <name>` in that worktree (the live test of the chat
   flow, the Monitor view and the gate replies).
3. Fix what it shows (protocol wording, digest content, progress line volume).
4. Publish: private GitHub repo with `.claude-plugin/marketplace.json`, user-wide install.
