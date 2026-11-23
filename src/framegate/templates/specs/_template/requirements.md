# <Feature name> — Requirements

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. ABC -->
Resolves:   <!-- optional: the deferred work this task does, e.g. D7, D9 (framegate deferred) -->

## Context
<why this feature exists, 3–5 lines>

## Goals

## Non-goals
<explicitly out of scope — prevents agents from "helpfully" adding things>

## Requirements
| ID | Requirement | Acceptance criterion (testable: given / when / then) |
|---|---|---|
| XXX-R-001 | | |
<!-- A text output: one literal example in its criterion; the tests compare against it.
     A check function or helper a criterion names: its name and module.
     Only behaviour: no criterion requires text in a docstring, a comment or a doc.
     A requirement that widens what an input may be (a new type, a new range): list every
     place the code uses that input; the tests reach each one with the new kind. -->

## Edge cases
<!-- empty / missing / out-of-range / duplicates / ordering / timing / very large input.
     Every new function: does it validate its own inputs; the value of every output on "no
     result"; which error wins when two inputs are bad. A parser: the accepted forms.
     Decided by: the existing method that decides the situation, and when it is checked.
     A wrong-type input: the rule by type first ("anything that is not exactly an X is
     refused"; does a subclass count?), its place in the error order, then one example per kind.
     Every error message: in full for one input, one notation for placeholders in the spec. -->
| ID | Situation | Expected behavior (exact: value, exception type + message pattern) | Decided by (method, when) |
|---|---|---|---|
| XXX-E-001 | | | |

## Invariants (property-based test candidates)
| ID | Invariant (must hold for all valid inputs) |
|---|---|
| XXX-I-001 | |

## Overlaps and sources of truth
<!-- Every pair of IDs that can apply to the same input: which one wins. Every value that must
     equal another (a copy, a derived value): its source, and at which moment they are equal. -->
| IDs / value | Shared situation | Rule that wins / source and moment of equality |
|---|---|---|
| XXX-R-001 vs XXX-E-001 | | |

## Existing data this spec reads
<!-- Every existing field / flag / event a requirement names in words: the exact member and
     where it is set. Assigned as: the line of code that sets it, copied in backticks
     (`framegate spec-check` looks for it in the file "Set where" names); `n/a: <why>` when no
     code assigns it. "None" only if the requirements name no existing data. -->
| Words in the spec | Exact member / event | Set where (file: method) | Assigned as (code, copied) |
|---|---|---|---|
| | | | |

## References
<!-- Other projects the agents read for this task (never write): only names declared in
     framegate.toml `[references]`, pinned at their commit by the approval. "None." if none. -->
| Name | Files / folders that matter | What for |
|---|---|---|
| | | |

## Ported from
<!-- Only when this feature copies code of a reference file for file; else "None." and nothing
     below. The tests and the code are then the reference's, changed only as listed here.
     Reference: its name in `[references]`. Commit: the commit you read. One row per file;
     Kind `tests` = a test file to carry over, `code` = everything else
     (`framegate port-stubs <feature>` writes the stubs of the `code` rows).
     What may differ: every allowed deviation, one bullet each; any other difference is a
     review finding. Ticket numbers and repository names in the reference's docstrings and
     comments stay the reference's, word for word.
     Kept quirks: behaviours of the reference kept on purpose against a stricter reading of
     this spec: `- <file: function>: <the behaviour>`. One that is fixed here is a
     requirement of this spec and a line under "Reference follow-up". -->
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
<!-- Deploy and CI files are never changed by a feature. What must change there because of it
     is a separate step afterwards: `- <file>: <what must change> (<why>)`. Something that is
     no file of this repository (a CI variable, a secret, a database migration):
     `- OUTSIDE [<where>]: <what must be done> (<why>)`; a person does it by hand.
     Only deploy / CI files and such outside steps (also a new test file where the CI lists
     test files by path); "None." when there is no such change. -->
None.

## Reference follow-up
<!-- A reference is never changed by a feature. What it should change because of this one:
     `- <reference file>: <what should change> (<why>)`. "None." when there is no such change. -->
None.

## Proposed doc updates
<!-- No step of the task edits a documentation file; what a doc should say afterwards is noted
     here, one bullet each. "None." when no doc changes.
     A file under docs/: `- <file>, <where in it>: today: <what it says now, quoted or
     summarised> -> proposed: <the new text> (<why>)`; where the task's code now contradicts
     it: `... today: <what it says now, quoted> -> code now: <what the code does instead>`.
     The owner answers each at the final gate (default: later).
     A README, CHANGELOG or handover note: `- <file>: <what changes, one brief line>`. -->
None.

## Green against stubs
<!-- Every test must fail before the implementation. The exceptions by design, one bullet
     each: `- <test name or node ID>: <why it cannot fail before the implementation>` (a scan
     or negative check, behaviour the existing code already has, a carried-over test of a
     port). Only these may pass the red check, and every spec ID of such a test needs another
     test that does fail. "None." for the usual feature. -->
None.

## Test data and fixtures
<which recorded/synthetic data the tests use, where it lives, how it is generated>
<!-- One row per ID that needs particular data. Observable: the public value a test reads to
     see the situation happened, with code that exists before this feature. Zero cases: may
     a check over picked cases pass with none?
     Measured with: every number in an acceptance criterion is measured with this spec's own
     definition (the command and date, or "by hand: <how>"), never copied from another test.
     Every count or field a requirement defines: data where it is not its default; every
     variant: one value that differs from the base case.
     Once this table pins data, every E- ID and every outcome a rule names gets a row: the
     data that holds the case, or `unit-only: <which test builds the case itself>: <why>`.
     Existing tests whose fixtures or assertions change: `tests/<file>::<Class>` (or
     `::<Class>::<test>`); `(unchanged)` after a named test that stays as it is.
     A new test that must go into an existing file: a line with its ID and the file,
     `XXX-R-002: in tests/<file>` (checked: the tests must touch that file). -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Measured with | Zero cases allowed |
|---|---|---|---|---|
| XXX-R-001 | | | | no |

## Verification harness
<how to prove this works end to end on real data: the command, the data, the expected result>

## Open questions
<must be empty before approval>
