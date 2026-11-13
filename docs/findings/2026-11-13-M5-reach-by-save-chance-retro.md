# Retro proposals from FCSim M5-reach-by-save-chance

_Copied 2026-11-13 from FCSim metrics/retro/M5-reach-by-save-chance.md. Verdicts (user): take 1-5; 6 is FCSim project work. Status: 1-5 applied on `feat/retro-keeper-reach` (2026-11-13), some in another form than proposed below: 1 is checked only once the fixture table pins data (a spec whose tests build their own inputs has nothing to miss), and only for edge-case IDs (outcomes named in words: template text); 2 as its own final check, not inside test smells, with guards (`Assert.Fail`, `assert False`) left out; 3 keeps the spec update and the test amend (two short agent runs: only they can tell that a default needs no change) and skips what follows when they change nothing; 4 also tells the implementer and the spec brief which files are over the limit before any code is written; 5 as a second step check that leaves this feature's later steps out. Open: 6 (FCSim), and the mutation score that moved on the same code._

Counts: questions 6 (4 rounds); failed attempts 0; escalations 0; human rejections 0; review findings 0; structure findings 2; final gate failures 0; permission denials 1; assumptions 1; parked items 0

No `## Backlog` section in retro_input.md, so no earlier proposal is repeated here.

## Brief

1. Edge cases and outcome branches (empty likely range, unknown outer span, saved / blocked shot) had no match data, so the test writer and test reviewer asked about them five times -> the spec template needs a fixture row (or "unit-only: why") for every edge case and every outcome a rule names, and the spec check refuses a spec without one (recommend: take)
2. A test assertion that only runs "if a saved shot exists" never ran and still passed -> the test-smell gate fails assertion lines in feature tests that the coverage run never reached (recommend: take)
3. At the final gate, answers that only accepted the defaults restarted the whole loop (spec update, test amend, a no-op fix run, every gate again, a second final gate): about 26 min of machine time, $0.63 and a second wait for you -> when every answer is "accept / default / leave", finish without re-running (recommend: take)
4. Two "file too long" findings came only at the review, after both steps had passed, and cost a 10-min fix run; the final report also asked about a file this feature did not touch -> the step gate prints the line count of every touched file over the limit, the plan names the new file when a step adds to such a file, and the final report asks only about files that grew (recommend: take)
5. Step 1 made new helpers public and broke another feature's frozen public-surface tests; both step gates passed and the break was found only in the later fix run -> the step gate runs the normal dev-loop suite (about 1 min), not only this feature's tests (recommend: take)
6. Three mutants survived (two are equivalent at an exact threshold, one is a missing "timing quality exactly 0 = missed" test); the score changed from 99.1 % to 97.4 % on the same code -> add the one boundary test; watch the score drift (recommend: skip)

## 1. Edge cases without fixture data become repeated questions

**Problem and evidence.** Q1, Q2, Q3 (test writer), Q4, Q5 (test reviewer, rounds 1 and 2): five questions over three gaps, all of one kind: the spec names an edge case or an outcome branch (RSC-E-003 empty likely span, RSC-E-007 review with `OuterKnown == false`, RSC-R-003 saved shot, blocked shot and `Keep` shot), and the only match fixture (the pinned job 39/10/Bot) does not contain it. The spec's "Test data and fixtures" table (specs/M5-reach-by-save-chance/requirements.md:85-92) has rows per requirement group, but no row for E-003, E-007, or the saved / blocked / `Keep` branches of R-003. The test writer and the test reviewer each found the gap on their own, and the human answered "accept" at the final gate (answers.md items 4, 5). The template (specs/_template/requirements.md:60-69) already asks for a row "per ID that needs particular data", but it does not say that each E-ID and each named outcome counts as its own case, and nothing checks it.

**Fix (order: check, then template, then dialogue question).**
- Spec-template rule: every E-ID, and every outcome or branch an R-rule names in words ("blocked", "saved", "decided before the strike"), gets its own row: the data that shows it, with the observable and the scan line, or `unit-only: <calculator / synthetic test> — <why match data is not needed>`.
- Spec check (mechanical, in the spec gate): every `<PFX>-E-\d+` in the requirements table appears in the ID column of "Test data and fixtures", or that row's Data cell starts with `unit-only:`.
- Spec-dialogue question (`/framegate:feature`): "For each edge case: which pinned job shows it? Scan now, or accept unit-only coverage?" The human decides once, at spec time, and the test writer and reviewer stop asking.

