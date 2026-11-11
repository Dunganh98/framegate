# Retro proposals from FCSim M5-moment-scan

_Copied 2026-11-12 from FCSim out/agentflow/M5-moment-scan/retro_proposals.md. Verdicts (user): take 1, 2, 3, 4, 5, 7, 8, 9, 10; 6 folded into 5. Status: 1-6, 9 and 10 applied on `feat/retro-moment-scan` (2026-11-12), some in another form than proposed below: 2 as a template comment plus a test-writer rule (no new table); 3 with a check (`agentflow spec-check` looks for the copied code in the named file); 4 as a table with a "Spec ID" column plus that check (no lint over prose); 9 also keeps a tool failure away from the final-gate repair; 10 by the gate item's number and by dropping repeats at collection (no global IDs: the cause was the chat rewording the questions, not label collisions). Open: 8 (on hold: 11-16 min on every feature to save ~34 min in the one of five features that fell short; no parallel runner) and 7 (FCSim project work)._


Counts: questions 5 (3 rounds); failed attempts 0; escalations 0; human rejections 0; review findings 1; final gate failures 2; permission denials 0; assumptions 11; parked items 0

## Brief

1. The spec stated a fixture number (2 set-piece takes) taken from an older test that counts set pieces another way; the test writer asked about it twice -> the spec template gets a "measured with" column for every number in an acceptance criterion; the spec dialogue measures it with the new `keymoments-scan` command, using the spec's own definition (recommend: take)
2. Counts that could always be 0 were not proven non-zero by any fixture: 1 question + 8 test-review objections, 2 extra test rounds -> the fixture table needs one row per counted output: the job where it is > 0, and for drivers, a count where the job differs from Bot (recommend: take)
3. The spec said "goal event's Player == his player id", but the engine stores the match-internal agent index there, so the count was always 0 -> the "Existing data" table gets a column with the assignment copied from the code, and a project-knowledge line about `MatchEvent.Player` (recommend: take)
4. A rule that was only in design.md (with several threads, the first failing job by job order is the one reported) had no spec ID, so no test covered it; the implementer assumed the opposite and the reviewer caught it -> a spec lint: every behaviour sentence in design.md "Error handling" must cite a spec ID with an acceptance criterion (recommend: take)
5. Input rules were left open (when a club counts as "not in the world", undefined enum, check order, "+1" / overflow, "all" inside a list, CLI missing value / default / exit code) -> the template's edge-case table gets a "decided by (existing method)" column, plus a command-line options table (recommend: take)
6. Output layout was left open (table separator and count order, JSON indentation and property order) -> the template asks for one literal example line per text output (recommend: fold into 5)
7. Runner code (the new `keymoments-scan` command) is never tested or mutated, and the implementer never ran it -> the test project references FCSim.Runner, and each runner command gets one in-process smoke test (recommend: take)
8. The mutation score fell short (77.5% < 80%) only at the final gate, so a test repair and a second ~33-minute final run were needed -> run Stryker on the changed files right after IMPLEMENT and send survivors to the test writer before the review (recommend: take)
9. Stryker failed to build its temp copy once (an infrastructure error) and that counted as a gate failure -> the mutation gate retries once with `--diag`, keeps the log and labels such a failure "infrastructure", not "tests" (recommend: take)
10. Two of the five answers could not be matched to their questions (agent-local labels "Q1/Q2" collide with the gate's numbering), and one question was asked twice -> the orchestrator gives each question a global ID, drops repeats, and matches answers by ID (recommend: take)

## 1. Fixture numbers copied from an older test that counts another way

**Problem and evidence.** Q1 and Q3 (the same question, asked in test round 1 and round 2). The original MSC-R-001 acceptance said that job (8, 6, Bot) gives `SetPieceTakes` = 2. The fixture row (requirements.md:90) cites "pinned in ActionReviewMatchTests (scan 2026-11-10)". That test's set-piece check also counts `SetPieceDefend`, but MSC-R-005 defines `SetPieceTakes` as SetPieceTake + FreeKickShot + PenaltyKick only, so the real value is 1. The number was copied, not measured with the spec's own definition. The ANS-1 spec update changed it to 1 (commit 9f65deac).

**Fix (template + spec-dialogue step).** Every number in an acceptance criterion carries how it was measured. Now that `keymoments-scan` exists (this feature), the spec dialogue runs it before it writes key-moment numbers.

```diff
 ## Test data and fixtures
-| ID | Data (seed / file / generator) | Observable that proves the situation | Zero cases allowed |
+<!-- Every number in an acceptance criterion ("gives 2", ">= 4") is measured with the spec's own
+     definition, not copied from another test (seen live: a count copied from a test that also
+     counted defending set pieces). "Measured with": the command + date, or "computed by hand: <how>". -->
+| ID | Data (seed / file / generator) | Observable that proves the situation | Measured with | Zero cases allowed |
```

Spec-dialogue question (`/agentflow:feature`): "For each number in an acceptance criterion: which command measured it, with which definition?"

**Scope:** framework (template column + dialogue question); project (K1: the scan command).

## 2. Counts that can be always 0 are not proven non-zero

**Problem and evidence.** Q2 (the test reviewer: an always-0 `FreeKickReviews` / `PenaltyReviews` would pass) and Q5 (the test writer found (39, 10, Bot) by a scan). The test-review rounds in events.jsonl show the same kind of gap 8 times: round 1 (line 62) had 6 objections: Shot/Tackle/FreeKick/Penalty reviews; Hurried/NoInput/MultiAction; HisGoals > 0 and goals that are not his; drivers equal to Bot; echo fields equal to the defaults; the table digit "9". Round 2 (line 66) had 2: Lazy equal to Bot, world 21 equal to world 22. These cost two extra TESTS/TEST_REVIEW rounds (~$3.5). The fixture table holds one row per requirement ID, but these requirements define 10+ counts each, and nothing asks for a case where each count is non-zero.

**Fix (template).** Add a per-output non-zero rule to the fixture table:

```diff
 <!-- One row per ID that needs particular data ...
+     Counted outputs: for every count / field a requirement defines, name a fixture where it is
+     != 0 (and != its default), else an always-0 / always-default implementation passes. For
+     variants (drivers, modes, another world/club), name one compared value where the variant
+     differs from the base case (seen live: 8 test-review objections in one feature). -->
+## Non-zero proof per output
+| Output (count / field) | Fixture where it is != 0 / != default | Variant differs from base in |
+|---|---|---|
```

Spec-dialogue question: "For each count in the output: which seed/job makes it non-zero?" (answered with the scan from proposal 1).

**Scope:** framework.

## 3. Engine field meaning wrong in the spec's data table

**Problem and evidence.** Q4 and A1. The "Existing data" table said his goals are Goal events with `Player == UserPlayerId`. In `MatchEngine.Ball.cs`, `MatchEvent.Player` is `shooter.Index`, the agent index in the match. A literal compare gave 0 in 510 scanned matches. The table's "Set where" column named the right file, but nobody read the assignment.

**Fix (template column + project knowledge).**

```diff
-| Words in the spec | Exact member / event | Set where (file: method) |
+<!-- "Assigned as": copy the assignment line from the code (e.g. `Player = shooter.Index`), so an
+     id-vs-index or raw-vs-clamped mix-up shows at spec time (seen live: Player was an agent index). -->
+| Words in the spec | Exact member / event | Set where (file: method) | Assigned as (code, copied) |
```

Plus K2 below.

**Scope:** framework (column); project (K2).

## 4. Behaviour only in design.md has no spec ID and goes untested

**Problem and evidence.** V1 (review test `Run_parallel_failure_propagates_first_failing_job_by_job_order`, VALID, fixed) and A4. design.md "Error handling" (line 104) says `Run` propagates the first failing job by job order. MSC-R-008's acceptance covers only the success path, so the test writer wrote no test. The implementer then logged A4, which contradicts design.md ("not strictly the lowest job index"). The runner's "exit code 2" in the same paragraph has no ID either, and it is untested (see proposal 7). A review round and a fix round (~$1.5, ~10 min) went to this.

**Fix (check, preferred).** A spec lint at the spec gate: in design.md `## Error handling` (and `## Input domain`), every sentence with a behaviour verb must name a spec ID.

```python
# agentflow/checks/spec_lint.py (sketch)
BEHAVIOUR = re.compile(r"\b(propagat|throw|rais|return|exit code|reject|rethrow)", re.I)
ID = re.compile(r"\b[A-Z]{2,5}-[REI]-\d{3}\b")
for sentence in sentences(section(design_md, "Error handling")):
    if BEHAVIOUR.search(sentence) and not ID.search(sentence):
        fail(f"design.md Error handling: behaviour without a spec ID: {sentence!r} -> add an edge case / requirement row")
```

Template line in design.md: `<which exceptions, where raised, what callers do; every rule cites the spec ID that tests it>`.

Implementer role prompt (secondary, one line): "An ASSUMPTION may fill a gap but never contradict design.md or plan.md; if they say X, do X."

**Scope:** framework.

## 5. Input-validation and parsing rules left to the implementer

**Problem and evidence.** 6 assumptions of one kind:
- A2 [MSC-E-002]: what "not in the world" means (`World.IsLeagueClub`), and when it is checked.
- A3 [MSC-R-001]: undefined enum value -> which exception.
- A5 [MSC-E-001]: slots are validated after de-duplication.
- A6 [MSC-R-010]: `all` inside a list.
- A9 [MSC-E-004]: `+1` and overflow are rejected (NumberStyles.None).
- A10: `--json` without a value, the `--threads` default, and the exit code for an `ArgumentException` from `Run`.

All were consistent with the spec (review.md), but each was a guess. Related: review.md "Suspicious, no test" found that `ParseInts("2147483647")` never ends (`n++` wraps at `int.MaxValue`). The spec gave no int range, so no test covered it.

**Fix (template).**

```diff
 ## Edge cases
-| ID | Situation | Expected behavior (exact: value, exception type + message pattern) |
+<!-- "Decided by": the existing method / predicate that decides the situation (e.g.
+     `World.IsLeagueClub(id)`), and when it is checked (before/after dedup, before any work).
+     Parsers: list the accepted forms (sign, leading zeros, whitespace, max value, overflow). -->
+| ID | Situation | Expected behavior (exact: value, exception type + message pattern) | Decided by (method, when) |
```

New design.md section, for features with a runner command:

```
## Command-line surface
| Option | Default | Given without a value | Bad value -> message + exit code |
```

Spec-dialogue question: "Which existing method decides each rejected input, and what are the boundary values (max int, empty, sign)?"

**Scope:** framework.

## 6. Output layout left open

**Problem and evidence.** A7 [MSC-R-011]: table separator and count order. A8 [MSC-R-012]: JSON indentation and property order. MSC-R-011 lists the fields but gives no literal line, so the implementer chose the layout. A6 is a parse rule; it is covered by proposal 5.

**Fix (template, fold into 5).** In requirements.md under the Requirements table: `<!-- Every text output (table, JSON, log line): give one literal example (header + one row, JSON excerpt) with separator, order and indentation; tests compare against it. -->`

**Scope:** framework.

## 7. Runner code is never tested or mutated

**Problem and evidence.** G2 and the passing final run both say "FCSim.Runner.csproj not mutated: no test project references it (KeyMomentScanCommand.cs, Program.cs)". steps.md step 3 says "The runner itself was not run". A10's runner choices (exit code 2 for `ArgumentException` from `Run`, the threads default) have no test. The pattern holds for every runner command: FCSim.Tests has no reference to FCSim.Runner.

**Fix (test + project reference).**

```diff
 <!-- tests/FCSim.Tests/FCSim.Tests.csproj -->
   <ProjectReference Include="..\..\src\FCSim.Core\FCSim.Core.csproj" />
+  <ProjectReference Include="..\..\src\FCSim.Runner\FCSim.Runner.csproj" />
```

```csharp
// tests/FCSim.Tests/Runner/KeyMomentScanCommandTests.cs (sketch)
[Fact] public void Bad_seed_list_returns_exit_code_2() =>
    Assert.Equal(2, KeyMomentScanCommand.Run(Args.Parse(new[] { "--seeds", "5-3" }), TestContent.Dir));
[Fact] public void One_job_prints_one_row() { /* --seeds 3 --slots 9 --threads 1, capture Console.Out */ }
```

Stryker then mutates the runner glue as well. Check first that the Runner's top-level `Program` causes no entry-point conflict in the test assembly (test SDK `GenerateProgramFile`); if it does, move the commands into a small `FCSim.Runner.Commands` library.

**Scope:** project.

## 8. Mutation shortfall found only at the final gate

**Problem and evidence.** G2: 77.5% (155/200) against the 80% threshold. The tests owner added 64 test lines (commit ea251a7b) and the rerun gave 89.5%. Each final-gate run took 1546–2017 s (mutation alone 685–951 s), so the shortfall cost one repair round plus one full final run. The 21 remaining survivors (mutation_survivors.md) are mostly equivalent: null guards before LINQ calls (MomentScan.cs:82/87/92, which LINQ also rejects with `ArgumentNullException`), the `dash <= 0` mutants that `item[0] == '-'` already excludes, and the replay loop bound. About 10 are unasserted message-text literals.

**Fix (orchestrator).** Run a diff-scoped Stryker (`--mutate` on the files the plan's steps changed) once after the last IMPLEMENT step, in parallel with the REVIEW agent. If it is below the threshold, send the survivor list to the test writer (the same route as `final_repair` owner=tests) before the final gate. The final gate keeps its mutation check as the confirmation run.

```python
# orchestrator: after IMPLEMENT, before REVIEW
if cfg.gates.mutation.enabled:
    pre = run_stryker(changed_files(feature), scope="feature-tests")
    if pre.score < cfg.gates.mutation.threshold:
        route_to("tests", reason="mutation pre-check", survivors=pre.survivors)
```

**Scope:** framework.

## 9. Stryker infrastructure failure counted as a test failure

**Problem and evidence.** G1: "Project src\FCSim.Core\FCSim.Core.csproj simulated build failed … Failed to analyze project builds. Stryker cannot continue." Nothing in the code changed before the next run (G2), which built and scored fine, so the failure was transient (temp copy / build lock). The gate reported a plain FAIL with no diag log.

**Fix (gate).** In the mutation check: if the output contains `Failed to analyze project builds` or `simulated build failed`, rerun once with `--diag`, save the log as `out/agentflow/<f>/stryker/diag.log`, and if it fails again report `[INFRA] mutation score` (not routed to the tests owner; shown to the human with the log path).

**Scope:** framework.

## 10. Answer lookup by question label fails; repeated question

**Problem and evidence.** Q2 and Q4 show "answer lookup failed … no numbered answer matched this question". answers.md answers them as items 2 and 4, but the questions carry agent-local labels: the test reviewer's has none, and Q4 is labelled "Q2:" by the test writer. Q3 is Q1 asked again in round 2 ("Q1 (repeat)"), so the human answered the same thing twice (answers 1 and 3).

**Fix (orchestrator).** When questions.md is collected after an agent run, give each entry a global ID (`QN-<feature-seq>`) and write it into questions_archive.md and the gate's numbered list. Answers are matched by that ID / position, never by the agent's label. A new question whose text matches an open one (same spec ID + similarity ≥ 0.8, or a "(repeat)" marker) is merged, not re-listed. Role prompt for the test writer: "Do not re-ask an open question; it stays open until the gate."

**Scope:** framework.

## Not acted on

- A11: an implementation detail of the V1 fix (replaying is safe because jobs are deterministic); consistent with design.md, and the reviewer verified it.

## Project knowledge

- K1 [Tools]: key-moment seed scan: `dotnet run -c Release --project src/FCSim.Runner -- keymoments-scan --seeds 1-40 --slots 1-10 --drivers bot|all|<names> [--world 21 --fixture 3 --home 2 --away 6 --threads N --json <path>]`. It prints per job the moment, kind, end, review, click and his-goal counts. Use it to find and measure fixture seeds. It replaces the line "No general key-moment seed-scan command exists yet".
- K2 [Architecture rules]: `MatchEvent.Player` is the agent index in the match (`shooter.Index`), not a player id. Map the user with `engine.State.Agents.First(a => a.PlayerId == userPlayerId).Index` (src/FCSim.Core/Match/MomentScan.cs).
- K3 [Test harness]: pinned standard-match job (world 21, fixture 3, home 2 v away 6) (39, 10, Bot): the user has a free-kick review and a penalty review, scores 2 goals, and his team scores 1 goal that is not his (tests/FCSim.Tests/Match/MomentScanTests.cs).
- K4 [Test harness]: "set piece" in ActionReviewMatchTests also counts `SetPieceDefend`. "Set-piece takes" (MomentScan) are SetPieceTake + FreeKickShot + PenaltyKick only. Don't copy counts between the two.
- K5 [Tools]: the mutation gate mutates only FCSim.Core (no test project references FCSim.Runner). Keep runner commands thin glue and put parsing/formatting in Core (as `MomentScanText`), until proposal 7 lands.

CLAUDE.md lines now enforced by a check: none. In .agentflow/project.md, the line "No general key-moment seed-scan command exists yet (retro M5-action-reviews #4)" is obsolete once K1 is accepted; delete it.
