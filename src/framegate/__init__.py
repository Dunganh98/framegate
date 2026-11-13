"""framegate: spec-driven, test-first workflow orchestrator for Claude Code."""

__version__ = "0.9.0"
#: where this package is released: the plugin marketplace and the git tags `v<version>`
#: (one version for the CLI and the plugin; tests/test_release.py keeps them equal)
SOURCE_URL = "https://github.com/Dunganh98/framegate"


def release_requirement() -> str:
    """The pip requirement that installs exactly this version from its git tag."""
    return f"framegate @ git+{SOURCE_URL}@v{__version__}"
