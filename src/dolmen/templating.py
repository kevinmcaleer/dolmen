"""The Liquid environment, and the Jekyll compatibility layer on top of it.

dolmen renders with **Liquid**, the same template language Jekyll uses, via
`python-liquid`. That makes an existing Jekyll site's templates run unmodified,
which is the whole point of the project.

python-liquid implements *Shopify* Liquid, though, and Jekyll adds things
Shopify never had. Three of them are supplied here:

* **`{% include file.html param=value %}`** — Jekyll's include syntax. Shopify
  writes `{% include 'file' param: value %}`, which python-liquid implements and
  no Jekyll site uses. `JekyllIncludeTag` replaces the built-in tag.
* **Jekyll's filters** — `relative_url`, `where_exp`, `group_by`, `markdownify`
  and the rest. Twenty of Jekyll's filters are already Liquid built-ins; the
  eighteen that are not are defined below.
* **Layouts that wrap.** Liquid has no layout concept at all. `render_layout`
  renders a document, hands the result to its layout as `content`, and repeats
  up the chain — Jekyll's model.

Known incompatibility: python-liquid's expression lexer reserves ~20 words
(`cols`, `limit`, `offset`, `contains`, `and`, …), so `{{ include.cols }}` fails
to parse where Jekyll accepts it. Use `{{ include["cols"] }}` instead. See the
migration notes in the README.
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

from liquid import (
    CachingFileSystemLoader,
    Environment,
    StrictDefaultUndefined,
    TokenStream,
    Undefined,
)
from liquid.ast import Node
from liquid.builtin.expressions import BooleanExpression
from liquid.exceptions import LiquidError
from liquid.filter import with_context, with_environment
from liquid.tag import Tag
from liquid.token import TOKEN_TAG

from .exceptions import RenderError
from .markdown import MarkdownRenderer
from .permalinks import slugify

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_MAX_LAYOUT_DEPTH = 16

#: `{% include name.html %}` — the partial's filename.
_INCLUDE_NAME_RE = re.compile(r"\s*(?P<name>[\w./-]+)\s*")
#: `key=value`, space separated, values optionally quoted.
_INCLUDE_PARAM_RE = re.compile(
    r"""(?P<key>[\w-]+)\s*=\s*(?P<value>"[^"]*"|'[^']*'|\{\{.*?\}\}|[^\s]+)"""
)


# -- Jekyll's include tag ----------------------------------------------------


class JekyllIncludeNode(Node):
    """Renders a partial with its parameters bound to `include.*`."""

    __slots__ = ("template_name", "params")

    def __init__(self, token: Any, template_name: str, params: dict[str, str]) -> None:
        super().__init__(token)
        self.template_name = template_name
        self.params = params

    def render_to_output(self, context: Any, buffer: Any) -> int:
        values: dict[str, Any] = {}
        for key, raw in self.params.items():
            if raw[:1] in {'"', "'"}:
                values[key] = raw[1:-1]
            else:
                # A bare word is a variable, resolved in the caller's scope.
                values[key] = _resolve_path(context, raw)

        template = context.env.get_template(self.template_name, context=context)
        # `include` is a namespace, and the caller's scope stays visible — both
        # as they are in Jekyll.
        with context.extend({"include": values}):
            return template.render_with_context(context, buffer, partial=True)


class JekyllIncludeTag(Tag):
    """`{% include file.html key=value %}`, replacing Shopify's include tag."""

    name = "include"
    block = False

    def parse(self, stream: Any) -> Node:
        token = stream.current
        # Read the raw tag text rather than the token stream: the lexer treats
        # `cols`, `limit` and friends as keywords, so `cols=3` will not tokenise.
        source = token.source
        start = token.start_index
        end = source.index("%}", start)
        inner = source[start:end].lstrip("{%-").strip().removesuffix("-")
        expression = inner[len(self.name):]

        match = _INCLUDE_NAME_RE.match(expression)
        if match is None:
            raise RenderError(f"could not read the include name from {inner!r}")
        params = {
            m.group("key"): m.group("value")
            for m in _INCLUDE_PARAM_RE.finditer(expression[match.end():])
        }
        next(stream)
        return JekyllIncludeNode(token, match.group("name"), params)


def _resolve_path(context: Any, word: str) -> Any:
    """Resolve a dotted variable reference like `post.title` against the scope."""
    parts = word.split(".")
    value = context.resolve(parts[0], default=None)
    for part in parts[1:]:
        if value is None:
            return None
        value = value.get(part) if isinstance(value, Mapping) else getattr(value, part, None)
    return value if value is not None else word


# -- the environment ---------------------------------------------------------


