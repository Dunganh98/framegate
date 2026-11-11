# HANDOVER — agentflow

**What:** spec-driven, test-first orchestrator for Claude Code = Python package + CLI `agentflow`
(`src/agentflow/`, the deterministic engine) + Claude Code plugin (`plugin/`: skills + hooks).
Design: docs/AGENTIC_WORKFLOW_SETUP.md; usage: README.md; plugin facts: docs/plugin.md;
`claude -p` facts: docs/claude-cli-notes.md; language adapters (python, dotnet): docs/adapters.md.

## State (2026-11-11, main; this work on feat/freekick-retro)
- **Gates:** SPEC -> FINAL (tests gate only with `[gates] tests = "human"|"auto"`), forward only.
  Between them fresh `claude -p` runs in a frame of checks (red stubs, traceability, smells,
  tag-based frame diff, lint/types, feature-scoped diff coverage + mutation, whole-suite
  regression, then `[gates.extra]` project checks, not blocking). Exit codes 0/1/10 (gate)/130.
- **First FCSim feature (M5-slide-gamble, 2026-11-10):** 4h42 wall, ~3h agent+check time,
  $12.35 for ~60 lines. ~2h of it were agentflow bugs, fixed the same day: the chat's Stop hook
  ran tests next to the agent (hooks now act only with `AGENTFLOW_AGENT`, set on agent runs);
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
  `.agentflow/project.md` read by the spec dialogue and the agents, grown by
  `agentflow learn` from the retro's K items (user picks; never automatic); a diagnosis
  run after the same checks fail twice (src fix -> next attempt; tests/spec fix ->
  parked, approved at the final gate in one reply). Also on the branch: `/agentflow:setup`,
  `agentflow doctor` (setup check without agents; on FCSim 40 s, 0 failures), the generic
  adapter (any language via `[generic]` commands; e2e-tested by driving pytest through it),
  the Python zero-case smell; zero-case smells are NOTEs (not blocking: 15 hits in FCSim's
  existing tests were mostly fine filters) and smells count only in the feature's test
  files. Per-path thresholds: not built; decide after the next FCSim feature.
- **Check output + usage limit (2026-11-11, from harnessOrch):** a failing check's output in the
  gate report and fix prompt keeps the failure lines (+ context, file:line locations, tail), not
  the last 30 lines; the whole output goes to `out/<f>/check_logs/`. A run refused by the
  Claude usage limit is not a failed attempt: agentflow waits until the reset (+1 min; 15 min
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
    these, so `agentflow doctor` now WARNs about packaged `## ` sections the copy lacks. FCSim's
    copy lacks "Existing data this spec reads" and "Mirrored code": add them or delete the copy.
  - retro_input pairs reworded questions with answers (spec IDs + words), searches every answer
    round incl. questions_archive.md, says "answer lookup failed" when a spec update followed.
  - Metrics: `questions` counts single questions + asking parks / provisional steps,
    `gate_failures` includes failed final-gate runs. FCSim's M5-freekick-review row
    (questions 1, gate_failures 0) should read 6 and 2; not rewritten.
- **Adapters:** python (pytest, mutmut via WSL on Windows), dotnet (xUnit, Stryker opt-in level).
- **Chat flow (plugin 0.3.1):** `/agentflow:setup` (new repo: scan, questions, toml +
  project.md, `agentflow doctor`, optional smoke run); `/agentflow:feature <f>` (branch `feat/<f>` from `[git] base`,
  spec dialogue, approve, background `agentflow run` + Monitor on `agentflow watch`, gate digests
  via `agentflow gate-summary`, plain-word replies mapped to approve/reject/answer/finish,
  wrap-up: retro proposals, PR offer, `agentflow cleanup`), `/agentflow:resume [f]`,
  `/agentflow:status [f]`. Run protocol text: `src/agentflow/templates/chat_protocol.md`
  (`agentflow protocol`, injected; plugin files can't be read from injections).
- **Notifications:** Windows toast at each gate, DONE, error (`[notify] toast`, env
  `AGENTFLOW_TOAST=0`). Fired once manually on 2026-11-10 (exit 0) — **user to confirm it showed**.
- **Verified:** plugin validates `--strict`; headless Haiku check ($0.05): skill loads, injections
  run, background run + Monitor live view + gate digest work (docs/plugin.md). Not yet run in an
  interactive session.
- Tests: `uv run pytest` (fast), `-m slow` (dotnet e2e, WSL mutmut), node hook tests; ruff + mypy.

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

## FCSim integration
- Worktree `D:\Me\Projects\FCSim-agentflow`, branch `feat/agentflow-setup` (agentflow.toml,
  roles extras, dotnet keys) — **not merged** into FCSim main.
- Because agentflow.toml is not on FCSim `main` yet, set `[git] base = "feat/agentflow-setup"`
  in that worktree's agentflow.toml (else `feat/<f>` would start from a main without it).
- Add `"Bash(agentflow *)"`, `"PowerShell(agentflow *)"` to `permissions.allow` in the
  worktree's `.claude/settings.local.json` (skill grants last one turn only).

## Next steps
1. Install the plugin locally in the FCSim worktree (`claude plugin marketplace add
   D:\Me\Projects\agentflow` + `claude plugin install agentflow@agentflow --scope local`), set the
   two items above, confirm the toast.
2. First real feature: `/agentflow:feature <name>` in that worktree (the live test of the chat
   flow, the Monitor view and the gate replies).
3. Fix what it shows (protocol wording, digest content, progress line volume).
4. Publish: private GitHub repo with `.claude-plugin/marketplace.json`, user-wide install.
