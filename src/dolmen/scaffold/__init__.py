"""`dolmen new` — write a minimal but complete site to disk.

The scaffold is deliberately small: one layout, one include, one data file, one
page and one post. It exists to show the conventions, not to be a theme.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..exceptions import StaticError

_CONFIG = """\
title: {title}
description: A site built with dolmen.
url: ""
baseurl: ""

# Where the built site is written.
destination: _site

# Posts land at /blog/<title>/ ; see the permalink docs for the placeholders.
permalink: /blog/:title/

collections:
  projects:
    output: true
    permalink: /projects/:name/

defaults:
  - scope:
      type: posts
    values:
      layout: post
  - scope:
      type: pages
    values:
      layout: page

exclude:
  - README.md
"""

_LAYOUT_DEFAULT = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% if page.title %}{{ page.title }} · {% endif %}{{ site.title }}</title>
  <meta name="description" content="{{ page.description | default(site.description) }}">
  <link rel="stylesheet" href="{{ '/assets/css/main.css' | relative_url }}">
</head>
<body>
  {{ include('header.html', title=site.title) }}
  <main class="wrap">
    {{ content }}
  </main>
  <footer class="wrap">
    <p>&copy; {{ site.time | date('%Y') }} {{ site.title }} · built with dolmen</p>
  </footer>
</body>
</html>
"""

_LAYOUT_PAGE = """\
---
layout: default
---
<article>
  {% if page.title %}<h1>{{ page.title }}</h1>{% endif %}
  {{ content }}
</article>
"""

_LAYOUT_POST = """\
---
layout: default
---
<article>
  <h1>{{ page.title }}</h1>
  <p class="meta">
    <time datetime="{{ page.date | date_to_xmlschema }}">{{ page.date | date_to_string }}</time>
    {% if page.tags %}· {{ page.tags | array_to_sentence_string }}{% endif %}
  </p>
  {{ content }}
</article>
"""

_INCLUDE_HEADER = """\
{# Parameters arrive as include.*, exactly as they do in Jekyll. #}
<header class="wrap site-header">
  <a class="site-title" href="{{ '/' | relative_url }}">{{ include.title }}</a>
  <nav>
    {% for item in site.data.navigation %}
      <a href="{{ item.link | relative_url }}">{{ item.name }}</a>
    {% endfor %}
  </nav>
</header>
"""

_DATA_NAV = """\
- name: Home
  link: /
- name: Blog
  link: /blog/
- name: About
  link: /about.html
"""

_INDEX = """\
---
title: Home
---

Welcome to **{title}**.

## Latest posts

{{% for post in site.posts %}}
- [{{{{ post.title }}}}]({{{{ post.url }}}}) — {{{{ post.date | date_to_string }}}}
{{% endfor %}}
"""

_ABOUT = """\
---
title: About
---

This site is built with [dolmen](https://github.com/kevinmcaleer/dolmen).

Link to other pages with wiki links: [[Home]].
"""

_POST = """\
---
title: "Hello, dolmen"
date: {date}
tags:
  - meta
---

This is your first post. It lives in `_posts/` and its filename sets the date.

```python
print("Markdown, Jinja2 templating, and Jekyll conventions.")
```

Wiki links resolve by title: [[About]].
"""

_CSS = """\
:root { --ink: #16181d; --paper: #fff; --muted: #5b6472; --accent: #2f6feb; }
@media (prefers-color-scheme: dark) {
  :root { --ink: #e7e9ee; --paper: #14161a; --muted: #98a1b0; --accent: #79a6ff; }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 46rem; margin: 0 auto; padding: 1.5rem; }
.site-header { display: flex; gap: 1.5rem; align-items: baseline; justify-content: space-between; }
.site-title { font-weight: 650; font-size: 1.15rem; text-decoration: none; color: var(--ink); }
nav a { margin-left: 1rem; color: var(--muted); text-decoration: none; }
nav a:hover { color: var(--accent); }
a { color: var(--accent); }
.meta { color: var(--muted); font-size: .9rem; }
pre { background: #0d1117; color: #e6edf3; padding: 1rem; border-radius: .5rem; overflow-x: auto; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }
pre code { background: none; padding: 0; }
.wikilink-broken { color: #c2410c; text-decoration: underline wavy; }
footer {
  color: var(--muted); font-size: .9rem; margin-top: 3rem;
  border-top: 1px solid color-mix(in srgb, var(--ink) 12%, transparent);
}
"""

_README = """\
# {title}

A site built with [dolmen](https://github.com/kevinmcaleer/dolmen).

```sh
dolmen serve --open   # build, serve on :4000, rebuild on change
dolmen build          # one-off build into _site/
```

Open <http://127.0.0.1:4000/_dolmen/> for the build front end.
"""

_GITIGNORE = "_site/\n.dolmen-cache/\n.DS_Store\n"


def create_site(path: Path, *, title: str | None = None, force: bool = False) -> Path:
    """Write a new site to `path`."""
    path = Path(path)
    if path.exists() and any(path.iterdir()) and not force:
        raise StaticError("directory is not empty (use --force to write anyway)", path)

    site_title = title or path.resolve().name.replace("-", " ").replace("_", " ").title()
    today = dt.date.today()

    files = {
        "_config.yml": _CONFIG.format(title=site_title),
        "_layouts/default.html": _LAYOUT_DEFAULT,
        "_layouts/page.html": _LAYOUT_PAGE,
        "_layouts/post.html": _LAYOUT_POST,
        "_includes/header.html": _INCLUDE_HEADER,
        "_data/navigation.yml": _DATA_NAV,
        "index.md": _INDEX.format(title=site_title),
        "about.md": _ABOUT,
        f"_posts/{today.isoformat()}-hello-dolmen.md": _POST.format(date=today.isoformat()),
        "assets/css/main.css": _CSS,
        "README.md": _README.format(title=site_title),
        ".gitignore": _GITIGNORE,
    }

    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return path
