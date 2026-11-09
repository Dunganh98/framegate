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
<!-- empty / NaN / missing / out-of-range / unit mismatch / duplicates / ordering / timing / very large input -->
| ID | Situation | Expected behavior (exact: value, exception type + message pattern) |
|---|---|---|
| XXX-E-001 | | |

## Invariants (property-based test candidates)
| ID | Invariant (must hold for all valid inputs) |
|---|---|
| XXX-I-001 | |

## Test data and fixtures
<which recorded/synthetic data the tests use, where it lives, how it is generated>

## Verification harness
<how to prove this works end to end on real data, e.g. "run pipeline on recorded failure case
 F-17; health index must decrease monotonically over the last 72h">

## Open questions
<must be empty before approval>
