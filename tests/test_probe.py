"""`framegate probe`: the facts `/framegate:setup` shows for one confirmation, from instant
checks only (seen on a repo with a `.venv`, black and `python -m ruff`: the setup took
framegate's defaults without a word and the agents were told commands the project does not
use)."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from conftest import DemoRepo, git, init_git
from framegate import cli, probe
from framegate.adapters import get_adapter, python_probe
from framegate.adapters import python as python_adapter
from framegate.checks import frame_diff
from framegate.config import load_config, parse_config

BLACK_PROJECT = """\
[project]
name = "argos"
version = "0.1.0"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6", "black==24.8.0"]

[tool.black]
line-length = 100

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["checks", "tests"]
"""


def black_project(root: pathlib.Path) -> pathlib.Path:
    """A team's repo: packages in the root, black + ruff, no type checker, a `.venv`."""
    root.mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(BLACK_PROJECT, encoding="utf-8")
    for name in ("orchestrator", "runner", "tests", "deploy", "docs"):
        (root / name).mkdir()
    for name in ("orchestrator", "runner", "tests"):
        (root / name / "__init__.py").write_text("", encoding="utf-8")
    (root / "deploy" / "job.yml").write_text("x: 1\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python\n", encoding="utf-8")
    (root / "azure-pipelines.yml").write_text("steps: []\n", encoding="utf-8")
    exe = pathlib.Path(
        ".venv", *(("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python"))
    )
    (root / exe).parent.mkdir(parents=True)
    (root / exe).write_text("", encoding="utf-8")
    return root


class FakeTools:
    """Answers for the interpreter's fact script and for `--version` of the named modules."""

    def __init__(self, root: pathlib.Path, versions: dict[str, str]) -> None:
        self.root = root
        self.versions = versions
        self.commands: list[list[str]] = []
        self.editable = [["argos", root.as_uri()], ["other", (root.parent / "x").as_uri()]]

    def run(self, cmd: list[str], cwd: pathlib.Path, **kw: object) -> tuple[int, str]:
        self.commands.append(list(cmd))
        if "-c" in cmd:
            facts = {"version": "3.12.4", "pip": True, "editable": self.editable}
            return 0, json.dumps(facts)
        module = cmd[2] if cmd[1] == "-m" else cmd[0]
        if module in self.versions:
            return 0, f"{module} {self.versions[module]}\nmore"
        return 1, f"{cmd[0]}: No module named {module}"


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> FakeTools:
    root = black_project(tmp_path / "repo")
    fake = FakeTools(root, {"pytest": "8.3.2", "ruff": "0.6.9", "black": "24.8.0"})
    monkeypatch.setattr(probe, "run_command", fake.run)
    monkeypatch.setattr(python_adapter, "run_command", fake.run)
    return fake


def test_the_projects_own_tools_are_found_in_its_files(tmp_path: pathlib.Path) -> None:
    root = black_project(tmp_path / "repo")
    found = python_probe.detected_tools(root)
    assert found["format"] == (
        ["python", "-m", "black", "--check", "{src}", "{tests}"],
        "pyproject.toml [tool.black]",
    )
    assert found["format_fix"][0] == ["python", "-m", "black", "{files}"]
    assert found["lint"][0][:4] == ["python", "-m", "ruff", "check"]
    assert found["test"] == (["python", "-m", "pytest"], "pyproject.toml [tool.pytest.ini_options]")
    assert "typecheck" not in found  # the project names no type checker
    # packages in the root are the source folders; the first existing `testpaths` folder
    assert python_probe.guess_layout(root) == (["orchestrator", "runner"], "tests")


