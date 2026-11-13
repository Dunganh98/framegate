"""Load and validate framegate.toml. Defaults equal the constants of the original setup guide."""

from __future__ import annotations

import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

CONFIG_FILE = "framegate.toml"
PHASES = (
    "SPEC",
    "SPEC_UPDATE",
    "TESTS",
    "TEST_REVIEW",
    "IMPLEMENT",
    "REVIEW",
    "TRIAGE",
    "DIAGNOSIS",
    "PR",
    "RETRO",
)
DEFAULT_ID_PATTERN = r"\b[A-Z][A-Z0-9]{1,9}-[REI]-\d{3}\b"

# section -> key -> accepted python type(s). "phases.<PHASE>" is handled separately.
SCHEMA: dict[str, dict[str, tuple[type, ...]]] = {
    "project": {"language": (str,), "knowledge": (str,)},
    "paths": {
        "src": (str, list),
        "tests": (str,),
        "review_tests": (str,),
        "specs": (str,),
        "out": (str,),
        "metrics": (str,),
        "phase_file": (str,),
        "reqs_file": (str,),
    },
    "env": {"python": (str,), "wsl_distro": (str,), "refresh": (list,)},
    "agents": {
        "claude": (str,),
        "max_attempts": (int,),
        "max_turns": (int,),
        "max_budget_usd": (int, float),
        "permission_mode": (str,),
        "test_review_rounds": (int,),
        "model": (str,),
        "effort": (str,),
        "step_batch_ids": (int,),
        "diagnose_after": (int,),
        "limit_wait_max_hours": (int, float),
    },
    "gates": {
        "tests": (str,),
        "final": (str,),
        "final_repair": (bool,),
        "diff_coverage": (int, float),
        "mutation_score": (int, float),
        "mutation_tool": (str,),
        "mutation_runner": (str,),
        "coverage_scope": (str,),
        "mutation_scope": (str,),
        "regression_timeout": (int,),
        "regression_baseline": (bool,),
        "coverage_timeout": (int,),
        "mutation_timeout": (int,),
        "commands": (dict,),
        "format_fix": (bool,),
        "extra": (dict,),
        "extra_timeout": (int,),
        "max_file_lines": (int,),
        "step_tests": (str,),
        "unreached_assertions": (bool,),
    },
    "risk": {
        "max_changed_lines": (int,),
        "max_changed_files": (int,),
        "max_removed_lines": (int,),
        "max_assumptions": (int,),
        "min_features": (int,),
        "never_auto": (list,),
    },
    "docs": {"code_map": (str,)},
    "protected": {"paths": (list,), "extra": (list,), "exceptions": (list,)},
    "frame": {"forbidden_patterns": (list,)},
    "spec": {"id_pattern": (str,)},
    "roles": {"extra_dir": (str,)},
    "git": {"base": (str,), "branch_prefix": (str,), "branch": (bool,), "markers": (str,)},
    "notify": {"toast": (bool,)},
    "phases": {},
    # name -> path, or name -> {path, note}: checked in parse_references
    "references": {},
    # language adapter sections: validated here, read by the adapter (cfg.language_options)
    # generic adapter: any language, commands with placeholders (adapters/generic.py)
    "generic": {
        "source_suffixes": (list,),
        "test_files": (list,),
        "tag_pattern": (str,),
        "skip_pattern": (str,),
        "test_all": (list,),
        "test_ids": (list,),
        "test_paths": (list,),
        "ids_separator": (str,),
        "stub_marker": (str,),
        "coverage": (list,),
        "coverage_ids": (list,),
        "coverage_report": (str,),
        "mutation": (list,),
        "mutation_score_pattern": (str,),
        "format_fix": (list,),
        "forbidden_patterns": (list,),
        "protected": (list,),
        "conventions": (str,),
        "test_timeout": (int,),
    },
    "python": {"pytest_args": (list,)},
    "dotnet": {
        "executable": (str,),
        "solution": (str,),
        "test_projects": (list,),
        "configuration": (str,),
        "base_filter": (str,),
        "final_filter": (str,),
        "build_before_test": (bool,),
        "warnings": (str,),
        "build_timeout": (int,),
        "test_timeout": (int,),
        "mutation_timeout": (int,),
        "mutation_level": (str,),
        "coverage_files": (str,),
    },
}
#: sections owned by a language adapter (stored raw in Config.language_options)
LANGUAGE_SECTIONS = ("dotnet", "generic", "python")
GATE_COMMAND_KEYS = ("lint", "format", "typecheck", "imports")
MUTATION_RUNNERS = ("auto", "native", "wsl")
#: [git] markers: how spec-approved/<f>, tests-approved/<f>, ... are stored. "refs" = private
#: refs (refs/<name>): no `git push`, `--tags` or `--all` publishes them; "tags" = git tags,
#: for a CI job that runs `framegate gates` and gets them through `git push --tags`
MARKER_KINDS = ("refs", "tags")
#: tests the final coverage / mutation runs use: the feature's tests (its spec IDs + the review
#: tests; the whole suite then runs once more without instrumentation) or the whole suite
GATE_SCOPES = ("feature", "suite")
#: [gates] step_tests: "suite" (default) = a step gate also runs the whole suite (the adapter's
#: everyday selection), with the tests of this feature's later steps left out: a step that
#: breaks another feature's tests fails at once, not at the final gate. "feature" = only the
#: tests of the step's and the earlier steps' IDs (a suite too slow to run after every step)
STEP_TESTS = ("suite", "feature")
#: [gates] tests: "defer" (default) = never stop at the tests gate: the test reviewer has checked
#: the tests against the spec; open questions, objections left and failed test checks go to the
#: final gate (two human gates: spec and final); "human" = always stop at the tests gate;
#: "auto" = pass it without a stop when the mechanical checks pass, the test reviewer has no
#: objections and no question is open (else stop as "human")
TESTS_GATE_MODES = ("defer", "human", "auto")
#: [gates] final: "human" (default) = the final gate always waits for the human; "auto" = a
#: feature the engine rates low risk (risk.py: clean run, small additive change, measured
#: coverage and mutation, a track record) is approved without a stop; anything else waits
FINAL_GATE_MODES = ("human", "auto")
PHASE_KEYS = ("write", "tools", "max_turns", "max_budget_usd", "model", "effort")
#: `claude --effort` levels
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
#: `claude --model` per phase when neither [phases.<P>] model nor [agents] model is set: the
#: writers that follow the approved spec and frozen tests run on Sonnet (half the token price of
#: Opus); the checkers (test reviewer, adversarial reviewer, triage) and the retro stay on Opus,
#: because a weaker checker defeats the frame. Aliases follow the latest model of each family.
DEFAULT_MODELS = {
    "TESTS": "sonnet",
    "IMPLEMENT": "sonnet",
    "SPEC_UPDATE": "sonnet",
    "PR": "sonnet",
    "TEST_REVIEW": "opus",
    "REVIEW": "opus",
    "TRIAGE": "opus",
    "DIAGNOSIS": "opus",
    "RETRO": "opus",
}
#: --max-turns per phase when neither [phases.<P>] max_turns nor [agents] max_turns is set.
#: --max-turns caps the model's API round trips (one assistant message, with any number of
#: parallel tool calls). The result's `num_turns` counts messages (prompt + one per tool result),
#: so it runs higher: the live RETRO with `num_turns` 46 made 23 API round trips under a cap of 30
#: (docs/claude-cli-notes.md). RETRO and PR only read and write one file; TEST_REVIEW (test
#: reviewer) and TRIAGE (the test reviewer triaging review findings) too.
DEFAULT_MAX_TURNS = {
    "TESTS": 50,
    "TEST_REVIEW": 30,
    "IMPLEMENT": 80,
    "REVIEW": 40,
    "TRIAGE": 30,
    "DIAGNOSIS": 40,
    "PR": 30,
    "RETRO": 30,
    "SPEC_UPDATE": 30,
}
FALLBACK_MAX_TURNS = 80
#: --max-budget-usd per agent run when neither [phases.<P>] max_budget_usd nor
#: [agents] max_budget_usd is set: about 3x the live FCSim costs per run (TESTS 0.69, TEST_REVIEW
#: 0.42, IMPLEMENT 0.40, REVIEW 0.46, PR 0.39, RETRO 1.15 USD; 2026-11-09). 0 = no cap.
DEFAULT_MAX_BUDGET_USD = {
    "TESTS": 2.0,
    "TEST_REVIEW": 1.5,
    "IMPLEMENT": 1.5,
    "REVIEW": 1.5,
    "TRIAGE": 1.5,
    "DIAGNOSIS": 2.0,
    "PR": 1.5,
    "RETRO": 3.0,
    "SPEC_UPDATE": 1.0,
}
FALLBACK_MAX_BUDGET_USD = 2.0


