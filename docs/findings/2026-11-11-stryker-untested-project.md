# Finding: mutation gate FAILs when a feature changes a project no test project references

_Seen 2026-11-11 in FCSim, feature `M5-moment-scan` (final gate, run 1). Status: fixed on `fix/stryker-untested-project`, commit 4065651._

## What happened
- The feature changed Core files (`src/FCSim.Core/Match/MomentScan*.cs`, `KeyMomentDriver.cs`) and two Runner files
  (`src/FCSim.Runner/Program.cs`, new `src/FCSim.Runner/KeyMomentScanCommand.cs`: thin CLI glue).
- `DotnetAdapter.mutation_check` groups changed files by `owning_project` → two groups: FCSim.Core and FCSim.Runner.
- `_run_stryker`: for FCSim.Runner, no test project's csproj text names `FCSim.Runner`, so `refs` falls back to
  **all** test projects (`... or test_projects`). Stryker then reports:
  `Project FCSim.Runner.csproj overall analysis failed hence can't be mutated.` /
  `Test project ...FCSim.Tests.csproj does not appear to test any mutable project` →
  `Failed to analyze project builds. Stryker cannot continue.` (exit 1).
- `_run_stryker` returns `FAIL "Stryker failed for FCSim.Runner.csproj (exit 1)"` at once. The Core group had already
  run to the end (score 77.50 %, 155/200), but its result is thrown away, so the gate brief says "the mutation check could not
  run (the tool failed to build the project)" — misleading: nothing failed to build.
- Correction: Core's 77.50 % (155/200) is **below** the 80 % threshold. The mutation gate of this run was
  FAIL either way; the bug hid the real reason (surviving mutants: `MomentScanText.cs` has 35 of the 45
  undetected) behind a Stryker error on a project that can't be mutated.
- The `simulated build failed` warnings for FCSim.Core in both logs are harmless (Core was found, built and mutated).

## Evidence
- FCSim `out/agentflow/M5-moment-scan/stryker/FCSim.Runner.log` (exit 1, lines quoted above),
  `.../stryker/FCSim.Core.log` (`The final mutation score is 77.50 %`, 11 min),
  `.../final_gates.md` (`[FAIL] mutation score (feature tests)`).
- Code: `src/agentflow/adapters/dotnet.py` `mutation_check` (grouping, ~L1016), `_run_stryker` (`refs` fallback and
  early `return CheckResult("FAIL", ...)`, ~L1150–1190).

## Why it matters
- Any feature that touches untested glue (a CLI runner, a Unity-side project, a tool) fails the mutation gate for a
  reason the tests can't fix; the final gate then shows a wrong cause ("failed to build").
- Cost: one Stryker run per such project is wasted, and the real score of the tested project is hidden.

## Expected behaviour
- A changed project that no test project references (directly or via its ProjectReferences) is **not** sent to
  Stryker. It is listed in the result as `not mutated (no test project references it): <project>: <files>`.
- Its changed lines are still counted by diff coverage as today (that gate already reports uncovered lines).
- If every changed project is untested: `SKIPPED` with that line, not FAIL.
- A Stryker failure for a project that *is* referenced stays a FAIL as today, but the message names the real cause
  (the last ~25 lines) and the results of groups that did finish are kept in the detail.

## Fix (fix/stryker-untested-project)
- `mutation_check` follows each test project's `<ProjectReference>`s transitively (resolved paths,
  `referenced_projects`); a changed project none of them reaches is not run and is listed as
  `not mutated (no test project references it): <X.csproj>: <files>`, also on the score line
  (`Mutation score ... on FCSim.Core.csproj, threshold 80% — FCSim.Runner.csproj not mutated: no test
  project references it`). Only unreferenced projects changed: SKIPPED.
- A failed Stryker run no longer stops the other runs; the FAIL keeps the finished runs' counts, and
  `mutation_survivors.md` is always written for them.
- `roles/pr.md`: the final brief quotes a failing gate's reason as `final_gates.md` states it.
- After this fix M5-moment-scan's mutation gate is **still FAIL**, now for the real reason: Core's
  77.50 % < 80 % (`MomentScanText.cs`: 35 of the 45 undetected). The fix does not make that run pass.

## Fix prompt (for a fresh session in this repo)
See the section below; it is self-contained.

---

Fix in agentflow (repo D:\Me\Projects\agentflow, Python, pytest). Work on a new branch `fix/stryker-untested-project` from `main`; do not push.

Problem: the .NET mutation gate FAILs when a feature changes a project that no test project references. Details and evidence: docs/findings/2026-11-11-stryker-untested-project.md (read it first). Code: src/agentflow/adapters/dotnet.py — `mutation_check` groups changed .cs files by `owning_project`; `_run_stryker` picks test projects per group with a text match on the test csproj (`proj.name.lower() in ...`) and falls back to all test projects when none match, then returns FAIL on the first Stryker failure, dropping the groups that already ran.

Change:
1. Decide per group whether any test project references it: the test csproj's `<ProjectReference>` paths, followed transitively through the referenced csproj files (resolve relative paths against each csproj's folder; compare resolved paths, not names). Replace the name-text match and the `or test_projects` fallback with this.
2. Groups with no referencing test project are not sent to Stryker. Add a line per such group to the result detail: `not mutated (no test project references it): <csproj name>: <changed files>`.
3. All groups unreferenced → `CheckResult("SKIPPED", ...)` with those lines. Some referenced → score from the referenced groups only, PASS/FAIL against the threshold as today, plus the not-mutated lines.
4. A Stryker failure on a referenced group stays FAIL, but keep the results of groups that finished in the message (`<proj>: d detected, u undetected`) next to the failure tail.
5. The "failed to build" wording in the gate brief is not in the code: the PR-phase agent wrote it from final_gates.md. So the not-mutated lines must be plain enough that an agent can't misread them as a failure (e.g. first line of the detail: `Mutation score X% (Core) — FCSim.Runner not mutated: no test project references it`).
6. docs/adapters.md (.NET mutation section): one or two lines on the rule. Update docs/findings/2026-11-11-stryker-untested-project.md: Status: fixed on <branch>, with the commit.

Tests (pytest, tests/test_dotnet.py next to `test_mutation_runs_stryker_per_project`, same fake-Stryker style, no real Stryker): (a) Core + Runner changed, tests reference Core only → Stryker called once (Core), result PASS/FAIL from Core's report, detail lists Runner as not mutated; (b) only Runner changed → SKIPPED, Stryker not called; (c) transitive reference (Tests → Lib → Core) counts as referenced; (d) referenced group fails → FAIL whose message also contains the other finished group's counts. Run `uv run pytest -q` (whole suite) and `uv run ruff check` / `uv run mypy src` if configured in pyproject.toml; all green.

Done = branch with the fix + tests + docs, suite green; report in ≤ 10 lines: files changed, test counts before/after, commit hash.
