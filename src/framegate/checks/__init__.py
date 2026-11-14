"""Frame checks. Each returns a CheckResult (PASS / FAIL / SKIPPED + output text)."""

from __future__ import annotations

import hashlib
import re

_DURATION = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b")


def check_signature(output: str) -> str:
    """What a failed test check found, as a short digest: its problem lines (the tests and IDs
    it names), without the first line (a count that moves with every added test), without
    durations and in any order. Two runs with the same digest failed on the same things."""
    lines = [" ".join(_DURATION.sub("", x).split()) for x in output.strip().splitlines()]
    found = sorted({x for x in (lines[1:] if len(lines) > 1 else lines) if x})
    return hashlib.sha256("\n".join(found).encode("utf-8")).hexdigest()[:16]
