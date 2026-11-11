# <Feature name> — Design

## Modules and responsibilities

## Public interfaces
<signatures exactly as in the stubs; every function lists the IDs it serves>

## Input domain and copy semantics
<!-- One row per public method / type above. Outside the domain: what happens (raise, clamp,
     undefined and untested). Copy: what a copy / clone / returned collection shares. -->
| Member | Parameter | Valid domain | Outside the domain (NaN, ±Inf, null, undefined enum, ...) | Copy: deep / shallow / shared |
|---|---|---|---|---|
| | | | | |

## Mirrored code
<!-- Only when a requirement says "as <X> does / computes". One row per such requirement: the
     method and lines of X, and every choice X makes that the new code (and the tests' data) must
     copy: cut-offs (e.g. t < 0.6), raw vs clamped values, tie-breaks, order of checks. For a
     variant (another strike type, per grade, "had he chosen X"): which input fields change and
     how; every other field is copied unchanged. -->
| Requirement | Mirrors (file: method, lines) | Choices to copy / fields that change |
|---|---|---|
| | | |

## Data flow
<!-- Existing golden / hash / snapshot / fingerprint / save-file tests that serialize a type this
     feature changes: list them, and say for each new member whether it is in or out (and how the
     test leaves it out). -->

## Error handling
<which exceptions, where raised, what callers do>

## Dependencies
<approved new dependencies, or "none">

## Architecture constraints
<import/layer rules, e.g. "features/ must not import from api/"; mirror them in the import checker config>
