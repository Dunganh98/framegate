# Agentic Development Workflow — Setup Guide

Spec-driven, test-first workflow for Claude Code, with deterministic gates. You approve the spec and the test summary; everything between those gates runs autonomously, and every agent works inside a "frame" of scripts that return pass/fail.

---

## How to use this file

1. Put this file in your repo at `docs/AGENTIC_WORKFLOW_SETUP.md`.
2. Start Claude Code in the repo root and say:

   > Read `docs/AGENTIC_WORKFLOW_SETUP.md` and execute Part 3 step by step. After each step, show me what you created and wait for my OK. Adapt paths to this repo's layout and ask before changing any existing config.

3. Once setup is done, use Part 4 for daily work.
4. Parts 8 and 9 are **plans for later**, not part of the setup. Part 9 lists the next improvements, adopted once the baseline runs well. Part 8 is extracting the workflow into a reusable framework, which comes after Part 9. Don't execute either until its trigger conditions are met.

**Note for Claude Code (executing the setup):** Some CLI flags, tool names and hook fields are version-dependent. Verify them against the installed version (`claude --help`, the hooks docs) before relying on them. Every place that needs verification is marked **[VERIFY]**.

---

## Part 1 — Principles

1. **Specs are the source of truth.** Every requirement, edge case and invariant has an ID. A requirement without an ID doesn't exist.
2. **The verifier is independent of the implementer.** Tests are written from the spec and stubs only, approved by a human, then frozen. Implementers cannot change them.
3. **Rules are checks, not prompts.** If a rule can be checked mechanically, it lives in `scripts/`, a hook, or CI, not in CLAUDE.md. CLAUDE.md is only for what can't be checked.
4. **State lives in files, not conversations.** Each phase and step is a fresh `claude -p` process. Context never accumulates, so nothing depends on auto-compact.
5. **Ambiguity means stop and ask, never guess.** Headless agents write questions to `out/<feature>/questions.md` and end. The orchestrator pauses for you.
6. **Every human correction becomes data.** Gate rejections, escalations and gate failures are logged. A retro agent turns recurring problems into new checks.

---

## Part 2 — Architecture

### Phases

| Phase | Who | Writes to | Ends with |
|---|---|---|---|
| SPEC | Interactive session (`/spec`) | `specs/<feature>/`, interface stubs in `src/` | **You approve** → tag `spec-approved/<feature>` |
| TESTS | Headless test-writer | `tests/`, `out/` | Red-against-stubs + traceability + smell checks → **you approve the summary** → tag `tests-approved/<feature>` |
| IMPLEMENT | Headless implementer, one fresh run per plan step | `src/`, `out/` | Step gates green → commit, next step |
| REVIEW | Headless adversarial reviewer | `tests/review/`, `out/` | Failing tests for findings → you triage → implementer fixes |
| PR | Headless | `out/` | `out/<feature>/pr.md`, final gate report |
| RETRO | Headless | `out/` | `out/<feature>/retro_proposals.md` + metrics row |

### Enforcement layers

| Layer | Mechanism | Guarantee |
|---|---|---|
| Phase lock | Orchestrator writes `.phase`; PreToolUse hook blocks writes outside the allowed paths | Strong within Claude Code (Write/Edit tools) |
| Tool restriction | `--allowedTools` per phase (e.g. Bash limited to pytest/ruff/mypy) | Strong within Claude Code |
| Stop hook | IMPLEMENT agent can't finish while the current step's tests fail | Soft (one forced continuation) |
| Frame diff gate | `check_frame_diff.py` compares against the `tests-approved/<feature>` tag | **Hard, tool-independent** |
| CI | Same gates on the PR | **Hard, final authority** |

The hooks are convenience and early feedback. **The tag-based diff checks in CI are the real guarantee.**

### Repository layout added by this setup

```
CLAUDE.md
.claude/
  settings.json              # hook registration
  commands/spec.md           # /spec slash command (interactive spec phase)
  hooks/protect_paths.py     # PreToolUse: phase-based write permissions
  hooks/require_green.py     # Stop: IMPLEMENT can't finish while tests are red
roles/
  tests.md implement.md review.md pr.md retro.md   # role prompts per phase
specs/
  _template/requirements.md design.md plan.md
  <feature>/...              # one folder per feature
scripts/
  orchestrate.py             # the workflow driver
  gates.py                   # runs all frame checks (step / final scope)
  check_red_stubs.py         # every new test must fail against stubs
  check_traceability.py      # every spec ID has >= 1 test, no unknown IDs
  check_test_smells.py       # no assert-free tests, no skip/xfail
  check_frame_diff.py        # nothing outside the frame changed since tests were approved
  check_mutation.py          # mutation score on changed files
tests/
  conftest.py                # req marker + --req selection
  review/                    # reviewer-generated tests
metrics/workflow_metrics.csv # one row per feature (tracked)
out/                         # working files and logs (gitignored)
```

---

## Part 3 — Setup steps (for Claude Code)

### Step 1 — Prerequisites

