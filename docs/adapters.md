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
| `TestRun` | `status`, `returncode`, `output`, `cases`, `incomplete` | `status`: `passed` / `failed` / `no_tests` / `error` (collection, build or usage problem). `cases`: per-test outcomes when a JUnit report was requested, else `None`. `incomplete`: why the run was cut short (timeout, crashed host, killed, missing report / coverage file), `""` if it ran to the end |
| `TestScope` | `req_ids`, `paths` | The tests a final coverage / mutation run uses: tagged with any of `req_ids` OR under `paths` (review tests); both empty = whole suite. `describe()` gives the text for the report header |
| `TestCase` | `name`, `outcome`, `message` | `outcome`: `passed` / `failed` / `error` / `skipped`; `message`: failure/error text ("" if none) |
| `CheckResult` | `status`, `output`, `summary`, `tool_failure` | `tool_failure`: set by the mutation check for a FAIL whose summary does not start with "Mutation score" (the tool gave no result): reported as a tool failure, never sent to a repair run. `status`: `PASS` / `FAIL` / `SKIPPED`. SKIPPED never fails a gate but is printed and lands in `final_gates.md` (the PR role must report it). `summary` (optional): one line shown under a `[PASS]` line, e.g. the mutation score |
| `Check` | `name`, `run()`, `needs_green` | A named, lazily-run check for the gate runner. `needs_green` (the mutation check): not started, SKIPPED with the reason, when the typecheck, the regression or the coverage run failed before it |

