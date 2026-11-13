# <The bug, in one line> — Bug report

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. FKB -->
Mode: bugfix

## Symptom
<what goes wrong, where the user sees it, since when (version / commit, if known); 3–5 lines>

## Reproduction
<!-- One row per way the bug shows. Given / when: the exact input (seed, file, command, steps).
     Observed now: the wrong result, copied from a run of today's code. Expected: the right
     result and where it follows from (a spec, a rule, the docs). Measured with: the command
     and date that produced "Observed now".
     Each row gets a test that FAILS on today's code for this reason and passes once fixed. -->
| ID | Given / when (exact input) | Observed now (wrong) | Expected (right, and why) | Measured with |
|---|---|---|---|---|
| XXX-R-001 | | | | |

## Must not change
<!-- Behavior next to the bug that a fix could break: the neighbouring cases, the other callers
     of the same code, saved data, hashes. Each row gets a test that PASSES today and after the
     fix. A case of the corrected behavior that already works today belongs here too. -->
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
     `unit-only: <which test builds the case itself>: <why>` (`framegate spec-check`). -->
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
     selection for CI. "None." when the feature needs no such change. -->
None.

## Non-goals
<what this fix does not touch: related bugs, cleanups, refactors (each is its own task)>

## Open questions
<must be empty before approval>
