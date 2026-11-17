# Replay candidates: commits from history as exercises for framegate

_Written 2026-11-16. Status: candidate list, plus the mechanical check of 17 commits (see
"Check results"). No replay was run._

## Purpose

Take commits from the history of real projects where the scope and the outcome are known,
turn each into a task, and let framegate run the whole cycle on it. The real commit stays
hidden and is the answer to compare with. This gives many full-cycle runs with a known result,
by kind of task, for two uses:

1. **Benchmark:** compare framegate versions (cost, time, final-gate rounds, hidden tests
   passed) on the same tasks.
2. **Setup loop for a new project:** a few replay runs find environment and config problems
   before the first real feature, and the difference between the agent's result and the
   team's commit proposes lines for `.framegate/project.md`.

Precondition (user, 2026-11-16): one feature runs completely through every step first.
A benchmark of a half-working cycle measures only the broken half.

## Rules agreed for building an exercise

- **The spec does not come from the diff.** The task text comes from the commit message, the
  ticket, and the doc lines the commit itself changed. A spec written from the diff contains
  the answer.
- **Only the public signatures come from the real commit,** as stubs (framegate commits stubs
  at spec time anyway). Without them the hidden tests cannot run against the agent's code.
- **Ground truth is the commit's tests, not its diff.** The agent's code may differ and be
  right. The real commit's tests run against the agent's result as hidden tests.
- **Check every candidate mechanically first:** the commit's tests fail on the parent and pass
  on the commit. A candidate that does not show this is dropped.
- **Hide the future:** run in a clone cut at the parent commit, so no later commit is
  reachable with `git show` or `git log --all`.
- **A skipped hidden test is "not proven", not a pass.**
- **Split the history for the setup loop:** some commits for learning, others never used for
  learning. Measure on the held-back ones before and after.
- **The human picks what is learned.** The loop proposes project knowledge; nothing goes into
  `project.md` unchecked.
- **No settings tuned from a few runs.** Three to five runs show that something is broken, not
  that a threshold or a model choice is better.
- **What a replay does not measure:** the spec dialogue and the human's time at the gates.
  Those are still measured on real features.

## How the list was made

Static screening: the diffs and the test files were read. The "fails on the parent, passes on
the commit" check was run afterwards for the 17 rows in "Check results"; it is still open for
the other rows.

Filter, per commit against its first parent:

- changes source code and tests (a refactor may change no test),
- at most 12 source files and 700 changed source lines,
- no CI or deploy file in the commit,
- the tests it adds need no external service (no InfluxDB, Kafka, Postgres); a need for a
  local package (pyspark, deltalake) is noted,
- FCSim: only `src/FCSim.Core` and `tests/`, no Unity or content files, no tuning commits
  ("Engine fix x", re-measured norms).

History used: the local clones as they were, **not fetched** (da-argos `origin/dev` ends
2026-10-05, da-otto 2026-11-05, da-core 2026-11-10); FCSim from a fresh clone of `main`.

**Pick:** A = use first. B = usable with the limit in the note. Sizes are added/removed lines.

## FCSim (C#)

Repository: https://github.com/Dunganh98/pro-footballer-career-sim

