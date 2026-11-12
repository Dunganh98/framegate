"""agentflow: spec-driven, test-first workflow orchestrator for Claude Code."""

__version__ = "0.5.3"
#: where this package is released: the plugin marketplace and the git tags `v<version>`
#: (one version for the CLI and the plugin; tests/test_release.py keeps them equal)
SOURCE_URL = "https://github.com/Dunganh98/agentflow"


def release_requirement() -> str:
    """The pip requirement that installs exactly this version from its git tag."""
    return f"agentflow @ git+{SOURCE_URL}@v{__version__}"