class ConfigError(Exception):
    """framegate.toml is missing or invalid."""


@dataclass
class Reference:
    """[references] <name>: a folder outside this repository (a sibling project) that the
    agents may read, never write. A spec names the ones its feature uses (references.py)."""

    name: str
    #: as written in framegate.toml: absolute, or relative to the repository root
    path: str
    #: one line: what this project is to the repository ("the runner this repo ports")
    note: str = ""


@dataclass
class Risk:
    """[risk]: when the engine rates a finished feature low risk (risk.py). Every limit must
    hold; a limit of 0 switches that test off."""

    #: changed source lines (added + removed) and source files since the tests were approved
    max_changed_lines: int = 200
    max_changed_files: int = 8
    #: removed lines in source files that existed before the feature (an additive change)
    max_removed_lines: int = 30
    #: assumptions the implementer logged (each is a spec gap it filled alone)
    max_assumptions: int = 3
    #: finished features in this repository before any feature counts as low risk
    min_features: int = 3
    #: paths (prefixes or globs) whose change is never low risk
    never_auto: list[str] = field(default_factory=list)


@dataclass
class Paths:
    #: the first source folder ([paths] src; a list there: Config.src_roots holds them all)
    src: str = "src"
    tests: str = "tests"
    review_tests: str = "tests/review"
    specs: str = "specs"
    out: str = "out"
    metrics: str = "metrics"
    phase_file: str = ".phase"
    reqs_file: str = ".reqs"