class Templating:
    """Owns the Liquid environment and renders documents through their layouts."""

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
            loader=CachingFileSystemLoader(
                [source / "_includes", source / "_layouts", source],
                ext="",
            ),
            # StrictDefaultUndefined, not StrictUndefined: strict mode should
            # catch typos, but `{{ x | default: y }}` must still work.
            undefined=StrictDefaultUndefined if strict else Undefined,
        )
        self.env.add_tag(JekyllIncludeTag)
        for name, function in self._filters().items():
            self.env.add_filter(name, function)
        for name, function in (extra_filters or {}).items():
            self.env.add_filter(name, function)

    # -- rendering -----------------------------------------------------------

    def render_string(
        self, text: str, context: Mapping[str, Any], *, name: str = "<string>"
    ) -> str:
        """Render a template held in memory (a document body, usually)."""
        try:
            return self.env.from_string(text).render(**context)
        except LiquidError as exc:
            raise RenderError(_message(exc), name) from exc

    def render_layout(
        self,
        layout: str,
        content: str,
        context: Mapping[str, Any],
        *,
        name: str = "<document>",
    ) -> str:
        """Wrap `content` in `layout`, then in that layout's own layout, upwards."""
        from . import frontmatter

        seen: list[str] = []
        current: str | None = layout
        output = content

        while current:
            if current in seen or len(seen) >= _MAX_LAYOUT_DEPTH:
                raise RenderError(f"layout loop: {' -> '.join([*seen, current])}", name)
            seen.append(current)

            path = self._layout_path(current, name)
            parsed = frontmatter.load(path)

            scope = dict(context)
            scope["content"] = output
            scope["layout"] = parsed.metadata
            output = self.render_string(parsed.content, scope, name=str(path))

            following = parsed.metadata.get("layout")
            current = str(following) if following else None

        return output

    def _layout_path(self, layout: str, referrer: str) -> Path:
        directory = self.source / "_layouts"
        for candidate in (layout, f"{layout}.html", f"{layout}.md"):
            path = directory / candidate
            if path.is_file():
                return path
        raise RenderError(f"layout {layout!r} not found in _layouts/", referrer)

    # -- filters -------------------------------------------------------------

    def _filters(self) -> dict[str, Callable[..., Any]]:
        """Jekyll's filters that Liquid does not already provide.

        Liquid already supplies `where`, `date`, `default`, `split`, `strip`,
        `append`, `prepend`, `size`, `first`, `last`, `truncate`, `truncatewords`,
        `join`, `sort`, `uniq`, `map`, `compact`, `reverse`, `escape` and
        `strip_html` — those are left alone.
        """
        return {
            "relative_url": self.relative_url,
            "absolute_url": self.absolute_url,
            "uri_escape": lambda v: urllib.parse.quote(str(v or ""), safe="/:?=&#"),
            "xml_escape": lambda v: html.escape(str(v or ""), quote=False),
            "slugify": lambda v, mode="default": slugify(v, mode),
            "markdownify": lambda v: self.markdown.render(str(v or "")),
            "normalize_whitespace": lambda v: re.sub(r"\s+", " ", str(v or "")).strip(),
            "number_of_words": lambda v: len(str(v or "").split()),
            "array_to_sentence_string": _array_to_sentence_string,
            "jsonify": lambda v: json.dumps(v, default=str),
            "inspect": lambda v: json.dumps(v, default=str),
            "group_by": _group_by,
            "sort_by": _sort_by,
            "where_exp": _where_exp,
            "date_to_string": lambda v: _date(v, "%d %b %Y"),
            "date_to_long_string": lambda v: _date(v, "%d %B %Y"),
            "date_to_xmlschema": lambda v: _coerce_datetime(v).isoformat() if v else "",
            "date_to_rfc822": lambda v: _date(v, "%a, %d %b %Y %H:%M:%S %z"),
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


# -- filter implementations --------------------------------------------------


@with_environment
@with_context
def _where_exp(
    sequence: Iterable[Any],
    variable: str,
    expression: str,
    *,
    context: Any,
    environment: Any,
) -> list[Any]:
    """Jekyll's `where_exp`: keep items for which a Liquid expression is true.

    The template lexer emits markup tokens, so the bare expression is wrapped in
    a tag and its inner token stream parsed — the same route the `if` tag takes.
    """
    stream = TokenStream(environment.tokenizer()(f"{{% if {expression} %}}"))
    token = stream.eat(TOKEN_TAG)
    condition = BooleanExpression.parse(environment, stream.into_inner(tag=token, eat=False))

    kept = []
    for item in sequence or []:
        with context.extend({str(variable): item}):
            if condition.evaluate(context):
                kept.append(item)
    return kept


def _is_absolute(path: str) -> bool:
    return path.startswith(("http://", "https://", "//", "mailto:", "#"))


def _get(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


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
    if "%s" in fmt:
        fmt = fmt.replace("%s", str(int(moment.timestamp())))
    return moment.strftime(fmt)


def _group_by(sequence: Iterable[Any], key: str) -> list[dict[str, Any]]:
    grouped: dict[Any, list[Any]] = {}
    for item in sequence or []:
        grouped.setdefault(_get(item, key), []).append(item)
    return [
        {"name": name, "items": items, "size": len(items)} for name, items in grouped.items()
    ]


def _sort_by(sequence: Iterable[Any], key: str, reverse: bool = False) -> list[Any]:
    items = list(sequence or [])
    return sorted(items, key=lambda i: (_get(i, key) is None, _get(i, key)), reverse=reverse)


def _array_to_sentence_string(sequence: Iterable[Any], connector: str = "and") -> str:
    items = [str(i) for i in (sequence or [])]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {connector} {items[1]}"
    return ", ".join(items[:-1]) + f", {connector} {items[-1]}"


def _message(exc: LiquidError) -> str:
    """Liquid errors carry a multi-line source excerpt; keep the first line."""
    return str(exc).splitlines()[0].strip()
