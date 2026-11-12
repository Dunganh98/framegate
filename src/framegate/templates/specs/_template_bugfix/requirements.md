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

## Test data and fixtures
<!-- One row per ID that needs particular data (a seed, a recording, a rare situation).
     Observable: the public value a test reads to see the situation happened (the test writer
     turns it into a fixture precondition test). -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Zero cases allowed |
|---|---|---|---|
| XXX-R-001 | | | no |

## Non-goals
<what this fix does not touch: related bugs, cleanups, refactors (each is its own task)>

## Open questions
<must be empty before approval>
