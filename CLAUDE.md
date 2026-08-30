# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`dolmen` is a static site generator: a Python replacement for Jekyll that keeps Jekyll's directory conventions and front matter but swaps Liquid for Jinja2, and adds a browser-based build front end (Monaco editor, live preview, drag-and-drop images) that runs only during development.

The distribution name is `dolmen-ssg` (PyPI's `dolmen` is taken by a dormant namespace package); the import package and CLI command are both `dolmen`.

## Commands

```sh
uv venv && uv pip install -e ".[dev]"    # set up

pytest                                    # all tests (fast; no network, no fixtures on disk to clean)
pytest tests/test_builder.py              # one file
pytest tests/test_builder.py::test_wiki_links_resolve_by_title   # one test
pytest -k permalink                       # by name

ruff check .                              # lint (line length 100)
ruff check --fix .                        # autofix

dolmen new /tmp/demo && cd /tmp/demo      # scaffold a throwaway site
dolmen build                              # build it
dolmen serve -p 4000                      # serve + live reload; front end at /_dolmen/
dolmen doctor                             # build and print every warning
```

There is no build step for the front end — `admin/assets/` is plain HTML/CSS/JS, and Monaco loads from a CDN at runtime.

## Architecture

### The build is five ordered passes

`builder.py` is the spine, and the ordering is load-bearing. Read it first.

1. **Discover** — walk the source; a file with front matter becomes a `Document`, everything else a `StaticFile`. Read `_data/`.
2. **Apply defaults** — `_config.yml`'s `defaults:` rules and per-collection defaults fill in missing front matter. Explicit front matter always wins.
3. **Assign URLs** — every document gets its permalink *before* anything renders.
4. **Render** — body through Jinja2, then markdown, then up the layout chain.
5. **Write** — documents to their permalink paths; static files copied.

Steps 3 and 4 are separate passes because templates loop over `site.posts` and read `post.url`; a single pass would see empty URLs for documents not yet rendered. If you add a pass, keep this invariant.

### Two Jekyll behaviours reproduced deliberately

Both live in `templating.py` and are easy to "fix" into being wrong:

- **Layouts wrap, they don't inherit.** `render_layout` renders the document, hands the result to its layout as `content`, then hands *that* to the layout's own layout, up the chain. This is not Jinja2's `{% extends %}`, and layouts have no `{% block %}`. Loop detection caps at `_MAX_LAYOUT_DEPTH`.
- **Includes take parameters.** Jinja2's `{% include %}` can't, so `include()` is a global function wrapped in `@pass_context`. The `pass_context` matters: without it the include can't see the caller's `site` and `page`, which was a real bug.

### baseurl is not part of page.url

`_assign_urls` deliberately does *not* prefix URLs with `baseurl`. Jekyll keeps `page.url` site-relative and leaves the prefix to the `relative_url` filter. Baking it in also pushes every output file into a `sub/` directory, which is not what a baseurl means. Templates must use `| relative_url` for internal links.

### Module map

| Module | Responsibility |
| --- | --- |
| `config.py` | Parse `_config.yml`; normalise collections, defaults, exclude/include, permalink |
| `frontmatter.py` | Split the `---` fenced YAML block off a file; `dump()` round-trips for the editor |
| `models.py` | `Document`, `StaticFile`, `Site`; the `site.*` / `page.*` mappings templates see |
| `permalinks.py` | Jekyll placeholders (`:year`, `:title`, `:categories`, …) and named styles |
| `markdown.py` | markdown-it-py setup, build-time Pygments highlighting, `[[wiki links]]` |
| `templating.py` | Jinja2 env, Jekyll-compatible filters, layout chain, parameterised includes |
| `plugins.py` | Hook dispatch; loads `_plugins/*.py` and `dolmen.plugins` entry points |
| `builder.py` | The five passes above |
| `server.py` | Dev server: serve `_site/`, watch, rebuild, SSE live reload |
| `admin/app.py` | HTTP API behind the front end (tree, read, write, create, upload, build) |
| `admin/assets/` | The front end itself — no build step, Monaco from CDN |
| `scaffold/` | `dolmen new` templates, as Python string constants |

### Rendering gotchas worth knowing before touching `markdown.py`

- markdown-it only uses a highlighter's output verbatim if it **starts with `<pre`**. Pygments' default output starts with `<div>`, which gets wrapped a second time. `_highlight` emits `nowrap=True` spans and supplies its own `<pre><code>`.
- Inline rules must push tokens via `state.push()`, never `state.tokens.append()`. Appending bypasses the pending-text buffer, so the token is emitted *before* the text that preceded it in the same paragraph.

### The front end is development-only

`admin/` is mounted by `server.py` at `/_dolmen` and is never written to the output directory. Anything it adds must not leak into a built site. Every path arriving from the browser goes through `resolve()` in `admin/app.py`, which rejects paths escaping the site source — keep that check on any new route that takes a path.

## Conventions

- Errors the user caused (bad front matter, missing layout, unparseable config) raise a `StaticError` subclass carrying the offending path, so the CLI prints one line instead of a traceback. Non-strict builds collect these as warnings and keep going; `--strict` re-raises. Preserve that split when adding failure modes.
- Tests build real sites in `tmp_path` via the `site` fixture in `conftest.py` rather than mocking the filesystem. Add to that fixture when a test needs new content.
- `:title` in a permalink is the **filename** slug (or front-matter `slug:`), not the slugified title — same as Jekyll. Tests assert on this.

## Known gaps

Tracked as issues; do not treat these as bugs to fix incidentally:

- Kramdown inline attribute lists (`{:class="cover"}`) are unsupported.
- Liquid tags (`{% assign %}`, `{% capture %}`) have no shim; sites must port to Jinja2.
- `jekyll-*` plugin names in `plugins:` are silently ignored so an unported config still builds.
