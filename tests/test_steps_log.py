"""The implementer's notes against the spec (checks/steps_log.py)."""

from __future__ import annotations

import pathlib

from framegate.checks import steps_log
from test_checks import adapter_for, write

SPEC = """\
# bug
Mode: bugfix

## Reproduction
| ID | Given / when | Expected |
|---|---|---|
| FR-R-001 | `parse("B,1;x")` | raises `Refused` |

## Must not change
| ID | Behavior to keep |
|---|---|
| FR-I-004 | A well-formed id that names no pump in scope is not refused (asked: only the form). |

## Suspected causes
- H1: items stay unchecked (src/pkg/common.py: parse_force) -> FR-R-001
- H2: `Refused` maps to exit 2 (src/pkg/cli.py: main) -> FR-R-001
- H3: `asset in force` ignores unknown ids (src/pkg/step.py: plan_retrain) -> ruled out: no
"""
NOTE = """\
## Step 1 — validate
- ROOT CAUSE: `parse_force` never validated the items.
- RULED OUT: H2 (the retrain plan in step.py mis-matches ids): it only tests membership.
- RULED OUT: H3 (cli.main does not turn Refused into exit 2): it already does.
- RULED OUT: H7: nothing.
- ASSUMPTION [FR-R-001]: a well-formed id that names no pump is not refused (plan.md says "asked").
- ASSUMPTION [FR-R-001]: an empty item between two commas is skipped, as before the fix.
"""
GOOD = """\
## Step 1 — validate
- RULED OUT: H2 (cli.main maps Refused to exit 2 already).
- RULED OUT: H3: plan_retrain only sees a parsed set.
Assumptions: none
"""


def test_a_restated_spec_row_and_swapped_cause_numbers_fail(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "specs/f/requirements.md", SPEC)
    adapter = adapter_for(tmp_path)
    assert steps_log.check(adapter, "f").status == "PASS"  # no notes yet
    write(tmp_path, "out/f/steps.md", NOTE)
    res = steps_log.check(adapter, "f")
    assert res.status == "FAIL" and res.summary == "4"
    lines = res.output.splitlines()[1:]
    assert lines[0].startswith("  ASSUMPTION [FR-R-001] 'a well-formed id")
    assert "the spec states this in FR-I-004" in lines[0]
    assert lines[1:] == [
        "  RULED OUT: H2 names step.py (the spec's H3); the spec's H2 is src/pkg/cli.py: main. "
        "Use the spec's cause numbers.",
        "  RULED OUT: H3 names main (the spec's H2); the spec's H3 is src/pkg/step.py: "
        "plan_retrain. Use the spec's cause numbers.",
        "  RULED OUT: H7: the spec's suspected causes have no H7.",
    ]
    # the note written again under the same heading takes the first one's place
    write(tmp_path, "out/f/steps.md", NOTE + "\n" + GOOD)
    assert steps_log.check(adapter, "f").status == "PASS"
