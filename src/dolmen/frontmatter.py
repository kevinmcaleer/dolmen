"""Splitting Jekyll-style YAML front matter off the top of a file.

Front matter is a YAML block fenced by `---` lines at the very start of the
file. A file without it is not a document dolmen renders — it is copied to the
output verbatim, exactly as Jekyll does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .exceptions import FrontMatterError

#: Opening fence must be the first thing in the file; closing fence ends the block.
_FENCE_RE = re.compile(r"\A---[ \t]*\r?\n(?P<meta>.*?)(?:\r?\n)?^---[ \t]*\r?$\r?\n?", re.S | re.M)


@dataclass(frozen=True)
class Document:
    """A file split into its front matter and its body."""

    metadata: dict[str, Any]
    content: str
    #: True when the file opened with a `---` fence.
    has_front_matter: bool


def split(text: str, path: Path | None = None) -> Document:
    """Split `text` into front matter and body."""
    match = _FENCE_RE.match(text)
    if match is None:
        return Document(metadata={}, content=text, has_front_matter=False)

    raw = match.group("meta")
    try:
        metadata = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"could not parse front matter: {exc}", path) from exc

    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise FrontMatterError("front matter must be a mapping", path)

    return Document(metadata=metadata, content=text[match.end():], has_front_matter=True)


def load(path: Path) -> Document:
    """Read and split a file from disk."""
    return split(path.read_text(encoding="utf-8"), path)


def dump(metadata: dict[str, Any], content: str) -> str:
    """Recombine front matter and body into a file's text.

    `sort_keys=False` keeps the author's key order, which matters because the
    web front end round-trips files people have hand-written.
    """
    if not metadata:
        return content
    meta = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"---\n{meta}\n---\n{content}"
