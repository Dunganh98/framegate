"""`framegate replay`: real commits as tasks, delivered in a sandbox that holds the code before
the commit, scored with the commit's own tests (docs/replay.md).

candidates.py  the candidates file
sandbox.py     the run folder: cut clone, hidden answer, environment, the mechanical check
score.py       hidden tests against the result, the engine's numbers, the results table
run.py         the driver agent, the caps, the scheduler, the command
chat.py        one chat message of the driver (`python -m framegate.replay.chat`)
driver.md      the driver's instructions
"""

from __future__ import annotations

from typing import Any

#: caps per candidate
MAX_USD = 25.0
MAX_HOURS = 3.0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("replay", help="real commits as tasks in a sandbox, scored by their tests")
    p.add_argument("candidates", nargs="?", help="the candidates file (docs/replay.md)")
    p.add_argument("--template", action="store_true", help="print an empty candidates file")
    p.add_argument("--out", help="folder of the runs (default: <candidates file>-replay)")
    p.add_argument("--parallel", type=int, default=3, help="candidates at a time (default 3)")
    p.add_argument("--max-usd", type=float, default=MAX_USD, help="cap per candidate (25)")
    p.add_argument("--max-hours", type=float, default=MAX_HOURS, help="cap per candidate (3)")
    p.add_argument("--only", action="append", default=[], metavar="NAME", help="this candidate")
    p.add_argument("--prepare-only", action="store_true", help="build and check the sandboxes")
    p.add_argument("--score-only", action="store_true", help="score the existing runs again")
    p.add_argument("--force", action="store_true", help="also candidates that have a score")
    p.add_argument(
        "--prune",
        action="store_true",
        help="after the score delete what can be rebuilt: environments, caches, the scored copy",
    )