Helpers: `run_command(cmd, cwd, env=None, timeout=None)` (missing tool -> code 127, "Tool not
found"; timeout -> process tree killed, code 124, "TIMEOUT: ..."; Ctrl+C / SIGTERM -> tree killed,
re-raised; runs through `procs.run`), `incomplete_reason(code, output, timeout)` (`timed out after
N s`, `killed (...)`, `test host crashed, run aborted`, or `""`), `incomplete_result(reason,
output, what)` (FAIL with `ERROR: <what> incomplete (<reason>)` as output head and summary),
`diff_coverage_summary(output, threshold)`,
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
| `stub_failure_problem(case)` | default | For a case that failed against the stubs: a reason why the failure doesn't count (e.g. not caused by the stub), else `None` (default: every failure counts). Listed as `FAILS FOR ANOTHER REASON THAN THE STUB` and fails the TESTS gate |
| `default_gate_commands()` | abstract | `{"lint"|"format"|"typecheck"|"imports": [argv]}`; omit a key to skip it. `[gates.commands]` overrides per key (`[]` disables) |
| `resolve_command(cmd)` | default | Expands `{src}`, `{tests}`, `{out}` in configured commands; an argument that is exactly `{src}` becomes one argument per source folder (`[paths] src` as a list), inside a longer argument it is the first (python also maps a leading `python` to the project interpreter) |
| `lint_checks(base_ref=None)` | default | Builds the `Check`s from the merged commands. The gate runner passes `base_ref` (= `tests-approved/<f>`) so an adapter can limit checks to the files changed since then (the .NET adapter does) |
| `regression_check()` | abstract | Final scope with feature-scoped coverage: the whole suite (the adapter's final selection) once, without coverage, `cfg.regression_timeout`. Check name `regression: whole suite, no coverage` |
| `coverage_checks(base_ref, scope=None)` | abstract | Final scope: the tests of `scope` (`None` = whole suite) with coverage (`cfg.coverage_timeout`), then diff coverage against `base_ref` (= `tests-approved/<f>`) `>= cfg.diff_coverage`, with `diff_coverage_summary` as `summary`. Names: `coverage run (feature tests)` / `diff coverage >= N% (feature tests)`, or `full test suite + coverage` / `diff coverage >= N%` for the whole suite. SKIPPED (with reason) when a coverage tool is missing, but the tests must still run. A cut-short coverage run fails both checks with `ERROR: ... incomplete` (no 0 %) |
| `mutation_check(feature, base_ref, changed_src, scope=None)` | abstract | Mutation score on `changed_src` (src files changed since the tag) `>= cfg.mutation_score`, killing mutants only with the tests of `scope` (`None` = whole suite), `cfg.mutation_timeout`; write survivors to `<out>/<feature>/mutation_survivors.md`. SKIPPED with reason if the tool is missing/unsupported or `mutation_tool = "none"`; a cut-short run is `ERROR: mutation run (...) incomplete` |
| `default_forbidden_patterns()` | abstract | Regexes forbidden in source lines added since the tag (suppressions, skips). `[frame] forbidden_patterns` replaces them |
| `default_protected_paths()` | abstract | Dependency/build/tool-config paths that must not change after the tag. Added to the core list (`tests/`, `specs/`, `.claude/`, `.agentflow/`, `CLAUDE.md`, `agentflow.toml`, CI files) and `[protected] extra`. Entries are prefixes; an entry containing `*`, `?` or `[` is a case-insensitive glob where `*` also matches `/` (`*.csproj` = a project file at any depth). The same holds for `[protected] paths/extra/exceptions` |
| `test_tools()` / `lint_tools()` | abstract | `--allowedTools` rules that let an agent run tests / lint+format+typecheck (rule syntax: `Bash(<prefix> *)`) |
| `allowed_tools(phase)` | default | Per-phase tool list: core tools (Read/Glob/Grep, Write/Edit/MultiEdit, git diff/log) + the two lists above. `[phases.<P>] tools` replaces it |
| `conventions_brief()` | abstract | < ~1.5 KB text: how stubs look, how tests are tagged, how to run tests/lint, what is forbidden. Appended to the TESTS / IMPLEMENT / FIX / REVIEW task prompts and printed by `agentflow spec-brief <feature>` (used by the plugin skill `/agentflow:feature`) |
| `ci_setup()` | default | GitHub Actions steps (YAML, 6-space indent) that install the toolchain; inserted into the CI snippet `agentflow init` prints |
| `config_template()` | default | Commented `agentflow.toml` section(s) for the language, appended by `agentflow init` (default: none) |
| `gitignore_entries()` | default | Extra `.gitignore` lines `init` adds: build outputs the orchestrator must never commit (it commits with `git add -A`) |
| `init_note()` | default | Setup advice printed at the end of `init` |

Adapter-owned config sections (e.g. `[dotnet]`) are declared in `config.SCHEMA` (type-checked like
every other key, booleans allowed where declared) and listed in `config.LANGUAGE_SECTIONS`; the raw
values land in `Config.language_options["<section>"]` and the adapter applies its own defaults.

The base role prompts (`src/agentflow/roles/*.md`) never name a language; they refer to "the
conventions brief" for tagging, property tests and forbidden patterns.

## Generic adapter (`generic.py`, `language = "generic"`)

Any language or test framework without a built-in adapter, configured entirely in
agentflow.toml (`agentflow init --language generic` writes a commented TypeScript / vitest
example; `/agentflow:setup` drafts it from the repository). Never auto-detected.

| `[generic]` key | Meaning |
|---|---|
| `test_all`, `test_ids`, `test_paths` (required: the first two) | commands; placeholders `{junit}` (JUnit XML agentflow reads), `{ids}` (joined by `ids_separator`, default `,`), `{ids_regex}` (`A|B`), `{paths}` (one argument per path), `{src}` `{tests}` `{out}` |
| `tag_pattern` (required) | regex over the test files; group 1 holds a test's spec IDs (found with the ID pattern) |
| `test_files`, `source_suffixes` | globs under `[paths] tests`; suffixes of source files (frame diff, format fix) |
| `stub_marker` | text in the failure of a test that hit a not-implemented stub (red check) |
| `skip_pattern` | regex for skipped tests (the only smell the generic scan knows) |
| `coverage`, `coverage_ids`, `coverage_report` | coverage command (whole suite / by IDs) and the Cobertura or LCOV report it writes; diff coverage via `diff-cover` |
| `mutation`, `mutation_score_pattern` | mutation command (`{files}` = changed source files) and a regex whose group 1 is the score in percent |
| `format_fix` | formatter command with `{files}` (the gates' format fix) |
| `forbidden_patterns`, `protected` | suppression regexes for the frame diff; dependency / config files frozen after the tests gate |
| `conventions` | text for the agents: how stubs look, how tests are tagged |
| `test_timeout` | seconds per test run |

lint / format / typecheck checks come from `[gates.commands]`. Compared with a built-in adapter:
no static smell scan beyond `skip_pattern`, mutation not limited to changed hunks, tagged test
locations are `file:line`. A project can start here and get a dedicated adapter later.

## Python adapter (`python.py`)

| Concern | Implementation |
|---|---|
| Interpreter | `[env] python`, else `<root>/.venv` interpreter, else the current one. agentflow must be installed in the project env (pinned dev dependency) for the pytest plugin |
| Tag + select | `@pytest.mark.req("ID", ...)` + `--req ID,ID` from the `agentflow.pytest_plugin` (entry point `pytest11`; no conftest edits) |
| Tests | `python -m pytest -q --no-header -p no:cacheprovider [--req ...] [-x] [--junitxml=...]`; exit 0/1/5 -> passed/failed/no_tests, else error |
| Traceability / smells | AST scan of `tests/**/test_*.py` and `*_test.py` (the guide's code) |
| Gates | `ruff check`, `ruff format --check`, `mypy src` via `python -m`; `lint-imports` only if `.importlinter` exists |
| Coverage | Feature scope: `pytest --req <IDs> --req-paths <review_tests> --cov=src --cov-branch --cov-report=xml:out/coverage.xml` (`--req-paths` keeps the tests under these paths, tagged or not), then `diff-cover out/coverage.xml --compare-branch=tests-approved/<f> --fail-under=90`; regression = plain `pytest -q`. Incomplete: exit 124 / 2 (interrupted) / 3 (internal error), or no `coverage.xml` |
| Mutation | mutmut >= 3 via the standalone runner `mutmut_runner.py` (copied to `out/.mutmut/`): `mutmut run <module>.x*` for the changed modules, `mutmut results --all true`, `mutmut show` for up to 50 survivors (diffs go into `mutation_survivors.md`); `mutants/` is deleted before and after the run (and gitignored by `init`). Feature scope: the runner sets `PYTEST_ADDOPTS="--req <IDs> --req-paths <review_tests>"` for mutmut's pytest runs (spec key `pytest_addopts`; needs the agentflow pytest plugin with `--req-paths` in the mutmut env, which is why `MUTMUT_ENV_VERSION` is 2). Score = (killed + timeout + caught by type check + segfault) / (those + survived + no tests + suspicious); skipped / not checked are left out. Where it runs: `[gates] mutation_runner` (see below). SKIPPED when mutmut can't run there |
| Forbidden | `type: ignore`, `noqa`, `pragma: no cover`, `pytest.skip`, `xfail` |
| Protected | `pyproject.toml`, `setup.cfg`, `setup.py`, `requirements*`, `uv.lock`, `poetry.lock`, `Pipfile`, `.importlinter`, `mypy.ini`, `ruff.toml`, `.ruff.toml`, `pytest.ini`, `tox.ini`, root `conftest.py` |
| Tools | `Bash(python -m pytest *)`, `Bash(pytest *)`, `Bash(ruff check *)`, `Bash(ruff format *)`, `Bash(python -m ruff *)`, `Bash(python -m mypy *)` |

### Python mutation through WSL

mutmut needs `fork`, so it can't run on native Windows. `[gates] mutation_runner`:

| Value | Meaning |
|---|---|
| `"auto"` (default) | `wsl` on Windows, `native` elsewhere |
| `"native"` | The project interpreter (`[env] python` / `.venv`) runs mutmut, which must be installed there. On native Windows: SKIPPED with a pointer to `"wsl"` |
| `"wsl"` | `wsl.exe [-d <[env] wsl_distro>] -e python3 <runner> <spec>` |

WSL flow (`adapters/wsl.py`, `adapters/mutmut_runner.py`):

1. Probe: `wsl.exe -e wslpath -a <repo>` (repo path, e.g. `/mnt/d/...`) and `python3 --version`.
   Either failing -> SKIPPED with the reason (no wsl.exe, distro won't start, no python3).
2. Venv: `~/.cache/agentflow/venvs/<sha256(WSL repo path)[:16]>` on the Linux filesystem (not
   on `/mnt/<drive>`: slow and visible to Windows). Contents: the project's dependencies
   (`[project] dependencies`, every optional extra, every `[dependency-groups]` group,
   `[tool.uv] dev-dependencies`, `requirements*.txt`; the project itself is not installed because
   mutmut puts its mutated copy on `sys.path`), `mutmut>=3`, `pytest`, and agentflow (its pytest
   plugin) from this source tree when agentflow runs from one (a project's own `agentflow`
   requirement is then dropped). Windows paths in requirements are translated.
3. The venv is rebuilt when its stamp changes: sha256 of the requirement list, `pyproject.toml`,
   `uv.lock`, `poetry.lock`, `Pipfile.lock`, `pdm.lock`, the requirements files, the agentflow
   version and `MUTMUT_ENV_VERSION`. Built with `uv` (PATH, `~/.local/bin`, `~/.cargo/bin`;
   `uv venv --python python3` + `uv pip install`), else `python3 -m venv` + pip. If neither
   works: SKIPPED with the setup log and the one-time hint (`curl -LsSf
   https://astral.sh/uv/install.sh | sh`, or `sudo apt install python3-venv`; Ubuntu's python3
   has no `ensurepip` by default).
4. mutmut runs in the repo through `/mnt/<drive>/...`; the runner writes
   `out/.mutmut/result.json`, which the adapter parses.

Measured on the e2e fixture (one module, 7 mutants, WSL Ubuntu, Python 3.12, uv 0.13, warm uv
cache): first run incl. venv creation 4.0 s, later runs 2.7 s (venv reused); score 85.7 % (6/7,
the survivor is `"ints only"` -> `"XXints onlyXX"`, which `pytest.raises(match=...)` accepts).
The first venv on a machine also downloads the packages.

## .NET / xUnit adapter (`dotnet.py`, class `DotnetAdapter`)

Static C# parsing lives in `csharp.py` (comment- and literal-aware scanner: namespaces, classes,
attribute lists, test methods, helper-method bodies; no build, no Roslyn).

| Concern | Implementation |
|---|---|
| `detect` | `*.sln`, `*.slnx` or `*.csproj` in the root |
| Toolchain | `[dotnet] executable`, else `dotnet` on PATH, else `DOTNET_ROOT` / `~/.dotnet` / Program Files. Every dotnet call gets `DOTNET_CLI_UI_LANGUAGE=en`, `VSLANG=1033`, `DOTNET_NOLOGO=1`, telemetry off (English messages on localized Windows) |
| Targets | `[dotnet] test_projects`, else the solution (`[dotnet] solution`, else the single `*.sln`/`*.slnx` in the root; several -> config error), else the test projects found under `[paths] tests` (`.csproj` mentioning `Microsoft.NET.Test.Sdk` or `IsTestProject`) |
| Stubs | Full signatures, XML doc comments naming the IDs, body `throw new NotImplementedException();` |
| Tag + select | `[Trait("Req", "XXX-R-001")]`, one attribute per ID, on the test method or its class (string literals only). Selection: `--filter "(Req=A\|Req=B)&(<base_filter>)"` |
| Tests | `dotnet build <target> -c <configuration>`, then `dotnet test <target> -c <configuration> --no-build --results-directory out/.dotnet/<kind> --logger trx [--filter ...]` (`build_before_test = false`: one `dotnet test` that builds). Status: any failed/errored case -> `failed`; build failure, crash without report or timeout -> `error`; no cases -> `no_tests` (VSTest exits 0 when a filter matches nothing); else `passed`. Build failures end the output with `BUILD FAILED (compile errors):` and the unique `error CSxxxx` lines (repo-relative). `fail_fast` is ignored (VSTest has no stop-on-first-failure) |
| Reports | TRX by default: built into VSTest, so the target project needs no extra package; parsed into `Ns.Class::Method` cases with message + stack trace and, when the core asks for `junit`, rewritten as JUnit XML (`out/<f>/red_stubs.xml`). If **every** test project references `JunitXml.TestLogger`, that logger is used instead (`LogFilePath=.../{assembly}.junit.xml`) and parsed with `parse_junit` |
| Review tests | `paths` -> the test classes declared in the `.cs` files there (static scan) -> `FullyQualifiedName~Ns.Class.` filter, ANDed with `base_filter`. `[paths] review_tests` must be inside a test project folder (e.g. `tests/App.Tests/Review`), otherwise the files are not compiled |
| Red stubs | Default `red_stub_run`; `stub_failure_problem`: a failed case counts only if its message/stack contains `NotImplementedException` (`Assert.Throws<X>` against a stub reports it as the actual exception). A compile error gives no report -> the TESTS gate fails with the `BUILD FAILED` lines |
| `collect_req_tags` | Scan of `[paths] tests/**/*.cs` (skipping `bin/`, `obj/`, `TestResults/`): methods with `[Fact]`/`[Theory]` (also `*Fact`/`*Theory` such as `SkippableFact`, and FsCheck `[Property]`) and their `Trait("Req", ...)` attributes plus those of the enclosing classes. Location `file::Class.Method` |
| `test_smells` | `NO ASSERTION` (no `Assert*`, `.Should*(`, `Throws`, `Verify*(`, `Fail(` in the body, following called helper methods declared in the test sources up to 3 levels), `SKIPPED (Skip =)` on the test attribute, `EARLY RETURN` (body starts with `return;`), `TRIVIAL ASSERTION` (`Assert.True(true)` / `Assert.False(false)`), `INLINE SKIP` (`Assert.Skip*(`, `Skip.If*(`). Checked against FCSim: 887 tests, 0 findings |
| lint | `dotnet format style <target> --verify-no-changes --include {changed}` |
| format | `dotnet format whitespace <target> --verify-no-changes --include {changed}` |
| typecheck | `dotnet build <target> -c <configuration> --no-incremental`; FAIL on build errors, and with `warnings = "changed"` (default) on warnings located in changed `.cs` files; `"any"` fails on every warning, `"ignore"` drops `--no-incremental` and ignores warnings |
| Regression | `dotnet test` with `final_filter`, no coverage, `[gates] regression_timeout` |
| Coverage | Feature scope: filter `(Req=A\|Req=B\|FullyQualifiedName~<review class>.)&(<final_filter>)` (`scope_filter`); `suite` scope: `final_filter`. With `--collect "XPlat Code Coverage"` (coverlet.collector, part of the xUnit template; if no test project references it: tests still run, coverage SKIPPED). Incomplete (status `error`, `TestRun.incomplete`): exit 124, `Test Run Aborted` / `test host process crashed` in the output, no TRX with a non-zero exit (and no compile errors), or no `coverage.cobertura.xml` after a run with results. Cobertura files are normalized to repo-relative posix paths (`out/coverage/normalized-N.xml`) and passed to `diff-cover --compare-branch=tests-approved/<f> --fail-under=<diff_coverage>`. diff-cover comes from agentflow's own environment (`pip install "agentflow[dotnet]"`), else `uvx --from diff-cover diff-cover`, else SKIPPED |
| Mutation | Stryker.NET (`dotnet-stryker` on PATH or in `~/.dotnet/tools`, or a local tool in `.config/dotnet-tools.json`; else SKIPPED with `dotnet tool install -g dotnet-stryker`). Runs in a temporary git worktree of the working tree (uncommitted changes copied in) where every `.cs` file of the mutated projects starts with `// Stryker disable all`, and in each changed file `// Stryker restore all` / `// Stryker disable all` frame the hunks changed since the tests-approved tag (widened to whole statements, never inside a string literal or block comment; a file without a diff, e.g. untracked, is mutated whole): Stryker compiles mutants only into the changed code (its `--mutate` alone only filters the report, see below), so hot code around a small change stays uninstrumented. Survivor lines are mapped back to the repository's file; the repository's files are never touched, the worktree is removed afterwards. One run per project owning changed `.cs` files under `src` that a test project references (its `<ProjectReference>`s, followed transitively through the referenced `.csproj` files, compared as resolved paths), with those test projects as `-tp`. A changed project no test project references is not run (Stryker can't mutate it): the result lists `not mutated (no test project references it): <X.csproj>: <files>` and names it on the score line; if no changed project is referenced, SKIPPED. A failed Stryker run is a FAIL that keeps the counts of the runs that finished (`<X.csproj>: d detected, u undetected`); their survivors are still written. `dotnet-stryker -tp <test project> --project <X.csproj> --mutate **/<file> ... --configuration <cfg> -r Json -O out/<f>/stryker/<project> --break-at 0`, plus `-f` with `test-case-filter` = the feature scope's filter ANDed with `base_filter` (`suite` scope: `base_filter`), ANDed with a filter already in the project's `stryker-config.json`. Timeout `[dotnet] mutation_timeout` if set, else `[gates] mutation_timeout`; exit 124, a killed Stryker or a missing report = `ERROR: mutation run (<project>) incomplete`. Score = (Killed + Timeout) / (Killed + Timeout + Survived + NoCoverage) over the changed files; survivors -> `mutation_survivors.md`. Runtime on the e2e fixture: ~15 s |
| Forbidden | `#pragma warning disable`, `SuppressMessage`, `ExcludeFromCodeCoverage`, `Skip =`, `catch (Exception)` without a variable, `catch {`, single-line empty `catch (...) { }`. The null-forgiving `!` is allowed |
| Protected | `*.csproj`, `*.sln`, `*.slnx`, `*Directory.Build.props`, `*Directory.Build.targets`, `*Directory.Packages.props`, `global.json`, `*NuGet.config`, `*.editorconfig`, `*.runsettings`, `.config/dotnet-tools.json`, `stryker-config.json` (globs: any depth) |
| Tools | `Bash(dotnet test *)`, `Bash(dotnet build *)`, `Bash(dotnet format *)` (no `dotnet add` / `dotnet new`) |
| `source_suffixes` | `(".cs",)` |
| Brief | C# 9-safe defaults (block namespaces; the project's CLAUDE.md / LangVersion wins), Trait tagging, stub style, "fail because of the NotImplementedException", property tests as `[Theory]` + `[MemberData]` from a fixed seed (FsCheck `[Property]` only if a test project references FsCheck), the exact filtered test command, forbidden patterns |
| `init` | Appends the `[dotnet]` section, adds `bin/`, `obj/`, `TestResults/` to `.gitignore`, prints the review-tests advice and a CI snippet with `actions/setup-dotnet@v4` (`global-json-file` when `global.json` exists, else `10.0.x`) |

Why lint/format only on changed files and no `-warnaserror`: an existing project usually has
formatting drift and warnings in old code; checking the whole solution would fail every feature
for reasons the agent may not touch. The frame (tests-approved tag) defines what the agent changed,
so the gates hold exactly that code to the standard. `--no-incremental` makes the compiler re-emit
warnings for code an earlier step already built.

### `[dotnet]` keys

| Key | Default | Meaning |
|---|---|---|
| `executable` | auto | Path to `dotnet` |
| `solution` | the single `*.sln`/`*.slnx` in the root | Build target and, without `test_projects`, test target |
| `test_projects` | `[]` (= the solution) | Test project paths; each gets its own `dotnet test` (faster than the solution when it has non-test projects) |
| `configuration` | `"Release"` | `-c` for build, test and Stryker |
| `base_filter` | `""` | VSTest filter ANDed into every run (agent command, step gate, Stop hook, red stubs, review tests, Stryker), e.g. `"Category!=Slow&Category!=Gate"` |
| `final_filter` | `""` | Filter of the final full suite + coverage (`""` = every test) |
| `build_before_test` | `true` | Build first, then `dotnet test --no-build` (clear compile-error reports) |
| `warnings` | `"changed"` | typecheck: `"changed"` / `"any"` / `"ignore"` |
| `build_timeout` | `900` | Seconds per build / format / diff-cover command |
| `test_timeout` | `3600` | Seconds per `dotnet test` |
| `mutation_timeout` | `[gates] mutation_timeout` (3600) | Seconds per Stryker run |
| `mutation_level` | `""` (Stryker's default, Standard) | Stryker `--mutation-level`: `Basic` / `Standard` / `Advanced` / `Complete`. `Basic` is faster on a big project but creates fewer mutants in the changed files too (a weaker gate); see the Stryker timing below |

### Notes for a first project (checked against FCSim, read-only)

- Detection, solution (`FCSim.slnx`) and test project (`tests/FCSim.Tests`) are found
  automatically; coverlet.collector is referenced; no JunitXml logger -> TRX.
- Suggested config: `[paths] review_tests = "tests/FCSim.Tests/Review"`,
  `[dotnet] base_filter = "Category!=Slow&Category!=Gate"`,
  `test_projects = ["tests/FCSim.Tests/FCSim.Tests.csproj"]` (avoids building the Runner for
  every test run; the typecheck still builds the solution), and `final_filter = "Category!=Gate"`
  if the gate tests are too slow for every feature.
- Builds run `CopyToUnity`: `dotnet build` copies the core DLL and content into `unity/Assets`
  (gitignored), which makes an open Unity editor reimport. The typecheck uses `--no-incremental`.
- agentflow writes to `[paths] out` (default `out/`, gitignored): `out/<f>/`, `out/.dotnet/`,
  `out/coverage/`, `out/runs/`. FCSim's runner also writes to `out/`; set `[paths] out` to e.g.
  `"out/agentflow"` to keep them apart.
- `dotnet` must be on the agents' PATH: the task prompts and allowed tools use plain `dotnet`.

### Measured on FCSim (live smoke run 2026-11-09, 1,328 dev-loop tests)

| Run | Time |
|---|---|
| Step gate (lint, format, typecheck `--no-incremental`, frame diff, step tests) | 12 s standalone, 24 s in the run |
| of which: `dotnet build FCSim.slnx -c Release --no-incremental` (warm build server) / filtered `dotnet test` | 6.2 s / 3.6 s |
| Final suite with coverlet, `final_filter = ""` (every test incl. Slow + Gate) | > 30 min, stopped (plain suite: ~4-5 min) |
| Final suite with coverlet, dev-loop filter | > 20 min, stopped (plain: ~1 min) |
| Final gates with coverage + Stryker restricted to the feature's `Req` tests | 142 s (Stryker 104 s, 6/6 mutants killed) |
| Stryker with the dev-loop filter as `test-case-filter` | initial test run 9.3 min, coverage capture failed, stopped at 12 min |

Stryker creates mutants for the whole project (40,273 in FCSim.Core); `--mutate` leaves 6 to test
("Removed by mutate filter"), so the cost is the test runs, not the mutant count. Coverage and
mutation over the full FCSim suite are not practical per feature, hence the default feature
scope (`[gates] coverage_scope` / `mutation_scope = "feature"`): coverage and Stryker use only the
feature's `Req` tests + review classes, the regression run is the plain suite (~1 min dev-loop
filter, ~4-5 min every test).

Re-measured live with the feature-scoped default (three-gate smoke run 2026-11-09, toy feature,
`final_filter = ""`): final gates 20 min 45 s, all PASS. Per check: lint 13 s, format 5 s,
typecheck 10 s, frame diff 0.2 s, **regression (every test incl. Slow + Gate) 692 s**, coverage
run (feature tests) 12 s, diff coverage 4 s, traceability 1.5 s, smells 2.5 s, **Stryker (feature
tests) 506 s** (~6 min of it before the first mutant test run: Stryker's own solution build;
17/19 mutants killed). The feature-scoped coverage is fast; the whole-suite regression and the
Stryker build dominate. Options: `final_filter` = the dev-loop filter (regression ~1 min, Slow +
Gate left to CI), Stryker on a warm build. Step gates: 34 s each.

### Stryker timing on FCSim (2026-11-10, Stryker.NET 5.0.0, no claude runs)

Throwaway worktree of FCSim `feat/agentflow-setup` + one 3-branch `Clamp` function in
FCSim.Core and 3 `Req`-tagged tests; warm Release build; the adapter's mutation check (feature
scope, `test-case-filter` = `(Req=PRB-R-001)&(Category!=Slow&Category!=Gate)`).

| Run | Seconds | Mutants tested / score |
|---|---|---|
| Adapter, as before (`--skip-version-check`, `-tp`, `--project`, `--mutate **/<file>`) | 99 | 6, 66.7% |
| Same, right after the regression (160 s) + coverage runs (live final-gate order) | 99 | 6 |
| `--since:<branch>` (diff mode) | 138 | 0 (the new file is untracked; diff mode still mutates the whole project) |
| Project mode (Stryker started in the test project's folder: no solution analysis, builds only the test project) | 99 | 6 |
| `-c 12` (concurrency, default 6) | 106 | 6 |
| `--mutation-level Basic` (CLI / adapter with `[dotnet] mutation_level = "Basic"`) | 65 / 66 | 4, 50% |

Where the 99 s go (debug log): analysis 4 s, `dotnet build FCSim.slnx` 11 s (warm), initial test
run 1 s, mutant creation 13 s, **compiling the mutated FCSim.Core 67 s** (12 rounds: mutants in
other files cause compile errors, Stryker rolls them back and recompiles), 6 mutant test runs
5 s. Stryker 5.0 creates mutants for the whole project (40,273) and applies `--mutate` only
afterwards ("37,569 removed by mutate filter"), so the compile rounds are a fixed cost per run
that no CLI option removes without weakening the gate; there is no `--no-build`. The live 506 s
did not reproduce on a warm build in the live order (99 s); likely a cold build or a busy machine
(the live Stryker log is gone). Kept: the adapter's command; added the opt-in
`[dotnet] mutation_level`.
