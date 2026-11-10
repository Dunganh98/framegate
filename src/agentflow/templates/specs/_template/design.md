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
