# <Feature name> — Requirements

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. HI2 -->

## Context
<why this feature exists, 3–5 lines>

## Goals

## Non-goals
<explicitly out of scope — prevents agents from "helpfully" adding things>

## Requirements
| ID | Requirement | Acceptance criterion (testable: given / when / then) |
|---|---|---|
| XXX-R-001 | | |
<!-- A text output (a table, JSON, a log line): put one literal example in its acceptance
     criterion (header + one row, a JSON excerpt) that shows separator, order and indentation;
     the tests compare against it.
     A check function, helper or checker a criterion names: its name and module (`src/...`,
     or the test module that holds the helper).
     No criterion requires text in a docstring, a comment or a doc: only behaviour. -->

## Edge cases
<!-- empty / NaN / missing / out-of-range / unit mismatch / duplicates / ordering / timing / very large input
     Every new function: does it clamp / validate its own inputs (or rely on the caller); the value of
     EVERY output (return and out-params) on "no result"; which error wins when two inputs are bad.
     Decided by: the existing method / predicate that decides the situation (e.g.
     `World.IsLeagueClub(id)`) and when it is checked (before / after de-duplication, before any
     work). A parser: the accepted forms (sign, leading zeros, spaces, largest value, overflow).
     Every error message: written out in full for one concrete input, with one notation for
     placeholders in the whole spec (repr, str, or str in literal quotes). -->
| ID | Situation | Expected behavior (exact: value, exception type + message pattern) | Decided by (method, when) |
|---|---|---|---|
| XXX-E-001 | | | |

## Invariants (property-based test candidates)
| ID | Invariant (must hold for all valid inputs) |
|---|---|
| XXX-I-001 | |

## Overlaps and sources of truth
<!-- Every pair of IDs that can apply to the same input: which one wins. Every value that must
     equal another (a copy, a derived value): its source, and whether the source can change
     later (then: equal at which moment?). "None" only if no two IDs overlap. -->
| IDs / value | Shared situation | Rule that wins / source and moment of equality |
|---|---|---|
| XXX-R-001 vs XXX-E-001 | | |

## Existing data this spec reads
<!-- Every existing field / flag / event a requirement names in words ("his goal", "the moment's
     goal event"): the exact member and where it is set. Tests read these members, not look-alikes
     (seen live: "the moment's goal event" was read as a flag with another meaning). "None" only if
     the requirements name no existing data.
     Assigned as: the line of code that sets the member, copied in backticks (e.g.
     `Player = shooter.Index`), so an id-vs-index or raw-vs-clamped mix-up shows now (seen live: a
     "player" field held the index in the match, not the player id). `framegate spec-check`
     looks for it in the file "Set where" names. `n/a: <why>` when no code assigns it. -->
| Words in the spec | Exact member / event | Set where (file: method) | Assigned as (code, copied) |
|---|---|---|---|
| | | | |

## References
<!-- Other projects on this machine that the agents read for this task (never write): only
     names declared in framegate.toml `[references]` (`framegate spec-brief` lists them). Only
     the ones listed here are given to the agents; `framegate approve-spec` pins each one's
     commit, and a run warns when it moved. "None." when the task needs none. -->
| Name | Files / folders that matter | What for |
|---|---|---|
| | | |

## Ported from
<!-- Only when this feature copies code of a reference ("file for file, same names"); else
     "None." and nothing below. Then the tests are the reference's tests, carried over, and the
     code is the reference's code, changed only as "What may differ" says; every step gate and
     the final gate show how far each target file is from its source (not blocking).
     Reference: its name in `[references]`. Commit: the commit you read (spec-brief shows it).
     One row per file, with paths relative to the reference's folder / to this repository;
     Kind `tests` = a test file of the reference to carry over, `code` = everything else.
     The stubs of the `code` rows are not written by hand: `framegate port-stubs <feature>`
     writes each target as the reference's text with the function bodies replaced.
     What may differ: every allowed deviation, one bullet each (import names, things only this
     repository has, things left out). A difference that is not listed is a review finding.
     Ticket / plan / ADR numbers and repository names inside the reference's docstrings and
     comments stay the reference's, word for word (they say where the code comes from, and
     every reworded line is a difference to explain); list a bullet here only where this
     repository must not carry one.
     Kept quirks: behaviours of the reference the port keeps on purpose although a stricter
     reading of this spec would forbid them (a regex `$` that accepts a trailing newline,
     `json.dumps` writing NaN), one bullet each: `- <file: function>: <the behaviour>`. The
     reviewer writes no finding about them and the triage dismisses one by citing the line.
     A behaviour of the reference that IS to be fixed here is no quirk: it is a requirement
     of this spec, and a line under "Reference follow-up". -->