@dataclass
class Config:
    root: pathlib.Path
    language: str = "python"
    paths: Paths = field(default_factory=Paths)
    #: source folders after the first ([paths] src as a list, e.g. a Godot project's core/,
    #: autoload/, scenes/); all of them: src_roots
    extra_src: list[str] = field(default_factory=list)
    python: str | None = None
    #: WSL distribution for mutation_runner = "wsl" (None = the default distribution)
    wsl_distro: str | None = None
    #: [env] refresh: a command `approve-spec` runs before it commits (e.g. the editable
    #: install again after the spec phase added a package); [] = none. A failure refuses
    #: the approval
    env_refresh: list[str] = field(default_factory=list)
    claude: str = "claude"
    max_attempts: int = 3
    #: [agents] max_turns: one cap for every phase without its own (None = DEFAULT_MAX_TURNS)
    max_turns: int | None = None
    #: [agents] max_budget_usd: one cost cap per run for every phase without its own
    #: (None = DEFAULT_MAX_BUDGET_USD; 0 = no cap)
    max_budget_usd: float | None = None
    permission_mode: str = "acceptEdits"
    #: how often the test reviewer's objections go back to the test writer before the gate
    test_review_rounds: int = 2
    #: [agents] model / effort: one `claude --model` / `--effort` for every phase without its own
    #: (model None = DEFAULT_MODELS; effort None = Claude Code's default)
    model: str | None = None
    effort: str | None = None
    phase_model: dict[str, str] = field(default_factory=dict)
    phase_effort: dict[str, str] = field(default_factory=dict)
    #: [agents] step_batch_ids: consecutive plan steps share one implementer run while their
    #: spec IDs add up to at most this many (a small plan = one run); 0 = one run per step
    step_batch_ids: int = 15
    #: [agents] diagnose_after: when the same checks fail this many attempts in a row, a
    #: diagnosis agent finds the root cause and proposes one fix (0 = off)
    diagnose_after: int = 2
    #: [agents] limit_wait_max_hours: a run refused by the usage limit waits for the reset and
    #: runs again (not a failed attempt) when the reset is at most this far away; a later
    #: reset (e.g. a weekly limit) stops the run like Ctrl+C (resume: framegate run). 0 = never
    #: wait
    limit_wait_max_hours: float = 6.0
    #: [project] knowledge: the project's knowledge file (architecture rules, things that
    #: must never change, test-harness facts, tools); read by the agents, grown by the retro
    knowledge: str = ".framegate/project.md"
    #: [gates] format_fix: the gates apply the formatter to the changed source files before
    #: checking (a formatting slip costs no implementer attempt)
    format_fix: bool = True
    #: [gates.commands] format_fix: the project's own formatter for that fix, `{files}` = the
    #: changed files (e.g. black where the project checks with black, not ruff format)
    format_fix_command: list[str] = field(default_factory=list)
    #: [gates] regression_baseline: `approve-spec` runs the whole suite once and records the
    #: tests that already fail; the final regression check then fails only on other failures
    #: (for a repository whose suite is not green locally, e.g. tests that need services)
    regression_baseline: bool = False
    #: [gates.extra] name -> command: project checks run at the final gates and shown at the
    #: final gate, never blocking (e.g. a balance measurement)
    extra_checks: dict[str, list[str]] = field(default_factory=dict)
    extra_timeout: int = 1800
    #: [gates] max_file_lines: a changed source file longer than this is listed in the final
    #: gate's structure report and asked about at the final gate (never blocking; 0 = off)
    max_file_lines: int = 400
    #: [gates] step_tests: "suite" | "feature" (see STEP_TESTS)
    step_tests: str = "suite"
    #: [gates] unreached_assertions: the final gate fails on an assertion of a test the
    #: feature added that the feature's own tests never ran (needs the coverage run)
    unreached_assertions: bool = True
    #: [docs] code_map: the living code map (per module: purpose, key classes, how they work
    #: together, where to look for X); drafted by /framegate:setup, updated by the PR phase
    code_map: str = "docs/code-map.md"
    #: [gates] tests: "defer" | "human" | "auto" (see TESTS_GATE_MODES)
    tests_gate: str = "defer"
    #: [gates] final: "human" | "auto" (see FINAL_GATE_MODES)
    final_gate: str = "human"
    #: [gates] final_repair: failed final gates get one repair run per owner (implementer for
    #: code failures, test writer for a weak diff-coverage / mutation score) before the human
    final_repair: bool = True
    risk: Risk = field(default_factory=Risk)
    diff_coverage: float = 90
    mutation_score: float = 80
    mutation_tool: str | None = None  # None -> the adapter's default tool
    #: where the mutation tool runs: auto | native | wsl (adapters that support it: python)
    mutation_runner: str = "auto"
    coverage_scope: str = "feature"
    mutation_scope: str = "feature"
    #: seconds per final-gate run before it is killed and reported as incomplete
    regression_timeout: int = 3600
    coverage_timeout: int = 1800
    mutation_timeout: int = 3600
    gate_commands: dict[str, list[str]] = field(default_factory=dict)
    protected_paths: list[str] | None = None  # None -> core + adapter defaults
    protected_extra: list[str] = field(default_factory=list)
    protected_exceptions: list[str] | None = None  # None -> [review_tests]
    forbidden_patterns: list[str] | None = None  # None -> adapter defaults
    id_pattern: str = DEFAULT_ID_PATTERN
    phase_write: dict[str, list[str]] = field(default_factory=dict)
    phase_tools: dict[str, list[str]] = field(default_factory=dict)
    phase_max_turns: dict[str, int] = field(default_factory=dict)
    phase_max_budget: dict[str, float] = field(default_factory=dict)
    roles_extra_dir: str | None = None
    #: [git] `framegate spec <f>` creates/switches to branch_prefix + f from `base` (branch=false:
    #: stay on the current branch)
    git_branch: bool = True
    git_base: str = "main"
    branch_prefix: str = "feat/"
    #: [git] markers: "refs" (private refs, never pushed) | "tags" (see MARKER_KINDS)
    git_markers: str = "refs"
    #: [notify] toast: Windows toast at each gate, DONE and on error (no-op elsewhere / on failure)
    toast: bool = True
    #: [references] name -> folder the agents may read (see Reference)
    references: dict[str, Reference] = field(default_factory=dict)
    #: raw, type-checked adapter sections, e.g. {"dotnet": {"configuration": "Release"}}
    language_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    loaded_from_file: bool = False

    # ---- paths ----
    @property
    def src_roots(self) -> list[str]:
        """Every source folder (posix, no trailing slash): [paths] src, one or a list."""
        return [self.paths.src, *self.extra_src]

    def in_src(self, rel: str) -> bool:
        """True for a repo-relative posix path inside one of the source folders."""
        return any(rel.startswith(root.rstrip("/") + "/") for root in self.src_roots)

    def path(self, rel: str) -> pathlib.Path:
        return self.root / rel

    @property
    def out_dir(self) -> pathlib.Path:
        return self.root / self.paths.out

    @property
    def phase_path(self) -> pathlib.Path:
        return self.root / self.paths.phase_file

    @property
    def reqs_path(self) -> pathlib.Path:
        return self.root / self.paths.reqs_file

    def feature_out(self, feature: str) -> pathlib.Path:
        return self.out_dir / feature

    def spec_dir(self, feature: str) -> pathlib.Path:
        return self.root / self.paths.specs / feature

    def reference_dir(self, name: str) -> pathlib.Path:
        """The folder of a declared reference, absolute (it may not exist on this machine)."""
        path = pathlib.Path(self.references[name].path).expanduser()
        return pathlib.Path(os.path.normpath(path if path.is_absolute() else self.root / path))

    def turn_cap(self, phase: str) -> int:
        """--max-turns of an agent run in this phase: [phases.<P>] max_turns, else
        [agents] max_turns, else the phase default."""
        if phase in self.phase_max_turns:
            return self.phase_max_turns[phase]
        if self.max_turns is not None:
            return self.max_turns
        return DEFAULT_MAX_TURNS.get(phase, FALLBACK_MAX_TURNS)

    def agent_model(self, phase: str) -> str:
        """`claude --model` for an agent run in this phase: [phases.<P>] model, else
        [agents] model, else DEFAULT_MODELS ("" = Claude Code's default)."""
        if phase in self.phase_model:
            return self.phase_model[phase]
        if self.model is not None:
            return self.model
        return DEFAULT_MODELS.get(phase, "")

    def agent_effort(self, phase: str) -> str:
        """`claude --effort` for this phase ("" = Claude Code's default)."""
        return self.phase_effort.get(phase, self.effort or "")

    def budget_cap(self, phase: str) -> float | None:
        """--max-budget-usd of an agent run in this phase: [phases.<P>] max_budget_usd, else
        [agents] max_budget_usd, else the phase default. None = no cap (configured 0)."""
        if phase in self.phase_max_budget:
            value = self.phase_max_budget[phase]
        elif self.max_budget_usd is not None:
            value = self.max_budget_usd
        else:
            value = DEFAULT_MAX_BUDGET_USD.get(phase, FALLBACK_MAX_BUDGET_USD)
        return value if value > 0 else None

    # ---- phase permissions ----
    def write_paths(self, phase: str) -> list[str]:
        """Path prefixes (posix, relative to root) an agent may write in this phase."""
        if phase in self.phase_write:
            return [_as_prefix(p) for p in self.phase_write[phase]]
        p = self.paths
        src = self.src_roots
        defaults = {
            "SPEC": [p.specs, *src],
            # the orchestrator reverts everything but specs/<f>/{requirements,design,plan}.md
            "SPEC_UPDATE": [p.specs, p.out],
            "TESTS": [p.tests, p.out],
            "TEST_REVIEW": [p.out],
            "IMPLEMENT": [*src, p.out],
            "REVIEW": [p.review_tests, p.out],
            "TRIAGE": [p.review_tests, p.out],
            "DIAGNOSIS": [p.out],
            "PR": [p.out, self.code_map],
            "RETRO": [p.out],
        }
        return [_as_prefix(x) for x in defaults.get(phase, [])]

    def frame_exceptions(self) -> list[str]:
        if self.protected_exceptions is not None:
            return [_norm(x) for x in self.protected_exceptions]
        return [_as_prefix(self.paths.review_tests)]


