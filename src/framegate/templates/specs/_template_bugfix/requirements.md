# <The bug, in one line> — Bug report

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. ABC -->
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
     A check function or helper a row names: its name and module. Every error message: in
     full for one concrete input, with one notation for placeholders in the whole spec.
     Only behaviour: no row requires text in a docstring, a comment or a doc
     (`framegate spec-check` fails such a row).
     A fix that widens what an input may be (a new type, a new range): list every place the
     code uses that input; the tests reach each one with the new kind. -->
| ID | Given / when (exact input) | Observed now (wrong) | Expected (right, and why) | Measured with |
|---|---|---|---|---|
| XXX-R-001 | | | | |

## Must not change
<!-- Behavior next to the bug that a fix could break: the neighbouring cases, the other callers
     of the same code, saved data, hashes. Each row gets a test that PASSES today and after the
     fix. Every case that is green before the fix belongs here too, never in an R row (an R
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
<!-- Every existing field / flag / event a row above names in words: the exact member and
     where it is set. Assigned as: the line of code that sets it, copied in backticks
     (`framegate spec-check` looks for it in the file "Set where" names); `n/a: <why>` when no
     code assigns it. "None" only if the rows name no existing data. -->
| Words in the spec | Exact member / event | Set where (file: method) | Assigned as (code, copied) |
|---|---|---|---|
| | | | |

## References
<!-- Other projects the agents read for this task (never write): only names declared in
     framegate.toml `[references]`. `framegate approve-spec` pins each one's commit. "None."
     when the task needs none. -->
| Name | Files / folders that matter | What for |
|---|---|---|
| | | |

## Test data and fixtures
<!-- One row per ID that needs particular data.
     Observable: the public value a test reads to see the situation happened.
     Once this table pins data, every E- ID gets a row: the data that shows the case, or
     `unit-only: <which test builds the case itself>: <why>` (`framegate spec-check`).
     Existing tests whose fixtures or assertions change: `tests/<file>::<Class>` (or
     `::<Class>::<test>`); `(unchanged)` after a named test that stays as it is. -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Zero cases allowed |
|---|---|---|---|
| XXX-R-001 | | | no |

## Pipeline follow-up
<!-- Deploy and CI files are never changed by a feature. What must change there because of it
     is a separate step afterwards: `- <file>: <what must change> (<why>)`. Something that is
     no file of this repository (a CI variable, a secret, a database migration):
     `- OUTSIDE [<where>]: <what must be done> (<why>)`; a person does it by hand.
     Only deploy / CI files and such outside steps; "None." when there is no such change.
     A new test file where the CI lists test files by path: a line for that CI file. -->
None.

## Reference follow-up
<!-- A reference is never changed by this fix. If it has the same bug:
     `- <reference file>: <what should change> (<why>)`. "None." when no reference shares it. -->
None.

## Proposed doc updates
<!-- No step of the task edits a documentation file; what a doc should say afterwards is noted
     here, one bullet each. "None." when no doc changes.
     A file under docs/: `- <file>, <where in it>: today: <what it says now, quoted or
     summarised> -> proposed: <the new text> (<why>)`; where the task's code now contradicts
     it: `... today: <what it says now, quoted> -> code now: <what the code does instead>`.
     The owner answers each at the final gate (default: later).
     A README, CHANGELOG or handover note: `- <file>: <what changes, one brief line>`; applied
     after the final gate, no question. -->
None.

## Non-goals
<what this fix does not touch: related bugs, cleanups, refactors (each is its own task)>

## Open questions
<must be empty before approval>
