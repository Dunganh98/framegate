# <The refactor, in one line> — Refactor brief

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. ABC -->
Mode: refactor
Resolves:   <!-- optional: the deferred work this task does, e.g. D7, D9 (framegate deferred) -->

## Goal
<what gets better (a file split, a duplication removed, a concept given its own class) and
 why now; 3–5 lines>

## Scope
<!-- The files / types / methods this refactor may change, and what happens to each (split
     into, moved to, renamed, merged). Code outside this list stays untouched. Write each
     existing file as its path in backticks: framegate measures, before any code moves,
     which lines of these files the tests run (test_checks.md, "Scope coverage"). -->
| File / type | What happens to it |
|---|---|
| | |

## Behavior to preserve
<!-- One row per behavior of the code in scope that callers or users rely on: the output for
     a given input, errors, order, side effects, stored formats. Each row is pinned by tests
     that PASS on today's code and must pass after every step: existing tests where they
     assert it already, new characterization tests where not. Use real inputs (seeds,
     recordings); a golden value is measured from today's code ("Measured with": the command
     and date), never worked out by hand.
     A check function or helper a row names: its name and module. Every error message: in
     full for one concrete input, with one notation for placeholders in the whole spec.
     Only behaviour: no row requires text in a docstring, a comment or a doc
     (`framegate spec-check` fails such a row). -->
| ID | Behavior (given / when / then, exact) | Measured with |
|---|---|---|
| XXX-R-001 | | |

## Existing tests of the code in scope
<!-- The test files (and golden / hash / snapshot tests) that exercise the code in scope
     today, found by searching the tests for its types and methods. The test writer starts
     here: an existing test that asserts a row above exactly gets the row's ID as a tag and
     no new test is written for it. New tests are for the gaps only. "None" if nothing
     tests this code yet. -->
| Test file | What it covers of the scope |
|---|---|
| | |

## Invariants (property-based test candidates)
| ID | Invariant (must hold for all valid inputs, before and after) |
|---|---|
| XXX-I-001 | |

## Structure targets
<!-- How to see that the refactor is done: statements one can check (file X under N lines;
     class Y only does Z; the public API of W is unchanged). The
     reviewer and the final structure report check them. -->
-

## Non-goals
<behavior changes, bug fixes, speed-ups: each is its own feature or bug fix>

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

## Pipeline follow-up
<!-- Deploy and CI files are never changed by a feature. What must change there because of it
     is a separate step afterwards: `- <file>: <what must change> (<why>)`. Something that is
     no file of this repository (a CI variable, a secret, a database migration):
     `- OUTSIDE [<where>]: <what must be done> (<why>)`; a person does it by hand.
     Only deploy / CI files and such outside steps; "None." when there is no such change.
     A new test file where the CI lists test files by path: a line for that CI file. -->
None.

## Reference follow-up
<!-- A reference is never changed by this refactor. What it should change because of it:
     `- <reference file>: <what should change> (<why>)`. "None." when there is no such change. -->
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

## Test data and fixtures
<!-- One row per ID that needs particular data.
     Once this table pins data, every E- ID gets a row: the data that shows the case, or
     `unit-only: <which test builds the case itself>: <why>` (`framegate spec-check`).
     Existing tests whose fixtures or assertions change: `tests/<file>::<Class>` (or
     `::<Class>::<test>`); `(unchanged)` after a named test that stays as it is. -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Zero cases allowed |
|---|---|---|---|
| XXX-R-001 | | | no |

## Open questions
<must be empty before approval>
