"""Doc comments of a source text: Python docstrings, and runs of `///`-style lines."""

from __future__ import annotations

import ast
from dataclasses import dataclass

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class Doc:
    """One doc comment: the member it documents and its lines (1-based, inclusive)."""

    owner: str
    first: int
    last: int
    text: str


def python_docs(text: str) -> list[Doc] | None:
    """The docstrings of a Python source text (None: it does not parse)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    out: list[Doc] = []

    def visit(node: ast.AST, name: str, owner: bool) -> None:
        body = getattr(node, "body", None) or []
        first = body[0] if owner and body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.append(Doc(name, first.lineno, first.end_lineno or first.lineno, first.value.value))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFS):
                visit(child, f"{name}.{child.name}" if name else child.name, True)
            elif not isinstance(child, ast.expr):
                visit(child, name, False)

    visit(tree, "", True)
    return out


def line_docs(text: str, prefix: str) -> list[Doc]:
    """Runs of lines that start with `prefix` (`///`): owner = the declaration after them."""
    lines = text.splitlines()
    out: list[Doc] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith(prefix):
            i += 1
            continue
        start = i
        while i < len(lines) and lines[i].lstrip().startswith(prefix):
            i += 1
        k = i
        while k < len(lines) and (not lines[k].strip() or lines[k].lstrip().startswith("[")):
            k += 1
        owner = " ".join(lines[k].split()) if k < len(lines) else ""
        body = "\n".join(x.lstrip()[len(prefix) :].strip() for x in lines[start:i])
        out.append(Doc(owner, start + 1, i, body))
    return out
