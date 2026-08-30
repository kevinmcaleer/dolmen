# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`dolmen` is a static site generator: a Python replacement for Jekyll that keeps Jekyll's directory conventions, front matter **and Liquid templates**, so an existing Jekyll site's templates run unmodified. It adds a browser-based build front end (Monaco editor, live preview, drag-and-drop images) that runs only during development.

The distribution name is `dolmen-ssg` (PyPI's `dolmen` is taken by a dormant namespace package); the import package and CLI command are both `dolmen`.

## Commands

```sh
uv venv && uv pip install -e ".[dev]"    # set up

pytest                                    # all tests
pytest --cov                              # with the 80% coverage gate
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
4. **Render** — body through Liquid, then markdown, then up the layout chain.
5. **Write** — documents to their permalink paths; static files copied.

Steps 3 and 4 are separate passes because templates loop over `site.posts` and read `post.url`; a single pass would see empty URLs for documents not yet rendered. If you add a pass, keep this invariant.

### The Liquid compatibility layer

Rendering is Liquid via `python-liquid`, which implements *Shopify* Liquid. Jekyll adds things Shopify never had, and `templating.py` supplies them. All three are easy to "fix" into being wrong:

- **`JekyllIncludeTag` replaces the built-in include tag.** Jekyll writes `{% include card.html title="Hi" %}`; Shopify writes `{% include 'card' title: 'Hi' %}`. The tag reads the **raw tag text** rather than the token stream — deliberately. The lexer treats `cols`, `limit` and `offset` as keywords, so `cols=3` will not tokenise. Don't "clean this up" by parsing tokens.
- **Layouts wrap, they don't inherit.** Liquid has no layout concept at all. `render_layout` renders the document, hands the result to its layout as `content`, then hands *that* to the layout's own layout, up the chain. Loop detection caps at `_MAX_LAYOUT_DEPTH`.
- **Strict mode uses `StrictDefaultUndefined`, not `StrictUndefined`.** Strict should catch typos while leaving `{{ x | default: y }}` working. `StrictUndefined` raises before the filter runs.

`where_exp` is the only filter with real machinery: it wraps the expression in `{% if %}`, takes the inner token stream, and parses it as a `BooleanExpression` — the same route the `if` tag uses. There is no public API for parsing a bare expression.

Twenty of Jekyll's filters are already Liquid built-ins (`where`, `date`, `default`, `split`, `strip_html`, `truncate`, …). Only add a filter to `_filters()` after checking it isn't one of them.

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
| `templating.py` | Liquid env, Jekyll's include tag, the 18 non-builtin Jekyll filters, layout chain |
| `plugins.py` | Hook dispatch; loads `_plugins/*.py` and `dolmen.plugins` entry points |
| `validate.py` | The checks behind the problems panel and `dolmen doctor` |
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

## Testing

**Every new feature ships with tests, and coverage must stay at or above 80%.** This is enforced, not aspirational: `fail_under = 80` in `pyproject.toml` means `pytest --cov` exits non-zero below it, and CI runs it.

```sh
pytest --cov                    # with the coverage gate
pytest --cov --no-cov-on-fail   # see failures without the coverage noise
pytest -q --no-cov              # fast, while iterating
```

When adding a feature, cover the failure paths too, not just the happy one — the bugs found in this codebase so far (double-wrapped code blocks, wiki links jumping to the start of a paragraph, `baseurl` mangling output paths, `page.description` under strict mode) were all edge cases a happy-path test would have missed.

Currently at ~87%. `server.py` is the weakest at ~64%: `serve()`, the watcher loop and the SSE stream need a running event loop, so they are exercised by hand rather than in tests. Prefer `TestClient` over manual verification wherever it reaches.

## Conventions

- Errors the user caused (bad front matter, missing layout, unparseable config) raise a `StaticError` subclass carrying the offending path, so the CLI prints one line instead of a traceback. Non-strict builds collect these as warnings and keep going; `--strict` re-raises. Preserve that split when adding failure modes.
- Tests build real sites in `tmp_path` via the `site` fixture in `conftest.py` rather than mocking the filesystem. Add to that fixture when a test needs new content.
- `:title` in a permalink is the **filename** slug (or front-matter `slug:`), not the slugified title — same as Jekyll. Tests assert on this.

## Validation

`validate.py` runs *after* a build, against the real output directory, so a link
is only called broken if the file genuinely is not there. It backs both the
front end's problems panel (`/_dolmen/api/problems`) and `dolmen doctor` — one
implementation, so the two can't drift.

Two rules when adding a check:

- **Every problem carries a `why`.** A checker that names a fault without
  explaining it teaches nothing, and these run in front of someone writing
  prose, not debugging a build. A test asserts every problem has one.
- **Prefer a miss to a false positive.** The panel is always on screen, and
  people stop reading a panel that cries wolf. Skip anything ambiguous —
  external links, relative URLs, templated attributes — rather than guessing.

A specific check's error suppresses the generic build warning for the same file,
so one fault is reported once.

## Known gaps

Tracked as issues; do not treat these as bugs to fix incidentally:

- **Reserved words can't follow a dot.** python-liquid's expression lexer reserves ~20 words, so `{{ include.cols }}` fails where Jekyll accepts it. Affected: `cols offset limit with as in for if else and or not true false nil empty blank contains reversed continue`. Workaround is `{{ include["cols"] }}`. `test_reserved_words_need_bracket_access` asserts this, so an upstream fix will surface as a failing test — that is intentional.
- Kramdown inline attribute lists (`{:class="cover"}`) are unsupported.
- Jekyll's own tags — `{% highlight %}`, `{% link %}`, `{% post_url %}`, `{% seo %}` — are not implemented. Add them as Tag subclasses next to `JekyllIncludeTag`.
- `jekyll-*` plugin names in `plugins:` are silently ignored so an unported config still builds.
