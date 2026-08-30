# dolmen

A static site generator written in Python, built to feel like Jekyll without needing Ruby — and with a **web front end for building the site**, so pages can be written in a browser with a live preview instead of a text editor and a terminal.

> **Status: early.** The build pipeline works end to end and is covered by tests. The web front end runs and can create, edit, save and upload — it is the current focus. See the open issues for what's next.

## Why

Jekyll is a good generator with a bad install story, and editing a site means hand-writing markdown and front matter in an editor while a terminal rebuilds in another window. `dolmen` keeps the parts of Jekyll worth keeping — the directory conventions, front matter, permalinks, collections, data files — and replaces the parts that get in the way.

The published site is still just static files. The front end exists only while you're building; it is never deployed.

## Install

```sh
git clone https://github.com/kevinmcaleer/dolmen
cd dolmen
uv venv && uv pip install -e ".[dev]"
```

## Use

```sh
dolmen new mysite          # scaffold a site
cd mysite
dolmen serve --open        # build, serve on :4000, rebuild on change
dolmen build               # one-off build into _site/
dolmen clean               # delete the output directory
dolmen doctor              # build and report every warning
```

With the server running, the build front end is at <http://127.0.0.1:4000/_dolmen/>.

## How a site is laid out

Jekyll's conventions, unchanged:

| Path | Meaning |
| --- | --- |
| `_config.yml` | Site config; every key is readable as `site.*` |
| `_layouts/` | Templates that wrap content |
| `_includes/` | Partials, called with parameters |
| `_data/` | YAML/JSON exposed as `site.data.<name>` |
| `_posts/` | Dated documents, `YYYY-MM-DD-title.md` |
| `_drafts/` | Undated posts, built only with `--drafts` |
| `_plugins/` | Site-local Python plugins, imported at build time |
| `_site/` | Build output (git-ignored) |
| anything else | A page if it has front matter, a static file if it doesn't |

Collections are declared exactly as in Jekyll:

```yaml
collections:
  projects:
    output: true
    permalink: /projects/:name/
```

## Templating: Jinja2, not Liquid

This is the one deliberate incompatibility, and the one that matters most when porting a site.

`dolmen` uses **Jinja2**. The variables (`site`, `page`, `content`) and the filter names (`relative_url`, `date_to_string`, `where`, `group_by`, `markdownify`, `slugify`, …) match Jekyll, so most expressions port unchanged:

```jinja
{{ page.title }}
{{ '/assets/css/main.css' | relative_url }}
{% for post in site.posts %}{{ post.title }}{% endfor %}
```

Three things differ:

**Filter arguments use parentheses**, not colons.

```liquid
{{ page.date | date: "%Y" }}     {# Jekyll #}
```
```jinja
{{ page.date | date('%Y') }}     {# dolmen #}
```

**Includes are called, and take keyword arguments.** Jinja2's `{% include %}` can't take parameters, so includes are a function; inside the include, arguments read off `include.*` as they do in Liquid, and the caller's `site` and `page` are still in scope.

```liquid
{% include card.html title="Hi" cols=3 %}          {# Jekyll #}
```
```jinja
{{ include('card.html', title='Hi', cols=3) }}     {# dolmen #}
```

**Layouts wrap, they don't inherit.** A layout receives the rendered document as `content` and needs no `{% block %}`. A layout with its own `layout:` in front matter nests inside that one, up the chain — same as Jekyll.

Liquid tags with no Jinja2 equivalent (`{% assign %}`, `{% capture %}`) become `{% set %}` and `{% set … %}…{% endset %}`.

## Wiki links

Any document can reference another by title, with no path:

```markdown
See [[Getting Started]] and [[Getting Started|the setup guide]].
```

Targets resolve against document titles, then slugs, then filenames. An unresolved link still renders, marked `.wikilink-broken`, so a typo is visible on the page rather than silent.

## Plugins

Drop a `.py` file in `_plugins/` and define any of the hooks:

```python
# _plugins/reading_time.py
def on_document_pre_render(site, document):
    words = len(document.body.split())
    document.metadata.setdefault("reading_time", max(1, round(words / 200)))

def filters():
    return {"shout": lambda value: str(value).upper()}
```

Hooks: `on_config`, `on_site_loaded`, `on_document_pre_render`, `on_document_rendered`, `on_post_build`, plus `filters()` and `markdown_extensions()`. Installed packages can advertise themselves in the `dolmen.plugins` entry-point group and be enabled via `plugins:` in the config.

## Markdown

CommonMark via `markdown-it-py`, plus tables, footnotes, definition lists, task lists, attributes and heading anchors. Fenced code is highlighted with Pygments **at build time**, so no client-side highlighter is needed.

Kramdown's inline attribute lists (`{:class="cover"}`) are not supported; the `attrs` plugin's `{.cover}` syntax is.

## Development

```sh
uv pip install -e ".[dev]"
pytest                                              # all tests
pytest tests/test_builder.py::test_wiki_links_resolve_by_title   # one test
ruff check .                                        # lint
```

## Name

A dolmen is a megalithic tomb: a few big flat stones, standing on their own for five thousand years with nothing holding them together. That is roughly the ambition for the output — plain files that keep working with no server, no database and no runtime.

PyPI already has a `dolmen` (a dormant namespace package from the cromlech/dolmen project), so the distribution would publish as **`dolmen-ssg`**. The import package and the command are both `dolmen`.

## Licence

MIT
