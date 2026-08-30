"""Exception types raised by dolmen.

Every error that is the *user's* fault (bad front matter, a missing layout, an
unparseable config) should surface as a StaticError subclass carrying the
offending path, so the CLI can print one clear line instead of a traceback.
"""

from __future__ import annotations

from pathlib import Path


class StaticError(Exception):
    """Base class for all errors raised by dolmen."""

    def __init__(self, message: str, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        super().__init__(message)

    def __str__(self) -> str:
        message = super().__str__()
        if self.path is not None:
            return f"{self.path}: {message}"
        return message


class ConfigError(StaticError):
    """_config.yml is missing, unparseable, or has a bad value."""


class FrontMatterError(StaticError):
    """A document's YAML front matter could not be parsed."""


class RenderError(StaticError):
    """A layout, include or Liquid-ish expression failed to render."""


class PluginError(StaticError):
    """A plugin failed to load or raised during a hook."""
