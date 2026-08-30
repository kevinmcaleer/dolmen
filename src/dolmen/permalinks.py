"""Turning a source path plus front matter into an output URL.

Implements Jekyll's permalink placeholders and named styles, so an existing
`permalink:` line in a `_config.yml` or in a document's front matter keeps
producing the same URLs.
"""

from __future__ import annotations

import datetime as dt
import posixpath
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any

#: Jekyll's built-in named permalink styles.
BUILTIN_STYLES = {
    "date": "/:categories/:year/:month/:day/:title:output_ext",
    "pretty": "/:categories/:year/:month/:day/:title/",
    "ordinal": "/:categories/:year/:y_day/:title:output_ext",
    "weekdate": "/:categories/:year/W:week/:short_day/:title:output_ext",
    "none": "/:categories/:title:output_ext",
}

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]")
_SLUG_HYPHEN_RE = re.compile(r"[-\s]+")

#: `_posts` filenames are `YYYY-MM-DD-title.ext`.
DATED_FILENAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>.+)$")


def slugify(value: Any, mode: str = "default") -> str:
    """Lower-case, hyphen-separated slug.

    `mode="raw"` only collapses whitespace, matching Jekyll's `slugify` filter.
    """
    text = str(value)
    if mode == "raw":
        return _SLUG_HYPHEN_RE.sub("-", text.strip())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = _SLUG_STRIP_RE.sub("", text).strip().lower()
    return _SLUG_HYPHEN_RE.sub("-", text)


def split_dated_filename(stem: str) -> tuple[dt.date | None, str]:
    """Split `2026-08-30-my-post` into its date and slug."""
    match = DATED_FILENAME_RE.match(stem)
    if match is None:
        return None, stem
    try:
        date = dt.date.fromisoformat(match.group("date"))
    except ValueError:
        return None, stem
    return date, match.group("slug")


def _placeholders(
    *,
    slug: str,
    date: dt.date | dt.datetime | None,
    categories: list[str],
    collection: str,
    output_ext: str,
    relative_dir: str,
    basename: str,
) -> dict[str, str]:
    values = {
        "title": slug,
        "slug": slug,
        "name": basename,
        "collection": collection,
        "output_ext": output_ext,
        "categories": "/".join(slugify(c) for c in categories),
        "path": relative_dir,
    }

    if date is not None:
        moment = (
            date
            if isinstance(date, dt.datetime)
            else dt.datetime.combine(date, dt.time.min)
        )
        values |= {
            "year": f"{moment.year:04d}",
            "short_year": f"{moment.year % 100:02d}",
            "month": f"{moment.month:02d}",
            "i_month": str(moment.month),
            "short_month": moment.strftime("%b"),
            "long_month": moment.strftime("%B"),
            "day": f"{moment.day:02d}",
            "i_day": str(moment.day),
            "y_day": f"{moment.timetuple().tm_yday:03d}",
            "week": f"{moment.isocalendar().week:02d}",
            "short_day": moment.strftime("%a"),
            "long_day": moment.strftime("%A"),
            "hour": f"{moment.hour:02d}",
            "minute": f"{moment.minute:02d}",
            "second": f"{moment.second:02d}",
        }
    return values


def apply(
    template: str,
    *,
    slug: str,
    date: dt.date | dt.datetime | None = None,
    categories: list[str] | None = None,
    collection: str = "posts",
    output_ext: str = ".html",
    relative_dir: str = "",
    basename: str = "",
) -> str:
    """Expand a permalink template into a URL path."""
    template = BUILTIN_STYLES.get(template, template)
    values = _placeholders(
        slug=slug,
        date=date,
        categories=categories or [],
        collection=collection,
        output_ext=output_ext,
        relative_dir=relative_dir,
        basename=basename or slug,
    )

    # Longest placeholder first so `:short_year` wins over `:short_...` prefixes.
    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    url = re.sub(r":(\w+)", replace, template)
    url = re.sub(r"/{2,}", "/", url)
    if not url.startswith("/"):
        url = "/" + url
    return url


def to_output_path(url: str) -> PurePosixPath:
    """The file a URL is written to.

    A URL ending in `/` becomes `index.html` inside that directory, so pretty
    permalinks work on any plain file server.
    """
    path = url.lstrip("/")
    if url.endswith("/") or not posixpath.splitext(path)[1]:
        return PurePosixPath(path) / "index.html"
    return PurePosixPath(path)
