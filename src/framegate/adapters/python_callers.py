"""Which tests of a Python test file reach a name: in their own code, or through a helper
or fixture of the same file."""

from __future__ import annotations

import ast

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _refs(func: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
    return out


def tests_naming(text: str, names: set[str]) -> list[str]:
    """`Class::test` / `test` per test function that reaches one of `names` ([] when the
    text does not parse)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    funcs: list[tuple[str, str, set[str]]] = []

    def collect(node: ast.AST, cls: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNCS):
                funcs.append((cls, child.name, _refs(child)))
            elif isinstance(child, ast.ClassDef):
                collect(child, child.name)

    collect(tree, "")
    reached = set(names)
    while True:
        more = {name for _, name, refs in funcs if refs & reached} - reached
        if not more:
            break
        reached |= more
    return [
        f"{cls}::{name}" if cls else name
        for cls, name, refs in funcs
        if name.startswith("test") and refs & reached
    ]
