from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess
import sys
from dataclasses import dataclass

from framegate.adapters import get_adapter
from framegate.adapters.base import LanguageAdapter, changed_lines
from framegate.config import Config, load_config
from framegate.state import GitError, git

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "origin/main"
CAP = 15
USAGE = (
    "Prints the test files that reach the code changed against origin/main: changed test "
    "files, tests that name a changed function or class, tests that import a changed module "
    "(directly, or through one module of this repository). --run runs them, one pytest call "
    "per file."
)


@dataclass
class Hit:
    file: str
    changed: bool = False
    direct: bool = False
    near: bool = False
    tests: int = 0
    loose: int = 0

    def key(self) -> tuple[bool, bool, bool, bool, bool, int, str]:
        named = self.tests > 0
        tiers = (self.changed, self.direct and named, self.direct, self.near and named)
        return (*(not x for x in tiers), not self.loose, -self.tests - self.loose, self.file)

    def why(self) -> str:
        parts = ["changed"] if self.changed else []
        parts += ["imports a changed module"] if self.direct else []
        parts += ["one import from a changed module"] if self.near and not self.direct else []
        parts += [f"{self.tests} test(s) name changed code"] if self.tests else []
        parts += [f"{self.loose} test(s) use a changed name, no import found"] if self.loose else []
        return ", ".join(parts)


def changed_files(cfg: Config, base: str) -> list[str]:
    diff = git(cfg, "-c", "core.quotepath=off", "diff", "--name-only", base)
    new = git(cfg, "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard")
    return sorted({x for x in (diff + new).splitlines() if x and (cfg.root / x).is_file()})


def module_name(rel: str, roots: list[str]) -> str:
    for root in roots:
        if rel.startswith(root.rstrip("/") + "/") and rel.endswith(".py"):
            parts = rel[len(root.rstrip("/")) + 1 : -3].split("/")
            return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    return ""


def imported(text: str, package: str = "") -> set[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*up, base] if base else up)
            out |= {base, *(f"{base}.{a.name}" for a in node.names)}
    return out


def is_test(cfg: Config, rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    under = rel.startswith(cfg.paths.tests.rstrip("/") + "/")
    return under and name.startswith("test_") and name.endswith(".py")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def importers(cfg: Config, mods: set[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {m: set() for m in mods}
    for root in cfg.src_roots:
        for p in (cfg.root / root).rglob("*.py"):
            mod = module_name(p.relative_to(cfg.root).as_posix(), cfg.src_roots)
            package = mod if p.name == "__init__.py" else mod.rpartition(".")[0]
            for m in imported(_read(p), package) & mods:
                out[m].add(mod)
    return out


def reach(cfg: Config, test: str, code: list[str], near: dict[str, set[str]]) -> dict[str, str]:
    text = _read(cfg.root / test)
    uses = imported(text)
    folder = pathlib.PurePosixPath(test).parent
    for up in (folder, *folder.parents):
        conftest = cfg.root / up / "conftest.py"
        uses |= imported(_read(conftest)) if conftest.is_file() else set()
    out: dict[str, str] = {}
    for f in code:
        mod, name = module_name(f, cfg.src_roots), f.rsplit("/", 1)[-1]
        if mod:
            how = "direct" if mod in uses else "near" if uses & near[mod] else ""
        elif name == "conftest.py":
            how = "near" if test.startswith(f.rsplit("/", 1)[0] + "/") else ""
        else:
            how = "direct" if name in text or name[:-3] in uses else ""
        if how:
            out[f] = how
    return out


def select(adapter: LanguageAdapter, base: str = BASE) -> tuple[list[Hit], list[str]]:
    cfg = adapter.cfg
    changed = changed_files(cfg, base)
    tests = sorted(
        p.relative_to(cfg.root).as_posix()
        for p in (cfg.root / cfg.paths.tests).rglob("test_*.py")
        if "__pycache__" not in p.parts
    )
    code = [f for f in changed if f.endswith(".py") and not is_test(cfg, f)]
    near = importers(cfg, {m for f in code if (m := module_name(f, cfg.src_roots))})
    seen = {t: reach(cfg, t, code, near) for t in tests}
    hits = {t: Hit(t, changed=t in changed) for t in tests}
    for t, how in seen.items():
        hits[t].direct = "direct" in how.values()
        hits[t].near = "near" in how.values()
    named: dict[str, dict[str, bool]] = {t: {} for t in tests}
    for f in code:
        names = adapter.changed_symbols(f, changed_lines(cfg, base, f))
        for node in adapter.tests_reaching(names):
            t = node.split("::", 1)[0]
            if t in named and (f in seen[t] or module_name(f, cfg.src_roots)):
                named[t][node] = named[t].get(node, False) or f in seen[t]
    for t, nodes in named.items():
        hits[t].tests = sum(nodes.values())
        hits[t].loose = len(nodes) - hits[t].tests
    found = sorted((h for h in hits.values() if h.why()), key=Hit.key)
    other = [f for f in changed if not f.endswith(".py")]
    notes = [f"not traced ({len(other)} file(s) that are not Python): {', '.join(other)}"]
    return found, notes if other else []


def cut(hits: list[Hit], cap: int) -> tuple[list[Hit], str]:
    if len(hits) <= cap:
        return hits, ""
    left = ", ".join(h.file for h in hits[cap:])
    return hits[:cap], f"left out {len(hits) - cap} file(s) over the cap of {cap}: {left}"


def run(files: list[str], root: pathlib.Path) -> int:
    red = []
    for f in files:
        cmd = [sys.executable, "-m", "pytest", f, "-q", "-m", "not slow", "-p", "no:cacheprovider"]
        if subprocess.run(cmd, cwd=root).returncode != 0:
            red.append(f)
    print(
        f"{len(files) - len(red)} of {len(files)} file(s) green"
        + "".join(f"\nRED {f}" for f in red)
    )
    return 1 if red else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="affected_tests.py", description=USAGE)
    p.add_argument("--base", default=BASE)
    p.add_argument("--cap", type=int, default=CAP, help=f"most files (default {CAP})")
    p.add_argument("--run", action="store_true", help='run each file: pytest -m "not slow"')
    args = p.parse_args(argv)
    adapter = get_adapter(load_config(ROOT, require=False))
    try:
        hits, notes = select(adapter, args.base)
    except GitError as e:
        print(e, file=sys.stderr)
        return 2
    kept, message = cut(hits, args.cap)
    for h in kept:
        print(f"{h.file}  ({h.why()})")
    for line in [message, *notes]:
        if line:
            print(line, file=sys.stderr)
    return run([h.file for h in kept], ROOT) if args.run else 0


if __name__ == "__main__":
    sys.exit(main())
