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

## Edge cases
<!-- empty / NaN / missing / out-of-range / unit mismatch / duplicates / ordering / timing / very large input
     Every new function: does it clamp / validate its own inputs (or rely on the caller); the value of
     EVERY output (return and out-params) on "no result"; which error wins when two inputs are bad. -->
| ID | Situation | Expected behavior (exact: value, exception type + message pattern) |
|---|---|---|
| XXX-E-001 | | |

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
     the requirements name no existing data. -->
| Words in the spec | Exact member / event | Set where (file: method) |
|---|---|---|
| | | |

## Test data and fixtures
<which recorded/synthetic data the tests use, where it lives, how it is generated>
<!-- One row per ID that needs particular data (a seed, a recording, a rare situation).
     Observable: the public value a test reads to see the situation happened, with code that
     exists before this feature (the test writer turns it into a fixture precondition test).
     Zero cases: may a check over picked cases pass with none? Default "no". -->
| ID | Data (seed / file / generator) | Observable that proves the situation | Zero cases allowed |
|---|---|---|---|
| XXX-R-001 | | | no |

## Verification harness
<how to prove this works end to end on real data, e.g. "run pipeline on recorded failure case
 F-17; health index must decrease monotonically over the last 72h">

## Open questions
<must be empty before approval>
