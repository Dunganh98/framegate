"""What a Python project's own files say about its tools and layout, for `framegate probe`
(the python adapter's part). Only files are read here; nothing is run.

Seen live (da-argos): the project formats with black and lints with `python -m ruff`, the
setup took framegate's defaults (ruff format) without a word, and the agents were told
commands the project does not use.
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

#: top-level folders that are never a source folder
NOT_SOURCE = {"tests", "test", "docs", "doc", "build", "dist", "venv", "env", "node_modules"}
NOT_SOURCE |= {"out", "specs", "metrics", "site-packages", "__pycache__"}

#: asked of the project interpreter in one process: its version, whether it has pip, and
#: the distributions installed in editable mode (PEP 610 direct_url.json) with their folder
ENV_SCRIPT = """\
import importlib.metadata as m, importlib.util as u, json, sys
found = []
for d in m.distributions():
    try:
        info = json.loads(d.read_text("direct_url.json") or "{}")
    except Exception:
        continue
    if info.get("dir_info", {}).get("editable"):
        found.append([str(d.metadata["Name"]), info.get("url", "")])
print(json.dumps({"version": sys.version.split()[0], "pip": bool(u.find_spec("pip")),
                  "editable": found}))
"""


def _text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _pyproject(root: pathlib.Path) -> dict[str, Any]:
    try:
        data: dict[str, Any] = tomllib.loads(_text(root / "pyproject.toml"))
    except tomllib.TOMLDecodeError:
        return {}
    return data


def _names(items: Any) -> set[str]:
    """Distribution names of a list of requirement strings (or a table keyed by name)."""
    if isinstance(items, dict):
        items = list(items)
    if not isinstance(items, list):
        return set()
    found = (re.match(r"\s*([A-Za-z0-9][\w.-]*)", x) for x in items if isinstance(x, str))
    return {m.group(1).lower().replace("_", "-") for m in found if m}


def dependency_names(root: pathlib.Path, data: dict[str, Any]) -> dict[str, str]:
    """Every dependency the project's files name (lower case) -> the file that names it:
    pyproject.toml (project, optional, dependency groups, uv / poetry / pdm dev lists) and
    requirements*.txt in the root and in `requirements/`."""
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    lists: list[Any] = [project.get("dependencies")]
    for key in ("optional-dependencies",):
        lists += list((project.get(key) or {}).values())
    lists += list((data.get("dependency-groups") or {}).values())
    lists.append((tool.get("uv") or {}).get("dev-dependencies"))
    poetry = tool.get("poetry") or {}
    lists += [poetry.get("dependencies"), poetry.get("dev-dependencies")]
    lists += [g.get("dependencies") for g in (poetry.get("group") or {}).values()]
    lists += list(((tool.get("pdm") or {}).get("dev-dependencies") or {}).values())
    out: dict[str, str] = {}
    for items in lists:
        for name in _names(items):
            out.setdefault(name, "pyproject.toml dependencies")
    files = sorted(root.glob("requirements*.txt")) + sorted(root.glob("requirements/*.txt"))
    for path in files:
        for name in _names(_text(path).splitlines()):
            out.setdefault(name, path.relative_to(root).as_posix())
    return out


def _section(root: pathlib.Path, name: str, header: str) -> str:
    """`<file> [<header>]` when the ini file has that section, else ""."""
    return f"{name} [{header}]" if f"[{header}]" in _text(root / name) else ""


def evidence(root: pathlib.Path) -> dict[str, str]:
    """Tool -> where the project names it (the first of: its own config file or section, a
    pre-commit hook, a dependency). A tool the project does not name is not in the result."""
    data = _pyproject(root)
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    deps = dependency_names(root, data)
    hooks = _text(root / ".pre-commit-config.yaml")

    def first(*found: str) -> str:
        return next((f for f in found if f), "")

    def file(name: str) -> str:
        return name if (root / name).is_file() else ""

    def table(*keys: str) -> str:
        node: Any = tool
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        return f"pyproject.toml [tool.{'.'.join(keys)}]" if node is not None else ""

    def hook(pattern: str) -> str:
        return ".pre-commit-config.yaml" if re.search(pattern, hooks) else ""

    ruff_toml = file("ruff.toml") or file(".ruff.toml")
    ruff_format = table("ruff", "format") or hook(r"id:\s*ruff-format")
    if not ruff_format and ruff_toml and "[format]" in _text(root / ruff_toml):
        ruff_format = f"{ruff_toml} [format]"
    found = {
        "pytest": first(
            file("pytest.ini"),
            table("pytest", "ini_options"),
            _section(root, "setup.cfg", "tool:pytest"),
            _section(root, "tox.ini", "pytest"),
            deps.get("pytest", ""),
            file("conftest.py") or file("tests/conftest.py"),
        ),
        "ruff": first(ruff_toml, table("ruff"), hook(r"ruff-pre-commit"), deps.get("ruff", "")),
        "ruff format": ruff_format,
        "black": first(table("black"), hook(r"psf/black|id:\s*black"), deps.get("black", "")),
        "flake8": first(
            file(".flake8"),
            _section(root, "setup.cfg", "flake8"),
            _section(root, "tox.ini", "flake8"),
            deps.get("flake8", ""),
        ),
        "pylint": first(
            file(".pylintrc"), file("pylintrc"), table("pylint"), deps.get("pylint", "")
        ),
        "mypy": first(
            file("mypy.ini"),
            file(".mypy.ini"),
            table("mypy"),
            _section(root, "setup.cfg", "mypy"),
            hook(r"mirrors-mypy"),
            deps.get("mypy", ""),
        ),
        "pyright": first(file("pyrightconfig.json"), table("pyright"), deps.get("pyright", "")),
    }
    return {name: where for name, where in found.items() if where}


def detected_tools(root: pathlib.Path) -> dict[str, tuple[list[str], str]]:
    """Per role (`test`, the gate keys `lint` / `format` / `typecheck`, and `format_fix`):
    the command the project's files point to, as it is written in framegate.toml ("python" =
    the project interpreter, `{src}` / `{tests}` / `{files}`), and the file that says so.
    black wins over ruff as the formatter unless the project configures `ruff format`."""
    ev = evidence(root)
    py = ["python", "-m"]
    out: dict[str, tuple[list[str], str]] = {}
    if "pytest" in ev:
        out["test"] = ([*py, "pytest"], ev["pytest"])
    if "ruff" in ev:
        out["lint"] = ([*py, "ruff", "check", "{src}", "{tests}"], ev["ruff"])
    elif "flake8" in ev:
        out["lint"] = ([*py, "flake8", "{src}", "{tests}"], ev["flake8"])
    elif "pylint" in ev:
        out["lint"] = ([*py, "pylint", "{src}"], ev["pylint"])
    if "black" in ev and "ruff format" not in ev:
        out["format"] = ([*py, "black", "--check", "{src}", "{tests}"], ev["black"])
        out["format_fix"] = ([*py, "black", "{files}"], ev["black"])
    elif "ruff format" in ev or "ruff" in ev:
        where = ev.get("ruff format") or ev["ruff"]
        out["format"] = ([*py, "ruff", "format", "--check", "{src}", "{tests}"], where)
    if "mypy" in ev:
        out["typecheck"] = ([*py, "mypy", "{src}"], ev["mypy"])
    elif "pyright" in ev:
        out["typecheck"] = ([*py, "pyright", "{src}"], ev["pyright"])
    return out


def _testpaths(root: pathlib.Path) -> list[str]:
    tool = _pyproject(root).get("tool", {})
    options = (tool.get("pytest") or {}).get("ini_options") if isinstance(tool, dict) else None
    paths = (options or {}).get("testpaths")
    if isinstance(paths, str):
        paths = paths.split()
    if not paths:
        m = re.search(r"^testpaths\s*=\s*(.+)$", _text(root / "pytest.ini"), re.M)
        paths = m.group(1).split() if m else []
    return [str(p).strip("/") for p in paths if isinstance(p, str)]


def guess_layout(root: pathlib.Path) -> tuple[list[str], str]:
    """(source folders, test folder) of a project with no framegate.toml yet: the first
    `testpaths` folder of the pytest config, else `tests` / `test`; `src` when it exists,
    else the top-level folders that are packages (hold an `__init__.py`)."""
    listed = [p for p in _testpaths(root) if (root / p).is_dir()]
    tests = next((p for p in [*listed, "tests", "test"] if (root / p).is_dir()), "tests")
    if (root / "src").is_dir():
        return ["src"], tests
    packages = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir()
        and not d.name.startswith((".", "_"))
        and d.name.lower() not in NOT_SOURCE
        and d.name != tests.split("/")[0]
        and (d / "__init__.py").is_file()
    )
    return packages or ["src"], tests
