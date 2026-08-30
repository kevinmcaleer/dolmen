"""Site validation — the checks behind the front end's problems panel.

Runs *after* a build, against the real output directory rather than the site
model, so a link is only reported broken if the file genuinely is not there.

Every problem carries a `why`, not just a message. A checker that names a fault
without explaining it teaches nothing, and these run in front of people who are
writing prose, not debugging a build system.

Checks are deliberately conservative: a false positive in a panel that is always
on screen is worse than a missed one, because people stop reading a panel that
cries wolf. Anything ambiguous (external links, generated URLs, templated
attributes) is skipped rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from .links import LinkIndex, anchor_for, build_index, heading_ids
from .permalinks import to_output_path

if TYPE_CHECKING:
    from .config import Config
    from .models import Document, Site

Severity = Literal["error", "warning", "info"]

#: Severity ordering, worst first — used to sort and to pick the badge colour.
SEVERITY_ORDER: dict[str, int] = {"error": 0, "warning": 1, "info": 2}

#: `href="..."` and `src="..."` in rendered HTML.
_ATTR_RE = re.compile(r"""\b(?:href|src)\s*=\s*["'](?P<url>[^"']*)["']""", re.I)
#: `{% include name.html %}` in a template or document body.
_INCLUDE_RE = re.compile(r"\{%-?\s*include\s+(?P<name>[\w./-]+)")
#: A fenced code block's language.
_FENCE_RE = re.compile(r"^```+[ \t]*(?P<lang>[\w+#.-]+)[ \t]*$", re.M)

#: Schemes and forms that are never checked.
_SKIP_PREFIXES = ("http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:", "#")

#: Front matter keys expected on every document, by collection.
_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "posts": ("title", "date"),
    "pages": ("title",),
}


@dataclass(frozen=True)
class Problem:
    """One finding, ready to render as a card in the panel."""

    severity: Severity
    #: Stable identifier for the check, e.g. "broken-link".
    rule: str
    title: str
    message: str
    #: Why this matters — shown under the message.
    why: str
    #: Source file the author would edit, relative to the site root.
    file: str | None = None
    #: 1-indexed line in that file, when it can be located.
    line: int | None = None
    #: Built URL to open in the preview, when there is one.
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "title": self.title,
            "message": self.message,
            "why": self.why,
            "file": self.file,
            "line": self.line,
            "url": self.url,
        }


@dataclass
class Report:
    """Every problem found, plus the counts the badge needs."""

    problems: list[Problem] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for p in self.problems if p.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for p in self.problems if p.severity == "warning")

    @property
    def infos(self) -> int:
        return sum(1 for p in self.problems if p.severity == "info")

    @property
    def total(self) -> int:
        return len(self.problems)

    @property
    def worst(self) -> Severity | None:
        """The most severe severity present, for the badge colour."""
        if not self.problems:
            return None
        return min((p.severity for p in self.problems), key=lambda s: SEVERITY_ORDER[s])

    def sorted(self) -> list[Problem]:
        """Worst first, then by file, so the panel reads top-down by urgency."""
        return sorted(
            self.problems,
            key=lambda p: (SEVERITY_ORDER[p.severity], p.file or "", p.line or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "problems": [p.to_dict() for p in self.sorted()],
            "errors": self.errors,
            "warnings": self.warnings,
            "infos": self.infos,
            "total": self.total,
            "worst": self.worst,
        }


class Validator:
    """Runs every check over a built site."""

    def __init__(
        self,
        site: Site,
        config: Config,
        *,
        build_warnings: list[str] | None = None,
        link_index: LinkIndex | None = None,
    ) -> None:
        self.site = site
        self.config = config
        self.build_warnings = build_warnings or []
        #: Reuse the build's index when there is one; rebuild it otherwise.
        self.link_index = link_index
        self.output = config.destination
        self._output_files = self._index_output()

    def run(self) -> Report:
        report = Report()
        specific = [
            *self._check_front_matter(),
            *self._check_layouts(),
            *self._check_includes(),
            *self._check_wikilinks(),
            *self._check_links_and_images(),
            *self._check_code_fences(),
        ]
        # A build warning is the generic version of a fault a specific check
        # explains far better, so only surface warnings nothing else covers.
        explained = {p.file for p in specific if p.severity == "error"}
        report.problems.extend(
            p for p in self._check_build_warnings() if p.file not in explained
        )
        report.problems.extend(specific)
        return report

    # -- the output index ----------------------------------------------------

    def _index_output(self) -> set[str]:
        """Every file actually written, as posix paths relative to the output."""
        if not self.output.is_dir():
            return set()
        return {
            str(PurePosixPath(p.relative_to(self.output).as_posix()))
            for p in self.output.rglob("*")
            if p.is_file()
        }

    def _exists(self, url: str) -> bool:
        """Whether a site-relative URL resolves to something that was written."""
        path = url.split("#", 1)[0].split("?", 1)[0]
        baseurl = self.config.baseurl
        if baseurl and path.startswith(baseurl):
            path = path[len(baseurl):] or "/"
        if not path.startswith("/"):
            return True  # relative link; resolving it needs the referrer's dir
        candidate = str(to_output_path(path))
        if candidate in self._output_files:
            return True
        # `/about` may have been written as `about.html`.
        bare = path.lstrip("/")
        return bare in self._output_files or f"{bare}.html" in self._output_files

    # -- checks --------------------------------------------------------------

    def _check_build_warnings(self) -> list[Problem]:
        """Warnings the build itself produced — template and YAML errors."""
        problems = []
        for warning in self.build_warnings:
            file, _, message = warning.partition(": ")
            problems.append(
                Problem(
                    severity="error",
                    rule="build-error",
                    title="The build reported a problem",
                    message=message or warning,
                    why=(
                        "The page was skipped or rendered incompletely, so what you "
                        "see in the preview is not what the site will publish."
                    ),
                    file=self._relative(file) if message else None,
                )
            )
        return problems

    def _check_front_matter(self) -> list[Problem]:
        problems = []
        for document in self.site.documents:
            required = _REQUIRED_KEYS.get(document.collection, ("title",))
            for key in required:
                if document.metadata.get(key) in (None, ""):
                    problems.append(
                        Problem(
                            severity="warning",
                            rule="missing-front-matter",
                            title=f"Missing `{key}` in front matter",
                            message=f"{document.relative_path} has no `{key}:`.",
                            why=_FRONT_MATTER_WHY.get(key, _FRONT_MATTER_WHY["_default"]),
                            file=str(document.relative_path),
                            line=1,
                            url=document.url or None,
                        )
                    )
        return problems

    def _check_layouts(self) -> list[Problem]:
        available = {
            p.stem for p in (self.config.source / "_layouts").glob("*.*") if p.is_file()
        }
        problems = []
        for document in self.site.documents:
            layout = document.layout
            if layout and layout not in available:
                problems.append(
                    Problem(
                        severity="error",
                        rule="missing-layout",
                        title=f"Layout `{layout}` does not exist",
                        message=(
                            f"{document.relative_path} asks for `layout: {layout}`, but "
                            f"_layouts/{layout}.html is not there."
                        ),
                        why=(
                            "The page renders as bare content with no navigation, styling "
                            "or metadata around it."
                        ),
                        file=str(document.relative_path),
                        line=_find_line(document, "layout:"),
                        url=document.url or None,
                    )
                )
        return problems

    def _check_includes(self) -> list[Problem]:
        directory = self.config.source / "_includes"
        problems = []
        for text, relative in self._templates():
            for match in _INCLUDE_RE.finditer(text):
                name = match.group("name")
                if (directory / name).is_file():
                    continue
                problems.append(
                    Problem(
                        severity="error",
                        rule="missing-include",
                        title=f"Include `{name}` does not exist",
                        message=f"{relative} includes `{name}`, which is not in _includes/.",
                        why=(
                            "Liquid raises on a missing include, so the whole page fails "
                            "to render rather than losing just that fragment."
                        ),
                        file=relative,
                        line=text[: match.start()].count("\n") + 1,
                    )
                )
        return problems

    def _check_wikilinks(self) -> list[Problem]:
        """Broken targets, unknown headings, and ambiguous titles."""
        index = self.link_index or build_index(self.site)
        problems = []

        for document, link in index.broken():
            problems.append(
                Problem(
                    severity="warning",
                    rule="broken-wikilink",
                    title=f"`[[{link.target}]]` does not resolve",
                    message=(
                        f"{document.relative_path} links to `{link.page}`, but no document "
                        "has that title, slug or filename."
                    ),
                    why=(
                        "The link still renders, marked as broken, so readers see a "
                        "dead link on the published page."
                    ),
                    file=str(document.relative_path),
                    line=link.line,
                    url=document.url or None,
                )
            )

        # A heading target has to name a heading that exists on the target page.
        for path, links in index.outgoing.items():
            source = index.documents[path]
            for link in links:
                if link.heading is None or link.resolved is None:
                    continue
                available = heading_ids(link.resolved.content or "")
                anchor = anchor_for(link.heading)
                if anchor in available:
                    continue
                problems.append(
                    Problem(
                        severity="warning",
                        rule="unknown-heading",
                        title=f"`{link.resolved.title}` has no heading `{link.heading}`",
                        message=(
                            f"{path} links to `[[{link.target}]]`, but that page has no "
                            f"matching heading."
                            + (f" It has: {', '.join(sorted(available)[:6])}." if available else "")
                        ),
                        why=(
                            "The link lands on the page but not the section, so the reader "
                            "has to hunt for what you were pointing at."
                        ),
                        file=path,
                        line=link.line,
                        url=source.url or None,
                    )
                )

        for title, documents in index.ambiguous.items():
            if not index.incoming_for_title(title):
                continue
            paths = ", ".join(str(d.relative_path) for d in documents)
            problems.append(
                Problem(
                    severity="warning",
                    rule="ambiguous-wikilink",
                    title=f"More than one document is titled `{documents[0].title}`",
                    message=f"`[[{documents[0].title}]]` could mean any of: {paths}.",
                    why=(
                        "Links resolve to the first by collection then path, which is "
                        "stable but arbitrary — rename one, or link by filename instead."
                    ),
                    file=str(documents[0].relative_path),
                    line=1,
                )
            )
        return problems

    def _check_links_and_images(self) -> list[Problem]:
        """Internal links and images in the rendered output."""
        problems = []
        for document in self.site.documents:
            if not document.content:
                continue
            seen: set[str] = set()
            for match in _ATTR_RE.finditer(document.content):
                url = match.group("url").strip()
                if not url or url in seen or url.startswith(_SKIP_PREFIXES):
                    continue
                seen.add(url)
                if self._exists(url):
                    continue
                is_image = _looks_like_image(url)
                problems.append(
                    Problem(
                        severity="warning",
                        rule="missing-asset" if is_image else "broken-link",
                        title=(
                            f"Image `{url}` is missing" if is_image
                            else f"Link to `{url}` is broken"
                        ),
                        message=(
                            f"{document.relative_path} points at `{url}`, which was not "
                            "written to the built site."
                        ),
                        why=(
                            "Readers get a broken image placeholder."
                            if is_image
                            else "Readers get a 404, and search engines drop the page's authority."
                        ),
                        file=str(document.relative_path),
                        line=_find_line(document, url),
                        url=document.url or None,
                    )
                )
        return problems

    def _check_code_fences(self) -> list[Problem]:
        """Fenced code whose language Pygments cannot lex — so it won't highlight."""
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound

        problems = []
        for document in self.site.documents:
            if not document.is_markdown:
                continue
            for match in _FENCE_RE.finditer(document.body):
                language = match.group("lang")
                if language.lower() in _NON_LANGUAGE_FENCES:
                    continue
                try:
                    get_lexer_by_name(language)
                except ClassNotFound:
                    problems.append(
                        Problem(
                            severity="info",
                            rule="unknown-code-language",
                            title=f"Unknown code language `{language}`",
                            message=(
                                f"{document.relative_path} has a ```{language} block, and "
                                "no highlighter matches that name."
                            ),
                            why=(
                                "The block still renders, but as plain unhighlighted text — "
                                "usually a typo like `pyton` or `bash-` for `bash`."
                            ),
                            file=str(document.relative_path),
                            line=document.body[: match.start()].count("\n") + 1,
                            url=document.url or None,
                        )
                    )
        return problems

    # -- helpers -------------------------------------------------------------

    def _templates(self) -> list[tuple[str, str]]:
        """Every layout, include and document body, for template-level checks."""
        found: list[tuple[str, str]] = []
        for folder in ("_layouts", "_includes"):
            directory = self.config.source / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    found.append(
                        (path.read_text(encoding="utf-8"), f"{folder}/{path.name}")
                    )
        for document in self.site.documents:
            found.append((document.body, str(document.relative_path)))
        return found

    def _relative(self, path: str) -> str | None:
        try:
            return str(Path(path).relative_to(self.config.source))
        except ValueError:
            return path or None


