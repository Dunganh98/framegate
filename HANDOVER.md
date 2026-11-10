# HANDOVER — agentflow

**What:** spec-driven, test-first orchestrator for Claude Code = Python package + CLI `agentflow`
(`src/agentflow/`, the deterministic engine) + Claude Code plugin (`plugin/`: skills + hooks).
Design: docs/AGENTIC_WORKFLOW_SETUP.md; usage: README.md; plugin facts: docs/plugin.md;
`claude -p` facts: docs/claude-cli-notes.md; language adapters (python, dotnet): docs/adapters.md.

## State (2026-11-10, main)
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
- **Adapters:** python (pytest, mutmut via WSL on Windows), dotnet (xUnit, Stryker opt-in level).
- **Chat flow (plugin 0.3.0):** `/agentflow:setup` (new repo: scan, questions, toml +
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
