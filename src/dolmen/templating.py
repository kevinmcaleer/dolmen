"""The Jinja2 environment, and the Jekyll-compatible helpers layered on it.

Two things about Jekyll are reproduced here deliberately:

* **Layouts wrap, they do not inherit.** A document renders to HTML, that HTML
  becomes `content` inside its layout, and the layout's output becomes `content`
  inside *its* layout, up the chain. This is Jekyll's model, not Jinja2's
  `{% extends %}`, and it is why layouts do not need a `{% block %}`.
* **Includes take parameters.** Jinja2's `{% include %}` cannot, so includes are
  called as `{{ include('card.html', title='Hi') }}` and read their arguments
  from `include.*`, exactly as Liquid does.

Note that the template *language* is Jinja2, not Liquid. Filter names and the
`site`/`page` variables match Jekyll, but Liquid tags (`{% assign %}`,
`{% capture %}`) do not — see the migration notes in the README.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from jinja2 import (
    ChoiceLoader,
    DictLoader,
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    Undefined,
    pass_context,
)
from jinja2.exceptions import TemplateError

from .exceptions import RenderError
from .markdown import MarkdownRenderer
from .permalinks import slugify

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_MAX_LAYOUT_DEPTH = 16


class Templating:
    """Owns the Jinja2 environment and renders documents through their layouts."""

    def __init__(
        self,
        source: Path,
        *,
        markdown: MarkdownRenderer,
        baseurl: str = "",
        url: str = "",
        extra_filters: Mapping[str, Callable[..., Any]] | None = None,
        strict: bool = False,
    ) -> None:
        self.source = source
        self.markdown = markdown
        self.baseurl = baseurl
        self.url = url

        self.env = Environment(
            loader=ChoiceLoader(
                [
                    FileSystemLoader(
                        [source / "_layouts", source / "_includes", source],
                        followlinks=True,
                    ),
                    DictLoader({}),
                ]
            ),
            autoescape=False,  # content is authored HTML/markdown, as in Jekyll
            undefined=StrictUndefined if strict else Undefined,
            keep_trailing_newline=True,
        )
        self.env.filters.update(self._filters())
        self.env.globals["include"] = pass_context(
            lambda context, name, **kwargs: self._include(context, name, **kwargs)
        )
        self.env.globals["now"] = lambda: dt.datetime.now(dt.UTC)
        if extra_filters:
            self.env.filters.update(extra_filters)

    # -- rendering -----------------------------------------------------------

    def render_string(
        self, text: str, context: Mapping[str, Any], *, name: str = "<string>"
    ) -> str:
        """Render a template held in memory (a document body, usually)."""
        try:
            template = self.env.from_string(text)
            return template.render(**context)
        except TemplateError as exc:
            raise RenderError(str(exc), name) from exc

    def render_layout(
        self,
        layout: str,
        content: str,
        context: Mapping[str, Any],
        *,
        name: str = "<document>",
    ) -> str:
        """Wrap `content` in `layout`, then in that layout's own layout, upwards."""
        from . import frontmatter  # local import: frontmatter imports nothing from here

        seen: list[str] = []
        current_layout: str | None = layout
        output = content

        while current_layout:
            if current_layout in seen or len(seen) >= _MAX_LAYOUT_DEPTH:
                raise RenderError(
                    f"layout loop: {' -> '.join([*seen, current_layout])}", name
                )
            seen.append(current_layout)

            path = self._layout_path(current_layout, name)
            parsed = frontmatter.load(path)

            layout_context = dict(context)
            layout_context["content"] = output
            # A layout's own front matter is readable as `layout.*`.
            layout_context["layout"] = parsed.metadata
            output = self.render_string(parsed.content, layout_context, name=str(path))

            next_layout = parsed.metadata.get("layout")
            current_layout = str(next_layout) if next_layout else None

        return output

    def _layout_path(self, layout: str, referrer: str) -> Path:
        directory = self.source / "_layouts"
        for candidate in (layout, f"{layout}.html", f"{layout}.md"):
            path = directory / candidate
            if path.is_file():
                return path
        raise RenderError(f"layout {layout!r} not found in _layouts/", referrer)

    def _include(self, context: Any, name: str, **kwargs: Any) -> str:
        """Liquid-style `{% include %}`: parameters arrive as `include.*`.

        The include inherits the caller's context — `site`, `page` and anything
        else in scope — so an include reads exactly as it does in Jekyll.
        """
        try:
            template = self.env.get_template(name)
        except TemplateNotFound as exc:
            raise RenderError(f"include {name!r} not found in _includes/") from exc
        scope = dict(context) if context else {}
        scope["include"] = _AttrDict(kwargs)
        return template.render(**scope)

    # -- filters -------------------------------------------------------------

    def _filters(self) -> dict[str, Callable[..., Any]]:
        return {
            # URLs
            "relative_url": self.relative_url,
            "absolute_url": self.absolute_url,
            "uri_escape": lambda v: urllib.parse.quote(str(v), safe="/:?=&#"),
            # text
            "slugify": lambda v, mode="default": slugify(v, mode),
            "markdownify": lambda v: self.markdown.render(str(v or "")),
            "strip_html": lambda v: _HTML_TAG_RE.sub("", str(v or "")),
            "strip_newlines": lambda v: str(v or "").replace("\n", "").replace("\r", ""),
            "normalize_whitespace": lambda v: re.sub(r"\s+", " ", str(v or "")).strip(),
            "number_of_words": lambda v: len(str(v or "").split()),
            "truncatewords": _truncatewords,
            "xml_escape": lambda v: html.escape(str(v or ""), quote=False),
            "split": lambda v, sep=" ": str(v or "").split(sep),
            "strip": lambda v: str(v or "").strip(),
            "append": lambda v, suffix="": f"{v}{suffix}",
            "prepend": lambda v, prefix="": f"{prefix}{v}",
            "size": _size,
            # dates
            "date": _date,
            "date_to_string": lambda v: _date(v, "%d %b %Y"),
            "date_to_long_string": lambda v: _date(v, "%d %B %Y"),
            "date_to_xmlschema": lambda v: _coerce_datetime(v).isoformat() if v else "",
            "date_to_rfc822": lambda v: _date(v, "%a, %d %b %Y %H:%M:%S %z"),
            # collections
            "where": _where,
            "where_exp": self._where_exp,
            "group_by": _group_by,
            "sort_by": lambda seq, key, reverse=False: sorted(
                seq, key=lambda i: (_get(i, key) is None, _get(i, key)), reverse=reverse
            ),
            "array_to_sentence_string": _array_to_sentence_string,
            "jsonify": lambda v: json.dumps(v, default=str),
            "compact": lambda seq: [i for i in seq if i is not None],
        }

    def relative_url(self, value: Any) -> str:
        """Prefix a site-root path with `baseurl`."""
        path = str(value or "")
        if _is_absolute(path):
            return path
        return f"{self.baseurl}/{path.lstrip('/')}" if path else self.baseurl or "/"

    def absolute_url(self, value: Any) -> str:
        path = str(value or "")
        if _is_absolute(path):
            return path
        return f"{self.url}{self.relative_url(path)}"

    def _where_exp(self, seq: Iterable[Any], variable: str, expression: str) -> list[Any]:
        """`where_exp`, evaluated as a Jinja2 expression rather than Liquid."""
        compiled = self.env.compile_expression(expression, undefined_to_none=True)
        return [item for item in seq if compiled(**{variable: item})]


