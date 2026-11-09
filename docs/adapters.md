# Language adapters

The agentflow core is language-neutral: orchestrator, phases, `progress.json`, `events.jsonl`,
the frame diff against `tests-approved/<feature>`, phase-based path protection, the gate runner
and the CLI. Everything that depends on the project's language or test framework sits behind
`LanguageAdapter` (`src/agentflow/adapters/base.py`). The adapter is chosen by

```toml
[project]
language = "python"   # or "dotnet"
```

and looked up in `ADAPTERS` (`src/agentflow/adapters/__init__.py`, `"language": "module:Class"`,
imported lazily). An adapter instance gets the loaded `Config` (`self.cfg`, `self.root`).

## Result types

| Type | Fields | Meaning |
|---|---|---|
| `TestRun` | `status`, `returncode`, `output`, `cases` | `status`: `passed` / `failed` / `no_tests` / `error` (collection, build or usage problem). `cases`: per-test outcomes when a JUnit report was requested, else `None` |
| `TestCase` | `name`, `outcome` | `outcome`: `passed` / `failed` / `error` / `skipped` |
| `CheckResult` | `status`, `output` | `status`: `PASS` / `FAIL` / `SKIPPED`. SKIPPED never fails a gate but is printed and lands in `final_gates.md` (the PR role must report it) |
| `Check` | `name`, `run()` | A named, lazily-run check for the gate runner |

Helpers: `run_command(cmd, cwd)` (missing tool -> code 127, "Tool not found"),
`command_check(name, cmd, cwd)`, `parse_junit(path)` (JUnit XML -> `TestCase` list; works for
pytest `--junitxml` and the .NET `JunitXml.TestLogger`), `tool_path(name, near)`.

## Interface

