"""Adapter registry. An adapter is selected by `[project] language` in agentflow.toml."""

from __future__ import annotations

import importlib
import pathlib

from agentflow.adapters.base import LanguageAdapter
from agentflow.config import Config, ConfigError

#: language -> "module:Class". Adapters are imported lazily.
ADAPTERS: dict[str, str] = {
    "python": "agentflow.adapters.python:PythonAdapter",
    "dotnet": "agentflow.adapters.dotnet:DotnetAdapter",
    # any other language, configured in agentflow.toml [generic]; never auto-detected
    "generic": "agentflow.adapters.generic:GenericAdapter",
}


def adapter_class(language: str) -> type[LanguageAdapter]:
    target = ADAPTERS.get(language)
    if target is None:
        raise ConfigError(f"unknown language '{language}' (known: {', '.join(ADAPTERS)})")
    module, cls = target.split(":")
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name == module:
            raise ConfigError(f"the '{language}' adapter is not available in this version") from e
        raise
    result: type[LanguageAdapter] = getattr(mod, cls)
    return result


def get_adapter(cfg: Config) -> LanguageAdapter:
    return adapter_class(cfg.language)(cfg)


def detect_language(root: pathlib.Path) -> str | None:
    """First registered language whose adapter recognizes the repository."""
    for language in ADAPTERS:
        try:
            if adapter_class(language).detect(root):
                return language
        except ConfigError:
            continue
    return None