Install as dev dependencies (adapt to the repo's tool: pip/uv/poetry):

```
pytest pytest-cov hypothesis ruff mypy import-linter diff-cover
```

Mutation testing:
- `mutmut` is the simplest option, but **[VERIFY]** whether the installed version supports native Windows. If it doesn't, run mutation testing in WSL/CI only, or use `cosmic-ray`.
- Mutation testing is slow. It runs only in the final scope and only on changed files.

Check that `claude` is on PATH. **[VERIFY]** On Windows, prefer the native installer (`claude.exe`). If `claude` resolves to a `.cmd` shim, multi-line arguments can get mangled. The orchestrator therefore passes the task prompt via stdin. Test this in Step 11.

Windows note: all hooks and scripts are Python, so they behave the same on Windows and Linux. No bash scripts.

### Step 2 — `.gitignore`

Append:

```
.phase
.reqs
out/
```

### Step 3 — CLAUDE.md

Keep it short. Anything enforceable goes into scripts instead. Merge with an existing CLAUDE.md; don't overwrite it.

```markdown
# Project
<1–3 lines: what this repo does, main language/frameworks, how to run tests>

# Workflow
- Development follows docs/AGENTIC_WORKFLOW_SETUP.md: spec -> tests -> implement -> review -> PR -> retro.
- specs/<feature>/ is the source of truth. If code and spec disagree, the spec wins.
- Never edit tests to make them pass. If a test or the spec looks wrong, write the problem to
  out/<feature>/questions.md and stop.
- If a requirement is ambiguous, do not guess: write the question to out/<feature>/questions.md and stop.
- Read out/<feature>/answers.md if it exists — it contains answers to earlier questions.
- No new dependencies unless the spec's design.md lists them.

# Conventions
<only conventions that no linter/checker enforces>

# Domain notes
<things an agent can't infer from code: units, sensor semantics, data quirks>
```

### Step 4 — Spec templates

Create `specs/_template/requirements.md`:

```markdown
# <Feature name> — Requirements

Feature code: XXX   <!-- 2–10 uppercase chars, prefix for all IDs, e.g. HI2 -->

## Context
<why this feature exists, 3–5 lines>

## Goals

## Non-goals
<explicitly out of scope — prevents agents from "helpfully" adding things>

## Requirements
| ID | Requirement | Acceptance criterion (testable: given / when / then) |
|---|---|---|
| XXX-R-001 | | |

## Edge cases
<!-- empty / NaN / missing / out-of-range / unit mismatch / duplicates / ordering / timing / very large input -->
| ID | Situation | Expected behavior (exact: value, exception type + message pattern) |
|---|---|---|
| XXX-E-001 | | |

## Invariants (property-based test candidates)
| ID | Invariant (must hold for all valid inputs) |
|---|---|
| XXX-I-001 | |

## Test data and fixtures
<which recorded/synthetic data the tests use, where it lives, how it is generated>

## Verification harness
<how to prove this works end to end on real data, e.g. "run pipeline on recorded failure case
 F-17; health index must decrease monotonically over the last 72h">

## Open questions
<must be empty before approval>
```

Create `specs/_template/design.md`:

```markdown
# <Feature name> — Design

## Modules and responsibilities

## Public interfaces
<signatures exactly as in the stubs; every function lists the IDs it serves>

## Data flow

## Error handling
<which exceptions, where raised, what callers do>

## Dependencies
<approved new dependencies, or "none">

## Architecture constraints
<import rules, e.g. "features/ must not import from api/"; mirror them in .importlinter>
```

Create `specs/_template/plan.md`. **The format is parsed by the orchestrator, so keep it exact:**

```markdown
# <Feature name> — Implementation plan

## Step 1: <short title>
Covers: XXX-R-001, XXX-E-001
<what to build in this step; small enough for one fresh agent context with room for fix cycles>

## Step 2: <short title>
Covers: XXX-R-002, XXX-I-001
...
```

### Step 5 — `/spec` slash command

Create `.claude/commands/spec.md`:

```markdown
---
description: Interactive spec phase for a feature
argument-hint: <feature-slug>
---
You are in the SPEC phase for feature `$ARGUMENTS`. Templates are in specs/$ARGUMENTS/.

1. Read specs/$ARGUMENTS/ and any requirements I provide.
2. Before writing anything, ask clarifying questions, grouped by: requirements and acceptance
   criteria; edge cases (empty, NaN, missing, out-of-range, units, timing, ordering); failure
   behavior; non-goals; test data and fixtures; verification harness on real data;
   dependencies and architecture constraints. At most ~10 questions per round. Repeat until
   nothing is open.
3. Write requirements.md, design.md and plan.md following the templates exactly
   (ID format <CODE>-R-001 / -E-001 / -I-001, "## Step N: title" and "Covers:" lines in plan.md).
   Every ID must be covered by exactly one plan step.
4. Create interface stubs in src/: full type-annotated signatures, docstrings that reference
   the IDs, bodies `raise NotImplementedError`. Stubs must import cleanly.
5. Do not write tests or implementation.
6. End with: (a) a short list of what I should review most carefully, (b) the command
   `python scripts/orchestrate.py approve-spec $ARGUMENTS`.
```

### Step 6 — Role prompts

These are appended to the system prompt of each headless run. **Keep each under ~3 KB** (Windows command-line length limit ≈ 32k characters).

`roles/tests.md`:

```markdown
You are the TEST WRITER. You write tests from the spec and interface stubs only.

- Sources: specs/<feature>/ and the stub signatures in src/. Nothing else defines behavior.
- Every ID in requirements.md (R, E, I) gets at least one test. Tag each test with
  @pytest.mark.req("<ID>", ...). Use decorators on test functions, not module-level pytestmark.
- One test per edge-case row, asserting the exact expected value or exception type and message.
- Invariants (I-IDs): property-based tests with hypothesis.
- Use the fixtures and data defined in "Test data and fixtures". Add a golden/regression test
  for the verification harness if the spec defines one.
- Mock only external I/O (files, network, DB, clock). Never mock the module under test.
- Every test must contain a concrete assertion. No skip, no xfail.
- All new tests must FAIL against the stubs (NotImplementedError). Run pytest to confirm.
- Do not write implementation code.
- If the spec is ambiguous or untestable, write the question to out/<feature>/questions.md and stop.
- Finish by writing out/<feature>/test_summary.md:
  | ID | Test(s) | What it checks (one line) | Type: example / edge / property / golden |
  followed by a short list of anything in the spec you could NOT test, and why.
```

`roles/implement.md`:

```markdown
You are the IMPLEMENTER for one plan step.

- Sources: specs/<feature>/, the stubs in src/, the frozen tests, out/<feature>/answers.md if present.
- Make the tests for the given IDs pass. Tests of earlier steps must stay green.
- You cannot and must not edit tests/, specs/, config or dependency files. If a test or the
  spec seems wrong, write the reason to out/<feature>/questions.md and stop.
- Keep the public signatures from the stubs. Do not add dependencies.
- Do not add `# type: ignore`, `noqa`, `pragma: no cover`, skips, or broad `except Exception`.
- Run ruff format, ruff check and mypy on src before finishing.
- If a requirement is ambiguous, write the question to out/<feature>/questions.md and stop. Do not guess.
- Finish by appending to out/<feature>/steps.md:
  "## Step N — <title>": what you built (2–4 lines), and EVERY assumption you made that the
  spec does not state explicitly.
```

`roles/review.md`:

```markdown
You are the ADVERSARIAL REVIEWER. You did not write this code and you assume it has bugs.

- Compare the implementation (git diff tests-approved/<feature>) against specs/<feature>/.
- Look for: spec violations, unhandled edge cases, inputs no test covers, silent numeric
  errors (NaN propagation, dtype/precision, unit mismatch), and for ML/data code: leakage
  between train/test, normalization fitted on the wrong data, windowing off-by-one,
  wrong axis or ordering.
- For every finding, write a FAILING test in tests/review/test_review_<feature>.py, tagged with
  the related ID via @pytest.mark.req and a comment explaining the finding. A finding without
  a failing test does not count.
- Do not modify src/.
- Write out/<feature>/review.md: one line per finding (test name, ID, severity high/med/low,
  explanation), plus anything suspicious you could not turn into a test.
```

`roles/pr.md`:

```markdown
You write the pull request summary. Be factual and short; the reader decides what to review.

Write out/<feature>/pr.md with these sections:
1. Summary (3–5 lines)
2. Spec coverage: table of IDs -> tests -> status
3. Gate results (from out/<feature>/final_gates.md), including any SKIPPED gate — say so explicitly
4. Assumptions made during implementation (from steps.md) — all of them
5. Review findings and how each was resolved
6. Mutation survivors (file:line, what the mutant changed)
7. Where the human should look first: interfaces, data handling, anything high-risk, ranked
8. Open questions
```

`roles/retro.md`:

```markdown
You run the retrospective. Goal: turn recurring problems into checks, so they cannot recur.

Inputs: out/events.jsonl (filter by feature), out/<feature>/ (test_gate.md, steps.md,
review.md, final_gates.md, answers.md), out/runs/*<feature>*.json.

Find patterns: human gate rejections and their reasons, repeated gate failures (which check,
how often), escalations, questions agents had to ask, review findings, mutation survivors,
assumptions that should have been in the spec.

Write out/<feature>/retro_proposals.md. For each proposal:
- Problem and evidence (counts, file references)
- Fix, preferring in this order:
  1. a CI check / lint rule / hook (mechanically detectable)
  2. a test, fixture, or spec-template change (a category of case keeps being missed)
  3. a CLAUDE.md or role-prompt line (only if 1 and 2 are impossible)
- The concrete change (diff or script sketch)
- Scope: `project` (only this repo: domain, spec content, local checks) or `framework` (generic: orchestrator, gates, check scripts, hooks, base roles, templates). See Part 8.
Also list CLAUDE.md lines that are now enforced by a check and can be deleted.
Do not apply anything — proposals only.
```

### Step 7 — pytest: `req` marker and `--req` selection

Create (or merge into) `tests/conftest.py`:

```python
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--req", default="",
        help="Comma-separated spec IDs; only run tests tagged with at least one of them.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "req(*ids): spec IDs (requirements/edge cases/invariants) this test covers"
    )


def pytest_collection_modifyitems(config, items):
    wanted = {x.strip() for x in config.getoption("--req").split(",") if x.strip()}
    if not wanted:
        return
    selected, deselected = [], []
    for item in items:
        ids = {a for m in item.iter_markers("req") for a in m.args}
        (selected if ids & wanted else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
```

Usage in tests:

```python
@pytest.mark.req("HI2-R-001", "HI2-E-003")
def test_health_index_empty_window_raises():
    ...
```

Configure in `pyproject.toml` (merge with existing):

```toml
[tool.mypy]
strict = true
files = ["src"]

[tool.ruff]
line-length = 100

[tool.coverage.run]
branch = true
source = ["src"]
```

Create `.importlinter` once the architecture constraints are known (ask the user about the layers). The gate skips import-linter if the file doesn't exist.

### Step 8 — Hooks

`.claude/hooks/protect_paths.py` (PreToolUse):

```python
#!/usr/bin/env python3
"""PreToolUse hook: enforce per-phase write permissions based on the .phase file.

No .phase file -> normal interactive session -> everything allowed.
Exit code 2 blocks the tool call; stderr is shown to Claude.
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())).resolve()
PHASE_FILE = ROOT / ".phase"

ALLOWED = {
    "SPEC":      ["specs/", "src/"],           # specs + interface stubs
    "TESTS":     ["tests/", "out/"],
    "IMPLEMENT": ["src/", "out/"],
    "REVIEW":    ["tests/review/", "out/"],
    "PR":        ["out/"],
    "RETRO":     ["out/"],
}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}  # [VERIFY] tool names for installed version


def main() -> None:
    if not PHASE_FILE.exists():
        sys.exit(0)
    phase = PHASE_FILE.read_text(encoding="utf-8").strip()
    data = json.load(sys.stdin)
    if data.get("tool_name") not in WRITE_TOOLS:
        sys.exit(0)
    tool_input = data.get("tool_input", {})
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        sys.exit(0)
    path = pathlib.Path(raw)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        print(f"[{phase}] Blocked: {raw} is outside the repository.", file=sys.stderr)
        sys.exit(2)
    allowed = ALLOWED.get(phase, [])
    if any(rel.startswith(prefix) for prefix in allowed):
        sys.exit(0)
    print(
        f"[{phase}] Blocked write to {rel}. Allowed in this phase: {allowed}. "
        "If the spec or the tests look wrong, write the problem to out/<feature>/questions.md and stop.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
```

`.claude/hooks/require_green.py` (Stop):

```python
#!/usr/bin/env python3
"""Stop hook: in the IMPLEMENT phase, the agent may not finish while the selected tests fail.

Runs the tests for the current and earlier steps (IDs in .reqs; full suite if .reqs is absent).
stop_hook_active prevents an endless loop: after one forced continuation the agent may stop,
and the orchestrator's gates catch whatever is still red.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())).resolve()


def main() -> None:
    data = json.load(sys.stdin)
    if data.get("stop_hook_active"):
        sys.exit(0)
    phase_file = ROOT / ".phase"
    if not phase_file.exists() or phase_file.read_text(encoding="utf-8").strip() != "IMPLEMENT":
        sys.exit(0)
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "--no-header"]
    reqs = ROOT / ".reqs"
    if reqs.exists() and reqs.read_text(encoding="utf-8").strip():
        cmd += ["--req", reqs.read_text(encoding="utf-8").strip()]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        sys.exit(0)
    tail = "\n".join(r.stdout.splitlines()[-40:])
    print("Tests are still failing. Do not stop yet. Fix the implementation (tests are frozen):\n" + tail,
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
```

`.claude/settings.json` (merge with existing settings):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "python .claude/hooks/protect_paths.py" }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "command", "command": "python .claude/hooks/require_green.py" }]
      }
    ]
  }
}
```

Notes:
- **[VERIFY]** On Windows, `python` may need to be `py`. Also verify that hooks fire in headless (`-p`) mode with the installed version.
- Restart Claude Code after changing hooks.
- Hooks don't see writes made through Bash. That's why each phase restricts Bash to pytest/ruff/mypy via `--allowedTools`, and why `check_frame_diff.py` exists as the hard check.

### Step 9 — Check scripts

`scripts/check_traceability.py`:

```python
#!/usr/bin/env python3
"""Fail if a spec ID of the feature has no test, or a test references an ID that exists in no spec.

Usage: python scripts/check_traceability.py <feature>
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[REI]-\d{3}\b")


def ids_in(folder: pathlib.Path) -> set[str]:
    ids: set[str] = set()
    for p in folder.rglob("*.md"):
        if "_template" not in p.parts:
            ids |= set(ID_RE.findall(p.read_text(encoding="utf-8")))
    return ids


def tested_ids() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for p in (ROOT / "tests").rglob("test_*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "req":
                    for arg in dec.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            found.setdefault(arg.value, []).append(f"{p.relative_to(ROOT)}::{node.name}")
    return found


def main() -> None:
    feature = sys.argv[1]
    feature_ids = ids_in(ROOT / "specs" / feature)
    all_ids = ids_in(ROOT / "specs")
    tested = tested_ids()
    missing = sorted(feature_ids - tested.keys())
    unknown = sorted(set(tested) - all_ids)
    print(f"{len(feature_ids)} spec IDs, {len(feature_ids & tested.keys())} covered by tests.")
    for i in missing:
        print(f"MISSING test for {i}")
    for i in unknown:
        print(f"UNKNOWN ID {i} used in {tested[i]}")
    sys.exit(1 if missing or unknown else 0)


if __name__ == "__main__":
    main()
```

`scripts/check_red_stubs.py`:

```python
#!/usr/bin/env python3
"""Before implementation, every test of the feature must FAIL against the stubs.

A test that passes against `raise NotImplementedError` asserts nothing useful.
Errors (import/collection/fixture problems) and skips are also reported.
Usage: python scripts/check_red_stubs.py <feature>
"""
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[REI]-\d{3}\b")


def main() -> None:
    feature = sys.argv[1]
    ids = set()
    for p in (ROOT / "specs" / feature).glob("*.md"):
        ids |= set(ID_RE.findall(p.read_text(encoding="utf-8")))
    if not ids:
        sys.exit(f"No spec IDs found in specs/{feature}/")
    report = ROOT / "out" / feature / "red_stubs.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--req", ",".join(sorted(ids)), f"--junitxml={report}"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if not report.exists():
        sys.exit("pytest produced no report (collection crashed?)")
    passed, errored, skipped, total = [], [], [], 0
    for tc in ET.parse(report).getroot().iter("testcase"):
        total += 1
        name = f'{tc.get("classname")}::{tc.get("name")}'
        tags = {child.tag for child in tc}
        if "error" in tags:
            errored.append(name)
        elif "skipped" in tags:
            skipped.append(name)
        elif "failure" not in tags:
            passed.append(name)
    print(f"{total} tests for feature {feature}.")
    for n in passed:
        print(f"PASSES AGAINST STUB (weak or wrong test): {n}")
    for n in errored:
        print(f"ERROR (import/fixture/collection problem): {n}")
    for n in skipped:
        print(f"SKIPPED: {n}")
    sys.exit(1 if (passed or errored or skipped or total == 0) else 0)


if __name__ == "__main__":
    main()
```

`scripts/check_test_smells.py`:

```python
#!/usr/bin/env python3
"""Flag tests without any assertion and tests marked skip/skipif/xfail.

Usage: python scripts/check_test_smells.py [feature]   (feature is accepted but unused)
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_MARKS = {"skip", "skipif", "xfail"}


def call_name(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def has_check(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert):
            return True
        if isinstance(n, ast.Call):
            name = call_name(n)
            if name in {"raises", "warns", "fail"} or name.startswith("assert"):
                return True  # pytest.raises, np.testing.assert_allclose, assert_frame_equal, ...
    return False


def main() -> None:
    problems = []
    for p in (ROOT / "tests").rglob("test_*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                where = f"{p.relative_to(ROOT)}::{node.name}"
                if not has_check(node):
                    problems.append(f"NO ASSERTION: {where}")
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    if getattr(target, "attr", None) in SKIP_MARKS:
                        problems.append(f"SKIP/XFAIL: {where}")
            if isinstance(node, ast.Call) and call_name(node) == "skip":
                problems.append(f"pytest.skip() call in {p.relative_to(ROOT)} line {node.lineno}")
    print("\n".join(problems) or "No test smells found.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

`scripts/check_frame_diff.py`:

```python
#!/usr/bin/env python3
"""Hard frame check: since tests were approved, nothing outside the frame may change.

Fails if protected paths changed (tests, specs, config, deps, workflow files) or if added
src lines contain suppression patterns.
Usage: python scripts/check_frame_diff.py <feature>
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTECTED = (
    "tests/", "specs/", "roles/", "scripts/", ".claude/", "CLAUDE.md",
    ".github/", ".gitlab-ci.yml", "azure-pipelines.yml",
    "pyproject.toml", "setup.cfg", "requirements", "uv.lock", "poetry.lock",
    ".importlinter", "mypy.ini", "ruff.toml",
)
EXCEPTIONS = ("tests/review/",)  # written by the reviewer after the tests were approved
FORBIDDEN = re.compile(r"type:\s*ignore|noqa|pragma:\s*no cover|pytest\.skip|xfail")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def main() -> None:
    tag = f"tests-approved/{sys.argv[1]}"
    if not git("tag", "--list", tag).strip():
        sys.exit(f"Tag {tag} not found — tests were never approved.")
    untracked = set(git("ls-files", "--others", "--exclude-standard").splitlines())
    changed = set(git("diff", "--name-only", tag).splitlines()) | untracked
    problems = [
        f"PROTECTED PATH CHANGED: {f}" for f in sorted(changed)
        if f.startswith(PROTECTED) and not f.startswith(EXCEPTIONS)
    ]
    added = [
        line[1:] for line in git("diff", "-U0", tag, "--", "src/").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for f in untracked:
        if f.startswith("src/") and f.endswith(".py"):
            added += (ROOT / f).read_text(encoding="utf-8").splitlines()
    problems += [f"SUPPRESSION ADDED: {line.strip()}" for line in added if FORBIDDEN.search(line)]
    print("\n".join(problems) or "Frame intact.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

`scripts/check_mutation.py`: **Claude Code implements this for the installed tool and version.** Requirements:
- Mutate only `src/` files changed since `tests-approved/<feature>` (`git diff --name-only <tag> -- src/`).
- Compute the mutation score (killed / total). Fail below **80%**. Make the threshold a constant at the top of the file.
- Write survivors (file, line, what was mutated) to `out/<feature>/mutation_survivors.md`.
- If no mutation tool is available on this platform, print `SKIPPED: <reason>` and exit 0. The PR summary must then report the gate as skipped.

### Step 10 — Gate runner

`scripts/gates.py`:

```python
#!/usr/bin/env python3
"""Run the frame checks. Exit 1 if any check fails.

  --scope step   fast checks after each implementation step (tests of current + earlier steps)
  --scope final  everything, including coverage on the diff, traceability and mutation testing
Usage: python scripts/gates.py <feature> --scope step|final [--skip-mutation]
"""
import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable


def checks(feature: str, scope: str, skip_mutation: bool) -> list[tuple[str, list[str]]]:
    tag = f"tests-approved/{feature}"
    c = [
        ("ruff lint", ["ruff", "check", "src", "tests"]),
        ("ruff format", ["ruff", "format", "--check", "src", "tests"]),
        ("mypy", [PY, "-m", "mypy", "src"]),
        ("frame diff", [PY, "scripts/check_frame_diff.py", feature]),
    ]
    if (ROOT / ".importlinter").exists():
        c.append(("import rules", ["lint-imports"]))
    if scope == "step":
        reqs = ROOT / ".reqs"
        sel = ["--req", reqs.read_text(encoding="utf-8").strip()] if reqs.exists() else []
        c.append(("tests (current + earlier steps)", [PY, "-m", "pytest", "-q", *sel]))
    else:
        c += [
            ("full test suite + coverage",
             [PY, "-m", "pytest", "-q", "--cov=src", "--cov-branch", "--cov-report=xml:out/coverage.xml"]),
            # [VERIFY] diff-cover accepts a tag as compare-branch with the installed version
            ("diff coverage >= 90%",
             ["diff-cover", "out/coverage.xml", f"--compare-branch={tag}", "--fail-under=90"]),
            ("traceability", [PY, "scripts/check_traceability.py", feature]),
            ("test smells", [PY, "scripts/check_test_smells.py"]),
        ]
        if not skip_mutation:
            c.append(("mutation score", [PY, "scripts/check_mutation.py", feature]))
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("feature")
    ap.add_argument("--scope", choices=["step", "final"], default="final")
    ap.add_argument("--skip-mutation", action="store_true")
    a = ap.parse_args()
    (ROOT / "out").mkdir(exist_ok=True)
    failed = []
    for name, cmd in checks(a.feature, a.scope, a.skip_mutation):
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            code, output = r.returncode, (r.stdout + r.stderr).strip()
        except FileNotFoundError as e:
            code, output = 1, f"Tool not found: {e}"
        print(f"[{'PASS' if code == 0 else 'FAIL'}] {name}")
        if code != 0:
            failed.append(name)
            print("\n".join(output.splitlines()[-30:]))
        elif "SKIPPED" in output:
            print(f"  {output.splitlines()[-1]}")
    print(f"\n{'ALL GATES PASSED' if not failed else 'FAILED: ' + ', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

### Step 11 — Orchestrator

`scripts/orchestrate.py`:

```python
#!/usr/bin/env python3
"""Agentic workflow orchestrator.

  spec <feature>          Copy spec templates, set .phase=SPEC. Then run `claude` and type /spec <feature>.
  approve-spec <feature>  Clear phase lock, commit, tag spec-approved/<feature>.
  run <feature>           TESTS -> [you approve] -> IMPLEMENT (per step) -> REVIEW -> PR -> RETRO. Resumable.
  status <feature>        Show progress.

Every agent run is a fresh headless `claude -p` process: no context accumulates between steps.
"""
import argparse
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
CLAUDE = shutil.which("claude")
MAX_ATTEMPTS = 3   # implementation attempts per step before escalating to you
MAX_TURNS = 80     # hard stop per agent run

READ = ["Read", "Glob", "Grep"]
WRITE = ["Write", "Edit", "MultiEdit"]
PYTEST = ["Bash(python -m pytest:*)", "Bash(pytest:*)"]
LINT = ["Bash(ruff check:*)", "Bash(ruff format:*)", "Bash(python -m mypy:*)"]
GIT_READ = ["Bash(git diff:*)", "Bash(git log:*)"]
# [VERIFY] permission-rule syntax for the installed version (e.g. "Bash(pytest:*)" vs "Bash(pytest *)")
TOOLS = {
    "TESTS": READ + WRITE + PYTEST + LINT,
    "IMPLEMENT": READ + WRITE + PYTEST + LINT,
    "REVIEW": READ + WRITE + PYTEST + GIT_READ,
    "PR": READ + ["Write"] + GIT_READ,
    "RETRO": READ + ["Write"],
}
TASKS = {
    "TESTS": "Feature '{f}': write the tests from specs/{f}/ and the interface stubs in src/. "
             "Write the summary table to out/{f}/test_summary.md.",
    "IMPLEMENT": "Feature '{f}': implement plan step {n} ('{title}'). Make the tests for these IDs pass: {ids}. "
                 "Tests of earlier steps must stay green. Check with: python -m pytest -q --req {cov}",
    "FIX": "Feature '{f}': make all currently failing tests pass without editing tests. "
           "Check with: python -m pytest -q",
    "REVIEW": "Feature '{f}': review the implementation against specs/{f}/. "
              "Implementation diff: git diff tests-approved/{f}",
    "PR": "Feature '{f}': write the pull request summary to out/{f}/pr.md. Inputs: specs/{f}/, "
          "out/{f}/test_summary.md, out/{f}/steps.md, out/{f}/review.md, out/{f}/final_gates.md, "
          "out/{f}/mutation_survivors.md and out/{f}/answers.md if present.",
    "RETRO": "Feature '{f}': run the retrospective. Inputs: out/events.jsonl (feature={f}), out/{f}/, "
             "out/runs/*{f}*.json. Write proposals to out/{f}/retro_proposals.md.",
}
PHASES = ["TESTS", "IMPLEMENT", "REVIEW", "PR", "RETRO", "DONE"]


# ---------- helpers ----------
def fdir(feature: str) -> pathlib.Path:
    d = OUT / feature
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_event(feature: str, kind: str, **data) -> None:
    OUT.mkdir(exist_ok=True)
    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "feature": feature, "kind": kind, **data}
    with open(OUT / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def tag_exists(name: str) -> bool:
    return bool(git("tag", "--list", name).strip())


def commit(msg: str) -> None:
    git("add", "-A")
    if git("diff", "--cached", "--name-only").strip():
        git("commit", "-m", msg)


def set_phase(phase: str) -> None:
    (ROOT / ".phase").write_text(phase, encoding="utf-8")


def clear_phase() -> None:
    (ROOT / ".phase").unlink(missing_ok=True)
    (ROOT / ".reqs").unlink(missing_ok=True)


def load_progress(feature: str) -> dict:
    p = fdir(feature) / "progress.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"phase": "TESTS", "completed_steps": []}


def save_progress(feature: str, prog: dict) -> None:
    (fdir(feature) / "progress.json").write_text(json.dumps(prog, indent=2), encoding="utf-8")


def plan_steps(feature: str) -> list[dict]:
    text = (ROOT / "specs" / feature / "plan.md").read_text(encoding="utf-8")
    parts = re.split(r"^##\s+Step\s+(\d+):\s*(.+)$", text, flags=re.M)
    steps = []
    for i in range(1, len(parts), 3):
        body = parts[i + 2]
        m = re.search(r"^Covers:\s*(.+)$", body, flags=re.M)
        ids = [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []
        steps.append({"num": int(parts[i]), "title": parts[i + 1].strip(), "reqs": ids})
    return steps


def run_agent(phase: str, feature: str, task: str, feedback: str = "", role: str | None = None) -> dict:
    """One fresh headless Claude Code run. Task goes via stdin (safe for multi-line text on Windows)."""
    set_phase(phase)
    role_text = (ROOT / "roles" / f"{(role or phase).lower()}.md").read_text(encoding="utf-8")
    prompt = task + (f"\n\n{feedback}" if feedback else "")
    cmd = [CLAUDE, "-p",  # [VERIFY] -p without a prompt argument reads the prompt from stdin
           "--append-system-prompt", role_text,
           "--allowedTools", ",".join(TOOLS[phase]),
           "--permission-mode", "acceptEdits",
           "--max-turns", str(MAX_TURNS),
           "--output-format", "json"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, input=prompt, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    runs = OUT / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (runs / f"{stamp}-{feature}-{phase}.json").write_text(r.stdout or r.stderr, encoding="utf-8")
    try:
        res = json.loads(r.stdout)
    except json.JSONDecodeError:
        res = {"is_error": True, "result": (r.stderr or r.stdout)[-2000:]}
    log_event(feature, "agent_run", phase=phase, seconds=round(time.time() - t0),
              cost=res.get("total_cost_usd"), turns=res.get("num_turns"), error=res.get("is_error"))
    if res.get("is_error"):
        print(f"[{phase}] agent run reported an error: {str(res.get('result'))[:500]}")
    return res


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def run_gates(feature: str, scope: str) -> tuple[bool, str]:
    code, out = run_cmd([sys.executable, "scripts/gates.py", feature, "--scope", scope])
    return code == 0, out[-6000:]


def handle_questions(feature: str) -> None:
    """Agents can't ask interactively in -p mode; they write questions.md and stop. We pause here."""
    qfile = fdir(feature) / "questions.md"
    if not qfile.exists() or not qfile.read_text(encoding="utf-8").strip():
        return
    questions = qfile.read_text(encoding="utf-8")
    print(f"\n=== AGENT QUESTIONS ({feature}) ===\n{questions}\n")
    print(f"Write your answers to out/{feature}/answers.md (appended for all later agents).")
    ans = input("Press Enter when done, or type ABORT if the spec itself must change: ").strip()
    log_event(feature, "questions", questions=questions, aborted=ans.upper() == "ABORT")
    archive = fdir(feature) / "questions_archive.md"
    with open(archive, "a", encoding="utf-8") as f:
        f.write(f"\n---\n{questions}\n")
    qfile.unlink()
    if ans.upper() == "ABORT":
        raise SystemExit("Aborted. Update the spec, run approve-spec again, then reset progress to TESTS.")


def human_gate(feature: str, name: str, files: list[pathlib.Path]) -> tuple[bool, str]:
    print(f"\n=== GATE: {name} ({feature}) ===")
    for f in files:
        print(f"  review: {f.relative_to(ROOT)}")
    ans = input("Type OK to approve. Anything else = rejection; your text is logged as the reason:\n> ").strip()
    approved = ans == "OK"
    log_event(feature, "human_gate", gate=name, approved=approved, reason=None if approved else ans)
    return approved, ans


def fix_loop(feature: str, label: str) -> None:
    """Implementer fixes failing tests (full suite) until step gates pass; escalate after MAX_ATTEMPTS."""
    (ROOT / ".reqs").unlink(missing_ok=True)
    attempt, feedback = 0, ""
    while True:
        attempt += 1
        run_agent("IMPLEMENT", feature, TASKS["FIX"].format(f=feature), feedback, role="implement")
        handle_questions(feature)
        ok, out = run_gates(feature, "step")
        log_event(feature, "gate", step=label, attempt=attempt, passed=ok)
        if ok:
            commit(f"{feature}: {label}")
            return
        feedback = f"Attempt {attempt} failed these gates:\n{out}"
        if attempt >= MAX_ATTEMPTS:
            print(f"\n{label}: failed {MAX_ATTEMPTS} times.\n{out}")
            ans = input("Fix or clarify (answers.md), then Enter to retry, or ABORT: ").strip()
            log_event(feature, "escalation", step=label, answer=ans)
            if ans.upper() == "ABORT":
                raise SystemExit("Aborted.")
            attempt = 0


# ---------- phases ----------
def phase_tests(feature: str, prog: dict) -> None:
    feedback = ""
    while True:
        run_agent("TESTS", feature, TASKS["TESTS"].format(f=feature), feedback)
        handle_questions(feature)
        sections = []
        for name, script in [("Red against stubs", "check_red_stubs.py"),
                             ("Traceability", "check_traceability.py"),
                             ("Test smells", "check_test_smells.py")]:
            code, out = run_cmd([sys.executable, f"scripts/{script}", feature])
            sections.append(f"### {name}: {'PASS' if code == 0 else 'FAIL'}\n~~~\n{out}\n~~~")
        gate_file = fdir(feature) / "test_gate.md"
        gate_file.write_text("\n\n".join(sections), encoding="utf-8")
        ok, reason = human_gate(feature, "tests", [fdir(feature) / "test_summary.md", gate_file])
        if ok:
            commit(f"{feature}: tests approved")
            git("tag", "-f", f"tests-approved/{feature}")
            prog["phase"] = "IMPLEMENT"
            save_progress(feature, prog)
            return
        feedback = f"The human rejected the tests. Reason:\n{reason}\nRevise the tests accordingly."


def phase_implement(feature: str, prog: dict) -> None:
    steps = plan_steps(feature)
    if not steps:
        raise SystemExit(f"No '## Step N:' sections found in specs/{feature}/plan.md")
    covered: list[str] = []
    for step in steps:
        covered += step["reqs"]
        if step["num"] in prog["completed_steps"]:
            continue
        (ROOT / ".reqs").write_text(",".join(covered), encoding="utf-8")
        attempt, feedback = 0, ""
        while True:
            attempt += 1
            task = TASKS["IMPLEMENT"].format(f=feature, n=step["num"], title=step["title"],
                                             ids=", ".join(step["reqs"]), cov=",".join(covered))
            run_agent("IMPLEMENT", feature, task, feedback)
            handle_questions(feature)
            ok, out = run_gates(feature, "step")
            log_event(feature, "gate", step=step["num"], attempt=attempt, passed=ok)
            if ok:
                commit(f"{feature}: step {step['num']} - {step['title']}")
                prog["completed_steps"].append(step["num"])
                save_progress(feature, prog)
                break
            feedback = f"Attempt {attempt} failed these gates. Tests and config are frozen; fix src/.\n{out}"
            if attempt >= MAX_ATTEMPTS:
                print(f"\nStep {step['num']} failed {MAX_ATTEMPTS} times. Last gate output:\n{out}")
                ans = input("Fix or clarify (answers.md), then Enter to retry, or ABORT: ").strip()
                log_event(feature, "escalation", step=step["num"], answer=ans)
                if ans.upper() == "ABORT":
                    raise SystemExit("Aborted.")
                attempt = 0
    (ROOT / ".reqs").unlink(missing_ok=True)
    prog["phase"] = "REVIEW"
    save_progress(feature, prog)


def phase_review(feature: str, prog: dict) -> None:
    run_agent("REVIEW", feature, TASKS["REVIEW"].format(f=feature))
    code, _ = run_cmd([sys.executable, "-m", "pytest", "-q", "tests/review"])
    if code == 1:  # 0 = all pass, 4/5 = no review tests -> nothing to do
        print("\nThe reviewer produced failing tests (findings).")
        print(f"Delete invalid findings from tests/review/ (see out/{feature}/review.md).")
        ok, _ = human_gate(feature, "review findings", [fdir(feature) / "review.md"])
        if ok:
            commit(f"{feature}: review findings accepted")
            code, _ = run_cmd([sys.executable, "-m", "pytest", "-q", "tests/review"])
            if code == 1:
                fix_loop(feature, "review fixes")
    ok, out = run_gates(feature, "final")
    (fdir(feature) / "final_gates.md").write_text(f"~~~\n{out}\n~~~", encoding="utf-8")
    log_event(feature, "final_gates", passed=ok)
    prog["phase"] = "PR"
    save_progress(feature, prog)


def phase_pr(feature: str, prog: dict) -> None:
    run_agent("PR", feature, TASKS["PR"].format(f=feature))
    print(f"\nPR summary: out/{feature}/pr.md — open the PR yourself and review it.")
    prog["phase"] = "RETRO"
    save_progress(feature, prog)


def phase_retro(feature: str, prog: dict) -> None:
    run_agent("RETRO", feature, TASKS["RETRO"].format(f=feature))
    events = [json.loads(line) for line in (OUT / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    ev = [e for e in events if e["feature"] == feature]
    row = [
        datetime.date.today().isoformat(), feature,
        sum(1 for e in ev if e["kind"] == "human_gate" and not e["approved"]),
        sum(1 for e in ev if e["kind"] == "escalation"),
        sum(1 for e in ev if e["kind"] == "questions"),
        sum(1 for e in ev if e["kind"] == "gate" and not e["passed"]),
        round(sum((e.get("cost") or 0) for e in ev if e["kind"] == "agent_run"), 2),
    ]
    mfile = ROOT / "metrics" / "workflow_metrics.csv"
    mfile.parent.mkdir(exist_ok=True)
    if not mfile.exists():
        mfile.write_text("date,feature,human_rejections,escalations,questions,gate_failures,cost_usd\n",
                         encoding="utf-8")
    with open(mfile, "a", encoding="utf-8") as f:
        f.write(",".join(map(str, row)) + "\n")
    commit(f"{feature}: workflow metrics")
    print(f"\nRetro proposals: out/{feature}/retro_proposals.md — apply the ones you accept in a normal session.")
    prog["phase"] = "DONE"
    save_progress(feature, prog)


RUNNERS = {"TESTS": phase_tests, "IMPLEMENT": phase_implement, "REVIEW": phase_review,
           "PR": phase_pr, "RETRO": phase_retro}


# ---------- CLI ----------
def main() -> None:
    if not CLAUDE:
        raise SystemExit("claude CLI not found on PATH")
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["spec", "approve-spec", "run", "status"])
    ap.add_argument("feature")
    a = ap.parse_args()
    f = a.feature

    if a.command == "spec":
        target = ROOT / "specs" / f
        if not target.exists():
            shutil.copytree(ROOT / "specs" / "_template", target)
        set_phase("SPEC")
        print(f"SPEC phase set. Now run `claude` and type: /spec {f}")
        print(f"When you're satisfied: python scripts/orchestrate.py approve-spec {f}")
    elif a.command == "approve-spec":
        clear_phase()
        commit(f"{f}: spec approved")
        git("tag", "-f", f"spec-approved/{f}")
        save_progress(f, {"phase": "TESTS", "completed_steps": []})
        log_event(f, "spec_approved")
        print(f"Tagged spec-approved/{f}. Next: python scripts/orchestrate.py run {f}")
    elif a.command == "status":
        print(json.dumps(load_progress(f), indent=2))
    else:
        if not tag_exists(f"spec-approved/{f}"):
            raise SystemExit(f"Spec not approved. Run: python scripts/orchestrate.py approve-spec {f}")
        prog = load_progress(f)
        try:
            while prog["phase"] != "DONE":
                RUNNERS[prog["phase"]](f, prog)
        finally:
            clear_phase()
        print(f"\nFeature {f}: DONE.")


if __name__ == "__main__":
    main()
```

**Smoke test (Claude Code does this at the end of setup):**
1. Create a toy feature `demo` with one requirement, one edge case, a one-step plan and one stub function.
2. Run `approve-spec demo`, then `run demo`, and walk through every gate.
3. Verify that: the hook blocks a write to `tests/` during IMPLEMENT; the Stop hook fires; questions.md pauses the run; `out/runs/` and `out/events.jsonl` get filled; the multi-line prompt arrives intact via stdin (on Windows especially).
4. Delete the demo feature, its tags and its metrics row afterwards.

### Step 12 — CI

CI runs the same final gates on every PR. The feature name comes from the branch name `feature/<feature>`. Tags must be pushed (`git push --tags`), and CI needs full history so the tag diffs work.

Example for GitHub Actions (`.github/workflows/gates.yml`). Adapt it for GitLab CI or Azure DevOps, which use the same commands:

```yaml
name: gates
on: pull_request
jobs:
  gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # needed for tag-based diffs
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"   # adapt to the repo's dependency setup
      - name: Run final gates
        run: |
          FEATURE="${GITHUB_HEAD_REF#feature/}"
          python scripts/gates.py "$FEATURE" --scope final
```

CI runs on Linux, so mutation testing works there even if it doesn't on your Windows machine. If it's too slow per PR, run it nightly instead and add `--skip-mutation` to the PR job.

---

## Part 4 — Daily usage

```bash
# 1. Spec (interactive — the only phase where you talk to Claude a lot)
git checkout -b feature/health-index-v2
python scripts/orchestrate.py spec health-index-v2
claude                     # then: /spec health-index-v2  (+ paste your requirements)
#   -> answer its questions, review requirements.md / design.md / plan.md / stubs
python scripts/orchestrate.py approve-spec health-index-v2

# 2. Everything else (autonomous between gates)
python scripts/orchestrate.py run health-index-v2
#   -> gate 1: review out/<feature>/test_summary.md + test_gate.md -> OK
#   -> implementation runs per step; it pauses only for questions or escalations
#   -> gate 2: review findings (delete invalid ones) -> OK
#   -> PR summary in out/<feature>/pr.md, retro in out/<feature>/retro_proposals.md

# 3. Open the PR, review in this order:
#    assumptions -> mutation survivors -> review findings -> "where to look first" -> code

# Resume after an interruption: just run `run` again (progress.json)
python scripts/orchestrate.py status health-index-v2
```

**What you review at each gate:**

| Gate | Check | Ignore |
|---|---|---|
| Spec | Edge-case table complete? Acceptance criteria testable? Non-goals stated? Verification harness defined? Plan steps small? | Wording |
| Tests | Summary table: does each line test what the ID means? "Could not test" list. Red/traceability/smells all PASS? | Test code itself (spot-check 2–3) |
| Review | Is each finding a real problem? Delete the rest. | — |
| PR | Assumptions, survivors, high-risk areas, then code | Code that is fully covered and unremarkable |

**Changing the spec mid-way:** answer ABORT at a question prompt, edit the spec in a normal session, run `approve-spec` again, then set `"phase": "TESTS"` in `out/<feature>/progress.json` and run again. Tests get re-approved against the new spec.

---

## Part 5 — The improvement loop

After every feature:

1. Read `out/<feature>/retro_proposals.md`.
2. Apply the proposals you accept **in a normal interactive session on a separate branch.** Changes to `scripts/`, `.claude/`, `roles/` and `CLAUDE.md` are protected during feature work, by design.
3. Delete CLAUDE.md lines that are now enforced by a check. CLAUDE.md should shrink over time while `scripts/` grows.

Track `metrics/workflow_metrics.csv`:
- **human_rejections and escalations falling** means the frame is learning, and you can loosen gates (Part 6).
- **Flat or rising** means the retro proposals aren't hitting the real causes. Read the rejection reasons in `out/events.jsonl` yourself.
- **Many questions per feature** means the spec template is missing a section. Add it to `specs/_template/`.

Also log corrections you make *outside* the gates (e.g. fixing code after merge): add an event by hand, or note it in `answers.md`, so the retro sees it.

---

## Part 6 — Rollout: climbing the trust curve

| Stage | Condition to enter | What changes |
|---|---|---|
| 1 | Setup done | Small, low-risk features only. Review everything, including all code. Log every correction. |
| 2 | ~3 features with few rejections at the tests gate and few PR corrections | Review PR summary + assumptions + survivors + high-risk code only. |
| 3 | Stage 2 stable | Parallel implementation of **independent** steps via git worktrees (mark steps `Depends on: none` in plan.md; extend the orchestrator). |
| 4 | Low rejection rate per module type | Auto-approve the tests gate for low-risk modules (e.g. I/O adapters, formatting). |

**Never fully automate:** spec approval, and the tests gate for business- or safety-critical logic (health index, RUL, alarm thresholds). Those are one-way doors: a silently wrong result there causes damage that a revert can't undo.

---

## Part 7 — Limits and troubleshooting

**Limits**
- **The frame guarantees conformance to the spec, not correctness.** A wrong spec gets implemented perfectly. Spec review remains your most important job.
- **Unit tests miss ML-specific errors** (leakage, normalization, windowing). Every ML feature needs a verification harness on real recorded data in its spec.
- **Hooks are not airtight** (e.g. writes via Bash). The frame-diff check in CI is the guarantee.
- **The Stop hook forces only one continuation.** The orchestrator gates catch the rest.

**Troubleshooting**

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent runs out of turns or context in a step | Step too big | Split the step in plan.md |
| Same gate fails on every attempt | Spec gap or wrong test | Look at questions; possibly ABORT and fix the spec |
| Many tests PASS AGAINST STUB | Tests assert on mocks or trivial values | Reject the tests gate with that reason |
| Implementer keeps asking about one topic | Missing spec-template section | Add the section to `specs/_template/` |
| Hook doesn't fire | Settings not reloaded, wrong interpreter name | Restart Claude Code; `python` vs `py` on Windows |
| Prompt garbled on Windows | `.cmd` shim argument mangling | Use the native `claude.exe`; keep role files short |
| diff-cover fails on the tag | Ref format | **[VERIFY]**; try `refs/tags/tests-approved/<feature>` |

---

## Part 8 — Plan: extract into a reusable framework (later)

**Status: deferred.** The workflow lives inside this repo until it is stable. Extracting too early freezes a design the retro loop is still changing, and every fix would then need a versioned release.

### 8.1 Trigger conditions (all must hold)

- [ ] The workflow has been used on **≥ 3 real features** in this repo.
- [ ] The last 2 features needed **no logic changes** to `orchestrate.py`, `gates.py`, `scripts/check_*.py` or the hooks. Only config-like changes (thresholds, paths) are allowed.
- [ ] A **second project** is ready to adopt it.
- [ ] Company constraints are clarified (8.8).
- [ ] Existing tools have been compared (8.2).
- [ ] Part 9 improvements are adopted, or consciously deferred.

### 8.2 Before building: compare existing tools

**pstack: compared, decision made.** pstack is instruction-based (skills, playbooks, principles) with strong real-artifact verification, but it has no hard gates (frozen tests, frame diff). **Decision: keep one orchestrator (ours) and copy only selected ideas from pstack, rewritten into our phases and gates.** Nothing from pstack is installed as a dependency. The selected ideas are items 9.2–9.7 in Part 9.

Still open: check GitHub's **spec-kit** and any other spec-driven toolkit before extraction, to avoid rebuilding something that exists.

### 8.3 Preparation now (while still in one repo)

These keep the later extraction mechanical:

1. **Keep generic and project-specific apart.** Generic: orchestrator, gates, check scripts, hooks, base role prompts, spec templates, `/spec`. Project-specific: CLAUDE.md, domain notes, `.importlinter`, CI specifics, role *additions*.
2. **Put project additions to roles in separate files**, e.g. `roles/local/review.md` for ML checks (leakage, normalization, windowing), instead of editing the base roles. They can then be appended at runtime.
3. **Keep tunable values as named constants at the top of each script**, never inline. These become config keys (8.5).
4. **Tag every retro proposal with a scope:** `project` or `framework`. The retro role already does this. The `framework`-tagged proposals show what the framework needs and when it has stabilized.

### 8.4 Target architecture

A plugin alone is not enough: the orchestrator runs **outside** Claude Code. So the framework has three parts in one repo:

| Part | Contains | Distributed as |
|---|---|---|
| Python package (working name `agentflow`) | Orchestrator, gates, check scripts, hook logic, config loader, `init` | pip/pipx from git or an internal package index |
| Claude Code plugin | `/spec` command, hook registration, base role prompts (as agents or skills) | Plugin marketplace (a git repo can serve as one) |
| Templates | Spec templates, CLAUDE.md skeleton, `agentflow.toml`, CI snippets | Copied into a project by `agentflow init` |

Plugin hooks only call the package (`python -m agentflow.hooks.protect_paths`), so the logic exists in exactly one place.

Framework repo layout:

```
agentflow/
  pyproject.toml
  src/agentflow/
    cli.py                     # agentflow init | spec | approve-spec | run | status | gates
    orchestrator.py
    gates.py
    config.py                  # loads + validates agentflow.toml, applies defaults
    checks/ traceability.py red_stubs.py test_smells.py frame_diff.py mutation.py
    hooks/  protect_paths.py require_green.py
    templates/                 # spec templates, CLAUDE.md skeleton, agentflow.toml, CI snippets
    roles/                     # base role prompts
  plugin/
    .claude-plugin/plugin.json # [VERIFY] plugin manifest format
    commands/spec.md
    hooks/hooks.json           # [VERIFY] calls python -m agentflow.hooks.* (use ${CLAUDE_PLUGIN_ROOT} if paths are needed)
  marketplace/                 # [VERIFY] marketplace manifest, if distributing via marketplace
  tests/                       # the framework tests itself (dogfooding the same gates)
  CHANGELOG.md
```

### 8.5 Config: from hardcoded constants to `agentflow.toml`

| Current location | Constant | Config key |
|---|---|---|
| everywhere | `src/`, `tests/`, `specs/`, `out/` | `[paths] src, tests, specs, out` |
| `protect_paths.py` | `ALLOWED` per phase | `[phases.<PHASE>] write = [...]` |
| `check_frame_diff.py` | `PROTECTED`, `EXCEPTIONS` | `[protected] paths, extra, exceptions` |
| `check_frame_diff.py` | `FORBIDDEN` patterns | `[frame] forbidden_patterns` |
| `check_traceability.py` | ID regex | `[spec] id_pattern` |
| `gates.py` | diff coverage 90 | `[gates] diff_coverage` |
| `check_mutation.py` | mutation threshold 80, tool | `[gates] mutation_score, mutation_tool` |
| `gates.py` | ruff / mypy / lint-imports commands | `[gates.commands] lint, format, typecheck, imports` |
| `orchestrate.py` | `MAX_ATTEMPTS`, `MAX_TURNS` | `[agents] max_attempts, max_turns` |
| `orchestrate.py` | `TOOLS` per phase | `[phases.<PHASE>] tools = [...]` |
| `settings.json` | `python` interpreter | `[env] python` |
| roles | project additions | `[roles] extra_dir` |
| Part 9 | telemetry on/off, retention | `[telemetry] enabled, keep_days` |
| Part 9 | verification feature map location, data paths | `[verification] map_dir, data_root` |
| Part 9 | review lenses and models | `[review] lenses, models` |
| Part 9 | friction thresholds | `[friction] max_attempts_before_check, score_threshold` |

Example:

```toml
[paths]
src = "src"
tests = "tests"

[gates]
diff_coverage = 90
mutation_score = 80
mutation_tool = "mutmut"          # "cosmic-ray" | "none"

[gates.commands]
lint = ["ruff", "check", "src", "tests"]
typecheck = ["python", "-m", "mypy", "src"]

[protected]
extra = ["config/", "data/reference/"]

[agents]
max_attempts = 3
max_turns = 80

[roles]
extra_dir = ".agentflow/roles"
```

Making the gate commands configurable is also what would later allow non-Python projects. Don't build for that until a non-Python project actually needs it.

### 8.6 Extraction steps

1. Do the tool comparison (8.2) and decide: adopt, extend, or build.
2. Create the framework repo and move the generic scripts into the package unchanged first. Get it working before refactoring.
3. Replace constants with `config.py` lookups, with defaults equal to the current values, so behavior is identical.
4. Add the CLI entry points (`agentflow spec|approve-spec|run|status|gates|init`).
5. Build the plugin: `/spec`, hook registration calling `python -m agentflow.hooks.*`, base roles.
6. Implement `agentflow init`: scaffold templates, config, CLAUDE.md skeleton, `.gitignore` entries and a CI snippet. **Never overwrite existing files**; print what was skipped.
7. Give the framework its own tests, using its own gates.
8. **Migrate this repo:** delete the local copies, install a pinned version, keep only project-specific files, and run the smoke test (Step 11) plus one real feature. Compare behavior with before.
9. Adopt in the second project via `agentflow init`.

### 8.7 Versioning and upgrades

- Use **semantic versioning**, and keep a CHANGELOG that marks breaking changes, e.g. config keys or the plan.md format.
- **Pin the version in every project.** A framework improvement from project A must never silently change the gates in project B.
- The framework version sits in the dependency file, which the frame already protects, so agents can't change it mid-feature.
- **Upgrade deliberately:** on its own branch, then run the smoke test, then the next feature.
- **The retro loop splits:** `project`-scoped proposals are applied in the project; `framework`-scoped ones become issues or PRs in the framework repo, released and then adopted by each project.

### 8.8 Company constraints (clarify before extraction)

- [ ] Are Claude Code plugins from a custom or internal marketplace allowed?
- [ ] Is there an internal package index (e.g. Azure Artifacts, Nexus, Artifactory)? Is installing from git allowed?
- [ ] Where may the framework repo live (company GitLab/GitHub/Azure DevOps)? Who else may use it?
- [ ] Do security or compliance rules apply to tools that run agents with write access in company repos?

### 8.9 Open decisions (make at extraction time)

- Name and ownership: personal tool or team tool?
- Role prompts as plugin **agents** or **skills**. Decide based on which one supports per-phase tool restrictions in the current Claude Code version.
- Whether the plugin is needed at all, or whether `agentflow init` writing `.claude/settings.json` and the command files is enough. That would be simpler, with one fewer distribution channel.
- Python-only or language-agnostic gates (see 8.5: only if a real need appears).

---

## Part 9 — Next improvement steps (after the baseline runs well)

**Status: planned.** The baseline (Parts 3–4) runs first on a few real features. Each item here is adopted only when its trigger is met. Do Part 9 before Part 8: extract the framework only after these have settled.

### 9.0 Rules for every extension

1. **One orchestrator.** pstack is a source of ideas, not a dependency. Each idea is rewritten in our terms: phases, roles, gates, hooks.
2. **Every addition must be wired.** For each item, state which phase it changes, which files it reads and writes, which gate checks it, and what telemetry it emits. An addition that is only a prompt is incomplete.
3. **Hard gates stay authoritative.** New instructions never replace a check.
4. **Cost is tracked, not optimized.** The target metric is cost per *accepted* feature, with escaped defects at or near zero. Never trade verification for speed or tokens.

### Recommended order

| # | Item | Origin | Effort | Needs | Main value |
|---|---|---|---|---|---|
| 1 | 9.1 Execution telemetry (levels 1–2) | ours | S | — | Data for every later decision |
| 2 | 9.2 Structured decision log | pstack `show-me-your-work` | S | — | Assumptions become reviewable data |
| 3 | 9.3 Verification harness | pstack `create-verification-skill` | M–L | Recorded data | Proof on the real system, not only tests |
| 4 | 9.4 Review panel with triage | pstack `/interrogate` | M | — | Better findings, visible dismissals |
| 5 | 9.5 Orchestrator modes | pstack playbooks | M | 9.2 | Bug fixes and refactors with the right first proof |
| 6 | 9.6 Design-friction detection | pstack `/architect` (phase 5) | M | 9.1 | Stops blind retries; points to spec problems |
| 7 | 9.7 Retro panel with backlog | pstack `/reflect` | S–M | 9.1, 9.2 | Fewer one-off rules, better proposals |

---

### 9.1 Execution telemetry

**Trigger:** start right after the first baseline feature. It's cheap, and every other item benefits from the data.

**Purpose:** answer "where did time, tokens and retries go, and why?" This complements requirement traceability (spec → tests). It doesn't replace it.

**Event files** (in `out/telemetry/`, gitignored; the aggregated summary is tracked):

| File | One line per | Key fields |
|---|---|---|
| `runs.jsonl` | Agent run | run_id, feature, mode, phase, step, attempt, model, duration_s, turns, tokens_in, tokens_out, cache_read, cache_write, cost_usd, outcome, failure_category |
| `tool_calls.jsonl` | Tool call | ts, run_id, phase, tool, target (file/command, truncated), blocked (bool) |
| `gates.jsonl` | Check within a gate run | run_id, feature, step, attempt, check, passed, duration_s |
| `test_failures.jsonl` | Failing test | ts, run_id, source (agent/gate), nodeid, spec_ids, when (setup/call) |
| `events.jsonl` (existing) | Human interaction | gate approvals and rejections with reasons, questions, escalations, escaped defects |

**Failure categories** are deterministic rules, not LLM judgment: `test`, `lint`, `format`, `type`, `frame`, `traceability`, `question`, `turn_limit`, `agent_error`, `friction` (9.6).

**Capture levels:**

1. **Gates as JSON.** `gates.py --json` appends one line per check to `gates.jsonl`.
2. **Tool-call and test-failure hooks.** A PostToolUse hook (below), plus a pytest hook in `conftest.py` (below). `protect_paths.py` also logs blocked writes with `blocked: true`. A blocked write to `tests/` is a strong signal (see 9.6).
3. **Optional:** `--output-format stream-json` for per-turn detail, parsed after the run.
4. **Optional:** Claude Code's OpenTelemetry export, only if dashboards become necessary. **[VERIFY]** setup for the installed version.

**Orchestrator changes:**
- Generate a `run_id` (uuid4) per agent run, and pass `AGENTFLOW_RUN_ID`, `AGENTFLOW_FEATURE`, `AGENTFLOW_STEP` and `AGENTFLOW_ATTEMPT` as environment variables to `claude -p` and `gates.py`. Hooks and pytest inherit them. **[VERIFY]** that hooks inherit the environment of the `claude` process.
- Read token usage from the JSON result: `usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens`, plus `subtype` to detect turn-limit endings. **[VERIFY]** field names.
- New subcommand: `orchestrate.py defect <feature> "<description>" [--ids XXX-R-001,...]`, which logs an **escaped defect** (a bug found after merge). This is the only metric that measures quality rather than speed, so log every one.

PostToolUse hook `.claude/hooks/log_tool.py` (never blocks):

```python
#!/usr/bin/env python3
"""PostToolUse hook: append one telemetry line per tool call. Never blocks, never fails."""
import datetime
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))


def main() -> None:
    try:
        data = json.load(sys.stdin)
        ti = data.get("tool_input") or {}
        phase_file = ROOT / ".phase"
        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "run_id": os.environ.get("AGENTFLOW_RUN_ID"),
            "session_id": data.get("session_id"),
            "phase": phase_file.read_text(encoding="utf-8").strip() if phase_file.exists() else None,
            "tool": data.get("tool_name"),
            "target": str(ti.get("file_path") or ti.get("command") or ti.get("pattern") or "")[:200],
            "blocked": False,
        }
        out = ROOT / "out" / "telemetry"
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "tool_calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
```

Register it in `.claude/settings.json` under `"PostToolUse"` with matcher `"*"` **[VERIFY]**.

Test failures mapped to spec IDs. This replaces `tests/conftest.py`. Change it on an improvement branch, never during a feature, because `tests/` is frozen then.

```python
import datetime
import json
import os
import pathlib

import pytest

_REQ_BY_NODE: dict[str, list[str]] = {}


def pytest_addoption(parser):
    parser.addoption(
        "--req", default="",
        help="Comma-separated spec IDs; only run tests tagged with at least one of them.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "req(*ids): spec IDs (requirements/edge cases/invariants) this test covers"
    )


def pytest_collection_modifyitems(config, items):
    for item in items:
        _REQ_BY_NODE[item.nodeid] = [a for m in item.iter_markers("req") for a in m.args]
    wanted = {x.strip() for x in config.getoption("--req").split(",") if x.strip()}
    if not wanted:
        return
    selected, deselected = [], []
    for item in items:
        (selected if set(_REQ_BY_NODE[item.nodeid]) & wanted else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def pytest_runtest_logreport(report):
    run_id = os.environ.get("AGENTFLOW_RUN_ID")
    if not run_id or not report.failed:
        return
    root = pathlib.Path(os.environ.get("AGENTFLOW_ROOT", os.getcwd()))
    out = root / "out" / "telemetry"
    out.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "source": "gate" if os.environ.get("AGENTFLOW_GATE") else "agent",
        "nodeid": report.nodeid,
        "spec_ids": _REQ_BY_NODE.get(report.nodeid, []),
        "when": report.when,
    }
    with open(out / "test_failures.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
```

**Aggregation:** `scripts/telemetry_report.py <feature>` is a **deterministic** script; no LLM computes numbers. It writes `out/<feature>/telemetry_report.md` and appends a summary row to `metrics/workflow_metrics.csv`. Its content:
- Cost, tokens and duration per phase and per step
- Attempts per step vs. step size (IDs covered, lines changed), which feeds a plan-sizing rule
- Top failing checks, i.e. which rules agents violate most, so they can be moved earlier (hook or role prompt)
- Spec IDs with the most test failures across attempts: the hardest or most ambiguous requirements
- Read/edit ratio and test runs per attempt (context problems vs. thrashing)
- Blocked writes per run (agents trying to leave the frame)
- Human interventions and escaped defects

**Wiring summary:** orchestrator (run_id, env vars, usage capture, `defect` command), `gates.py --json`, two hooks, conftest, report script, and the retro reads the report (9.7).

---

### 9.2 Structured decision log

**Origin:** pstack's decision log (`show-me-your-work`): one TSV row per decision.

**Trigger:** together with 9.1.

**Change:** replace the free-text `out/<feature>/steps.md` with `out/<feature>/decisions.tsv`:

| Column | Content |
|---|---|
| ts | ISO timestamp |
| run_id | Given to the agent in its task prompt |
| phase, step | Where |
| kind | `assumption` / `design` / `deviation` / `workaround` / `hypothesis` (9.5 bugfix) |
| spec_ids | Comma-separated, may be empty |
| decision | One line |
| reason | One line |
| evidence | Test name, command output reference, file:line |

**Wiring:**
- **Roles:** the test-writer, implementer and reviewer append rows. The implementer *must* log every assumption the spec doesn't state explicitly. No tabs or newlines inside fields.
- **Gate:** `check_decision_log.py <feature> <run_id>` checks that the file parses (8 columns) and that every IMPLEMENT run appended ≥ 1 row. A silent run fails the gate.
- **PR role:** lists *all* `assumption`, `deviation` and `workaround` rows, grouped by spec ID.
- **Retro:** reads the log. Recurring assumptions on the same topic mean the spec template is missing a section.

---

### 9.3 Verification harness on the real system

**Origin:** pstack's `create-verification-skill` / `maintain-verification-skill`: a repo-level capability to launch the system, drive it, and capture evidence. It replaces "the build passed" with proof.

**Trigger:** first, when a feature's correctness can't be fully judged from unit tests (true for most ML features). Second, once recorded reference data is available to the pipeline.

**Artifacts:**
- `.claude/skills/verify-<project>/SKILL.md`: how an agent launches the pipeline, checks health, runs a case, captures evidence, and cleans up. This is human-readable guidance.
- `scripts/verify/run_verification.py`: the **deterministic** runner, the part that gates rely on.
- `verification/<feature>.toml`: the feature map. Each entry has an ID (`<CODE>-V-001`), the spec IDs it proves, the command, input data (path under the configured data root), the expected evidence files, and assertions.

Example entry for predictive maintenance:

```toml
[[case]]
id = "HI2-V-001"
proves = ["HI2-R-003", "HI2-I-001"]
command = ["python", "-m", "pdm.pipeline", "--input", "{data_root}/failures/F-17", "--out", "{evidence}"]
evidence = ["health_index.csv", "health_index.png"]
assert = [
  "health_index monotonically decreasing over last 72h (tolerance 0.02)",
  "first threshold crossing >= 24h before recorded failure timestamp",
]
```

Each assertion maps to a small Python check function in `scripts/verify/assertions.py`. The text is for humans; the function is the check.

**Wiring into the phases:**
- **Spec:** the "Verification harness" section becomes mandatory and gets `V` IDs. The ID regex in all scripts becomes `[REIV]`.
- **TESTS phase:** the test-writer also writes `verification/<feature>.toml` entries. The phase's write paths gain `verification/`. These entries are frozen with the tests tag, and `verification/` and `scripts/verify/` are added to `PROTECTED` in the frame diff.
- **Traceability:** V-IDs must appear in the feature map, not in pytest markers.
- **IMPLEMENT:** may run the verification for self-checks. Add `Bash(python scripts/verify/run_verification.py:*)` to its tools.
- **Final gate:** a new check, `verification`, runs all cases for the feature and writes evidence to `out/<feature>/evidence/`.
- **PR:** links the evidence files and states the assertion results.
- **Maintenance:** `orchestrate.py verify-all` runs every feature map periodically (e.g. nightly in CI). A failure is either a product bug or a stale map, and a human decides which. **The map must never be edited to hide a product bug.**

**Data:** reference data usually doesn't belong in git (size, confidentiality). Use the configured data root (network share, DVC, or similar) and record dataset versions in the feature map.

---

### 9.4 Review panel with triage

**Origin:** pstack's `/interrogate`: several reviewers, then a lead that sorts findings into *act on / consider / noted / dismissed*, with the dismissed ones shown.

**Trigger:** when single-reviewer findings are noisy or miss classes of bugs (visible in telemetry as escaped defects or low finding acceptance).

**Change to the REVIEW phase:**
1. Run 2–3 reviewer agents, each with one **lens**, as separate fresh runs:
   - `spec`: spec conformance and edge-case coverage
   - `numeric`: numerical and ML correctness (NaN, dtype, units, leakage, normalization, windowing)
   - `failure`: failure behavior, error handling, robustness

   Optionally use different models per lens (`--model`), since Claude Code allows choosing the model per headless run.
2. Each lens writes `out/<feature>/review/<lens>.md` plus failing tests in `tests/review/<lens>/`.
3. A **lead** run (`roles/review_lead.md`) merges everything into `out/<feature>/review.md` with four groups:
   - **Act on:** requires a failing test. These go to the implementer.
   - **Consider:** plausible but unproven. These go to the human.
   - **Noted:** informational.
   - **Dismissed:** with a reason for each, so the human can override.

   The lead deletes duplicate review tests. Its write paths are `tests/review/` and `out/`.
4. The human gate shows `review.md`. You can move items between groups. Only "act on" tests that remain go to the fix loop.

**Wiring:** new roles `review_spec.md`, `review_numeric.md`, `review_failure.md`, `review_lead.md`; the REVIEW phase loops over lenses; telemetry records findings per lens and their final group. **Lens acceptance rate** (findings that end up in "act on" and were confirmed by you) shows which lenses earn their cost. Drop a lens that never produces accepted findings.

---

### 9.5 Orchestrator modes: feature, bugfix, refactor

**Origin:** pstack's playbooks. Each task type has a different "first proof": reproduce before fixing a bug, record behavior before refactoring.

**Trigger:** the first bug fix or refactor you want to run through the workflow.

**Change:** `orchestrate.py run <feature> --mode feature|bugfix|refactor`, stored in `progress.json`. Spec templates per mode: `specs/_template/` (feature), `specs/_template_bugfix/` (symptom, reproduction steps, expected vs. actual, suspected causes), and `specs/_template_refactor/` (behavior to preserve, scope, non-goals). `spec` copies the template matching `--mode`.

| Phase | feature (as now) | bugfix | refactor |
|---|---|---|---|
| Spec | Requirements, edge cases, plan, stubs | Bug report: symptom, repro, expected behavior | Scope, behavior to preserve, plan |
| Baseline tests | Tests vs. **stubs**, must be **red** | **Reproduction** tests vs. **current code**, must be **red** for the reported reason | **Characterization** tests vs. **current code**, must be **green** (incl. golden snapshots) |
| Human gate | Test summary | Repro tests + "fails for the right reason" | Characterization coverage of the touched code |
| Implement | Per plan step | Usually one step. Implementer logs ≥ 2 competing `hypothesis` rows with evidence ruling them out (9.2) | Small steps; all characterization tests green after **every** step |
| Review | Full panel (9.4) | One lens (`spec`) + regression check | One lens (`spec`) + behavior equivalence |
| Verification (9.3) | Feature cases | Original repro on the real system | Existing cases unchanged |

**Wiring:**
- `check_red_stubs.py` generalizes to `check_baseline.py <feature> --expect red|green --against stubs|head`.
- `phase_tests` picks the role prompt per mode: `tests.md`, `tests_repro.md`, `tests_characterize.md`.
- For bugfix, `check_decision_log.py` additionally requires ≥ 2 `hypothesis` rows.
- Telemetry records `mode` on every event, so the report compares modes.

A perf mode (baseline trace, then compare) is deferred until there is profiling tooling to make it deterministic.

---

### 9.6 Design-friction detection

**Origin:** pstack's `/architect` treats repeated implementation friction as evidence that the design is wrong.

**Trigger:** after 9.1 has data from a few features, and when steps regularly need the maximum number of attempts.

**Idea:** blind retries waste tokens when the real problem is the spec or the interface. Detect friction early and escalate with a diagnosis instead of retrying again.

**Signals** (computed by `scripts/check_friction.py <feature> <step>` from telemetry and the diff):

| Signal | Source |
|---|---|
| Same test(s) failing across ≥ 2 attempts | `test_failures.jsonl` |
| Blocked writes to `tests/` or `specs/` | `tool_calls.jsonl` (`blocked: true`) |
| Questions on the same spec IDs | `events.jsonl` |
| `cast(`, broad `except`, long `isinstance` chains added in the diff | git diff of `src/` |
| Turn-limit endings | `runs.jsonl` |

**Wiring:** the orchestrator calls `check_friction.py` after each failed attempt. If the friction score exceeds the threshold, it **escalates immediately**, even before `MAX_ATTEMPTS`, with a short report: the signals, the affected spec IDs, and the failing tests. Your options are: clarify in `answers.md` and retry, or ABORT and redesign the spec. This is logged as an event of kind `friction` and analyzed by the retro.

---

### 9.7 Retro panel with backlog

**Origin:** pstack's `/reflect`: several reviewers, a synthesizer, proposals sorted into accepted/rejected/backlog, and nothing applied without approval.

**Trigger:** after 9.1 and 9.2, once there are ≥ 3 features of data.

**Change to the RETRO phase:**
1. Inputs become `out/<feature>/telemetry_report.md` (deterministic numbers, 9.1), `decisions.tsv` (9.2), `review.md` and the gate history, instead of raw logs.
2. Two retro reviewer runs with lenses: **process/gates** (which checks, hooks and steps cost the most retries) and **spec/test quality** (which assumptions, questions and findings point to template gaps).
3. A synthesizer writes `out/<feature>/retro_proposals.md` with three groups:
   - **Proposed:** evidence across ≥ 2 features, or a single high-severity issue
   - **Backlog:** seen once. Stored in `metrics/retro_backlog.md` (tracked) with an occurrence count.
   - **Rejected:** with a reason
4. The synthesizer reads the backlog first. An item seen again gets its count incremented and is promoted to Proposed at ≥ 2 occurrences. This prevents one odd feature from becoming a permanent rule.
5. Each proposal keeps its `scope: project | framework` tag (Part 8) and the fix preference order: check > test/template > prompt.
6. Proposals are still applied only by you, in a normal session on an improvement branch.

---

### 9.8 Files added or changed by Part 9

| Item | New | Changed |
|---|---|---|
| 9.1 | `.claude/hooks/log_tool.py`, `scripts/telemetry_report.py` | `orchestrate.py` (run_id, env, usage, `defect`), `gates.py` (`--json`), `protect_paths.py` (log blocked writes), `tests/conftest.py`, `.claude/settings.json` |
| 9.2 | `scripts/check_decision_log.py` | roles (tests, implement, review, pr, retro), `orchestrate.py` (pass run_id in task) |
| 9.3 | `.claude/skills/verify-<project>/`, `scripts/verify/`, `verification/` | spec template, ID regex `[REIV]`, `check_traceability.py`, `check_frame_diff.py` (PROTECTED), `protect_paths.py` (TESTS paths), `gates.py` (final check), `orchestrate.py` (`verify-all`), CI (nightly) |
| 9.4 | `roles/review_{spec,numeric,failure,lead}.md` | `orchestrate.py` (REVIEW loop, lead), `pr.md` role |
| 9.5 | `specs/_template_bugfix/`, `specs/_template_refactor/`, `roles/tests_{repro,characterize}.md`, `scripts/check_baseline.py` | `orchestrate.py` (`--mode`), `check_decision_log.py` |
| 9.6 | `scripts/check_friction.py` | `orchestrate.py` (escalate on friction) |
| 9.7 | `roles/retro_{process,spec}.md`, `roles/retro_synth.md`, `metrics/retro_backlog.md` | `orchestrate.py` (RETRO panel) |

Each item is adopted on its own improvement branch, followed by the smoke test (Step 11) and one real feature before the next item starts.