Reference:
Commit:

| Source (path in the reference) | Target (path in this repository) | Kind (code / tests) |
|---|---|---|
| | | |

What may differ:
-

Kept quirks:
-

## Pipeline follow-up
<!-- Deploy files (Dockerfile, compose, k8s manifests, ...) and CI files (yml, scripts) are
     never changed by a feature, not even now: they stay locked. What must change there
     BECAUSE of this feature is a separate step after it; note it here so it is not lost
     (the final summary raises it). One bullet per change: `- <file>: <what must change>
     (<why>)`, e.g. `- platform/deploy/Dockerfile: COPY the new runner/ folder (the image
     must contain the new package)`, a new dependency the image must install, a new test
     selection for CI. Something that is no file of this repository
     (a CI variable, a secret in the vault, a migration of the real database, an environment
     switch): `- OUTSIDE [<where>]: <what must be done> (<why>)`; a person does it by hand.
     Only deploy / CI files and such outside steps: nothing else belongs here. "None." when the
     feature needs no such change. -->
None.

## Reference follow-up
<!-- A reference (another project, read-only) is never changed by a feature. What the
     REFERENCE should change because of this feature is work for that project; note it here
     so it is not lost (the final summary raises it, and the list is kept in this folder).
     One bullet per change: `- <reference file>: <what should change> (<why>)`, e.g.
     `- platform/runner/common.py: parse_ts() must refuse a non-string instead of crashing
     (fixed in this port; the reference has the same bug)`, a deliberate improvement of a
     port the reference should take over. "None." when there is no such change. -->
None.

## Proposed doc updates
<!-- No step of the task edits a documentation file. What a doc should say once the task is
     done, or where the task goes another way than the documented plan, is noted here, one
     bullet each.
     A file under docs/ (plan, design, architecture, reference): `- <file>, <where in it>:
     today: <what it says now, quoted or summarised> -> proposed: <the new text> (<why>)`.
     The owner answers each at the final gate (default: later).
     A README, CHANGELOG or handover note: `- <file>: <what changes, one brief line>`. No
     question: applied after the final gate is approved.
     One docs step then applies them as its own commit. "None." when no doc changes. -->
None.

## Green against stubs
<!-- Every test must FAIL before the implementation ("red against stubs"). The exceptions,
     by design, are listed here, one bullet each: `- <test name or node ID>: <why it cannot
     fail before the implementation>`. Typical: a scan or negative check ("no analytic name
     appears in the code", "X is not imported"), a structure test; in a port, a carried-over
     test of that kind (the test writer may not change it). Only the listed tests may pass
     the check; a listed test that fails there is a failure. Every spec ID such a test is
     tagged with needs another test that does fail against the stubs (checked). "None."
     for the usual feature. -->
None.

## Test data and fixtures
<which recorded/synthetic data the tests use, where it lives, how it is generated>
<!-- One row per ID that needs particular data (a seed, a recording, a rare situation).
     Observable: the public value a test reads to see the situation happened, with code that
     exists before this feature (the test writer turns it into a fixture precondition test).
     Zero cases: may a check over picked cases pass with none? Default "no".
     Measured with: every number in an acceptance criterion ("gives 2", ">= 4") is measured
     with this spec's own definition (the command and date, or "by hand: <how>"), never copied
     from another test (seen live: a count copied from a test that counted another way).
     Counted outputs: for every count / field a requirement defines, a row with data where it
     is not 0 (not its default); for a variant (a mode, a driver, another world) one value that
     differs from the base case. Else an implementation that always returns 0 passes.
     Edge cases and named outcomes: once this table pins data, EVERY E- ID gets its own row
     (several IDs in one cell are fine: "XXX-E-001, E-002", "XXX-E-003..E-005"), and so does
     every outcome or branch a rule names in words ("saved", "blocked", "no reach"), also when
     the base data is the same. Does the pinned data hold the case? If not: other data that
     does, or in the Data cell `unit-only: <which test builds the case itself>: <why no pinned
     data is needed>`. `framegate spec-check` fails an E- ID without a row (seen live: three
     cases the only pinned match did not hold; the test writer and the test reviewer asked
     about them five times, and one assertion never ran).
     Existing tests: name each existing test whose fixtures or assertions change as
     `tests/<file>::<Class>` (or `::<Class>::<test>`); write `(unchanged)` after a named test
     that stays as it is. A named test without a changed line fails the test checks. -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Measured with | Zero cases allowed |
|---|---|---|---|---|
| XXX-R-001 | | | | no |

## Verification harness
<how to prove this works end to end on real data, e.g. "run pipeline on recorded failure case
 F-17; health index must decrease monotonically over the last 72h">

## Open questions
<must be empty before approval>
