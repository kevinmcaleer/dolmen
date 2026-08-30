"""Markdown rendering.

Jekyll renders with kramdown; we render with markdown-it-py in CommonMark mode
plus the plugins that cover the kramdown features actually used in practice
(tables, footnotes, definition lists, attributes, task lists).

Two deliberate extras beyond CommonMark:

* fenced code blocks are highlighted with Pygments at build time, so the output
  needs no client-side highlighter;
* `[[wiki links]]` resolve against the site's documents — the "wiki like
  functionality for quickly referencing other parts of the site".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .permalinks import slugify

#: `[[target]]` or `[[target|label]]`.
WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")

#: Resolves a wiki-link target to a URL, or None when the target is unknown.
LinkResolver = Callable[[str], str | None]


def _highlight(code: str, lang: str, _attrs: str) -> str:
    """Pygments highlighter wired into markdown-it's fence renderer.

    markdown-it only uses a highlighter's output verbatim when it starts with
    `<pre`; anything else it wraps in its own `<pre><code>`. So emit the bare
    highlighted spans and supply the wrapper ourselves, or the block ends up
    nested twice.
    """
    if not lang:
        return ""  # fall through to markdown-it's default escaping
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return ""
    inner = highlight(code, lexer, HtmlFormatter(nowrap=True))
    return (
        f'<pre class="highlight"><code class="language-{escape(lang, quote=True)}">'
        f"{inner}</code></pre>"
    )


@dataclass
class MarkdownRenderer:
    """Renders markdown to HTML, with wiki links resolved against the site."""

    #: Called for each `[[target]]`; unresolved targets render as broken links.
    link_resolver: LinkResolver | None = None
    #: Extra markdown-it plugins contributed by site plugins.
    extensions: list[Callable[[MarkdownIt], Any]] = field(default_factory=list)
    #: Heading levels that get an `id` and an anchor link.
    anchor_levels: tuple[int, ...] = (1, 2, 3, 4, 5, 6)

    def __post_init__(self) -> None:
        self._md = self._build()

    def _build(self) -> MarkdownIt:
        md = (
            MarkdownIt("commonmark", {"html": True, "linkify": True, "highlight": _highlight})
            .enable("table")
            .enable("strikethrough")
            .use(footnote_plugin)
            .use(deflist_plugin)
            .use(tasklists_plugin, enabled=True)
            .use(attrs_plugin, spans=True)
            # slug_func is ours so `[[Page#Section]]` can compute the same
            # anchor the heading was given.
            .use(
                anchors_plugin,
                max_level=max(self.anchor_levels),
                permalink=False,
                slug_func=slugify,
            )
        )
        md.use(self._wikilink_plugin)
        for extension in self.extensions:
            md.use(extension)
        return md

    # -- wiki links ----------------------------------------------------------

    def _wikilink_plugin(self, md: MarkdownIt) -> None:
        """Register `[[...]]` as an inline rule, ahead of normal link parsing."""

        def rule(state: Any, silent: bool) -> bool:
            src = state.src
            pos = state.pos
            if not src.startswith("[[", pos):
                return False
            match = WIKILINK_RE.match(src, pos)
            if match is None:
                return False
            if not silent:
                self._push_wikilink(state, match.group(1).strip(), match.group(2))
            state.pos = match.end()
            return True

        md.inline.ruler.before("link", "wikilink", rule)

    def _push_wikilink(self, state: Any, target: str, label: str | None) -> None:
        url = self.link_resolver(target) if self.link_resolver else None
        text = (label or target).strip()

        open_token = state.push("link_open", "a", 1)
        if url:
            open_token.attrs = {"href": url, "class": "wikilink"}
        else:
            open_token.attrs = {
                "href": "#",
                "class": "wikilink wikilink-broken",
                "title": f"Unresolved: {target}",
            }

        text_token = state.push("text", "", 0)
        text_token.content = text

        state.push("link_close", "a", -1)

    # -- public API ----------------------------------------------------------

    def render(self, text: str) -> str:
        return self._md.render(text)

    def render_inline(self, text: str) -> str:
        return self._md.renderInline(text)

    @staticmethod
    def highlight_css(style: str = "default") -> str:
        """The stylesheet matching the build-time Pygments output."""
        return HtmlFormatter(style=style, cssclass="highlight").get_style_defs(".highlight")


def find_wikilinks(text: str) -> list[str]:
    """Every wiki-link target in `text`, for backlink indexing."""
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]