| Member | Kind | Contract |
|---|---|---|
| `name` | class attr | Language key, e.g. `"python"`, `"dotnet"` |
| `source_suffixes` | class attr | Source file suffixes; new untracked files under `[paths] src` with these suffixes are scanned for forbidden patterns |
| `detect(root)` | classmethod, abstract | True if the repo looks like this language (used by `agentflow init` without `--language`) |
| `run_tests(req_ids, paths, fail_fast, junit)` | abstract | Run tests. `req_ids`: only tests tagged with >= 1 of these IDs (empty = all). `paths`: restrict to these test paths (the orchestrator passes `[paths] review_tests`). `junit`: write a JUnit XML report there and fill `cases`. Used by the step gate, the Stop hook (`fail_fast=True`, IDs from `.reqs`) and the review phase |
| `test_command(req_ids)` | abstract | The command line an agent runs to check the IDs; goes into the IMPLEMENT/FIX task prompt |
| `collect_req_tags()` | abstract | Static scan of the test sources: `{spec ID: ["file::test", ...]}`. Feeds the traceability check. Must not run or build anything |
| `test_smells()` | abstract | Static scan: one line per test without an assertion, skipped/ignored test, inline skip |
| `red_stub_run(req_ids, report)` | default | Runs the feature's tests with a JUnit report; the core fails the TESTS gate on any `passed` / `error` / `skipped` case or zero cases. Override if the framework needs special handling |
| `default_gate_commands()` | abstract | `{"lint"|"format"|"typecheck"|"imports": [argv]}`; omit a key to skip it. `[gates.commands]` overrides per key (`[]` disables) |
| `resolve_command(cmd)` | default | Expands `{src}`, `{tests}`, `{out}` in configured commands (python also maps a leading `python` to the project interpreter) |
| `lint_checks()` | default | Builds the `Check`s from the merged commands |
| `coverage_checks(base_ref)` | abstract | Final scope: full suite with coverage, then diff coverage against `base_ref` (= `tests-approved/<f>`) `>= cfg.diff_coverage`. SKIPPED (with reason) when a coverage tool is missing, but the full suite must still run |
| `mutation_check(feature, base_ref, changed_src)` | abstract | Mutation score on `changed_src` (src files changed since the tag) `>= cfg.mutation_score`; write survivors to `<out>/<feature>/mutation_survivors.md`. SKIPPED with reason if the tool is missing/unsupported or `mutation_tool = "none"` |
| `default_forbidden_patterns()` | abstract | Regexes forbidden in source lines added since the tag (suppressions, skips). `[frame] forbidden_patterns` replaces them |
| `default_protected_paths()` | abstract | Dependency/build/tool-config path prefixes that must not change after the tag. Added to the core list (`tests/`, `specs/`, `.claude/`, `.agentflow/`, `CLAUDE.md`, `agentflow.toml`, CI files) and `[protected] extra` |
| `test_tools()` / `lint_tools()` | abstract | `--allowedTools` rules that let an agent run tests / lint+format+typecheck (rule syntax: `Bash(<prefix> *)`) |
| `allowed_tools(phase)` | default | Per-phase tool list: core tools (Read/Glob/Grep, Write/Edit/MultiEdit, git diff/log) + the two lists above. `[phases.<P>] tools` replaces it |
| `conventions_brief()` | abstract | < ~1.5 KB text: how stubs look, how tests are tagged, how to run tests/lint, what is forbidden. Appended to the TESTS / IMPLEMENT / FIX / REVIEW task prompts and printed by `agentflow spec-brief <feature>` (used by the plugin's `/spec`) |
| `ci_setup()` | default | GitHub Actions steps (YAML, 6-space indent) that install the toolchain; inserted into the CI snippet `agentflow init` prints |

The base role prompts (`src/agentflow/roles/*.md`) never name a language; they refer to "the
conventions brief" for tagging, property tests and forbidden patterns.

## Python adapter (`python.py`)

| Concern | Implementation |
|---|---|
| Interpreter | `[env] python`, else `<root>/.venv` interpreter, else the current one. agentflow must be installed in the project env (pinned dev dependency) for the pytest plugin |
| Tag + select | `@pytest.mark.req("ID", ...)` + `--req ID,ID` from the `agentflow.pytest_plugin` (entry point `pytest11`; no conftest edits) |
| Tests | `python -m pytest -q --no-header -p no:cacheprovider [--req ...] [-x] [--junitxml=...]`; exit 0/1/5 -> passed/failed/no_tests, else error |
| Traceability / smells | AST scan of `tests/**/test_*.py` and `*_test.py` (the guide's code) |
| Gates | `ruff check`, `ruff format --check`, `mypy src` via `python -m`; `lint-imports` only if `.importlinter` exists |
| Coverage | `pytest --cov=src --cov-branch --cov-report=xml:out/coverage.xml`, then `diff-cover out/coverage.xml --compare-branch=tests-approved/<f> --fail-under=90` |
| Mutation | mutmut >= 3 (`mutmut run`, `mutmut results --all true`, filtered to changed modules). SKIPPED on native Windows (mutmut needs fork) and when not installed |
| Forbidden | `type: ignore`, `noqa`, `pragma: no cover`, `pytest.skip`, `xfail` |
| Protected | `pyproject.toml`, `setup.cfg`, `setup.py`, `requirements*`, `uv.lock`, `poetry.lock`, `Pipfile`, `.importlinter`, `mypy.ini`, `ruff.toml`, `.ruff.toml`, `pytest.ini`, `tox.ini`, root `conftest.py` |
| Tools | `Bash(python -m pytest *)`, `Bash(pytest *)`, `Bash(ruff check *)`, `Bash(ruff format *)`, `Bash(python -m ruff *)`, `Bash(python -m mypy *)` |

## Guidance for the .NET / xUnit adapter (`dotnet.py`, class `DotnetAdapter`)

Already registered in `ADAPTERS`; until the module exists, `language = "dotnet"` gives a clear
"adapter is not available" error (and `agentflow init --language dotnet` still scaffolds).

| Concern | Suggested mapping |
|---|---|
| `detect` | `*.sln`, `*.slnx` or `*.csproj` in the root |
| Stubs | Full signatures, XML doc comments naming the IDs, body `throw new NotImplementedException();` |
| Tagging | `[Trait("Req", "XXX-R-001")]` per ID (multiple attributes allowed) on the test method or class |
| `run_tests` | `dotnet test --filter "Req=A\|Req=B"` (`\|` = OR in the filter syntax); `paths` -> test project(s) or `FullyQualifiedName~Review`; `fail_fast` has no direct switch (ignore or `--blame`-free run); JUnit via `--logger "junit;LogFilePath=<junit>"` (JunitXml.TestLogger) or parse TRX. Map: 0 passed, 1 failed (check the output for "No test matches" -> `no_tests`, build errors -> `error`) |
| `collect_req_tags` | Regex over `tests/**/*.cs`: `\[Trait\("Req",\s*"(ID)"\)\]` attached to the next method/class name |
| `test_smells` | Methods with `[Fact]`/`[Theory]` without `Assert.`/`Should`/`Throws`; `Skip =` in attributes; `[Fact(Skip=...)]` |
| Red stubs | Default `red_stub_run` works: a `NotImplementedException` makes the test `failed`; a test that passes against the stub is reported |
| Gates | lint: `dotnet format --verify-no-changes` (or analyzers via `dotnet build -warnaserror`); format: `dotnet format whitespace --verify-no-changes`; typecheck: `dotnet build --no-restore -warnaserror` |
| Coverage | coverlet (`--collect:"XPlat Code Coverage"` -> Cobertura XML), then `diff-cover <cobertura.xml> --compare-branch=<base_ref>` (diff-cover reads Cobertura) or ReportGenerator |
| Mutation | Stryker.NET: `dotnet stryker --since:<base_ref>` (or `--mutate` per changed file), threshold from `cfg.mutation_score`, survivors from the JSON report |
| Forbidden | `#pragma warning disable`, `SuppressMessage`, `ExcludeFromCodeCoverage`, `Skip\s*=` |
| Protected | `*.sln`, `*.slnx`, `*.csproj` (as a prefix list: `Directory.Build.props`, `Directory.Packages.props`, `global.json`, `NuGet.config`, `.editorconfig`, `stryker-config.json`; `.csproj` files under `src/` need `[protected] extra` entries or an override of `protected_paths`) |
| Tools | `Bash(dotnet test *)`, `Bash(dotnet build *)`, `Bash(dotnet format *)` |
| `source_suffixes` | `(".cs",)` |

Note on protected `.csproj` files: protection is prefix-based, so a project file inside `src/`
can't be listed generically. Either list the paths in `[protected] extra` at init time or let the
adapter compute them (override `default_protected_paths()` to glob `**/*.csproj` and return the
relative paths).
