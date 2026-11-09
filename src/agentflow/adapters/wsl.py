"""WSL helpers: run Linux-only tools (mutmut) from native Windows."""

from __future__ import annotations

import os
import pathlib
import re
import shutil
from collections.abc import Sequence

from agentflow.adapters.base import run_command


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


def probe(root: pathlib.Path, distro: str | None) -> tuple[str | None, str]:
    """Check that WSL can run python3 for this repo. Returns (WSL path of root, problem)."""
    if wsl_exe() is None:
        return None, "wsl.exe not found (WSL is not installed)"
    code, out = run_command(wsl_command(["wslpath", "-a", str(root)], distro), root, timeout=120)
    if code != 0:
        name = distro or "the default distribution"
        return None, f"WSL could not start {name} (exit {code}): {_clean(out)[-300:]}"
    wsl_root = _clean(out).strip().splitlines()[-1].strip() if out.strip() else ""
    if not wsl_root.startswith("/"):
        wsl_root = fallback_path(root)
    code, out = run_command(wsl_command(["python3", "--version"], distro), root, timeout=120)
    if code != 0:
        return None, "python3 not found in WSL (install it in the distribution)"
    return wsl_root, ""


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
