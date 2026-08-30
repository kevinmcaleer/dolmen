"""Structural facts about a site, for the front end's structure editors.

Where `validate.py` answers "what is wrong", this answers "how does it fit
together": which documents use a layout, which templates call an include and
with what parameters, and what shape a data file has.

The YAML helpers exist for one reason: **a structural edit must produce a
minimal diff.** Round-tripping a nav file through a naive load/dump reorders
keys, reflows lists and strips comments, which turns "move one link up" into an
unreviewable change. `dump_sequence` keeps the author's key order and quoting
conventions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .config import Config
    from .models import Site

#: `{% include name.html a=1 b="two" %}` — the include and its arguments.
_INCLUDE_CALL_RE = re.compile(
    r"\{%-?\s*include\s+(?P<name>[\w./-]+)(?P<args>[^%]*?)-?%\}"
)
#: `key=value` inside an include call.
_INCLUDE_ARG_RE = re.compile(r"(?P<key>[\w-]+)\s*=")
#: `include.foo` and `include["foo"]` inside an include's own body.
_INCLUDE_PARAM_RE = re.compile(
    r"""include(?:\.(?P<dot>[\w-]+)|\[\s*["'](?P<bracket>[^"']+)["']\s*\])"""
)


@dataclass
class TemplateUse:
    """One template, and everything that refers to it."""

    name: str
    path: str
    #: Source paths of documents or templates that use it.
    used_by: list[str] = field(default_factory=list)
    #: Front matter, for layouts that have some.
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Parameter names the include reads off `include.*`.
    parameters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "used_by": sorted(self.used_by),
            "count": len(self.used_by),
            "metadata": self.metadata,
            "parameters": self.parameters,
        }


def layouts(site: Site, config: Config) -> list[TemplateUse]:
    """Every layout, with the documents and layouts that use it."""
    from . import frontmatter

    directory = config.source / "_layouts"
    found: dict[str, TemplateUse] = {}
    if not directory.is_dir():
        return []

    for path in sorted(directory.glob("*.*")):
        if not path.is_file():
            continue
        parsed = frontmatter.load(path)
        found[path.stem] = TemplateUse(
            name=path.stem,
            path=f"_layouts/{path.name}",
            metadata=parsed.metadata,
        )

    for document in site.documents:
        layout = document.layout
        if layout in found:
            found[layout].used_by.append(str(document.relative_path))

    # Layouts nest, so a layout's own `layout:` counts as a use.
    for use in list(found.values()):
        parent = use.metadata.get("layout")
        if parent and str(parent) in found:
            found[str(parent)].used_by.append(use.path)

    return sorted(found.values(), key=lambda u: u.name)


def includes(site: Site, config: Config) -> list[TemplateUse]:
    """Every include, who calls it, and the parameters it reads."""
    directory = config.source / "_includes"
    found: dict[str, TemplateUse] = {}
    if not directory.is_dir():
        return []

    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        name = str(path.relative_to(directory))
        text = path.read_text(encoding="utf-8", errors="replace")
        found[name] = TemplateUse(
            name=name,
            path=f"_includes/{name}",
            parameters=sorted(_parameters_read(text)),
        )

    for source_path, text in _all_templates(site, config):
        for match in _INCLUDE_CALL_RE.finditer(text):
            name = match.group("name")
            if name in found:
                found[name].used_by.append(source_path)

    return sorted(found.values(), key=lambda u: u.name)


def _parameters_read(text: str) -> set[str]:
    """Which `include.*` names an include's body reads."""
    return {
        match.group("dot") or match.group("bracket")
        for match in _INCLUDE_PARAM_RE.finditer(text)
    }


def _all_templates(site: Site, config: Config) -> list[tuple[str, str]]:
    """Everything that could call an include: layouts, includes, documents."""
    pairs: list[tuple[str, str]] = []
    for folder in ("_layouts", "_includes"):
        directory = config.source / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                pairs.append(
                    (
                        f"{folder}/{path.relative_to(directory)}",
                        path.read_text(encoding="utf-8", errors="replace"),
                    )
                )
    for document in site.documents:
        pairs.append((str(document.relative_path), document.body))
    return pairs


# -- data files --------------------------------------------------------------


@dataclass
class DataFile:
    """A `_data/` file, described well enough to render as a table."""

    name: str
    path: str
    #: "sequence" (list of mappings), "mapping", or "other".
    shape: str
    #: Union of keys across rows, in first-seen order, for a sequence.
    columns: list[str] = field(default_factory=list)
    rows: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "shape": self.shape,
            "columns": self.columns,
            "rows": self.rows,
        }


def data_files(config: Config) -> list[DataFile]:
    """Every `_data/` file, classified so the front end can pick an editor."""
    directory = config.source / "_data"
    if not directory.is_dir():
        return []

    found = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml", ".json"}:
            continue
        relative = path.relative_to(directory)
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        found.append(
            DataFile(
                name=str(relative),
                path=f"_data/{relative}",
                shape=_shape(loaded),
                columns=_columns(loaded),
                rows=loaded if isinstance(loaded, list) else [],
            )
        )
    return found


def _shape(value: Any) -> str:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return "sequence"
    if isinstance(value, dict):
        return "mapping"
    return "other"


def _columns(value: Any) -> list[str]:
    """Union of keys in first-seen order — the order authors actually wrote."""
    if not isinstance(value, list):
        return []
    columns: list[str] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        for key in row:
            if key not in columns:
                columns.append(str(key))
    return columns


def dump_sequence(rows: list[dict[str, Any]]) -> str:
    """Serialise a list of mappings the way a person would have written it.

    `sort_keys=False` keeps each row's key order, and block style keeps the
    file diffable. Without both, reordering one nav item rewrites the file.
    """
    return yaml.safe_dump(
        rows,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def read_sequence(path: Path) -> list[dict[str, Any]] | None:
    """Load a data file that is a list of mappings, or None if it is not."""
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(loaded, list) and all(isinstance(row, dict) for row in loaded):
        return loaded
    return None
