"""Adapter registry. An adapter is selected by `[project] language` in framegate.toml."""

from __future__ import annotations

import importlib
import pathlib

from framegate.adapters.base import LanguageAdapter
from framegate.config import Config, ConfigError

#: language -> "module:Class". Adapters are imported lazily.
ADAPTERS: dict[str, str] = {
    "python": "framegate.adapters.python:PythonAdapter",
    "dotnet": "framegate.adapters.dotnet:DotnetAdapter",
    # any other language, configured in framegate.toml [generic]; never auto-detected
    "generic": "framegate.adapters.generic:GenericAdapter",
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