class _AttrDict(dict):
    """A dict whose keys are also attributes, so `include.title` works.

    Jekyll include parameters are hyphen-friendly (`include.use-links`); those
    remain reachable with `include['use-links']`.
    """

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            return Undefined(name=item)


# -- filter implementations --------------------------------------------------


def _is_absolute(path: str) -> bool:
    return path.startswith(("http://", "https://", "//", "mailto:", "#"))


def _get(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _size(value: Any) -> int:
    try:
        return len(value)
    except TypeError:
        return 0


def _coerce_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, str):
        if value.strip().lower() == "now":
            return dt.datetime.now(dt.UTC)
        return dt.datetime.fromisoformat(value)
    raise ValueError(f"cannot read {value!r} as a date")


def _date(value: Any, fmt: str = "%d %b %Y") -> str:
    if value in (None, ""):
        return ""
    try:
        moment = _coerce_datetime(value)
    except (ValueError, TypeError):
        return str(value)
    # Liquid's `%s` (epoch seconds) is not portable through strftime on all platforms.
    if "%s" in fmt:
        fmt = fmt.replace("%s", str(int(moment.timestamp())))
    return moment.strftime(fmt)


def _truncatewords(value: Any, count: int = 15, suffix: str = "...") -> str:
    words = str(value or "").split()
    if len(words) <= count:
        return " ".join(words)
    return " ".join(words[:count]) + suffix


def _where(seq: Iterable[Any], key: str, value: Any = None) -> list[Any]:
    """Jekyll's `where`: match a key, or keep truthy values when none is given."""
    items = list(seq or [])
    if value is None:
        return [i for i in items if _get(i, key)]
    return [i for i in items if _matches(_get(i, key), value)]


def _matches(found: Any, wanted: Any) -> bool:
    if isinstance(found, (list, tuple, set)):
        return wanted in found
    return found == wanted


def _group_by(seq: Iterable[Any], key: str) -> list[dict[str, Any]]:
    grouped: dict[Any, list[Any]] = {}
    for item in seq or []:
        grouped.setdefault(_get(item, key), []).append(item)
    return [
        {"name": name, "items": items, "size": len(items)} for name, items in grouped.items()
    ]


def _array_to_sentence_string(seq: Iterable[Any], connector: str = "and") -> str:
    items = [str(i) for i in (seq or [])]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {connector} {items[1]}"
    return ", ".join(items[:-1]) + f", {connector} {items[-1]}"
