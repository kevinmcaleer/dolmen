"""The wiki-link index: resolution, backlinks, and ambiguity.

`[[Target]]` resolves against the whole site, so it needs an index built once
per build rather than a search per link. The index also gives every document its
**backlinks** — who points at it — which is the half of wiki linking that makes
a site navigable rather than merely cross-referenced.

Resolution order, most specific first:

1. an exact title match
2. the slugified title (`[[getting-started]]` finds "Getting Started")
3. the document's own slug, which comes from its filename
4. a filename-stem match

A target may name a heading: `[[Page#Some Section]]` resolves to that page's URL
plus the anchor the markdown renderer generated for that heading.

**Collisions.** Two documents can share a title. Rather than silently picking
one, the index records the ambiguity, resolves to the first by collection then
path (so the choice is at least stable across builds), and reports it as a
problem. Silently linking to the wrong page is worse than a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .markdown import WIKILINK_RE
from .permalinks import slugify

if TYPE_CHECKING:
    from .models import Document, Site

#: Headings in rendered HTML, for resolving `[[Page#Section]]`.
_HEADING_RE = re.compile(r'<h[1-6][^>]*\bid="(?P<id>[^"]+)"', re.I)

#: Collections are searched in this order when a title is ambiguous.
_COLLECTION_PRIORITY = ("pages", "posts")


@dataclass(frozen=True)
class Link:
    """One `[[target]]` occurrence in a document."""

    #: The raw target as written, including any `#heading`.
    target: str
    #: The page part, without the heading.
    page: str
    #: The heading part, or None.
    heading: str | None
    #: Display text, when written as `[[target|label]]`.
    label: str | None
    #: 1-indexed line in the *source file*, front matter included.
    line: int
    #: The document it resolved to, or None.
    resolved: Document | None = None

    @property
    def is_broken(self) -> bool:
        return self.resolved is None


@dataclass
class LinkIndex:
    """Every wiki link in the site, resolved, plus the reverse mapping."""

    #: Source path -> the links it contains.
    outgoing: dict[str, list[Link]] = field(default_factory=dict)
    #: Target document path -> the documents linking to it.
    incoming: dict[str, list[Document]] = field(default_factory=dict)
    #: Lower-cased title -> every document claiming it, when more than one does.
    ambiguous: dict[str, list[Document]] = field(default_factory=dict)
    #: Source path -> the document, since Document is unhashable and paths are
    #: the stable identity everywhere else in the build.
    documents: dict[str, Document] = field(default_factory=dict, repr=False)

    def backlinks(self, document: Document) -> list[Document]:
        """Documents linking to `document`, deduplicated and ordered by path."""
        found = self.incoming.get(str(document.relative_path), [])
        seen: dict[str, Document] = {}
        for source in found:
            seen.setdefault(str(source.relative_path), source)
        return sorted(seen.values(), key=lambda d: str(d.relative_path))

    def links_from(self, document: Document) -> list[Link]:
        return self.outgoing.get(str(document.relative_path), [])

    def incoming_for_title(self, title: str) -> bool:
        """Whether anything actually links to an ambiguous title.

        An unused duplicate title is not a problem worth reporting — only one
        that something is trying to link to.
        """
        wanted = title.casefold()
        return any(
            link.page.casefold() == wanted
            for links in self.outgoing.values()
            for link in links
        )

    def broken(self) -> list[tuple[Document, Link]]:
        """Every unresolved link, with the document it appears in."""
        return [
            (self.documents[path], link)
            for path, links in self.outgoing.items()
            for link in links
            if link.is_broken
        ]


def split_target(target: str) -> tuple[str, str | None]:
    """Split `Page#Section` into its page and heading parts."""
    page, sep, heading = target.partition("#")
    return page.strip(), heading.strip() if sep else None


def build_index(site: Site) -> LinkIndex:
    """Index every wiki link in the site and resolve it."""
    index = LinkIndex()
    by_title: dict[str, list[Document]] = {}
    by_title_slug: dict[str, Document] = {}
    by_slug: dict[str, Document] = {}
    by_stem: dict[str, Document] = {}

    for document in _ordered(site.documents):
        if document.title:
            by_title.setdefault(document.title.casefold(), []).append(document)
            by_title_slug.setdefault(slugify(document.title), document)
        by_slug.setdefault(document.slug, document)
        by_stem.setdefault(document.source.stem.casefold(), document)

    index.ambiguous = {
        title: documents for title, documents in by_title.items() if len(documents) > 1
    }

    for document in site.documents:
        links = _links_in(document, by_title, by_title_slug, by_slug, by_stem)
        index.outgoing[str(document.relative_path)] = links
        index.documents[str(document.relative_path)] = document
        for link in links:
            if link.resolved is not None:
                index.incoming.setdefault(str(link.resolved.relative_path), []).append(
                    document
                )
    return index


def _ordered(documents: list[Document]) -> list[Document]:
    """Stable order for collision resolution: known collections first, then path."""

    def key(document: Document) -> tuple[int, str]:
        try:
            rank = _COLLECTION_PRIORITY.index(document.collection)
        except ValueError:
            rank = len(_COLLECTION_PRIORITY)
        return (rank, str(document.relative_path))

    return sorted(documents, key=key)


def _links_in(
    document: Document,
    by_title: dict[str, list[Document]],
    by_title_slug: dict[str, Document],
    by_slug: dict[str, Document],
    by_stem: dict[str, Document],
) -> list[Link]:
    links = []
    for match in WIKILINK_RE.finditer(document.body):
        target = match.group(1).strip()
        page, heading = split_target(target)
        resolved = _resolve(page, by_title, by_title_slug, by_slug, by_stem)
        links.append(
            Link(
                target=target,
                page=page,
                heading=heading,
                label=(match.group(2) or "").strip() or None,
                # Body-relative, shifted past the front matter.
                line=document.body[: match.start()].count("\n") + document.body_line,
                resolved=resolved,
            )
        )
    return links


def _resolve(
    page: str,
    by_title: dict[str, list[Document]],
    by_title_slug: dict[str, Document],
    by_slug: dict[str, Document],
    by_stem: dict[str, Document],
) -> Document | None:
    """Exact title, then slugified title, then the document's own slug, then filename."""
    key = page.casefold()
    if key in by_title:
        return by_title[key][0]
    slug = slugify(page)
    if slug in by_title_slug:
        return by_title_slug[slug]
    if slug in by_slug:
        return by_slug[slug]
    return by_stem.get(key)


def heading_ids(html: str) -> set[str]:
    """Every heading id in a rendered page, for verifying `[[Page#Section]]`."""
    return {m.group("id") for m in _HEADING_RE.finditer(html)}


def anchor_for(heading: str) -> str:
    """The anchor a heading text becomes — must match the markdown renderer."""
    return slugify(heading)
