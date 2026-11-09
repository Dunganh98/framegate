"""Python final gates with real mutmut through WSL (slow; `uv run pytest -m slow`).

Runs the Python e2e fixture (tests/conftest.py `demo_repo`, fake claude) to DONE with
`[gates] mutation_runner = "wsl"`: the WSL venv is created under ~/.cache/agentflow/venvs/,
mutmut runs on /mnt/<drive>/..., and the score + survivors land in final_gates.md /
mutation_survivors.md. A second `agentflow gates --scope final` reuses the venv. Skipped when
WSL with python3 is not available."""

from __future__ import annotations

import hashlib
import os
import re
import time

import pytest

from agentflow.adapters import wsl
from agentflow.adapters.base import run_command
from conftest import DemoRepo, git

pytestmark = pytest.mark.slow


def test_final_gates_mutation_through_wsl(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    wsl_root, problem = wsl.probe(r.root, None) if os.name == "nt" else (None, "not Windows")
    if wsl_root is None:
        pytest.skip(f"needs Windows with WSL and python3: {problem}")
    venv = f"~/.cache/agentflow/venvs/{hashlib.sha256(wsl_root.encode()).hexdigest()[:16]}"
    r.set_toml("gates", "mutation_runner", '"wsl"')
    try:
        assert r.af("approve-spec", "demo") == 0
        assert r.af("run", "demo") == 10
        assert r.af("approve", "demo") == 0
        t0 = time.time()
        assert r.af("run", "demo") == 0  # IMPLEMENT -> REVIEW -> final gates -> PR -> RETRO
        flow = time.time() - t0
        final = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
        assert "[PASS] mutation score" in final, final
        cold = next(x.strip() for x in final.splitlines() if "Mutation score" in x)
        assert re.search(r"Mutation score \d+\.\d% \(\d+/\d+ killed\)", cold), final
        assert "WSL" in cold and "venv reused" not in cold
        survivors = (r.root / "out/demo/mutation_survivors.md").read_text(encoding="utf-8")
        assert survivors.startswith("# Mutation survivors")
        assert "```diff" in survivors and "__mutmut_" in survivors, survivors  # real diffs
        assert not (r.root / "mutants").exists()
        tracked = git(r.root, "ls-files")
        assert "mutants/" not in tracked and "out/.mutmut" not in tracked

        capsys.readouterr()
        t0 = time.time()
        assert r.af("gates", "demo", "--scope", "final") == 0
        gates = time.time() - t0
        warm = next(
            x.strip() for x in capsys.readouterr().out.splitlines() if "Mutation score" in x
        )
        assert "venv reused" in warm, warm
        with capsys.disabled():
            print(f"\n[wsl mutation] cold: run to DONE {flow:.0f} s; {cold}")
            print(f"[wsl mutation] warm: all final gates {gates:.0f} s; {warm}")
    finally:
        run_command(wsl.wsl_command(["sh", "-c", f"rm -rf {venv}"]), r.root, timeout=120)
