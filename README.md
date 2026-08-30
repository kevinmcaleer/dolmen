# dolmen

A static site generator written in Python that runs **Jekyll's own Liquid templates** without needing Ruby — and adds a **web front end for building the site**, so pages can be written in a browser with a live preview instead of a text editor and a terminal.

> **Status: early.** The build pipeline works end to end and is covered by tests. The web front end runs and can create, edit, save and upload — it is the current focus. See the open issues for what's next.

## Why

Jekyll is a good generator with a bad install story, and editing a site means hand-writing markdown and front matter in an editor while a terminal rebuilds in another window. `dolmen` keeps the parts of Jekyll worth keeping — the directory conventions, front matter, permalinks, collections, data files — and replaces the parts that get in the way.

The published site is still just static files. The front end exists only while you're building; it is never deployed.

## Install

```sh
git clone https://github.com/kevinmcaleer/dolmen
cd dolmen
uv venv                        # creates .venv
source .venv/bin/activate      # fish: .venv/bin/activate.fish
uv pip install -e ".[dev]"
```

`-e` is an editable install: the `dolmen` command runs the code in `src/`, so edits take effect immediately with no reinstall.

Check it worked:

```sh
dolmen --version
```

## Try it out

`sandbox/` is git-ignored, so anything you build there stays local:

```sh
dolmen new sandbox/mysite
cd sandbox/mysite
dolmen serve --open
```

That builds the site, serves it at <http://127.0.0.1:4000/>, opens a browser, and rebuilds whenever you save. The build front end is at <http://127.0.0.1:4000/_dolmen/>.

Edit `index.md` or `_posts/*.md` in either the front end or your editor — both trigger a rebuild and reload the page.

## Commands

```sh
dolmen new PATH            # scaffold a site
dolmen serve               # build, serve on :4000, rebuild on change
dolmen build               # one-off build into _site/
dolmen clean               # delete the output directory
dolmen doctor              # build and report every warning
```

Every command except `new` takes `--source` so you can stay at the repo root:

```sh
dolmen serve --source sandbox/mysite
```

Useful flags: `--port/-p`, `--drafts` (include `_drafts/`), `--strict` (warnings become errors), `--no-admin`, `--no-reload`.

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

## Templating: Liquid, same as Jekyll

dolmen renders with **Liquid** — the same template language Jekyll uses — via [`python-liquid`](https://github.com/jg-rp/liquid). Your existing templates run unmodified:

```liquid
{% assign recent = site.posts %}
{{ page.title }}
{{ '/assets/css/main.css' | relative_url }}
{% for post in recent %}{% include card.html title=post.title %}{% endfor %}
```

`assign`, `capture`, `unless`, `case`, `for`/`forloop`, `if`/`elsif`, `comment` — all present. So are Jekyll's filters: `relative_url`, `absolute_url`, `where`, `where_exp`, `group_by`, `sort_by`, `markdownify`, `slugify`, `jsonify`, `number_of_words`, `array_to_sentence_string`, `xml_escape`, `uri_escape`, `date_to_string`, `date_to_xmlschema` and the rest.

**Layouts wrap, they don't inherit.** A layout receives the rendered document as `content`. A layout with its own `layout:` in front matter nests inside that one, up the chain — same as Jekyll.

### The one known incompatibility

python-liquid's expression lexer reserves about twenty words, so a reserved word can't follow a dot:

```liquid
{{ include.cols }}      {# fails: "expected an identifier, found cols" #}
{{ include["cols"] }}   {# works #}
```

Affected: `cols offset limit with as in for if else and or not true false nil empty blank contains reversed continue`. Jekyll accepts all of these, so a migrated site may need a handful of rewrites — on kevsrobots.com, nine across ~1,900 Liquid tags.

Not yet implemented: Jekyll's own `{% highlight %}`, `{% link %}`, `{% post_url %}` and `{% seo %}` tags, and kramdown inline attribute lists (`{:class="cover"}`).

## Checking a site

`dolmen doctor` reports every problem it can find, and the front end shows the
same list in a panel from the bottom of the window:

- broken internal links and missing images
- unresolved `[[wiki links]]`
- layouts and includes that do not exist
- missing front matter (`title`, and `date` on posts)
- code fences tagged with a language nothing can highlight

Each finding says what is wrong, where, and why it matters. `doctor` exits
non-zero on errors, or on warnings too with `--strict` — useful in CI.

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
pytest --cov                                        # tests, with the 80% coverage gate
pytest tests/test_builder.py::test_wiki_links_resolve_by_title   # one test
ruff check .                                        # lint
```

New features ship with tests; coverage must stay at or above 80% and CI enforces it.

## Name

A dolmen is a megalithic tomb: a few big flat stones, standing on their own for five thousand years with nothing holding them together. That is roughly the ambition for the output — plain files that keep working with no server, no database and no runtime.

PyPI already has a `dolmen` (a dormant namespace package from the cromlech/dolmen project), so the distribution would publish as **`dolmen-ssg`**. The import package and the command are both `dolmen`.

## Licence

MIT
