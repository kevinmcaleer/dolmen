"""Site-local plugins in `_plugins/`."""

from __future__ import annotations

from pathlib import Path

import pytest

from dolmen.builder import Builder
from dolmen.exceptions import PluginError


def write_plugin(site: Path, name: str, source: str) -> None:
    directory = site / "_plugins"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(source, encoding="utf-8")


def test_pre_render_hook_can_add_front_matter(site: Path):
    write_plugin(site, "reading_time.py", """
def on_document_pre_render(site, document):
    document.metadata["reading_time"] = max(1, round(len(document.body.split()) / 200))
""")
    (site / "index.md").write_text(
        "---\ntitle: Home\nlayout: default\n---\n{{ page.reading_time }} min\n", encoding="utf-8"
    )
    Builder.from_source(site).build()
    assert "1 min" in (site / "_site/index.html").read_text(encoding="utf-8")


def test_plugin_can_register_a_filter(site: Path):
    write_plugin(site, "shout.py", """
def filters():
    return {"shout": lambda value: str(value).upper()}
""")
    (site / "index.md").write_text(
        "---\ntitle: Home\nlayout: default\n---\n{{ 'quiet' | shout }}\n", encoding="utf-8"
    )
    Builder.from_source(site).build()
    assert "QUIET" in (site / "_site/index.html").read_text(encoding="utf-8")


def test_rendered_hook_can_rewrite_the_html(site: Path):
    write_plugin(site, "stamp.py", """
def on_document_rendered(site, document, html):
    return html.replace("</body>", "<!-- stamped --></body>")
""")
    Builder.from_source(site).build()
    assert "<!-- stamped -->" in (site / "_site/index.html").read_text(encoding="utf-8")


def test_post_build_hook_runs_with_the_output_directory(site: Path):
    write_plugin(site, "manifest.py", """
def on_post_build(site, output_dir):
    (output_dir / "manifest.txt").write_text(str(len(site.documents)))
""")
    Builder.from_source(site).build()
    assert (site / "_site/manifest.txt").is_file()


def test_a_raising_plugin_names_itself(site: Path):
    write_plugin(site, "broken.py", """
def on_site_loaded(site):
    raise ValueError("deliberate")
""")
    with pytest.raises(PluginError, match="deliberate"):
        Builder.from_source(site).build()


def test_unported_jekyll_plugin_names_are_ignored(site: Path):
    config = (site / "_config.yml").read_text(encoding="utf-8")
    (site / "_config.yml").write_text(
        config + "\nplugins:\n  - jekyll-feed\n  - jekyll-seo-tag\n", encoding="utf-8"
    )
    result = Builder.from_source(site).build()
    assert result.documents > 0


def test_an_unknown_static_plugin_is_an_error(site: Path):
    config = (site / "_config.yml").read_text(encoding="utf-8")
    (site / "_config.yml").write_text(config + "\nplugins:\n  - nope\n", encoding="utf-8")
    with pytest.raises(PluginError, match="nope"):
        Builder.from_source(site).build()