| Pick | Type | Commit | What | Code / tests | Note |
|---|---|---|---|---|---|
| A | bugfix | [9c42f2a](https://github.com/Dunganh98/pro-footballer-career-sim/commit/9c42f2a15c04145f2bb1cdaebb0e202accdd8661) | No meeting or press after the club's last match | +13/-1 / +51 | 1 file (`EventDirector.cs`) |
| A | bugfix | [e8b49ad](https://github.com/Dunganh98/pro-footballer-career-sim/commit/e8b49adad86773d16f88b0d4507945f67d3388a0) | Never picked where he is below Accomplished | +6/-7 / +45 | 1 file (`PositionCover.cs`) |
| A | feature | [5aaa24e](https://github.com/Dunganh98/pro-footballer-career-sim/commit/5aaa24eadf847580281583d16afeae257da4bb09) | Stats per competition | +89/-18 / +48 | 3 files |
| A | feature | [98b62fa](https://github.com/Dunganh98/pro-footballer-career-sim/commit/98b62fa4dae511b71b0336bb759a32e6b7659e58) | Optional shirt name | +154 / +204 | 4 files, one new (`ShirtNames.cs`) |
| A | feature | [094822e](https://github.com/Dunganh98/pro-footballer-career-sim/commit/094822e0c4edc48c06b3b9a4d1cbf1d64b88cad0) | One-match week fitness view | +52 / +66 | 1 file |
| A | feature | [9a0cf05](https://github.com/Dunganh98/pro-footballer-career-sim/commit/9a0cf054a2867566e82d1e066fb21e5dd1c68f80) | ScorerLine grouping, FixtureView misses | +93 / +49 | Two small features in one commit |
| B | bugfix | [546e018](https://github.com/Dunganh98/pro-footballer-career-sim/commit/546e018378f5413e25627e52fba48c1db666fac5) | Rebound: clear the last strike | +17 / +107 | Match engine: see the seed note |

## da-argos (Python)

Repository: https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos

| Pick | Type | Commit | What | Code / tests | Note |
|---|---|---|---|---|---|
| A | bugfix | [4bb80f7](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos/commit/4bb80f71324adfe9f3729e137002c4751fa419a6) | Clamp compared a naive datetime with an aware one | +35/-4 / +33 | Also bumps the version in pyproject.toml |
| A | bugfix | [9d76057](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos/commit/9d7605764ffcb415a25e35b89d5f0451ad9a2f04) | Timezone contract as asserted naive UTC (D38) | +51/-28 / +78 | 1 file (`orchestrator/workflow.py`) |
| A | bugfix | [bdd55f1](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos/commit/bdd55f10298cfa2a05f34856573f847399d1f1de) | DTT forecast anchored to raw HI, monotonic trend | +38/-13 / +41 | Pure model logic; July tree, older layout |
| B | bugfix | [949d63a](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos/commit/949d63a802c1849a907de72d4ab313f9b7e97f9a) | Kafka: null HI instead of 0.0 when a cycle has no data | +60/-26 / +38 | July tree, most of it in the old `main.py` |
| B | bugfix | [2065119](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos/commit/20651196e390d15e1796572fb0d07f1bae1e375a) | Ingest: stop the OOM ratchet | +85/-22 / +144 | Tests skip without `deltalake` |
| B | feature | [e014d7b](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos/commit/e014d7becdbe037f066742bfe45a65983936f719) | Gate training on data span and density | +99/-23 / +73 | July tree, all in `main.py`; also edits CLAUDE.md |
| B | refactor | [01e9f83](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-argos/commit/01e9f83e25ddef8e63908a51b0044a7d2f3e8f4b) | Collapse the duplicate train and infer paths in `cae.py` | +282/-323 / 0 | No test names `cae` at that commit: nothing hidden to compare with |

## da-core (Python)

Repository: https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-core

| Pick | Type | Commit | What | Code / tests | Note |
|---|---|---|---|---|---|
| A | feature | [a0b0d3e](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-core/commit/a0b0d3eed2a62c66a44dbabe5d1d006f4422be1b) | `Contract.key`, the MERGE key every runner write uses | +55/-5 / +43 | 7 files, pure tests |
| A | feature | [8f36239](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-core/commit/8f36239ba42cf53b4fa704b3b3c733b093094186) | `array<T>` column types; bool, not boolean | +74/-24 / +82 | One test needs pyspark |
| A | bugfix | [5e60f4c](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-core/commit/5e60f4c46a9abac31bdb58d6747cb5e99357aa4f) | Escape DDL comments the way Spark parses them | +9/-2 / +36 | Two tests: one pure, one needs pyspark |
| B | refactor | [13a7780](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-core/commit/13a778036b60d80f3d8aa3f665d90b03432214fe) | On/off pipeline with one `fit()` verb | +94/-105 / +101 | Changes the interface, so not a pure refactor |
| B | feature | [9323432](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-core/commit/9323432b6c02b34279ddb45443d16f7e9e3abe41) | DTT: period-dependent params required, rul renamed to dtt | +100/-60 / +53 | Breaking change; replaces two golden parquet files |

## da-otto (Python)

Repository: https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-otto

All four need `deltalake` installed: half of `tests/test_controller.py` sits behind a
module-level `importorskip("deltalake")` and skips without it.

| Pick | Type | Commit | What | Code / tests | Note |
|---|---|---|---|---|---|
| A | bugfix | [fd83df7](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-otto/commit/fd83df740a26e043c6a4cee16b4775983767ddbe) | A released lock is free at once | +4/-2 / +6 | 1 test |
| A | bugfix | [03fce5e](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-otto/commit/03fce5e5a2f9de0af437989dd07f608c8cdaea38) | Close failed fits in one write per reason | +4/-3 / +10 | 1 test |
| A | feature | [1c52d52](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-otto/commit/1c52d52d03147c030df62658f78f1dbffae1d7a5) | Controller state through the writer, lock row by conditional MERGE | +394/-105 / +185 | 13 tests; the largest pick |
| B | feature | [419bd56](https://dev.azure.com/GL-BuschGroup-Developments/GL-CoC-Data_Analytics_and_AI/_git/da-otto/commit/419bd5654d20bcb95839ea8746f5e8d99456901e) | Daily run scores trained pumps, asks for first trainings | +349/-90 / +258 | Builds on 1c52d52; also touches a Spark test file |

## framegate (Python)

Repository: https://github.com/Dunganh98/framegate

| Pick | Type | Commit | What | Code / tests | Note |
|---|---|---|---|---|---|
| A | bugfix | [82610f7](https://github.com/Dunganh98/framegate/commit/82610f7e383620e30c441370aea8c3195431f132) | progress.json: try the move again on a locked file | +18 / +38 | 1 file |
| A | bugfix | [4065651](https://github.com/Dunganh98/framegate/commit/406565145f1c553aff0d8e1ee254017096701add) | Stryker: skip projects no test project references | +106/-21 / +141 | The commit touches two doc files; check whether `docs/findings/2026-11-11-stryker-untested-project.md` is one of them (it would be the bug report) |
| A | feature | [80863e9](https://github.com/Dunganh98/framegate/commit/80863e94b232cb559ffcdc5cd97c40b83568a446) | doctor: warn when a template copy lacks packaged sections | +36/-2 / +17 | 1 file |
| A | feature | [9faa0ea](https://github.com/Dunganh98/framegate/commit/9faa0ea81897cd852add0a24bc935075419bc9c3) | Gate report shows the diff coverage % | +27/-2 / +51 | Old package name `agentflow` |
| B | bugfix | [21acd96](https://github.com/Dunganh98/framegate/commit/21acd96caa5d59a37cf34ecafb7f33d2186338ac) | Cut-short test runs handled | +117/-15 / +190 | Old package name |
| B | feature | [024cfd9](https://github.com/Dunganh98/framegate/commit/024cfd909084c929ed664e4ae7fbff1749664078) | `install-tools` | +203/-3 / +206 | Old package name; installs packages: check how its tests isolate that |

## Check results (2026-11-16)

The "fails on the parent, passes on the commit" check, run on 17 commits. Per commit: two
clones cut at the parent and at the commit, the commit's test files copied over the parent,
the changed test files run in both. "Proving" = tests that fail on the parent and pass on the
commit; they are the hidden tests that count. Every commit below passed the check, and the
commit object was not reachable from the parent clone.

| Repository | Commit | Proving / tests run | Skipped | Note |
|---|---|---|---|---|
| FCSim | 9c42f2a | compile error on the parent / 3 | 0 | The test names a new member, `EventDirector.StopAhead`: the stub gives away part of the fix |
| FCSim | 5aaa24e | compile error on the parent / 9 | 0 | Needs the stub of `LeagueStats.AllCompetitions` |
| FCSim | 98b62fa | compile error on the parent / 26 | 0 | Needs stubs of `ShirtNames`, `Player.ShirtName`, `WorldGenerator.AssignInitialShirtNames` |
| FCSim | e8b49ad | 2 / 25 | 0 | Also re-pins a seed in `ProfileHistoryTests`: a correct other fix can fail it. Reserve |
| da-argos | 4bb80f7 | 2 / 71 | 0 | The test file takes about 2 minutes |
| da-argos | 9d76057 | 3 / 7 | 0 | |
| da-argos | bdd55f1 | 2 / 22 | 0 | |
| da-core | a0b0d3e | 6 / 49 | 0 | |
| da-core | 8f36239 | 5 / 5 | 0 | Import error on the parent: needs stubs |
| da-core | 5e60f4c | 1 / 49 | 1 | The pyspark test skipped (no JVM): one proving test only |
| da-otto | fd83df7 | 1 / 62 | 6 | The skips are Spark tests and one timeout test, none of them proving |
| da-otto | 03fce5e | 1 / 100 | 8 | Same |
| da-otto | 1c52d52 | 22 / 47 | 6 | Same |
| framegate | 82610f7 | 2 / 5 | 0 | |
| framegate | 80863e9 | 1 / 3 | 0 | |
| framegate | 4065651 | 4 / 54 | 0 | |
| framegate | 9faa0ea | 3 / 41 | 0 | One proving test is `test_e2e::test_full_flow`; the files take about 3 minutes |

What the check itself found about environments:

- **da-otto has no environment.** It needs its own venv (pytest, pydantic 2, pyyaml, pandas,
  numpy, pyarrow, deltalake) and `DA_CORE_SRC` pointing at a da-core `src/`. The check used
  today's da-core `dev`; an exercise should pin da-core at the commit's date.
- **da-argos' `.venv` holds an editable install of the real repository** (and framegate
  0.8.0). An exercise must not use it: imports can resolve to today's code. One venv per
  exercise.
- **FCSim:** the .NET SDK is at `~/.dotnet` (10.0.401), not on `PATH`. Fetching from a local
  clone fails on the Git LFS files; set `GIT_LFS_SKIP_SMUDGE=1` (the tests do not need them).
- **Cutting the history** worked with `git init` + `git fetch <source> <parent sha>` (the
  source is asked through `--upload-pack="git -c uploadpack.allowAnySHA1InWant=true
  upload-pack"`), which changes nothing in the source repository.

## Notes for building the exercises

- **Skipped tests look like passes.** Several hidden tests use `importorskip` (deltalake,
  pyspark). Install the package in the exercise environment or count the test as "not proven".
- **FCSim match engine.** A correct fix that draws random numbers in another order can fail
  seed-pinned tests. Hence the Career, Session and Model commits; the one Match commit is B.
- **Spec source.** Many commits change a doc in the same commit (`technical-design.md`,
  `CONTROLLER_NOTES.md`, `RUNTIME_DESIGN.md`, `ARCHITECTURE.md`). Those lines are not in the
  parent, so they are a clean source for the hidden spec; the task text comes from the commit
  message.
- **Not part of the task.** Version bumps in `pyproject.toml`; the CLAUDE.md edit in e014d7b.
- **Old trees.** The July da-argos commits (monolithic `main.py`) and the `agentflow` commits
  predate today's layout and name: today's `framegate.toml` does not fit them as it is.
- **FCSim has no framegate.toml on `main`** (the setup is on `feat/agentflow-setup`); a replay
  at an old `main` parent needs the config brought in.
- **framegate has no framegate.toml** at all: it needs a setup before its own exercises run.

## Gaps

- **Refactor:** no clean one in the five repositories. 01e9f83 has no test that pins the code;
  13a7780 changes an interface. FCSim's `M5-setpiece-model-split` was made by framegate, so it
  is not an independent answer.
- **Port:** none of usable size. The "take custody of the drivers (C2)" commits in da-otto
  (981b57f, dc751fd) are about 2,000 lines in 12 files plus 750 lines of tests.

## Proposed first round

Eight exercises: four bug fixes and four features, both languages, 4 to 394 changed lines.

| Repository | Commit | Type |
|---|---|---|
| FCSim | 9c42f2a | bugfix |
| FCSim | 5aaa24e | feature |
| da-argos | bdd55f1 | bugfix |
| da-argos | 9d76057 | bugfix |
| da-core | a0b0d3e | feature |
| da-otto | fd83df7 | bugfix |
| da-otto | 1c52d52 | feature |
| framegate | 82610f7 | bugfix |

## Next steps

1. For each first-round commit: check out the parent, add the commit's test files, confirm
   they fail; check out the commit, confirm they pass. Drop what does not show both.
2. Per exercise: a branch (or clone) cut at the parent; the task text from the commit message
   and ticket; the hidden spec and the commit's tests kept outside what the agents can read.
3. Do one replay by hand, end to end, before automating anything. If it teaches something,
   automate the commit filter first (deterministic, no agent).
4. Decide per project which commits are for learning and which are held back.
