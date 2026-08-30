"""Loading and normalising `_config.yml`.

Jekyll's config is a flat YAML mapping where a handful of keys are meaningful to
the build and everything else is arbitrary site data exposed to templates as
`site.*`. We keep that shape: `Config` behaves like a mapping, and the
build-critical keys are normalised into typed attributes.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigError

CONFIG_NAME = "_config.yml"

#: Jekyll's default permalink style for posts.
DEFAULT_PERMALINK = "/:categories/:year/:month/:day/:title:output_ext"

#: Paths never copied to the output directory, on top of the `_`-prefixed
#: special directories which are always excluded.
DEFAULT_EXCLUDE = [
    ".git",
    ".github",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".DS_Store",
    "Gemfile",
    "Gemfile.lock",
    "pyproject.toml",
    "uv.lock",
]

#: Directories that carry build meaning rather than being publishable content.
SPECIAL_DIRS = ("_layouts", "_includes", "_data", "_plugins", "_sass", "_site")


@dataclass
class CollectionConfig:
    """Configuration for one collection (`_posts` or a `collections:` entry)."""

    name: str
    output: bool = False
    permalink: str | None = None
    #: Front-matter defaults applied to every document in the collection.
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def directory(self) -> str:
        return f"_{self.name}"


@dataclass
class Config(Mapping):
    """A parsed `_config.yml`, plus the resolved source/destination paths."""

    source: Path
    values: dict[str, Any] = field(default_factory=dict)

    # -- mapping protocol so templates can read arbitrary config keys ---------

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    # -- build-critical keys -------------------------------------------------

    @property
    def destination(self) -> Path:
        dest = self.values.get("destination", "_site")
        path = Path(dest)
        return path if path.is_absolute() else (self.source / path).resolve()

    @property
    def baseurl(self) -> str:
        """Sub-path the site is served from, normalised to '' or '/prefix'."""
        raw = str(self.values.get("baseurl") or "").strip()
        if not raw or raw == "/":
            return ""
        return "/" + raw.strip("/")

    @property
    def url(self) -> str:
        return str(self.values.get("url") or "").rstrip("/")

    @property
    def permalink(self) -> str:
        return str(self.values.get("permalink") or DEFAULT_PERMALINK)

    @property
    def exclude(self) -> list[str]:
        return [*DEFAULT_EXCLUDE, *(self.values.get("exclude") or [])]

    @property
    def include(self) -> list[str]:
        """Paths to publish even though they start with `_` or `.`."""
        return list(self.values.get("include") or [])

    @property
    def plugins(self) -> list[str]:
        return list(self.values.get("plugins") or [])

    @property
    def defaults(self) -> list[dict[str, Any]]:
        """Jekyll `defaults:` scope/values rules."""
        return list(self.values.get("defaults") or [])

    @property
    def collections(self) -> dict[str, CollectionConfig]:
        """Every collection, with `posts` always present."""
        declared = self.values.get("collections") or {}
        if isinstance(declared, list):
            # Jekyll allows a bare list of names, meaning output: false.
            declared = {name: {} for name in declared}

        collections: dict[str, CollectionConfig] = {
            "posts": CollectionConfig(name="posts", output=True, permalink=self.permalink)
        }
        for name, options in declared.items():
            options = options or {}
            if not isinstance(options, Mapping):
                raise ConfigError(f"collection {name!r} must be a mapping", self.config_path)
            collections[name] = CollectionConfig(
                name=name,
                output=bool(options.get("output", False)),
                permalink=options.get("permalink"),
                defaults={
                    k: v for k, v in options.items() if k not in {"output", "permalink"}
                },
            )
        return collections

    @property
    def config_path(self) -> Path:
        return self.source / CONFIG_NAME

    def to_template_dict(self, *, time: dt.datetime | None = None) -> dict[str, Any]:
        """The mapping exposed to templates as `site`, minus the documents.

        Documents are attached by the builder, which owns the site model.
        """
        data = dict(self.values)
        data.setdefault("title", "")
        data.setdefault("description", "")
        data["url"] = self.url
        data["baseurl"] = self.baseurl
        data["time"] = time or dt.datetime.now(dt.UTC)
        return data


#: Directories whose presence marks a directory as a site even with no config.
SITE_MARKER_DIRS = ("_posts", "_layouts", "_includes", "_data", "_drafts", "_plugins")

#: Extensions worth opening to look for front matter.
_CONTENT_SUFFIXES = {".md", ".markdown", ".mkdn", ".mkd", ".html", ".htm"}

#: Never worth walking when deciding whether something is a site.
_IGNORED_DIRS = {"node_modules", "__pycache__", "venv", "_site", "site-packages"}


def looks_like_a_site(source: Path) -> bool:
    """Whether `source` is plausibly a site root, absent a `_config.yml`.

    A config is optional — defaults are fine — but building a directory that is
    not a site copies every file in it into the output, so there has to be
    *some* evidence. Any Jekyll-style directory counts, as does a single file
    with front matter, which is the smallest real site there is.
    """
    source = Path(source)
    if (source / CONFIG_NAME).is_file():
        return True
    if any((source / name).is_dir() for name in SITE_MARKER_DIRS):
        return True

    # A file with front matter is content; a README in a code repo is not.
    for path in sorted(source.rglob("*")):
        if path.suffix.lower() not in _CONTENT_SUFFIXES or not path.is_file():
            continue
        parts = path.relative_to(source).parts
        if any(part.startswith(".") or part in _IGNORED_DIRS for part in parts):
            continue
        # Content belonging to a nested site says nothing about this directory —
        # otherwise a code repo with a demo site in it looks like a site itself.
        if any((source.joinpath(*parts[:depth]) / CONFIG_NAME).is_file()
               for depth in range(1, len(parts))):
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                if handle.readline().rstrip("\r\n") == "---":
                    return True
        except OSError:
            continue
    return False


def load_config(source: Path, overrides: Mapping[str, Any] | None = None) -> Config:
    """Read `_config.yml` from `source`.

    A missing config is not an error — an empty site still builds — but an
    unparseable one is.
    """
    try:
        source = Path(source).resolve(strict=True)
    except OSError as exc:
        # Resolving "." raises when the shell's working directory has been
        # deleted or renamed underneath it. The directory looks present in the
        # prompt, so say what actually happened rather than reporting the
        # config as missing.
        raise ConfigError(
            "this directory no longer exists. If it was deleted or renamed while "
            "your shell was inside it, `cd` to it again (or to somewhere that "
            "exists) and retry.",
            source,
        ) from exc

    path = source / CONFIG_NAME
    values: dict[str, Any] = {}

    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"could not parse YAML: {exc}", path) from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError("top level of the config must be a mapping", path)
        values = loaded

    if overrides:
        values.update(overrides)

    return Config(source=source, values=values)