**Concrete change.**
```diff
--- specs/_template/requirements.md
 <!-- One row per ID that needs particular data (a seed, a recording, a rare situation).
+     Every E- ID, and every outcome a rule names in words ("blocked", "saved", "no reach"),
+     is its own row, even if the base fixture is the same. If no match data shows it, write
+     `unit-only: <test kind> — <why>`. The spec check fails an E- ID without a row.
```
```python
# framegate spec check (sketch)
e_ids = set(re.findall(rf"\b{pfx}-E-\d{{3}}\b", req_table))
rows  = parse_table(section(md, "Test data and fixtures"))
covered = {i for r in rows for i in expand_ids(r["ID"])}   # "RSC-R-001, R-002" -> both
missing = sorted(e_ids - covered)
if missing: fail(f"edge cases without a fixture row or 'unit-only:': {missing}")
```
**Scope:** framework (template, spec check, dialogue question).

## 2. A conditional assertion that never runs passes silently

**Problem and evidence.** Q3: `ShotReview_SaveChance_OnTargetIsCalculator_OffTargetIsNaN` (tests/FCSim.Tests/Match/ReachBySaveChanceTests.cs:935-938) asserts `SaveChance > 0` only `if` the outcome is Saved. The pinned job has no saved shot, so the line never runs. The project rule "a loop over zero cases must not pass" exists (project.md, Test harness), and the "test smells" final gate passed (events.jsonl line 168/183). The test writer flagged it as a question, but nothing mechanical stops it. Q5 is the same pattern for blocked and `Keep` shots.

**Fix.** A check: the "coverage run (feature tests)" already runs only the feature's tests. Also collect coverage of the feature's test files, and fail the test-smell gate when a line containing `Assert.` inside a `[Fact]` / `[Theory]` body is never executed. The message names the line and points to the spec's fixture table (proposal 1). An accepted exception needs a `// unreached: <answer ref>` comment on the line.

**Concrete change (sketch, dotnet adapter).**
```python
# test smells: unreached assertions in feature test files
cov = parse_cobertura(feature_cov_xml, include=feature_test_files)   # add test asm to coverlet include
for f, line, hits in cov.lines():
    src = read_line(f, line)
    if "Assert." in src and hits == 0 and "// unreached:" not in src:
        smell(f"{f}:{line}: assertion never ran in the feature tests (zero-case branch)")
```
**Scope:** framework (dotnet adapter, test-smell gate).

## 3. Answers that only accept defaults restart the whole final loop

**Problem and evidence.** Q4, Q5, Q6: with `tests = "defer"` (framegate.toml:49), the test reviewer's questions and the structure report came to the final gate. You answered "accept both" and took the default "leave it" for all three files (answers.md items 4-8). Nothing had to change, but the orchestrator still ran (events.jsonl lines 176-189):

| run | seconds | cost |
|---|---|---|
| spec_update (no files changed) | 14 | $0.14 |
| tests amend (no files changed) | 14 | $0.15 |
| implementer "structure fixes" (steps.md: "No code changed") | 264 | $0.11 |
| REVIEW gate | 312 | — |
| final gates again (mutation 605 s) | 936 | — |
| PR agent again | 32 | $0.23 |
| **total** | **1,572 s (~26 min)** | **$0.63** |

Then you waited at a second final gate (21:35 → 22:40). Q1-Q3 were also re-listed in answers.md as "No answer needed".

**Fix.** A check in the orchestrator: classify each final-gate answer. If every answer is "accept" / the recommended default / "leave" and the spec_update produced no file change, skip the tests amend, implementer, gates and PR re-run, record the answers in answers.md, and approve with the first final-gate result. Structure-report items answered "leave" are never sent to an implementer as "structure fixes".

**Concrete change (sketch).**
```python
def after_final_answers(items, spec_changed):
    trivial = all(it.answer_kind in {"accept", "default", "leave", "no_answer_needed"} for it in items)
    if trivial and not spec_changed:
        record_answers(items); return Approve(reuse=last_final_gates)
    fixes = [it for it in items if it.source == "structure report" and it.answer_kind != "leave"]
    if fixes: run_structure_fixes(fixes)
    ...
```
**Scope:** framework (orchestrator).

## 4. Structure findings show up only at the review

**Problem and evidence.** S1 (`ShotReview` in KeyMoments.cs, 1,114 lines) and S2 (`BuildShotReview` in MatchEngine.ShotFeel.cs, 849 lines): both files were already over the 400-line limit before the feature (1,093 / 835, events.jsonl line 168 "before"). Each step added code to them, and both step gates passed. The reviewer found it, and the fix cost 1 implementer run (600 s, $0.42, 15 turns) + 1 gate run (274 s). The plan named no target file for the new fields. Q6: the final structure report then asked about three over-limit files, including UserShotModel.cs, which this feature did not change (869 → 869), and two that shrank.

**Fix.**
- Check: the step gate prints, for every touched source file over the limit, `file: before -> after (limit 400)` and flags a growing file as a warning in the implementer's gate output. The implementer sees it while the code is fresh.
- Plan template: a step that adds members to a file over the limit names the new file ("new review fields → `ShotReview.cs`, own file").
- Final structure report: ask only about files that grew in this feature. Files that shrank or did not change go to a note line, not a question.

