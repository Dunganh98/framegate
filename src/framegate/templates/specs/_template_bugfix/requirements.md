# <The bug, in one line> — Bug report

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. FKB -->
Mode: bugfix
Resolves:   <!-- optional: the deferred work this task does, e.g. D7, D9 (framegate deferred) -->

## Symptom
<what goes wrong, where the user sees it, since when (version / commit, if known); 3–5 lines>

## Reproduction
<!-- One row per way the bug shows. Given / when: the exact input (seed, file, command, steps).
     Observed now: the wrong result, copied from a run of today's code. Expected: the right
     result and where it follows from (the task, a spec, a rule in the code; a doc alone is no
     source). Measured with: the command and date that produced "Observed now".
     Each row gets a test that FAILS on today's code for this reason and passes once fixed.
     A check function, helper or checker a criterion names: its name and module (`src/...`,
     or the test module that holds the helper).
     Every error message: written out in full for one concrete input, with one notation for
     placeholders in the whole spec (repr, str, or str in literal quotes).
     No criterion requires text in a docstring, a comment or a doc: only behaviour
     (`framegate spec-check` fails such a row).
     A fix that widens what an input may be (aware as well as naive datetimes, a new type, a
     new range): list every place the function uses that input; the tests reach each with
     the new kind (seen live: the tests faked an empty frame, so the new input never reached
     the row filter that crashed on it). -->
| ID | Given / when (exact input) | Observed now (wrong) | Expected (right, and why) | Measured with |
|---|---|---|---|---|
| XXX-R-001 | | | | |

## Must not change
<!-- Behavior next to the bug that a fix could break: the neighbouring cases, the other callers
     of the same code, saved data, hashes. Each row gets a test that PASSES today and after the
     fix. A case of the corrected behavior that already works today belongs here too, and
     so does every case that is green before the fix by design: an accepted input, an
     existing error or behaviour that wins, a must-stay-true case (never an R row: an R
     test must fail today). -->
| ID | Behavior to keep (given / when / then, exact) |
|---|---|
| XXX-I-001 | |

## Suspected causes
<!-- At least two candidate causes, each with where to look (file: method). The implementer
     confirms one and rules the others out with evidence. -->
- H1:
- H2:

## Existing data this spec reads
<!-- Every existing field / flag / event a row above names in words: the exact member, where
     it is set, and the line of code that sets it, copied in backticks (`framegate spec-check`
     looks for it in the file "Set where" names). `n/a: <why>` when no code assigns it.
     "None" only if the rows name no existing data. -->
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

## Test data and fixtures
<!-- One row per ID that needs particular data (a seed, a recording, a rare situation).
     Observable: the public value a test reads to see the situation happened (the test writer
     turns it into a fixture precondition test).
     Once this table pins data, every E- ID gets a row: the data that shows the case, or
     `unit-only: <which test builds the case itself>: <why>` (`framegate spec-check`).
     Existing tests: name each existing test whose fixtures or assertions change as
     `tests/<file>::<Class>` (or `::<Class>::<test>`); write `(unchanged)` after a named test
     that stays as it is. A named test without a changed line fails the test checks. -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Zero cases allowed |
|---|---|---|---|
| XXX-R-001 | | | no |

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
     feature needs no such change.
     A new test file where the CI lists test files by path: a line for that CI file (the CI
     runs only the listed ones; `framegate spec-check` warns). -->
None.

## Reference follow-up
<!-- A reference (another project, read-only) is never changed by this fix. If the reference
     has the same bug, or should take over the fix, note it here so it is not lost (the final
     summary raises it): `- <reference file>: <what should change> (<why>)`. "None." when
     no reference shares the bug. -->
None.

## Proposed doc updates
<!-- No step of the task edits a documentation file. What a doc should say once the task is
     done, or where the task goes another way than the documented plan, is noted here, one
     bullet each.
     A file under docs/ (plan, design, architecture, reference): `- <file>, <where in it>:
     today: <what it says now, quoted or summarised> -> proposed: <the new text> (<why>)`.
     The owner answers each at the final gate (default: later). Where the task's code now
     contradicts what it says: `... today: <what it says now, quoted> -> code now: <what the
     code does instead>`; the owner approves the code (the doc now or later) or rejects it.
     A README, CHANGELOG or handover note: `- <file>: <what changes, one brief line>`. No
     question: applied after the final gate is approved.
     One docs step then applies them as its own commit. "None." when no doc changes. -->
None.

## Non-goals
<what this fix does not touch: related bugs, cleanups, refactors (each is its own task)>

## Open questions
<must be empty before approval>
