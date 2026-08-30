"""The extension system.

"Highly extensible" means three things here:

1. **Site-local plugins** — any `.py` file in `_plugins/` is imported at build
   time and may define any of the hook functions below. No registration needed.
2. **Installed plugins** — packages advertising the `dolmen.plugins` entry-point
   group are loaded when named in `_config.yml`'s `plugins:` list.
3. **Hooks are plain functions.** A plugin defines only the hooks it cares
   about; missing ones are skipped.

Available hooks, all optional::

    def on_config(config: Config) -> None
    def on_site_loaded(site: Site) -> None
    def on_document_pre_render(site: Site, document: Document) -> None
    def on_document_rendered(site: Site, document: Document, html: str) -> str | None
    def on_post_build(site: Site, output_dir: Path) -> None
    def filters() -> dict[str, Callable]
    def markdown_extensions() -> list[Callable[[MarkdownIt], None]]

`on_document_rendered` is the only hook whose return value is used: return a
string to replace the HTML, or None to leave it alone.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

from .exceptions import PluginError

HOOK_NAMES = (
    "on_config",
    "on_site_loaded",
    "on_document_pre_render",
    "on_document_rendered",
    "on_post_build",
)

ENTRY_POINT_GROUP = "dolmen.plugins"


@dataclass
class PluginManager:
    """Loads plugins and dispatches hooks to them, in load order."""

    plugins: list[ModuleType] = field(default_factory=list)

    # -- loading -------------------------------------------------------------

    def load_local(self, source: Path) -> None:
        """Import every `.py` file in `_plugins/`, alphabetically."""
        directory = source / "_plugins"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            self.plugins.append(_import_path(path))

    def load_entry_points(self, names: Iterable[str]) -> None:
        """Load installed plugins named in `_config.yml`'s `plugins:` list."""
        wanted = set(names)
        if not wanted:
            return
        available = {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}
        for name in sorted(wanted):
            entry = available.get(name)
            if entry is None:
                # Jekyll plugin names (jekyll-feed, …) are silently ignored so an
                # unported config still builds; a dolmen plugin would be a typo.
                if name.startswith("jekyll-"):
                    continue
                raise PluginError(f"plugin {name!r} is not installed")
            try:
                self.plugins.append(entry.load())
            except Exception as exc:  # noqa: BLE001 - surfaced with context below
                raise PluginError(f"plugin {name!r} failed to load: {exc}") from exc

    # -- dispatch ------------------------------------------------------------

    def call(self, hook: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Call `hook` on every plugin that defines it, returning the results."""
        results = []
        for plugin in self.plugins:
            function = getattr(plugin, hook, None)
            if function is None:
                continue
            try:
                results.append(function(*args, **kwargs))
            except Exception as exc:  # noqa: BLE001 - surfaced with context below
                name = getattr(plugin, "__name__", repr(plugin))
                raise PluginError(f"{name}.{hook} raised: {exc}") from exc
        return results

    def collect_filters(self) -> dict[str, Callable[..., Any]]:
        filters: dict[str, Callable[..., Any]] = {}
        for result in self.call("filters"):
            filters.update(result or {})
        return filters

    def collect_markdown_extensions(self) -> list[Callable[..., Any]]:
        extensions: list[Callable[..., Any]] = []
        for result in self.call("markdown_extensions"):
            extensions.extend(result or [])
        return extensions


def _import_path(path: Path) -> ModuleType:
    """Import a file by path under a private module name."""
    module_name = f"dolmen_plugins.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginError("could not be imported", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surfaced with context below
        raise PluginError(f"raised while importing: {exc}", path) from exc
    return module
