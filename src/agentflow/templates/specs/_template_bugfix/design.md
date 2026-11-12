# <The bug> — Fix design

## Where the bug is
<the code path from the input to the wrong result, in order (file: method); what is known and
 what is still a guess>

## Fix
<the smallest change that corrects it; if the cause is still open, one line per suspected
 cause. Who else calls this code, and what changes for them>

## Public interfaces
<signatures that change, with the IDs they serve; or "none change">

## Data flow
<!-- Existing golden / hash / snapshot / save-file tests whose stored values change with the
     fix: list them, say why the new value is right, and that the human confirms it. -->

## Error handling
<!-- Only if the fix adds or changes an error rule; else "None". Every row names the spec ID
     whose test covers it (`agentflow spec-check` fails on a row without one). -->
| Situation | Exception / result, where raised, what callers do | Spec ID |
|---|---|---|
| | | |

## Dependencies
<approved new dependencies, or "none">