def test_other_tools_and_an_empty_project(tmp_path: pathlib.Path) -> None:
    assert python_probe.detected_tools(tmp_path) == {}
    assert python_probe.guess_layout(tmp_path) == (["src"], "tests")
    (tmp_path / "setup.cfg").write_text("[flake8]\nmax-line-length = 100\n", encoding="utf-8")
    (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest==8.0\nblack\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.format]\n", encoding="utf-8")
    found = python_probe.detected_tools(tmp_path)
    # ruff is configured: it lints, and its formatter wins over a black that is only installed
    assert found["lint"][0][2] == "ruff" and found["format"][0][2:4] == ["ruff", "format"]
    assert found["format"][1] == "pyproject.toml [tool.ruff.format]" and "format_fix" not in found
    assert found["typecheck"] == (["python", "-m", "pyright", "{src}"], "pyrightconfig.json")
    assert found["test"][1] == "requirements-dev.txt"
    (tmp_path / "pyproject.toml").write_text("not toml [", encoding="utf-8")
    assert python_probe.detected_tools(tmp_path)["lint"][0][2] == "flake8"


def test_probe_before_the_config_exists(fake_tools: FakeTools) -> None:
    root = fake_tools.root
    init_git(root)
    git(root, "remote", "add", "origin", "https://dev.azure.com/org/proj/_git/argos")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "one")
    lines: list[str] = []
    assert probe.run(start=root, echo=lines.append) == 0
    out = "\n".join(lines)
    assert lines[0].endswith("(no framegate.toml yet: proposals)")
    assert "Language: python (detected from the project's files)" in out
    interp = next(x for x in lines if x.startswith("Interpreter: "))
    assert ".venv" in interp and "[OK: Python 3.12.4] (the project's .venv)" in interp
    assert "Test: python -m pytest -q [OK: pytest 8.3.2] (pyproject.toml [tool." in out
    assert "Lint: python -m ruff check orchestrator runner tests [OK: ruff 0.6.9]" in out
    # the project's formatter, not framegate's default, with the lines that make it so
    i = lines.index(
        "Format: python -m black --check orchestrator runner tests [OK: black 24.8.0] "
        "(pyproject.toml [tool.black])"
    )
    assert lines[i + 1] == (
        "  framegate's default is `python -m ruff format --check orchestrator runner tests`; "
        "to use the project's tool set [gates.commands] "
        'format = ["python", "-m", "black", "--check", "{src}", "{tests}"], '
        'format_fix = ["python", "-m", "black", "{files}"]'
    )
    assert (
        "Types: python -m mypy orchestrator runner [MISSING] "
        "(framegate's default: the project's files do not name it)"
    ) in out
    assert "Source folders: orchestrator, runner (guessed from the folders)" in out
    assert "Test folder: tests (guessed from the folders)" in out
    assert "Base branch: main (a branch of that name; origin/HEAD is not set)" in out
    assert "Remote: origin on Azure DevOps, https://dev.azure.com/org/proj/_git/argos; 1 " in out
    assert "State branch: off; suggested: no (a remote, one author address)" in out
    # the built-in names that exist here, by their path; the folder is a proposal
    assert "Locked deploy / CI paths: built in: Dockerfile, azure-pipelines.yml" in out
    assert "Lock candidates ([protected] extra): deploy/ (a folder with a deploy / CI name)" in out
    assert (
        'Reinstall ([env] refresh): ["python", "-m", "pip", "install", "-e", ".", "--no-deps"] '
        "(candidate: argos is installed editable in the interpreter)"
    ) in out
    assert "References: none" in out and lines[-1].startswith("Files: framegate.toml missing")
    # every tool was asked in the project's interpreter, each once, and nothing else ran
    venv = str(root / ".venv")
    assert all(c[0].startswith(venv) for c in fake_tools.commands)
    asked = sorted(c[2] for c in fake_tools.commands if c[-1] == "--version")
    assert asked == ["black", "mypy", "pytest", "ruff"]
    assert len(fake_tools.commands) == 5 and not (root / "framegate.toml").exists()


