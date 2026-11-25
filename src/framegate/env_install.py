"""`framegate setup --apply --install <requirement>`: a package the tests lack.

The install runs in the project's environment. When it fails, nobody is sent to repair
the machine: packages the failed install changed are put back, the tests that fail for a
missing module are listed in [gates] unrunnable_tests with the reason, and the setup goes on.
"""

from __future__ import annotations

from collections.abc import Callable

from framegate import suite_run, unrunnable
from framegate.adapters.base import LanguageAdapter, run_command

TIMEOUT = 1800


def _why(output: str) -> str:
    lines = [x.strip() for x in output.splitlines() if x.strip()]
    errors = [x for x in lines if x.lower().startswith("error")]
    return " ".join((errors or lines or ["no output"])[-1].split())[:160]


def restore(adapter: LanguageAdapter, before: set[str] | None) -> bool | None:
    """Put the packages back as `before`. None: nothing had changed; else: did it work."""
    after = adapter.installed_packages()
    if before is None or after is None or after == before:
        return None
    for cmd in adapter.restore_commands(before - after, after - before):
        run_command(cmd, adapter.cfg.root, env=adapter.tool_env(), timeout=TIMEOUT)
    return adapter.installed_packages() == before


def install(
    adapter: LanguageAdapter, requirement: str, echo: Callable[[str], None]
) -> dict[str, str]:
    """Install `requirement`. Returns the unrunnable entries (test -> reason) to list when
    it failed ({} = installed, or no test lacks a module)."""
    cmd = adapter.install_command(requirement)
    if cmd is None:
        echo(f"--install {requirement}: the {adapter.name} adapter cannot install a package.")
        return {}
    before = adapter.installed_packages()
    code, out = run_command(cmd, adapter.cfg.root, env=adapter.tool_env(), timeout=TIMEOUT)
    if code == 0:
        echo(f"Installed: {requirement}")
        return {}
    why = _why(out)
    put_back = restore(adapter, before)
    state = {None: "unchanged", True: "restored", False: "NOT restored (see `pip freeze`)"}
    run = suite_run.whole_suite(adapter, skip_known_red=False)
    lacking = {
        test: f"needs {module}: installing {requirement} failed on this machine ({why})"
        for test, module in adapter.missing_module_tests(run).items()
        if not unrunnable.entry_of(adapter.cfg, test)
    }
    echo(
        f"Install of {requirement} failed ({why}); the environment is {state[put_back]}; "
        f"{len(lacking)} test entry(ies) that lack a module are listed as unrunnable."
    )
    return lacking