**Concrete change.**
```diff
--- specs/_template/plan.md (step block)
+ Files over the line limit this step adds to: <file (lines)> -> new code goes to <new file>, or "none"
```
```python
# step gate: structure line
for f in touched_src:
    n, b = lines(f), lines_at(base, f)
    if n > limit: report(f"{f}: {b} -> {n} (limit {limit})" + ("  GREW" if n > b else ""))
# final structure report: questions only for files with n > limit and n > b
```
**Scope:** framework (step gate, plan template, final structure report).

## 5. The step gate did not catch a broken frozen test of another feature

**Problem and evidence.** steps.md "Structure fixes" and final_summary.md:43: step 1 made `SetPieceShotModel.OuterSaveChance`, `FreeKickOuterSpan` and `PenaltyOuterSpan` public. This broke the frozen public-surface checks in `SetPieceModelSplitTests` (feature M5-setpiece-model-split, not tagged Slow). Both step gates passed (49 s and 51 s, events.jsonl lines 159, 161). The break was found only by the implementer's own dev-loop run during the structure-fix run, inside the 600 s fix in proposal 4. So the step gate ran only this feature's tests. No item ID records this because no gate failed. Without the structure fix, it would have surfaced only at the REVIEW / final gate.

**Fix.** A check: the step gate runs the adapter's `base_filter` suite (here `Category!=Slow&Category!=Gate`, about 1 min per CLAUDE.md), not only the feature's tests. If that is too slow for a project, it runs at least the test classes whose files mention a type the step changed. Add the project knowledge line K1 so the next implementer knows the rule up front.

**Concrete change.**
```python
# step gate
run_tests(filter=f"({feature_filter})")        # today
run_tests(filter=adapter.base_filter)           # proposed: whole dev loop, frozen tests included
```
**Scope:** framework (step gate). Project part: K1 below.

## 6. Mutation survivors

**Problem and evidence.** mutation_survivors.md (3 of 116 survived, score 97.4 %; the first final-gate run on the same code scored 99.1 %, 115/116, events.jsonl line 168 vs 183):
- `SetPieceShotModel.Review.cs:153` and `:175`, `>=` → `>` at the span threshold. These are equivalent unless a chance lands exactly on the threshold (0.5 / the outer threshold) at a 0.05 m grid point. Not worth a test.
- `ShotReview.cs:79` `GradeOf`: `q > 0` → `q >= 0`. Killable: `Assert.Equal(3, ShotReview.GradeOf(0.0))` and `Assert.Equal(2, ShotReview.GradeOf(1e-9))`. The method was only moved by this feature, so the gap is older.

The score moved 1.7 points with no code change. Watch for that in later runs. If it recurs, the mutation gate should print which survivors differ from the previous run.

**Fix.** Add the two-line `GradeOf` boundary test in a later feature that touches shot grading. No check now: the gate passed (threshold 80 %), and this is the first time this is seen.
**Scope:** project.

## Not acted on

- D1: the denial was right. The implementer tried a PowerShell script to cut line ranges out of a source file, and the conventions require the Edit / Write tools for file changes. It then used them. One occurrence, and its cost is inside S1/S2's fix run.
- A1: the plan tagged a review-level test (`SetPieceReviews_OuterUnknown_HoldsTheUnclippedStart`) to step 1 although its wiring was planned for step 2. The implementer pulled the wiring forward correctly at no extra cost. One-off. If it recurs, the plan check should compare test tags with the step that wires the code.

## Project knowledge

- K1 [Architecture rules]: `SetPieceShotModel`'s public surface is frozen by `SetPieceModelSplitTests` (tests/FCSim.Tests/Match/SetPieceModelSplitTests.cs). New helpers on it are `internal`; Tests and Runner see them through InternalsVisibleTo.
- K2 [Test harness]: the pinned standard-match job (39, 10, Bot) has 2 open-play shot reviews (one goal, one off target; no saved, blocked or `Keep` shot), 1 free-kick and 1 penalty review, all with known likely / outer spans. A case that needs a saved or blocked shot, an empty likely span or an unknown outer span needs another scanned seed or a synthetic calculator test.
- K3 [Architecture rules]: review data types and builders have their own files: `ShotReview.cs` / `SetPieceReview.cs` and `MatchEngine.ShotReview.cs` / `MatchEngine.SetPieceReviews.cs`. `KeyMoments.cs` (1,035), `MatchEngine.ShotFeel.cs` (774) and `UserShotModel.cs` (869) are over the 400-line limit, so new types and review code go elsewhere.
- K4 [Architecture rules]: `UserShotModel.KeeperSaveChanceAt` is the true-reach chance (`KeeperTryAt(...).SaveChance`). It matches the rolled chance only with Band params and `TrueReach`. The old keeper's `Resolve` rolls a different (logistic) chance, so don't call it for old-keeper reviews.

CLAUDE.md lines now enforced by a check and deletable: none.
