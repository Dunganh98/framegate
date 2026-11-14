"""Python parts of a port (ported.py): the stub of a ported code file, the imports of such a
stub that will not resolve here, and the test functions of a test file.

The stub is the reference file's own text: module / class / function docstrings, signatures,
annotations, decorators, constants, imports and every other module-level statement byte for
byte; only the bodies of functions and methods are replaced by `raise NotImplementedError`
(the docstring stays). Seen live (da-argos, a runner ported from da-otto): stubs written by
hand in this repository's stub style (own wording, docstrings naming spec IDs) were kept by
the implementer, so "unchanged" files differed from the reference and the review found it.

What stays whole, and is said so:
- a function whose body does nothing already (`...`, `pass`, `raise NotImplementedError`: an
  overload, a protocol or abstract method);
- a function that runs while the module is imported (called at module or class level, used
  as a decorator, or called by such a function): with its body replaced the stub could not be
  imported, and the red check needs tests that can be collected.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from collections.abc import Iterator, Sequence

RAISE = b"raise NotImplementedError"
_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
_Func = ast.FunctionDef | ast.AsyncFunctionDef


def _outer_functions(tree: ast.AST) -> list[_Func]:
    """Functions and methods that are not inside another function, in file order."""
    out: list[_Func] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNC):
                out.append(child)
            else:
                walk(child)

    walk(tree)
    return out


def _is_main_guard(node: ast.AST) -> bool:
    """`if __name__ == "__main__":` runs for a script, not on import."""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    left = node.test.left
    return isinstance(left, ast.Name) and left.id == "__name__"


def _import_time(node: ast.AST) -> Iterator[ast.AST]:
    """The nodes that run when the module is imported: everything but function and lambda
    bodies (a function's decorators and argument defaults do run)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Lambda) or _is_main_guard(child):
            continue
        if isinstance(child, _FUNC):
            for part in (*child.decorator_list, *child.args.defaults, *child.args.kw_defaults):
                if part is not None:
                    yield part
                    yield from _import_time(part)
            continue
        yield child
        yield from _import_time(child)


def _called(nodes: Iterator[ast.AST] | Sequence[ast.AST]) -> set[str]:
    """Names that are called (`f()`, `x.f()`) among the nodes."""
    out: set[str] = set()
    for n in nodes:
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                out.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                out.add(n.func.attr)
    return out


def _decorator_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (*_FUNC, ast.ClassDef)):
            out |= {d.id for d in n.decorator_list if isinstance(d, ast.Name)}
    return out


def _runs_at_import(tree: ast.AST, funcs: list[_Func]) -> set[str]:
    """Names of the functions that run while the module is imported, and of those they call."""
    names = {f.name for f in funcs}
    wanted = (_called(_import_time(tree)) | _decorator_names(tree)) & names
    while True:
        more = {
            name for f in funcs if f.name in wanted for name in _called(list(ast.walk(f))) & names
        }
        if more <= wanted:
            return wanted
        wanted |= more


def _docstring(fn: _Func) -> ast.stmt | None:
    first = fn.body[0]
    is_doc = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    return first if is_doc else None


def _does_nothing(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr):
        return isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def stub_source(source: bytes) -> tuple[bytes, list[str]]:
    """(the stub, notes) of a Python file's text; raises SyntaxError (or ValueError) when
    the text cannot be parsed, before or after."""
    bom = b"\xef\xbb\xbf" if source.startswith(b"\xef\xbb\xbf") else b""
    text = source[len(bom) :]
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    eol = b"\r\n" if b"\r\n" in text else b"\n"

    def content(lineno: int) -> bytes:
        return lines[lineno - 1].rstrip(b"\r\n")

    funcs = _outer_functions(tree)
    kept = _runs_at_import(tree, funcs)
    edits: list[tuple[int, int, bytes]] = []
    trivial = 0
    for fn in funcs:
        doc = _docstring(fn)
        rest = fn.body[1:] if doc else fn.body
        if fn.name in kept:
            continue
        if rest and all(_does_nothing(s) for s in rest):
            trivial += 1
            continue
        if not rest:  # a docstring only: the raise goes under it
            assert doc is not None and doc.end_lineno is not None
            at = starts[doc.end_lineno - 1] + (doc.end_col_offset or 0)
            indent = content(doc.lineno)[: doc.col_offset]
            new = eol + indent + RAISE if not indent.strip() else b"; " + RAISE
            edits.append((at, at, new))
            continue
        first, last = rest[0], rest[-1]
        lineno, col = first.lineno, first.col_offset
        decorators = getattr(first, "decorator_list", None)
        if decorators:  # the statement starts at its first decorator's `@`
            lineno = decorators[0].lineno
            line = content(lineno)
            col = len(line) - len(line.lstrip())
        assert last.end_lineno is not None
        end = starts[last.end_lineno - 1] + len(content(last.end_lineno))
        edits.append((starts[lineno - 1] + col, end, RAISE))
    out = text
    for start, end, new in sorted(edits, reverse=True):
        out = out[:start] + new + out[end:]
    ast.parse(out)
    notes = [f"{len(edits)} function bodies replaced"]
    if kept:
        notes.append(
            "kept whole, they run when the module is imported: "
            + ", ".join(sorted(kept))
            + " (a test of them passes before the implementation: list it under 'Green "
            "against stubs', or it fails the red check)"
        )
    if trivial:
        notes.append(f"{trivial} left as they are (the body does nothing in the reference)")
    return bom + out, notes


