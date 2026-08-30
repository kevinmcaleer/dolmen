"""The build pipeline: source tree in, `_site/` out.

The order matters and is worth stating once:

1. **Discover** — walk the source, splitting files into documents (front matter)
   and static files (everything else). Read `_data/`.
2. **Apply defaults** — `_config.yml`'s `defaults:` rules, then collection
   defaults, fill in front matter the author left out.
3. **Assign URLs** — needed before rendering, because documents link to each
   other and wiki links resolve against the finished URL map.
4. **Render** — body through Liquid, then markdown, then up the layout chain.
5. **Write** — documents to their permalink paths, static files copied.

Steps 3 and 4 are separate passes for a reason: a template that loops over
`site.posts` needs every post's URL to already exist.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .config import SPECIAL_DIRS, Config, load_config
from .exceptions import StaticError
from .markdown import MarkdownRenderer
from .models import Document, Site, StaticFile, read_document
from .permalinks import apply as apply_permalink
from .plugins import PluginManager
from .templating import Templating

#: Files whose body is run through Liquid even though they are not markdown.
TEMPLATED_EXTENSIONS = {".html", ".htm", ".xml", ".json", ".txt", ".css"}


@dataclass
class BuildResult:
    """What a build produced, for the CLI and the dev server to report on."""

    output_dir: Path
    documents: int = 0
    static_files: int = 0
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)


class Builder:
    """Builds a site. One instance per build; cheap to recreate."""

    def __init__(self, config: Config, *, strict: bool = False) -> None:
        self.config = config
        self.strict = strict
        self.plugins = PluginManager()
        self.site = Site(config=config, time=dt.datetime.now(dt.UTC))
        self.warnings: list[str] = []

    # -- entry point ---------------------------------------------------------

    @classmethod
    def from_source(
        cls,
        source: Path,
        *,
        overrides: dict[str, Any] | None = None,
        strict: bool = False,
    ) -> Builder:
        return cls(load_config(Path(source), overrides), strict=strict)

    def build(self) -> BuildResult:
        started = dt.datetime.now(dt.UTC)

        self._load_plugins()
        self._discover()
        self._apply_defaults()
        self._assign_urls()
        self.plugins.call("on_site_loaded", self.site)

        self._render()
        written = self._write()

        self.plugins.call("on_post_build", self.site, self.config.destination)

        return BuildResult(
            output_dir=self.config.destination,
            documents=len([d for d in self.site.documents if self._is_published(d)]),
            static_files=written,
            duration=(dt.datetime.now(dt.UTC) - started).total_seconds(),
            warnings=self.warnings,
        )

    # -- 0. plugins ----------------------------------------------------------

    def _load_plugins(self) -> None:
        self.plugins.load_local(self.config.source)
        self.plugins.load_entry_points(self.config.plugins)
        self.plugins.call("on_config", self.config)

    # -- 1. discovery --------------------------------------------------------

    def _discover(self) -> None:
        source = self.config.source
        destination = self.config.destination
        collections = self.config.collections

        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            if _is_within(path, destination):
                continue

            relative = PurePosixPath(path.relative_to(source).as_posix())
            if self._excluded(relative):
                continue

            collection = _collection_for(relative, collections)
            if collection is None:
                self.site.static_files.append(StaticFile(source=path, relative_path=relative))
                continue

            try:
                document = read_document(
                    path, source_root=source, collection=collection, config=self.config
                )
            except StaticError as exc:
                if self.strict:
                    raise
                self.warnings.append(str(exc))
                continue

            if document is None:
                # No front matter: a file living in a collection dir but not a
                # document (an image beside a post, say). Copy it verbatim.
                self.site.static_files.append(StaticFile(source=path, relative_path=relative))
                continue

            self.site.documents.append(document)

        self.site.data = self._read_data()

    def _excluded(self, relative: PurePosixPath) -> bool:
        parts = relative.parts
        name = str(relative)

        if any(fnmatch.fnmatch(name, pattern) for pattern in self.config.include):
            return False
        if any(_matches_exclude(name, parts, pattern) for pattern in self.config.exclude):
            return True
        # Anything starting with `_` is a build input, not output — except the
        # collection directories, whose documents are published to permalinks.
        if parts and parts[0].startswith("_"):
            first = parts[0]
            if first in SPECIAL_DIRS:
                return True
            if first == "_drafts":
                return False
            return first[1:] not in self.config.collections
        # Dotfiles are not published, but dot-directories like `.well-known` are
        # opt-in via `include:`.
        return any(part.startswith(".") for part in parts)

    def _read_data(self) -> dict[str, Any]:
        """`_data/*.yml|yaml|json` becomes `site.data.<name>`, nested by folder."""
        directory = self.config.source / "_data"
        if not directory.is_dir():
            return {}

        data: dict[str, Any] = {}
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml", ".json"}:
                continue
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                if self.strict:
                    raise StaticError(f"could not parse data file: {exc}", path) from exc
                self.warnings.append(f"{path}: could not parse data file: {exc}")
                continue

            branch = data
            for part in path.relative_to(directory).parts[:-1]:
                branch = branch.setdefault(part, {})
            branch[path.stem] = loaded
        return data

    # -- 2. defaults ---------------------------------------------------------

    def _apply_defaults(self) -> None:
        """Fill in front matter from `defaults:` and collection config.

        Explicit front matter always wins; later matching rules win over earlier
        ones, which is Jekyll's behaviour.
        """
        collections = self.config.collections

        for document in self.site.documents:
            merged: dict[str, Any] = {}

            collection = collections.get(document.collection)
            if collection is not None:
                merged |= collection.defaults

            for rule in self.config.defaults:
                if _scope_matches(rule.get("scope") or {}, document):
                    merged |= rule.get("values") or {}

            for key, value in merged.items():
                document.metadata.setdefault(key, value)

    # -- 3. URLs -------------------------------------------------------------

    def _assign_urls(self) -> None:
        """Assign each document its site-relative URL.

        `baseurl` is deliberately *not* applied here. Jekyll keeps `page.url`
        site-relative and leaves the prefix to the `relative_url` filter; baking
        it in here would also push every file into a `sub/` directory in the
        output, which is not what a baseurl means.
        """
        collections = self.config.collections

        for document in self.site.documents:
            explicit = document.metadata.get("permalink")
            relative_dir = str(document.relative_path.parent)
            relative_dir = "" if relative_dir == "." else relative_dir

            if explicit:
                url = str(explicit)
                if not url.startswith("/"):
                    url = "/" + url
            elif document.collection == "pages":
                url = self._page_url(document)
            else:
                collection = collections.get(document.collection)
                template = (
                    collection.permalink
                    if collection and collection.permalink
                    else "/:collection/:title:output_ext"
                )
                # Collection dirs are stripped from `:path`.
                inner = PurePosixPath(*document.relative_path.parts[1:]).parent
                url = apply_permalink(
                    template,
                    slug=document.slug,
                    date=document.date,
                    categories=document.categories,
                    collection=document.collection,
                    output_ext=document.output_ext,
                    relative_dir="" if str(inner) == "." else str(inner),
                    basename=document.source.stem,
                )

            document.url = url

    def _page_url(self, document: Document) -> str:
        """Pages keep their path in the source tree; `index` files become dirs."""
        relative = document.relative_path
        stem = relative.stem
        parent = str(relative.parent)
        parent = "" if parent == "." else f"/{parent}"
        if stem == "index":
            return f"{parent}/" if parent else "/"
        return f"{parent}/{stem}{document.output_ext}"

    # -- 4. rendering --------------------------------------------------------

    def _render(self) -> None:
        url_map = {d.title.casefold(): d.url for d in self.site.documents if d.title}

        def resolve(target: str) -> str | None:
            document = self.site.find_by_title(target)
            if document is not None:
                return document.url
            return url_map.get(target.casefold())

        markdown = MarkdownRenderer(
            link_resolver=resolve,
            extensions=self.plugins.collect_markdown_extensions(),
        )
        templating = Templating(
            self.config.source,
            markdown=markdown,
            baseurl=self.config.baseurl,
            url=self.config.url,
            extra_filters=self.plugins.collect_filters(),
            strict=self.strict,
        )

        site_context = self.site.to_template_dict()

        for document in self.site.documents:
            if not self._is_published(document):
                continue
            try:
                self._render_document(document, templating, markdown, site_context)
            except StaticError as exc:
                if self.strict:
                    raise
                self.warnings.append(str(exc))

    def _render_document(
        self,
        document: Document,
        templating: Templating,
        markdown: MarkdownRenderer,
        site_context: dict[str, Any],
    ) -> None:
        self.plugins.call("on_document_pre_render", self.site, document)

        context = {
            "site": site_context,
            "page": document.to_template_dict(),
            "content": "",
        }

        body = document.body
        if document.is_markdown or document.source.suffix.lower() in TEMPLATED_EXTENSIONS:
            body = templating.render_string(body, context, name=str(document.source))

        if document.is_markdown:
            body = markdown.render(body)

        document.content = body
        # Re-read `page` so a layout sees the rendered content and any excerpt.
        context["page"] = document.to_template_dict()

        output = body
        if document.layout:
            output = templating.render_layout(
                document.layout, body, context, name=str(document.source)
            )

        for replacement in self.plugins.call(
            "on_document_rendered", self.site, document, output
        ):
            if isinstance(replacement, str):
                output = replacement

        document.content = output

    def _is_published(self, document: Document) -> bool:
        """Whether a document gets written to the output directory.

        Documents in a collection with `output: false` stay readable through
        `site.<collection>` but are never written — Jekyll's behaviour.
        """
        collection = self.config.collections.get(document.collection)
        if collection is not None and not collection.output:
            return False
        if document.draft:
            return bool(self.config.get("show_drafts"))
        return True

    # -- 5. writing ----------------------------------------------------------

    def _write(self) -> int:
        destination = self.config.destination
        if destination.exists():
            _empty_directory(destination)
        destination.mkdir(parents=True, exist_ok=True)

        for document in self.site.documents:
            if not self._is_published(document):
                continue
            target = destination / document.output_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(document.content, encoding="utf-8")

        written = 0
        for static_file in self.site.static_files:
            target = destination / static_file.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(static_file.source, target)
            written += 1
        return written


# -- helpers ----------------------------------------------------------------


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _matches_exclude(name: str, parts: tuple[str, ...], pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    if fnmatch.fnmatch(name, pattern):
        return True
    # A bare directory name excludes everything beneath it.
    return pattern in parts


def _collection_for(
    relative: PurePosixPath, collections: dict[str, Any]
) -> str | None:
    """Which collection a path belongs to: `_posts` → posts, `_x` → x, else pages."""
    first = relative.parts[0] if relative.parts else ""
    if first == "_drafts":
        return "posts"
    if first.startswith("_"):
        name = first[1:]
        return name if name in collections else None
    if relative.suffix.lower() in {".md", ".markdown", ".mkdn", ".mkd", ".html", ".htm"}:
        return "pages"
    return None


def _scope_matches(scope: dict[str, Any], document: Document) -> bool:
    """Jekyll `defaults:` scope — a path prefix and/or a collection type."""
    wanted_type = scope.get("type")
    if wanted_type and wanted_type != document.collection:
        return False
    path = str(scope.get("path", "") or "")
    if not path:
        return True
    return str(document.relative_path).startswith(path.strip("/"))


def _empty_directory(directory: Path) -> None:
    """Clear a directory without deleting it, so a served path stays valid."""
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
