"""WSL helpers: run Linux-only tools (mutmut) from native Windows."""

from __future__ import annotations

import os
import pathlib
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass

from framegate.adapters.base import run_command


def wsl_exe() -> str | None:
    """Path of wsl.exe on Windows, else None."""
    if os.name != "nt":
        return None
    return shutil.which("wsl.exe") or shutil.which("wsl")


def wsl_command(argv: Sequence[str], distro: str | None = None) -> list[str]:
    """`wsl.exe [-d distro] -e <argv>` (no shell, arguments passed as they are)."""
    exe = wsl_exe() or "wsl.exe"
    return [exe, *(["-d", distro] if distro else []), "-e", *argv]


def fallback_path(path: str | os.PathLike[str]) -> str:
    r"""`D:\x\y` -> `/mnt/d/x/y` (the default automount root)."""
    p = str(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/?(.*)$", p)
    if not m:
        return p
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}".rstrip("/")


@dataclass
class Probe:
    """What the WSL probe found. `root` is None when mutmut cannot run through WSL on this
    machine; `problem` and `fix` then say why and what to do about it."""

    #: WSL path of the repo root
    root: str | None = None
    #: the distribution to run in (None = the default one)
    distro: str | None = None
    problem: str = ""
    fix: str = ""
    #: why a distribution other than the default one is used
    note: str = ""


#: Docker Desktop's own distributions: no Python, not made for user tools
INTERNAL_DISTROS = ("docker-desktop",)


def parse_distros(text: str) -> list[tuple[str, bool]]:
    """`wsl.exe -l -v` -> (name, is the default) per distribution. The header is localized,
    so it is skipped by position; a row ends with the WSL version number."""
    out = []
    for line in _clean(text).splitlines()[1:]:
        parts = line.replace("*", " ", 1).split()
        if len(parts) >= 3 and parts[-1].isdigit():
            out.append((parts[0], line.lstrip().startswith("*")))
    return out


def distros(root: pathlib.Path) -> list[tuple[str, bool]]:
    """The installed distributions ([] when WSL has none or cannot list them)."""
    exe = wsl_exe()
    if exe is None:
        return []
    code, out = run_command([exe, "-l", "-v"], root, timeout=120)
    return parse_distros(out) if code == 0 else []


def _try(root: pathlib.Path, distro: str | None) -> tuple[str | None, str]:
    """Can this distribution run python3 for the repo? Returns (WSL path of root, what is
    wrong with it: the end of a sentence that starts with the distribution)."""
    code, out = run_command(wsl_command(["wslpath", "-a", str(root)], distro), root, timeout=120)
    if code != 0:
        return None, f"{NO_START} (exit {code}): {' '.join(_clean(out).split())[-300:]}"
    wsl_root = _clean(out).strip().splitlines()[-1].strip() if out.strip() else ""
    if not wsl_root.startswith("/"):
        wsl_root = fallback_path(root)
    code, out = run_command(wsl_command(["python3", "--version"], distro), root, timeout=120)
    if code != 0:
        return None, "has no python3"
    return wsl_root, ""


APT = "sudo apt install python3 python3-venv"
NO_START = "could not start"


def probe(root: pathlib.Path, distro: str | None) -> Probe:
    """Find a WSL distribution that can run python3 for this repo. A configured distribution
    (`[env] wsl_distro`) is the only one tried. Without one the default distribution comes
    first; when it cannot (on a machine with Docker Desktop the default is often
    `docker-desktop`, which has no Python), the other installed ones are tried in the order
    WSL lists them, and the first with python3 is used. Read-only: nothing is installed."""
    if wsl_exe() is None:
        return Probe(
            problem="wsl.exe not found (WSL is not installed)",
            fix="install WSL with a distribution that has Python: wsl --install -d Ubuntu",
        )
    wsl_root, wrong = _try(root, distro)
    if wsl_root is not None:
        return Probe(wsl_root, distro)
    if distro:
        other = "distribution in [env] wsl_distro (wsl -l -v lists them)"
        return Probe(
            problem=f"the WSL distribution {distro} ([env] wsl_distro) {wrong}",
            fix=f"name an installed {other}"
            if wrong.startswith(NO_START)
            else f"install python3 in {distro} (Debian/Ubuntu: {APT}), or name another {other}",
        )
    found = distros(root)
    default = next((name for name, is_default in found if is_default), "")
    problem = f"the default WSL distribution{f' ({default})' if default else ''} {wrong}"
    others = [n for n, is_default in found if not is_default and not n.startswith(INTERNAL_DISTROS)]
    for name in others:
        other_root, _ = _try(root, name)
        if other_root is not None:
            return Probe(other_root, name, note=problem)
    if others:
        tried = ", ".join(others)
        return Probe(
            problem=f"{problem}, and no other one can run python3 either (tried: {tried})",
            fix=f"install python3 in {others[0]} (Debian/Ubuntu: wsl -d {others[0]} -e {APT})",
        )
    return Probe(
        problem=f"{problem}, and no other distribution is installed",
        fix="install a distribution with Python: wsl --install -d Ubuntu",
    )


def _clean(text: str) -> str:
    """wsl.exe's own messages are UTF-16; drop the NULs they leave in decoded text."""
    return text.replace("\x00", "")


def to_wsl(path: pathlib.Path, root: pathlib.Path, wsl_root: str, distro: str | None) -> str:
    """WSL path of `path`: relative to the already translated repo root when inside it, else
    via `wslpath -a` (fallback: /mnt/<drive>/...)."""
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        return wsl_root if rel == "." else wsl_root.rstrip("/") + "/" + rel
    except ValueError:
        pass
    code, out = run_command(wsl_command(["wslpath", "-a", str(path)], distro), root, timeout=120)
    text = _clean(out).strip()
    if code == 0 and text.startswith("/"):
        return text.splitlines()[-1].strip()
    return fallback_path(path)
