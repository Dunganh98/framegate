# <The refactor> — Target design

## Modules and responsibilities
<the structure after the refactor: per file / type one line on what it is for, and where each
 piece of today's code goes>

## Public interfaces
<the signatures callers and tests use: unchanged, or moved / renamed (old -> new); a renamed
 public member needs every caller listed>
<!-- One row per NEW or CHANGED public member: its name, signature, parameter types, default
     values, error type / message, stored or wire format. Shape from: `task`, `code: <the
     existing pattern it follows>`, `docs: <file>` or `asked` (the user answered).
     `framegate spec-check` fails a row without one, and a public name of the stubs without
     a row. -->
| New / changed member | Shape from (task / code: <pattern> / docs: <file> / asked) |
|---|---|
| | |

## Data flow
<!-- Existing golden / hash / snapshot / save-file tests that touch the code in scope: they
     must stay byte-identical. List them. -->

## Error handling
<!-- Only if the refactor adds or changes an error rule; else "None". Every row names the spec
     ID whose test covers it (`framegate spec-check` fails on a row without one).
     Source: where the error type, the message and what is allowed come from: `task`,
     `code: <file>:<symbol>` (existing code that refuses this way), `docs: <file>` or `asked`
     (the user answered). `framegate spec-check` fails a row without one: ask the user. -->
| Situation | Exception / result, where raised, what callers do | Spec ID | Source (task / code: <file>:<symbol> / docs: <file> / asked) |
|---|---|---|---|
| | | | |

## Dependencies
<approved new dependencies, or "none">

## Architecture constraints
<import/layer rules the new structure must keep>
