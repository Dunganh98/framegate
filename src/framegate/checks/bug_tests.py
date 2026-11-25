"""A bug report decides the existing tests of the buggy function up front.

design.md, "Where the bug is", names the code path as `<file>: <function>`. Every existing
test that reaches one of these functions (adapter.tests_reaching) is named in
requirements.md: under "Must not change" (it keeps passing), or in "Test data and fixtures"
(it changes; `(unchanged)` after one that stays). `framegate spec-check` lists the others.
"""

from __future__ import annotations

import re

from framegate.adapters.base import LanguageAdapter
from framegate.checks import named_tests
from framegate.specs import feature_ids, feature_mode

WHERE = "Where the bug is"
KEEP = "Must not change"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
SHOWN = 20


def _section(text: str, title: str) -> str:
    clean = _COMMENT.sub("", text)
    m = re.search(rf"^##\s+{re.escape(title)}\b.*$", clean, re.M | re.I)
    if not m:
        return ""
    rest = clean[m.end() :]
    end = re.search(r"^##\s", rest, re.M)
    return rest[: end.start()] if end else rest


def functions(adapter: LanguageAdapter, design: str) -> list[str]:
    """The function names of the `<source file>: <function>` places under "Where the bug is"."""
    suffixes = "|".join(re.escape(s) for s in adapter.source_suffixes) or r"\.\w+"
    place = re.compile(rf"[\w./\\-]*\w(?:{suffixes})`?\s*:\s*`?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
    found = [m.group(1).rsplit(".", 1)[-1] for m in place.finditer(_section(design, WHERE))]
    return list(dict.fromkeys(found))


def _word(name: str, text: str, whole: bool = False) -> bool:
    end = r"(?!\w)(?!::)" if whole else r"(?!\w)"
    return bool(re.search(rf"(?<!\w){re.escape(name)}{end}", text))


def spec_problems(adapter: LanguageAdapter, feature: str) -> list[str]:
    """Bug fix only: the existing tests of the buggy functions the spec does not name."""
    cfg = adapter.cfg
    if feature_mode(cfg, feature) != "bugfix":
        return []
    folder = cfg.spec_dir(feature)
    design = folder / "design.md"
    names = functions(adapter, design.read_text(encoding="utf-8") if design.is_file() else "")
    if not names:
        return []
    try:
        tests = adapter.tests_reaching(names)
        tags = adapter.collect_req_tags()
    except Exception:  # a test file the scan cannot read: the tests phase reports it
        return []
    own = {w for i in feature_ids(cfg, feature) for w in tags.get(i, [])}
    spec = folder / "requirements.md"
    keep = _section(spec.read_text(encoding="utf-8") if spec.is_file() else "", KEEP)
    change = [(rel, parts) for rel, parts, _ in named_tests.named(cfg, feature)]
    missing = []
    for test in tests:
        rel, *parts = test.split("::")
        if any(f"{rel}::{p}" in own for p in parts):
            continue  # a test of this task
        named = any(r == rel and tuple(parts[: len(p)]) == p for r, p in change)
        in_keep = _word(parts[-1], keep) or any(_word(c, keep, whole=True) for c in parts[:-1])
        if not named and not in_keep:
            missing.append(test)
    if not missing:
        return []
    more = f", and {len(missing) - SHOWN} more" if len(missing) > SHOWN else ""
    return [
        f"requirements.md: {len(missing)} existing test(s) reach {', '.join(names)} (design.md, "
        f'"{WHERE}") and the spec does not say what happens to them. Name each under "{KEEP}" '
        f'(it keeps passing) or in "{named_tests.SECTION}" (it changes; `(unchanged)` after '
        f"one that stays): {', '.join(missing[:SHOWN])}{more}"
    ]