_FRONT_MATTER_WHY = {
    "title": (
        "The title is the page's <title>, its heading, and the text of every link "
        "to it — an untitled page is invisible in navigation and in search results."
    ),
    "date": (
        "Posts sort by date and their permalink is built from it. Without one the "
        "post falls back to the file's modification time, which changes on every edit."
    ),
    "_default": "This field is expected on documents in this collection.",
}

#: Fence "languages" that are conventions rather than real lexers.
_NON_LANGUAGE_FENCES = {"mermaid", "text", "plaintext", "output", "console", "none", "txt"}

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".ico")


def _looks_like_image(url: str) -> bool:
    return url.split("?", 1)[0].lower().endswith(_IMAGE_SUFFIXES)


def _find_line(document: Document, needle: str) -> int | None:
    """The 1-indexed line of `needle` in the document's source file."""
    try:
        text = document.source.read_text(encoding="utf-8")
    except OSError:
        return None
    index = text.find(needle)
    if index == -1:
        return None
    return text[:index].count("\n") + 1


def validate(
    site: Site,
    config: Config,
    *,
    build_warnings: list[str] | None = None,
    link_index: LinkIndex | None = None,
) -> Report:
    """Run every check over a built site."""
    return Validator(
        site, config, build_warnings=build_warnings, link_index=link_index
    ).run()
