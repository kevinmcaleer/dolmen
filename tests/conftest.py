"""Shared fixtures: a tiny site on disk that tests can mutate freely."""

from __future__ import annotations

from pathlib import Path

import pytest

CONFIG = """\
title: Test Site
description: A site for tests.
url: "https://example.com"
baseurl: ""
permalink: /blog/:year/:title/

collections:
  projects:
    output: true
    permalink: /projects/:name/
  notes:
    output: false

defaults:
  - scope:
      type: posts
    values:
      layout: post
"""

LAYOUT_DEFAULT = """\
<!DOCTYPE html>
<html><head><title>{{ page.title }}</title></head>
<body>{% include nav.html label="Menu" %}<main>{{ content }}</main></body></html>
"""

LAYOUT_POST = """\
---
layout: default
---
<article data-date="{{ page.date | date: '%Y-%m-%d' }}">{{ content }}</article>
"""

INCLUDE_NAV = """\
<nav data-label="{{ include.label }}">
{% for item in site.data.navigation %}<a href="{{ item.link | relative_url }}">{{ item.name }}</a>
{% endfor %}</nav>
"""


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """A minimal site covering pages, posts, a collection, data and includes."""
    files = {
        "_config.yml": CONFIG,
        "_layouts/default.html": LAYOUT_DEFAULT,
        "_layouts/post.html": LAYOUT_POST,
        "_includes/nav.html": INCLUDE_NAV,
        "_data/navigation.yml": "- name: Home\n  link: /\n",
        "_data/authors/kev.yml": "name: Kevin\n",
        "index.md": "---\ntitle: Home\nlayout: default\n---\n\nWelcome to [[About]].\n",
        "about.md": "---\ntitle: About\nlayout: default\n---\n\nAll about it.\n",
        "_posts/2026-01-15-first-post.md": (
            "---\ntitle: First Post\ntags:\n  - alpha\n  - beta\n---\n\nBody of the first post.\n"
        ),
        "_posts/2026-03-02-second-post.md": (
            "---\ntitle: Second Post\ntags:\n  - beta\n---\n\n```python\nx = 1\n```\n"
        ),
        "_drafts/unfinished.md": "---\ntitle: Unfinished\n---\n\nNot ready.\n",
        "_projects/robot-arm.md": "---\ntitle: Robot Arm\n---\n\nA project.\n",
        "_notes/private.md": "---\ntitle: Private\n---\n\nHidden.\n",
        "assets/css/main.css": "body { margin: 0; }\n",
        "assets/img/logo.png": "",
        "README.md": "not front matter, so this is copied verbatim\n",
    }
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path