# ---------- imports of a stub ----------
def _dotted(path: str) -> str:
    """`platform/runner/common.py` -> `platform.runner.common` (a package's __init__: the
    package)."""
    parts = path.replace("\\", "/").removesuffix(".py").split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


def _is_module(base: pathlib.Path, parts: Sequence[str]) -> bool:
    path = base.joinpath(*parts)
    return path.is_dir() or path.with_name(path.name + ".py").is_file()


def imported_modules(text: bytes) -> list[tuple[int, int, str]]:
    """(line, relative level, module) of every import statement of a Python text; level 0 =
    absolute. `from . import x` gives the module `x`."""
    out: list[tuple[int, int, str]] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            out += [(node.lineno, 0, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append((node.lineno, node.level, node.module))
            else:
                out += [(node.lineno, node.level, a.name) for a in node.names]
    return sorted(out)


def import_problems(
    root: pathlib.Path,
    bases: Sequence[pathlib.Path],
    target: str,
    text: bytes,
    ported: dict[str, str],
    installed: set[str] | None,
) -> list[str]:
    """Imports of the stub `target` (repo-relative) that will not resolve in this repository.
    `bases`: the folders absolute imports start from; `ported`: reference path -> target path
    of the port's code files; `installed`: the top-level names the project's interpreter can
    import (None: not known, only local and standard-library names are checked)."""
    problems: list[str] = []
    stdlib = set(sys.stdlib_module_names)
    sources = {_dotted(src): dst for src, dst in ported.items() if src.endswith(".py")}
    here = (root / target).parent
    for line, level, module in imported_modules(text):
        parts = module.split(".")
        where = f"{target}:{line}: `{'.' * level}{module}`"
        if level:
            base = here
            for _ in range(level - 1):
                base = base.parent
            if not _is_module(base, parts):
                problems.append(
                    f"{where}: no such module next to the target (not ported yet? add its "
                    "row to 'Ported from', or change the import and list it under 'What "
                    "may differ')"
                )
            continue
        if any(_is_module(base, parts) for base in bases):
            continue
        moved = next(
            (dst for src, dst in sources.items() if src == module or src.endswith("." + module)),
            None,
        )
        if moved:
            problems.append(
                f"{where}: the reference's module is ported here as {moved}: change the "
                "import to this repository's name ('What may differ': import names)"
            )
        elif parts[0] in stdlib or (installed is not None and parts[0] in installed):
            continue
        elif any(_is_module(base, parts[:1]) for base in bases):
            problems.append(f"{where}: no such module in this repository's `{parts[0]}`")
        else:
            problems.append(
                f"{where}: not found in this repository"
                + ("" if installed is None else " or its environment")
                + " (a module that is not ported, or a dependency that is not installed: port "
                "it, change the import, or leave the import out and list it under 'What may "
                "differ')"
            )
    return problems


# ---------- the tests of a test file ----------
def test_names(source: bytes) -> list[str]:
    """The test functions of a test file's text, in file order: `test_x`, `TestA::test_x`."""
    out: list[str] = []

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, _FUNC) and node.name.startswith("test"):
                out.append(prefix + node.name)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}::")

    walk(ast.parse(source).body, "")
    return out
