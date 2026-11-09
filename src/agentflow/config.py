"""Load and validate agentflow.toml. Defaults equal the constants of the original setup guide."""

from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

CONFIG_FILE = "agentflow.toml"
PHASES = ("SPEC", "TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW", "TRIAGE", "PR", "RETRO")
DEFAULT_ID_PATTERN = r"\b[A-Z][A-Z0-9]{1,9}-[REI]-\d{3}\b"

# section -> key -> accepted python type(s). "phases.<PHASE>" is handled separately.
SCHEMA: dict[str, dict[str, tuple[type, ...]]] = {
    "project": {"language": (str,)},
    "paths": {
        "src": (str,),
        "tests": (str,),
        "review_tests": (str,),
        "specs": (str,),
        "out": (str,),
        "metrics": (str,),
        "phase_file": (str,),
        "reqs_file": (str,),
    },
    "env": {"python": (str,), "wsl_distro": (str,)},
    "agents": {
        "claude": (str,),
        "max_attempts": (int,),
        "max_turns": (int,),
        "max_budget_usd": (int, float),
        "permission_mode": (str,),
        "test_review_rounds": (int,),
    },
    "gates": {
        "tests": (str,),
        "diff_coverage": (int, float),
        "mutation_score": (int, float),
        "mutation_tool": (str,),
        "mutation_runner": (str,),
        "coverage_scope": (str,),
        "mutation_scope": (str,),
        "regression_timeout": (int,),
        "coverage_timeout": (int,),
        "mutation_timeout": (int,),
        "commands": (dict,),
    },
    "protected": {"paths": (list,), "extra": (list,), "exceptions": (list,)},
    "frame": {"forbidden_patterns": (list,)},
    "spec": {"id_pattern": (str,)},
    "roles": {"extra_dir": (str,)},
    "phases": {},
    # language adapter sections: validated here, read by the adapter (cfg.language_options)
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
    },
}
#: sections owned by a language adapter (stored raw in Config.language_options)
LANGUAGE_SECTIONS = ("dotnet",)
GATE_COMMAND_KEYS = ("lint", "format", "typecheck", "imports")
MUTATION_RUNNERS = ("auto", "native", "wsl")
#: tests the final coverage / mutation runs use: the feature's tests (its spec IDs + the review
#: tests; the whole suite then runs once more without instrumentation) or the whole suite
GATE_SCOPES = ("feature", "suite")
#: [gates] tests: "human" = always stop at the tests gate; "auto" = pass it without a stop when
#: the mechanical checks pass, the test reviewer has no objections and no question is open
TESTS_GATE_MODES = ("human", "auto")
PHASE_KEYS = ("write", "tools", "max_turns", "max_budget_usd")
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
    "PR": 30,
    "RETRO": 30,
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
    "PR": 1.5,
    "RETRO": 3.0,
}
FALLBACK_MAX_BUDGET_USD = 2.0


class ConfigError(Exception):
    """agentflow.toml is missing or invalid."""


@dataclass
class Paths:
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
    python: str | None = None
    #: WSL distribution for mutation_runner = "wsl" (None = the default distribution)
    wsl_distro: str | None = None
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
    #: [gates] tests: "human" | "auto" (see TESTS_GATE_MODES)
    tests_gate: str = "human"
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
    #: raw, type-checked adapter sections, e.g. {"dotnet": {"configuration": "Release"}}
    language_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    loaded_from_file: bool = False

    # ---- paths ----
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

    def turn_cap(self, phase: str) -> int:
        """--max-turns of an agent run in this phase: [phases.<P>] max_turns, else
        [agents] max_turns, else the phase default."""
        if phase in self.phase_max_turns:
            return self.phase_max_turns[phase]
        if self.max_turns is not None:
            return self.max_turns
        return DEFAULT_MAX_TURNS.get(phase, FALLBACK_MAX_TURNS)

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
        defaults = {
            "SPEC": [p.specs, p.src],
            "TESTS": [p.tests, p.out],
            "TEST_REVIEW": [p.out],
            "IMPLEMENT": [p.src, p.out],
            "REVIEW": [p.review_tests, p.out],
            "TRIAGE": [p.review_tests, p.out],
            "PR": [p.out],
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
    """Walk up from `start` to the first directory that contains agentflow.toml."""
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


def parse_config(root: pathlib.Path, data: dict[str, Any]) -> Config:
    cfg = Config(root=root)
    for section, body in data.items():
        if section not in SCHEMA:
            raise ConfigError(f"unknown section [{section}] (allowed: {', '.join(SCHEMA)})")
        if not isinstance(body, dict):
            raise ConfigError(f"[{section}] must be a table")
        if section != "phases":
            _check_types(section, body)

    proj = data.get("project", {})
    cfg.language = proj.get("language", cfg.language)

    for key, value in data.get("paths", {}).items():
        setattr(cfg.paths, key, _norm(value).rstrip("/"))

    cfg.python = data.get("env", {}).get("python")
    cfg.wsl_distro = data.get("env", {}).get("wsl_distro")

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

    gates = data.get("gates", {})
    cfg.tests_gate = gates.get("tests", cfg.tests_gate)
    if cfg.tests_gate not in TESTS_GATE_MODES:
        raise ConfigError(
            f"[gates] tests must be one of {', '.join(TESTS_GATE_MODES)}, got '{cfg.tests_gate}'"
        )
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
    for key in ("regression_timeout", "coverage_timeout", "mutation_timeout"):
        value = gates.get(key, getattr(cfg, key))
        if value < 1:
            raise ConfigError(f"[gates] {key} must be >= 1")
        setattr(cfg, key, value)
    for key, cmd in gates.get("commands", {}).items():
        if key not in GATE_COMMAND_KEYS:
            raise ConfigError(
                f"[gates.commands] unknown key '{key}' (allowed: {GATE_COMMAND_KEYS})"
            )
        cfg.gate_commands[key] = _str_list(f"[gates.commands] {key}", cmd)

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

    cfg.id_pattern = data.get("spec", {}).get("id_pattern", cfg.id_pattern)
    cfg.roles_extra_dir = data.get("roles", {}).get("extra_dir")
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
            target = cfg.phase_write if key == "write" else cfg.phase_tools
            target[phase] = _str_list(f"[phases.{phase}] {key}", value)

    env_claude = os.environ.get("AGENTFLOW_CLAUDE")
    if env_claude:
        cfg.claude = env_claude
    return cfg


def load_config(root: pathlib.Path, *, require: bool = True) -> Config:
    path = root / CONFIG_FILE
    if not path.is_file():
        if require:
            raise ConfigError(f"{CONFIG_FILE} not found in {root}. Run `agentflow init` first.")
        return parse_config(root, {})
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    cfg = parse_config(root, data)
    cfg.loaded_from_file = True
    return cfg
