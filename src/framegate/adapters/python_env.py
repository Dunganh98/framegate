"""A failed package install in a Python environment: what to put back, which tests lack
a module."""

from __future__ import annotations

import pathlib
import re

from framegate.adapters.base import TestRun

_MISSING = re.compile(r"No module named ['\"]([\w.]+)['\"]")


def _name(pin: str) -> str:
    return re.sub(r"[-_.]+", "-", pin.split("==")[0]).lower()


def restore_commands(python: str, gone: set[str], new: set[str]) -> list[list[str]]:
    """Uninstall the packages that are only in `new`, reinstall the `gone` pins."""
    back = {_name(p) for p in gone}
    extra = sorted(p.split("==")[0] for p in new if _name(p) not in back)
    pip = [python, "-m", "pip"]
    cmds = [[*pip, "uninstall", "-y", *extra]] if extra else []
    if gone:
        cmds.append([*pip, "install", "--no-deps", "--force-reinstall", *sorted(gone)])
    return cmds


def _file(root: pathlib.Path, case: str) -> tuple[str, str]:
    """(the test file of a report name like `tests.test_x.TestA::test_b`, the rest)."""
    head, _, tail = case.partition("::")
    parts = head.split(".")
    for k in range(len(parts), 0, -1):
        rel = "/".join(parts[:k]) + ".py"
        if (root / rel).is_file():
            rest = [*parts[k:], *([tail] if tail else [])]
            return rel, "::".join(re.split(r"[\[(]", x)[0] for x in rest)
    return "", ""


def missing_module_tests(root: pathlib.Path, run: TestRun) -> dict[str, str]:
    """Entry -> module. A file all of whose failures lack a module is one entry."""
    red = [c for c in run.cases or [] if c.outcome in ("failed", "error")]
    by_file: dict[str, list[tuple[str, str]]] = {}
    for c in red:
        rel, rest = _file(root, c.name)
        m = _MISSING.search(c.message)
        if rel:
            by_file.setdefault(rel, []).append((rest, m.group(1).split(".")[0] if m else ""))
    out: dict[str, str] = {}
    for rel, found in by_file.items():
        if all(mod for _, mod in found):
            out[rel] = found[0][1]
        else:
            out.update({f"{rel}::{rest}": mod for rest, mod in found if mod and rest})
    return out