def _norm(p: str) -> str:
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _as_prefix(p: str) -> str:
    """Directory prefix with a trailing slash; file names (with a dot in the last part) as-is."""
    p = _norm(p)
    if p.endswith("/"):
        return p
    last = p.rsplit("/", 1)[-1]
    return p if "." in last and not last.startswith(".") else p + "/"


def find_root(start: str | os.PathLike[str] | None = None) -> pathlib.Path | None:
    """Walk up from `start` to the first directory that contains framegate.toml."""
    here = pathlib.Path(start or os.getcwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_FILE).is_file():
            return d
    return None


def _check_types(section: str, data: dict[str, Any]) -> None:
    allowed = SCHEMA[section]
    for key, value in data.items():
        if key not in allowed:
            raise ConfigError(f"[{section}] unknown key '{key}' (allowed: {', '.join(allowed)})")
        types = allowed[key]
        if (isinstance(value, bool) and bool not in types) or not isinstance(value, types):
            names = "/".join(t.__name__ for t in types)
            raise ConfigError(f"[{section}] {key} must be {names}, got {type(value).__name__}")


def _str_list(where: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{where} must be a list of strings")
    return list(value)


def _effort(where: str, value: Any) -> str:
    if value == "" or value in EFFORT_LEVELS:
        return str(value)
    raise ConfigError(f"{where} must be one of {', '.join(EFFORT_LEVELS)} (or empty)")


REFERENCE_NAME = re.compile(r"^[A-Za-z][\w-]*$")


def parse_references(body: dict[str, Any]) -> dict[str, Reference]:
    """[references]: `name = "<path>"` or `name = { path = "<path>", note = "<one line>" }`."""
    refs: dict[str, Reference] = {}
    for name, value in body.items():
        where = f"[references] {name}"
        if not REFERENCE_NAME.match(name):
            raise ConfigError(f"{where}: a name is letters, digits, '-' and '_'")
        if isinstance(value, str):
            value = {"path": value}
        if not isinstance(value, dict) or set(value) - {"path", "note"}:
            raise ConfigError(f'{where} must be a path or {{ path = "...", note = "..." }}')
        path, note = value.get("path"), value.get("note", "")
        if not isinstance(path, str) or not path.strip():
            raise ConfigError(f"{where} needs a path (a folder outside this repository)")
        if not isinstance(note, str):
            raise ConfigError(f"{where}: note must be a string")
        refs[name] = Reference(name, path.strip(), " ".join(note.split()))
    return refs


def parse_config(root: pathlib.Path, data: dict[str, Any]) -> Config:
    cfg = Config(root=root)
    for section, body in data.items():
        if section not in SCHEMA:
            raise ConfigError(f"unknown section [{section}] (allowed: {', '.join(SCHEMA)})")
        if not isinstance(body, dict):
            raise ConfigError(f"[{section}] must be a table")
        if section not in ("phases", "references"):
            _check_types(section, body)

    proj = data.get("project", {})
    cfg.language = proj.get("language", cfg.language)
    cfg.knowledge = _norm(proj.get("knowledge", cfg.knowledge))

    for key, value in data.get("paths", {}).items():
        if key == "src":
            roots = [value] if isinstance(value, str) else _str_list("[paths] src", value)
            roots = [_norm(r).rstrip("/") for r in roots if r.strip()]
            if not roots:
                raise ConfigError("[paths] src must name at least one folder")
            cfg.paths.src, cfg.extra_src = roots[0], roots[1:]
            continue
        setattr(cfg.paths, key, _norm(value).rstrip("/"))

    cfg.python = data.get("env", {}).get("python")
    cfg.wsl_distro = data.get("env", {}).get("wsl_distro")
    cfg.env_refresh = _str_list("[env] refresh", data.get("env", {}).get("refresh", []))

    agents = data.get("agents", {})
    cfg.claude = agents.get("claude", cfg.claude)
    cfg.max_attempts = agents.get("max_attempts", cfg.max_attempts)
    cfg.max_turns = agents.get("max_turns", cfg.max_turns)
    if "max_budget_usd" in agents:
        cfg.max_budget_usd = float(agents["max_budget_usd"])
        if cfg.max_budget_usd < 0:
            raise ConfigError("[agents] max_budget_usd must be >= 0 (0 = no cap)")
    cfg.permission_mode = agents.get("permission_mode", cfg.permission_mode)
    if cfg.max_attempts < 1 or (cfg.max_turns is not None and cfg.max_turns < 1):
        raise ConfigError("[agents] max_attempts and max_turns must be >= 1")
    cfg.test_review_rounds = agents.get("test_review_rounds", cfg.test_review_rounds)
    if cfg.test_review_rounds < 0:
        raise ConfigError("[agents] test_review_rounds must be >= 0")
    if "model" in agents:
        cfg.model = agents["model"].strip()
    if "effort" in agents:
        cfg.effort = _effort("[agents] effort", agents["effort"])
    cfg.step_batch_ids = agents.get("step_batch_ids", cfg.step_batch_ids)
    if cfg.step_batch_ids < 0:
        raise ConfigError("[agents] step_batch_ids must be >= 0 (0 = one run per step)")
    cfg.diagnose_after = agents.get("diagnose_after", cfg.diagnose_after)
    if cfg.diagnose_after < 0:
        raise ConfigError("[agents] diagnose_after must be >= 0 (0 = no diagnosis)")
    cfg.limit_wait_max_hours = float(agents.get("limit_wait_max_hours", cfg.limit_wait_max_hours))
    if cfg.limit_wait_max_hours < 0:
        raise ConfigError("[agents] limit_wait_max_hours must be >= 0 (0 = never wait)")

    gates = data.get("gates", {})
    cfg.tests_gate = gates.get("tests", cfg.tests_gate)
    if cfg.tests_gate not in TESTS_GATE_MODES:
        raise ConfigError(
            f"[gates] tests must be one of {', '.join(TESTS_GATE_MODES)}, got '{cfg.tests_gate}'"
        )
    cfg.final_gate = gates.get("final", cfg.final_gate)
    if cfg.final_gate not in FINAL_GATE_MODES:
        raise ConfigError(
            f"[gates] final must be one of {', '.join(FINAL_GATE_MODES)}, got '{cfg.final_gate}'"
        )
    cfg.final_repair = gates.get("final_repair", cfg.final_repair)
    risk = data.get("risk", {})
    for key in (
        "max_changed_lines",
        "max_changed_files",
        "max_removed_lines",
        "max_assumptions",
        "min_features",
    ):
        value = risk.get(key, getattr(cfg.risk, key))
        if value < 0:
            raise ConfigError(f"[risk] {key} must be >= 0 (0 = this test is off)")
        setattr(cfg.risk, key, value)
    cfg.risk.never_auto = [
        _norm(x) for x in _str_list("[risk] never_auto", risk.get("never_auto", []))
    ]
    cfg.diff_coverage = gates.get("diff_coverage", cfg.diff_coverage)
    cfg.mutation_score = gates.get("mutation_score", cfg.mutation_score)
    cfg.mutation_tool = gates.get("mutation_tool", cfg.mutation_tool)
    cfg.mutation_runner = gates.get("mutation_runner", cfg.mutation_runner)
    if cfg.mutation_runner not in MUTATION_RUNNERS:
        raise ConfigError(
            f"[gates] mutation_runner must be one of {', '.join(MUTATION_RUNNERS)}, "
            f"got '{cfg.mutation_runner}'"
        )
    for key in ("coverage_scope", "mutation_scope"):
        value = gates.get(key, getattr(cfg, key))
        if value not in GATE_SCOPES:
            raise ConfigError(
                f"[gates] {key} must be one of {', '.join(GATE_SCOPES)}, got '{value}'"
            )
        setattr(cfg, key, value)
    cfg.format_fix = gates.get("format_fix", cfg.format_fix)
    cfg.step_tests = gates.get("step_tests", cfg.step_tests)
    if cfg.step_tests not in STEP_TESTS:
        raise ConfigError(
            f"[gates] step_tests must be one of {', '.join(STEP_TESTS)}, got '{cfg.step_tests}'"
        )
    cfg.unreached_assertions = gates.get("unreached_assertions", cfg.unreached_assertions)
    cfg.max_file_lines = gates.get("max_file_lines", cfg.max_file_lines)
    if cfg.max_file_lines < 0:
        raise ConfigError("[gates] max_file_lines must be >= 0 (0 = no file size question)")
    for key in ("regression_timeout", "coverage_timeout", "mutation_timeout", "extra_timeout"):
        value = gates.get(key, getattr(cfg, key))
        if value < 1:
            raise ConfigError(f"[gates] {key} must be >= 1")
        setattr(cfg, key, value)
    cfg.regression_baseline = gates.get("regression_baseline", cfg.regression_baseline)
    for key, cmd in gates.get("commands", {}).items():
        if key == "format_fix":
            cfg.format_fix_command = _str_list("[gates.commands] format_fix", cmd)
            if cfg.format_fix_command and "{files}" not in cfg.format_fix_command:
                raise ConfigError(
                    '[gates.commands] format_fix needs a "{files}" argument (the changed files)'
                )
            continue
        if key not in GATE_COMMAND_KEYS:
            raise ConfigError(
                f"[gates.commands] unknown key '{key}' "
                f"(allowed: {(*GATE_COMMAND_KEYS, 'format_fix')})"
            )
        cfg.gate_commands[key] = _str_list(f"[gates.commands] {key}", cmd)
    for name, cmd in gates.get("extra", {}).items():
        argv = _str_list(f"[gates.extra] {name}", cmd)
        if not argv:
            raise ConfigError(f"[gates.extra] {name} must not be empty")
        cfg.extra_checks[name] = argv

    prot = data.get("protected", {})
    if "paths" in prot:
        cfg.protected_paths = [_norm(x) for x in _str_list("[protected] paths", prot["paths"])]
    cfg.protected_extra = [_norm(x) for x in _str_list("[protected] extra", prot.get("extra", []))]
    if "exceptions" in prot:
        cfg.protected_exceptions = _str_list("[protected] exceptions", prot["exceptions"])

    frame = data.get("frame", {})
    if "forbidden_patterns" in frame:
        cfg.forbidden_patterns = _str_list(
            "[frame] forbidden_patterns", frame["forbidden_patterns"]
        )

    code_map = data.get("docs", {}).get("code_map", cfg.code_map)
    if not code_map.strip():
        raise ConfigError("[docs] code_map must not be empty")
    cfg.code_map = _norm(code_map)
    cfg.id_pattern = data.get("spec", {}).get("id_pattern", cfg.id_pattern)
    cfg.roles_extra_dir = data.get("roles", {}).get("extra_dir")
    git_cfg = data.get("git", {})
    cfg.git_branch = git_cfg.get("branch", cfg.git_branch)
    cfg.git_base = git_cfg.get("base", cfg.git_base)
    cfg.branch_prefix = git_cfg.get("branch_prefix", cfg.branch_prefix)
    cfg.git_markers = git_cfg.get("markers", cfg.git_markers)
    if cfg.git_markers not in MARKER_KINDS:
        raise ConfigError(
            f"[git] markers must be one of {', '.join(MARKER_KINDS)}, got '{cfg.git_markers}'"
        )
    if not cfg.git_base.strip():
        raise ConfigError("[git] base must not be empty")
    cfg.toast = data.get("notify", {}).get("toast", cfg.toast)
    cfg.references = parse_references(data.get("references", {}))
    for section in LANGUAGE_SECTIONS:
        if section in data:
            body = dict(data[section])
            for key, value in body.items():
                if isinstance(value, list):
                    _str_list(f"[{section}] {key}", value)
            cfg.language_options[section] = body

    for phase, body in data.get("phases", {}).items():
        if phase not in PHASES:
            raise ConfigError(f"[phases.{phase}] unknown phase (allowed: {', '.join(PHASES)})")
        if not isinstance(body, dict):
            raise ConfigError(f"[phases.{phase}] must be a table")
        for key, value in body.items():
            if key not in PHASE_KEYS:
                raise ConfigError(f"[phases.{phase}] unknown key '{key}' (allowed: {PHASE_KEYS})")
            if key == "max_turns":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ConfigError(f"[phases.{phase}] max_turns must be an int >= 1")
                cfg.phase_max_turns[phase] = value
                continue
            if key == "max_budget_usd":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ConfigError(
                        f"[phases.{phase}] max_budget_usd must be a number >= 0 (0 = no cap)"
                    )
                cfg.phase_max_budget[phase] = float(value)
                continue
            if key == "model":
                if not isinstance(value, str):
                    raise ConfigError(f"[phases.{phase}] model must be a string")
                cfg.phase_model[phase] = value.strip()
                continue
            if key == "effort":
                cfg.phase_effort[phase] = _effort(f"[phases.{phase}] effort", value)
                continue
            target = cfg.phase_write if key == "write" else cfg.phase_tools
            target[phase] = _str_list(f"[phases.{phase}] {key}", value)

    env_claude = os.environ.get("FRAMEGATE_CLAUDE")
    if env_claude:
        cfg.claude = env_claude
    return cfg


def load_config(root: pathlib.Path, *, require: bool = True) -> Config:
    path = root / CONFIG_FILE
    if not path.is_file():
        if require:
            raise ConfigError(f"{CONFIG_FILE} not found in {root}. Run `framegate init` first.")
        return parse_config(root, {})
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    cfg = parse_config(root, data)
    cfg.loaded_from_file = True
    return cfg
