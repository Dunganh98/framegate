# <The refactor, in one line> — Refactor brief

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. MSR -->
Mode: refactor

## Goal
<what gets better (a file split, a duplication removed, a concept given its own class) and
 why now; 3–5 lines>

## Scope
<!-- The files / types / methods this refactor may change, and what happens to each (split
     into, moved to, renamed, merged). Code outside this list stays untouched. -->
| File / type | What happens to it |
|---|---|
| | |

## Behavior to preserve
<!-- One row per behavior of the code in scope that callers or users rely on: the output for
     a given input, errors, order, side effects, stored formats. Each row gets characterization
     tests that PASS on today's code and must pass after every step. Use real inputs (seeds,
     recordings); a golden value is measured from today's code ("Measured with": the command
     and date), never worked out by hand. -->
| ID | Behavior (given / when / then, exact) | Measured with |
|---|---|---|
| XXX-R-001 | | |

## Invariants (property-based test candidates)
| ID | Invariant (must hold for all valid inputs, before and after) |
|---|---|
| XXX-I-001 | |

## Structure targets
<!-- How to see that the refactor is done: statements one can check (file X under 400 lines;
     class Y only does Z; no method over 60 lines; the public API of W is unchanged). The
     reviewer and the final structure report check them. -->
-

## Non-goals
<behavior changes, bug fixes, speed-ups: each is its own feature or bug fix>

## Existing data this spec reads
<!-- Every existing field / flag / event a row above names in words: the exact member, where
     it is set, and the line of code that sets it, copied in backticks (`agentflow spec-check`
     looks for it in the file "Set where" names). `n/a: <why>` when no code assigns it.
     "None" only if the rows name no existing data. -->
| Words in the spec | Exact member / event | Set where (file: method) | Assigned as (code, copied) |
|---|---|---|---|
| | | | |

## Test data and fixtures
<!-- One row per ID that needs particular data (a seed, a recording, a rare situation). -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Zero cases allowed |
|---|---|---|---|
| XXX-R-001 | | | no |

## Open questions
<must be empty before approval>
