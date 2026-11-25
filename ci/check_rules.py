"""Ratchet check: per-file counts may not exceed ci/allowlist.txt, and the list may only shrink.

    python ci/check_rules.py ci/allowlist.txt <roots>            check (CI)
    python ci/check_rules.py ci/allowlist.txt <roots> --update   rewrite the list from today's state
    python ci/check_rules.py --rules                             print the rules

One list line per rule and file: ``file-size pkg/module.py 411``.
A file without a line has limit 0. A count below its line fails until the line is lowered.
"""

import ast
import io
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path

MAX_COMMENT, MAX_FILE = 2, 400
HISTORY = re.compile(
    r"\b(previously|formerly|originally|used to|no longer|any ?more"
    r"|until now|for now|by now|(is|are|has|have|does|do|will) now"
    r"|now (is|are|has|have|uses|returns|raises|lives|reads|writes|takes)"
    r"|(was|were|been) (fixed|moved|renamed|replaced|removed|added|changed)"
    r"|renamed (from|to)|bug ?fix|hotfix)\b",
    re.I,
)
TICKET = re.compile(r"\b(?!(?:UTF|SHA|ISO|RFC|UTC|CP|MD)-)[A-Z]{2,}-\d+\b")
SUPPRESS = re.compile(r"#\s*(noqa|type:\s*ignore|pragma:\s*no cover|fmt:\s*(off|skip))")
DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
SKIP = re.compile(r"pytest\.(skip|importorskip|xfail)\(|mark\.(skip|xfail)")
RULES = {
    "comment-block": f"at most {MAX_COMMENT} consecutive comment lines",
    "comment-history": "no history words (previously, no longer, was renamed) or ticket ids"
    " (ABC-123) in comments",
    "file-size": f"at most {MAX_FILE} lines per file; a longer file in the list may not grow",
    "suppression": "no new noqa, type: ignore, pragma: no cover, fmt: off/skip,"
    " pytest skip/importorskip/xfail",
    "docstring": "no new docstrings; the code and its names say what it does",
    "allow-list": "a count in ci/allowlist.txt may only go down; a file without a line has limit 0",
}


def docstrings(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    nodes = (n for n in ast.walk(tree) if isinstance(n, DOCUMENTED))
    return sum(ast.get_docstring(n) is not None for n in nodes)


def measure(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    c = Counter()
    comment_rows = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            if lines[tok.start[0] - 1].lstrip().startswith("#"):
                comment_rows.append(tok.start[0])
            prose = SUPPRESS.sub("", tok.string)
            c["suppression"] += bool(SUPPRESS.search(tok.string))
            c["comment-history"] += bool(HISTORY.search(prose) or TICKET.search(prose))
    run = 0
    for i, row in enumerate(comment_rows):
        run = run + 1 if i and row == comment_rows[i - 1] + 1 else 1
        c["comment-block"] += run == MAX_COMMENT + 1
    c["suppression"] += len(SKIP.findall(text))
    c["docstring"] = docstrings(text)
    c["file-size"] = len(lines) if len(lines) > MAX_FILE else 0
    return {k: v for k, v in c.items() if v}


def scan(roots):
    found = {}
    for root in roots:
        one = root.endswith(".py")
        files = [Path(root)] if one else sorted(Path(root).rglob("*.py"))
        for f in files:
            if "_pb2" in f.name or ".venv" in f.parts:
                continue
            for rule, n in measure(f).items():
                found[rule, f.as_posix()] = n
    return found


def main(allowlist, roots, update=False):
    found = scan(roots)
    if update:
        rows = "".join(f"{r} {p} {n}\n" for (r, p), n in sorted(found.items()))
        Path(allowlist).write_text(rows, encoding="utf-8", newline="\n")
        return 0
    allowed = {}
    if Path(allowlist).exists():
        for line in Path(allowlist).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rule, path, n = line.split()
                allowed[rule, path] = int(n)
    worse = [
        f"{r} {p}: {n} > {allowed.get((r, p), 0)}"
        for (r, p), n in sorted(found.items())
        if n > allowed.get((r, p), 0)
    ]
    stale = [
        f"{r} {p}: allow-list says {n}, is {found.get((r, p), 0)}: lower it"
        for (r, p), n in sorted(allowed.items())
        if found.get((r, p), 0) < n
    ]
    print("\n".join(worse + stale) or "check_rules: ok")
    return 1 if worse or stale else 0


def cli(argv):
    if "--rules" in argv:
        print("\n".join(f"{rule}: {text}" for rule, text in RULES.items()))
        return 0
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return 2
    return main(args[0], args[1:], update="--update" in argv)


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