def test_probe_with_a_config(demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    r = demo_repo
    fake = FakeTools(r.root, {"pytest": "8.3.2", "black": "24.8.0"})
    fake.editable = []
    monkeypatch.setattr(probe, "run_command", fake.run)
    monkeypatch.setattr(python_adapter, "run_command", fake.run)
    r.add_toml(
        '[gates.commands]\nlint = ["flake8", "{src}"]\ntypecheck = []\n'
        'format = ["python", "-m", "black", "--check", "{src}"]\n'
        '[env]\nrefresh = ["python", "-m", "pip", "install", "-e", "."]\n'
    )
    r.set_toml("protected", "extra", '["deploy/"]')
    r.set_toml("git", "base", '"dev"')
    data = probe.collect(r.root)
    assert data["config"] and data["language"] == {"name": "python", "source": "framegate.toml"}
    rows = {row["role"]: row for row in data["tools"]}
    assert rows["lint"]["command"] == "flake8 src" and rows["lint"]["answers"] == "missing"
    assert rows["lint"]["source"] == "framegate.toml [gates.commands]"
    assert rows["format"]["answers"] == "ok" and rows["format"]["version"] == "black 24.8.0"
    assert rows["typecheck"]["answers"] == "off"
    assert ["flake8", "--version"] in fake.commands  # asked as the gate runs it
    assert data["base"] == {
        "branch": "dev",
        "source": "framegate.toml [git] base",
        "guess": "main",
    }
    assert data["remote"] is None and data["state_branch"] == {"value": "", "suggested": False}
    assert data["locked"] == ["deploy/"] and data["editable"]["configured"]
    lines = probe.render(data)
    assert "Types: off (framegate.toml [gates.commands])" in lines
    assert (
        "Base branch: dev (framegate.toml [git] base; origin/HEAD or the usual name says main)"
        in lines
    )
    assert "Remote: none; 1 author address(es) in the last 200 commits" in lines
    assert any(
        x.startswith("Reinstall ([env] refresh): [") and "framegate.toml" in x for x in lines
    )
    assert "Files: framegate.toml exists, .framegate/project.md exists" in lines[-1]


def test_small_parts(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert probe.version_command(["py", "-m", "ruff", "check", "src"]) == [
        "py",
        "-m",
        "ruff",
        "--version",
    ]
    assert probe.version_command(["dotnet", "format", "x.sln"]) == ["dotnet", "--version"]
    answers = iter([(124, "TIMEOUT"), (127, "Tool not found"), (2, "usage"), (0, "\n v1.2 \nx")])
    monkeypatch.setattr(probe, "run_command", lambda *a, **k: next(answers))
    states = [probe.ask_version(["x", "--version"], tmp_path) for _ in range(4)]
    assert [s for s, _ in states] == ["timeout", "missing", "no answer", "ok"]
    assert states[-1][1] == "v1.2"
    # outside git and with no known language: still an answer, nothing guessed
    lines = probe.render(probe.collect(tmp_path))
    assert "Language: unknown (not detected)" in lines
    assert "Base branch: unknown (no branch found)" in lines
    assert not any(x.startswith(("Interpreter", "Test:", "Lint:")) for x in lines)


def test_deploy_and_ci_paths_are_named_exactly(tmp_path: pathlib.Path) -> None:
    """Row 10 of the setup table: the built-in names that exist, and the candidates with why
    (by name, and by a bounded look into yml and CI files)."""
    manifest = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\n"
    files = {
        "src/app/a.py": "",
        "src/app/Dockerfile": "FROM python\n",
        "src/app/values.yml": manifest,  # inside the source folder: the file, not the folder
        "tests/data/compose.yml": "services: {}\n",  # test data: not a deploy file
        "Dockerfile": "FROM python\n",
        ".dockerignore": ".venv\n",
        "docker-compose.dev.yaml": "services: {}\n",
        "docker/api.dockerfile": "FROM python\n",
        "docker/entrypoint.txt": "",
        ".github/workflows/ci.yml": "jobs:\n  a:\n    steps:\n      - run: ./tools/build.sh x\n",
        ".gitlab-ci.yml": "build:\n  script: [pwsh tools\\release.ps1]\n",
        "tools/build.sh": "",
        "tools/release.ps1": "",
        "tools/unused.sh": "",
        "k8s/app.yml": manifest,
        "manifests/base/deployment.yaml": manifest,
        "manifests/base/notes.yml": "a: 1\n",
        "settings/app.yml": "debug: true\n",
        "iac/main.tf": "",
        "iac/modules/net/main.tf": "",
        "platform/chart/Chart.yaml": "name: x\n",
        "platform/chart/templates/deploy.yaml": "{{- if .Values.on }}\napiVersion: v1\n",
        "gen/a_pb2.py": "",
        "Procfile": "web: run\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    cfg = parse_config(tmp_path, {"protected": {"builtin_off": [".dockerignore"]}})
    tracked = sorted(files)
    assert probe.builtin_found(cfg, tracked) == [
        ".github/",
        ".gitlab-ci.yml",
        "Dockerfile",
        "docker-compose.dev.yaml",
        "docker/api.dockerfile",
        "src/app/Dockerfile",
    ]
    locked = [*frame_diff.core_protected(cfg), "tests/", "specs/"]
    ci_files = [f for f in tracked if frame_diff.maybe_pipeline_file(cfg, f)]
    keep = ["src/", "tests/", "specs/"]
    found = probe.lock_candidates(tracked, locked, tmp_path, keep, ci_files)
    assert found == {  # `.dockerignore`, taken off the built-in list, is not proposed again
        "Procfile": "a deploy / CI file by its name",
        "docker/": "holds a Dockerfile",
        "*_pb2.py": "generated files",
        "iac/": "Terraform files (*.tf)",
        "k8s/": "a folder with a deploy / CI name",
        "platform/chart/": "a Helm chart (Chart.yaml)",
        "manifests/base/": "Kubernetes manifests (yml with apiVersion and kind)",
        "src/app/values.yml": "Kubernetes manifests (yml with apiVersion and kind)",
        "tools/build.sh": "a script a CI file calls",
        "tools/release.ps1": "a script a CI file calls",
    }
    # by name only (no root): nothing is read
    by_name = probe.lock_candidates(tracked, locked)
    assert "manifests/base/" not in by_name and "tools/build.sh" not in by_name
    assert "k8s/" in by_name and "iac/" in by_name
    # the budget: at most READ_MAX files are opened, however many yml files there are
    many = [f"conf/{i:03}/x.yml" for i in range(200)]
    opened: list[str] = []

    def head(root: pathlib.Path, rel: str) -> str:
        opened.append(rel)
        return ""

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(probe, "_head", head)
        assert probe.lock_candidates(many, locked, tmp_path, keep, [".gitlab-ci.yml"]) == {}
    assert len(opened) == probe.READ_MAX


def test_the_interpreter_is_the_runs_own(demo_repo: DemoRepo) -> None:
    """The probe names the interpreter the adapter runs everything with: one piece of code."""
    adapter = get_adapter(load_config(demo_repo.root))
    assert isinstance(adapter, python_adapter.PythonAdapter)
    assert adapter.interpreter() == (
        adapter.python,
        "framegate's own interpreter: the project has no .venv",
    )
    demo_repo.add_toml('[env]\npython = "py-from-config"\n')
    adapter = get_adapter(load_config(demo_repo.root))
    assert adapter.interpreter() == ("py-from-config", "framegate.toml [env] python")
    assert adapter.resolve_command(["python", "-m", "black"])[0] == "py-from-config"


def test_the_command_for_real(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    """One run with real processes: the interpreter and pytest answer, JSON is valid."""
    capsys.readouterr()
    assert cli.main(["probe", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["interpreter"]["answers"] == "ok"
    assert data["interpreter"]["version"].startswith("Python 3.")
    test = next(row for row in data["tools"] if row["role"] == "test")
    assert test["answers"] == "ok" and test["version"].startswith("pytest ")
    assert [p["path"] for p in data["src"]] == ["src"] and data["tests"]["exists"]
