"""The site model: documents, collections, and the `site` object templates see.

A `Document` is any source file with front matter. Documents belong to a
collection (`posts`, or one declared under `collections:`); everything else with
front matter is a `page`. Files without front matter are static assets and never
become Documents.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from . import frontmatter, permalinks
from .config import Config

#: Extensions rendered through the markdown pipeline. Everything else with front
#: matter is still templated, but its body is passed through as-is.
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mkdn", ".mkd"}


@dataclass
class Document:
    """One source file with front matter, and everything derived from it."""

    #: Path on disk.
    source: Path
    #: Path relative to the site source, used for `defaults:` scope matching.
    relative_path: PurePosixPath
    collection: str
    metadata: dict[str, Any]
    #: Raw body, before markdown or templating.
    body: str

    url: str = ""
    #: 1-indexed line of the source file on which `body` starts.
    body_line: int = 1
    #: Rendered body, filled in during the build.
    content: str = ""
    date: dt.datetime | None = None
    slug: str = ""

    @property
    def is_markdown(self) -> bool:
        return self.source.suffix.lower() in MARKDOWN_EXTENSIONS

    @property
    def output_ext(self) -> str:
        return ".html" if self.is_markdown else self.source.suffix

    @property
    def layout(self) -> str | None:
        layout = self.metadata.get("layout")
        return str(layout) if layout else None

    @property
    def title(self) -> str:
        return str(self.metadata.get("title", ""))

    @property
    def draft(self) -> bool:
        """Drafts live in `_drafts` or say `published: false`."""
        if self.metadata.get("published") is False:
            return True
        return self.relative_path.parts[:1] == ("_drafts",)

    @property
    def categories(self) -> list[str]:
        return _as_list(self.metadata.get("categories") or self.metadata.get("category"))

    @property
    def tags(self) -> list[str]:
        return _as_list(self.metadata.get("tags") or self.metadata.get("tag"))

    @property
    def output_path(self) -> PurePosixPath:
        return permalinks.to_output_path(self.url)

    def to_template_dict(self) -> dict[str, Any]:
        """The mapping exposed to templates as `page`.

        Front matter keys come first so a document can override any derived
        value except the ones the build owns (`url`, `content`, `path`).
        """
        data = dict(self.metadata)
        data |= {
            "url": self.url,
            "content": self.content,
            "path": str(self.relative_path),
            "collection": self.collection,
            "slug": self.slug,
            "date": self.date,
            "categories": self.categories,
            "tags": self.tags,
            "id": self.url,
            "excerpt": self.metadata.get("excerpt", ""),
        }
        data.setdefault("title", "")
        return data


@dataclass
class StaticFile:
    """A file copied to the output verbatim."""

    source: Path
    relative_path: PurePosixPath

    @property
    def url(self) -> str:
        return "/" + str(self.relative_path)


@dataclass
class Site:
    """Everything the build knows about, and the `site` object in templates."""

    config: Config
    documents: list[Document] = field(default_factory=list)
    static_files: list[StaticFile] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    time: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    # -- views over the documents -------------------------------------------

    @property
    def posts(self) -> list[Document]:
        """Posts, newest first — the order Jekyll exposes."""
        posts = [d for d in self.documents if d.collection == "posts" and not d.draft]
        return sorted(posts, key=_sort_key_date, reverse=True)

    @property
    def pages(self) -> list[Document]:
        return [d for d in self.documents if d.collection == "pages"]

    def collection(self, name: str) -> list[Document]:
        docs = [d for d in self.documents if d.collection == name and not d.draft]
        if name == "posts":
            return sorted(docs, key=_sort_key_date, reverse=True)
        return sorted(docs, key=lambda d: str(d.relative_path))

    @property
    def tags(self) -> dict[str, list[Document]]:
        return _group(self.posts, lambda d: d.tags)

    @property
    def categories(self) -> dict[str, list[Document]]:
        return _group(self.posts, lambda d: d.categories)

    def find_by_title(self, title: str) -> Document | None:
        """Resolve a wiki-link target: exact title, then slug, then filename."""
        wanted = title.strip().casefold()
        for doc in self.documents:
            if doc.title.casefold() == wanted:
                return doc
        wanted_slug = permalinks.slugify(title)
        for doc in self.documents:
            if doc.slug == wanted_slug or doc.source.stem.casefold() == wanted:
                return doc
        return None

    def to_template_dict(self) -> dict[str, Any]:
        data = self.config.to_template_dict(time=self.time)
        collections = {
            name: [d.to_template_dict() for d in self.collection(name)]
            for name in self.config.collections
        }
        data |= {
            "posts": collections.get("posts", []),
            "pages": [d.to_template_dict() for d in self.pages],
            "documents": [d.to_template_dict() for d in self.documents],
            "static_files": [{"path": f.url} for f in self.static_files],
            "data": self.data,
            "collections": collections,
            "tags": {k: [d.to_template_dict() for d in v] for k, v in self.tags.items()},
            "categories": {
                k: [d.to_template_dict() for d in v] for k, v in self.categories.items()
            },
        }
        # Collections are also addressable directly, e.g. `site.learn`.
        for name, docs in collections.items():
            data.setdefault(name, docs)
        return data


# -- helpers ----------------------------------------------------------------


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _sort_key_date(doc: Document) -> dt.datetime:
    """Undated documents sort oldest, so they never displace real posts."""
    if doc.date is None:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    if doc.date.tzinfo is None:
        return doc.date.replace(tzinfo=dt.UTC)
    return doc.date


def _group(docs: list[Document], key: Any) -> dict[str, list[Document]]:
    grouped: dict[str, list[Document]] = {}
    for doc in docs:
        for value in key(doc):
            grouped.setdefault(value, []).append(doc)
    return grouped


def read_document(
    path: Path,
    *,
    source_root: Path,
    collection: str,
    config: Config,
) -> Document | None:
    """Read a source file into a Document, or None if it has no front matter."""
    parsed = frontmatter.load(path)
    if not parsed.has_front_matter:
        return None

    relative = PurePosixPath(path.relative_to(source_root).as_posix())
    date, slug = permalinks.split_dated_filename(path.stem)

    document = Document(
        source=path,
        relative_path=relative,
        collection=collection,
        metadata=parsed.metadata,
        body=parsed.content,
        body_line=parsed.body_line,
        slug=permalinks.slugify(slug),
    )
    document.date = _resolve_date(document, date)
    if document.date is None and collection == "posts":
        # Drafts carry no date in the filename; Jekyll uses the file's mtime so
        # that dated permalinks still resolve.
        document.date = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
    return document


def _resolve_date(document: Document, filename_date: dt.date | None) -> dt.datetime | None:
    """Front matter `date:` wins over the date in the filename."""
    raw = document.metadata.get("date")
    if isinstance(raw, dt.datetime):
        return raw
    if isinstance(raw, dt.date):
        return dt.datetime.combine(raw, dt.time.min)
    if isinstance(raw, str):
        try:
            return dt.datetime.fromisoformat(raw)
        except ValueError:
            pass
    if filename_date is not None:
        return dt.datetime.combine(filename_date, dt.time.min)
    return None
