"""End-to-end build behaviour, exercised through the real file system."""

from __future__ import annotations

from pathlib import Path

import pytest

from dolmen.builder import Builder
from dolmen.exceptions import StaticError


def build(source: Path, **overrides):
    builder = Builder.from_source(source, overrides=overrides)
    return builder, builder.build()


def read(source: Path, relative: str) -> str:
    return (source / "_site" / relative).read_text(encoding="utf-8")


def test_builds_pages_posts_and_collections(site: Path):
    _, result = build(site)
    assert result.warnings == []
    assert (site / "_site/index.html").is_file()
    assert (site / "_site/about.html").is_file()
    assert (site / "_site/blog/2026/first-post/index.html").is_file()
    assert (site / "_site/projects/robot-arm/index.html").is_file()


def test_static_files_are_copied_verbatim(site: Path):
    build(site)
    assert read(site, "assets/css/main.css") == "body { margin: 0; }\n"
    # No front matter, so it is an asset rather than a page.
    assert read(site, "README.md").startswith("not front matter")


def test_build_inputs_are_never_published(site: Path):
    build(site)
    output = site / "_site"
    assert not (output / "_config.yml").exists()
    assert not (output / "_layouts").exists()
    assert not (output / "_includes").exists()
    assert not (output / "_data").exists()


def test_collection_with_output_false_is_not_written(site: Path):
    build(site)
    assert not (site / "_site/notes").exists()


def test_drafts_are_excluded_unless_asked_for(site: Path):
    builder, _ = build(site)
    assert all(d.title != "Unfinished" for d in builder.site.posts)
    assert not list((site / "_site").rglob("unfinished*"))

    builder, _ = build(site, show_drafts=True)
    draft = next(d for d in builder.site.documents if d.title == "Unfinished")
    # Undated drafts fall back to the file mtime, so the permalink has a year.
    assert draft.url.endswith("/unfinished/")
    assert (site / "_site" / draft.output_path).is_file()


def test_defaults_supply_the_layout(site: Path):
    build(site)
    html = read(site, "blog/2026/first-post/index.html")
    # post -> default, so the nav from the outer layout must be present.
    assert "<article" in html
    assert '<nav data-label="Menu">' in html


def test_layouts_nest_upwards(site: Path):
    build(site)
    html = read(site, "blog/2026/first-post/index.html")
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>First Post</title>" in html


def test_includes_receive_parameters_and_the_callers_context(site: Path):
    build(site)
    html = read(site, "index.html")
    assert 'data-label="Menu"' in html
    # site.data reached the include, not just the page.
    assert '<a href="/">Home</a>' in html


def test_data_directory_nests_by_folder(site: Path):
    builder, _ = build(site)
    assert builder.site.data["navigation"][0]["name"] == "Home"
    assert builder.site.data["authors"]["kev"]["name"] == "Kevin"


def test_posts_are_newest_first(site: Path):
    builder, _ = build(site)
    assert [p.title for p in builder.site.posts] == ["Second Post", "First Post"]


def test_tags_are_indexed_across_posts(site: Path):
    builder, _ = build(site)
    assert sorted(builder.site.tags) == ["alpha", "beta"]
    assert len(builder.site.tags["beta"]) == 2


def test_wiki_links_resolve_by_title(site: Path):
    build(site)
    assert '<a href="/about.html" class="wikilink">About</a>' in read(site, "index.html")


def test_unresolved_wiki_link_is_marked_broken(site: Path):
    (site / "index.md").write_text(
        "---\ntitle: Home\nlayout: default\n---\n\nSee [[No Such Page]].\n", encoding="utf-8"
    )
    build(site)
    assert "wikilink-broken" in read(site, "index.html")


def test_code_is_highlighted_once_at_build_time(site: Path):
    build(site)
    html = read(site, "blog/2026/second-post/index.html")
    assert '<pre class="highlight"><code class="language-python">' in html
    # Regression: markdown-it used to wrap the highlighter's output a second time.
    assert "<pre><code" not in html


def test_output_directory_is_emptied_between_builds(site: Path):
    build(site)
    stale = site / "_site/stale.html"
    stale.write_text("old", encoding="utf-8")
    build(site)
    assert not stale.exists()


def test_bad_front_matter_warns_but_does_not_stop_the_build(site: Path):
    (site / "broken.md").write_text("---\ntitle: [oops\n---\nBody\n", encoding="utf-8")
    _, result = build(site)
    assert any("broken.md" in w for w in result.warnings)
    assert (site / "_site/index.html").is_file()


def test_strict_mode_turns_that_warning_into_an_error(site: Path):
    (site / "broken.md").write_text("---\ntitle: [oops\n---\nBody\n", encoding="utf-8")
    builder = Builder.from_source(site, strict=True)
    with pytest.raises(StaticError):
        builder.build()


def test_missing_layout_is_reported_with_the_document_path(site: Path):
    (site / "orphan.md").write_text(
        "---\ntitle: Orphan\nlayout: nope\n---\nBody\n", encoding="utf-8"
    )
    _, result = build(site)
    assert any("nope" in w and "orphan.md" in w for w in result.warnings)


def test_baseurl_prefixes_urls_and_relative_url(site: Path):
    build(site, baseurl="/sub")
    html = read(site, "index.html")
    assert '<a href="/sub/">Home</a>' in html


def test_building_a_directory_that_is_not_a_site_is_refused(tmp_path: Path):
    """Regression: `dolmen build` in the wrong folder built the whole tree.

    Every file in the directory — `.venv`, source code, dotfiles — was copied
    into `_site`. A config is optional, but *some* evidence is not.
    """
    (tmp_path / "notes.txt").write_text("not a site", encoding="utf-8")
    (tmp_path / "README.md").write_text("# A code repo\n", encoding="utf-8")
    with pytest.raises(StaticError, match="does not look like a dolmen site"):
        Builder.from_source(tmp_path)
    assert not (tmp_path / "_site").exists()


def test_no_config_is_fine_when_the_directory_looks_like_a_site(tmp_path: Path):
    """`_config.yml` is optional — every setting has a default."""
    posts = tmp_path / "_posts"
    posts.mkdir()
    (posts / "2026-01-01-hello.md").write_text(
        "---\ntitle: Hello\n---\nBody\n", encoding="utf-8"
    )
    result = Builder.from_source(tmp_path).build()
    assert result.documents == 1
    assert (tmp_path / "_site/2026/01/01/hello.html").is_file()


def test_a_single_file_with_front_matter_is_a_site(tmp_path: Path):
    """The smallest real site there is."""
    (tmp_path / "index.md").write_text("---\ntitle: Tiny\n---\nJust me.\n", encoding="utf-8")
    Builder.from_source(tmp_path).build()
    assert "Just me." in (tmp_path / "_site/index.html").read_text(encoding="utf-8")


def test_a_nested_site_does_not_make_its_parent_look_like_one(tmp_path: Path):
    """Regression: a repo with a demo site inside it passed the check.

    `rglob` found front matter in the nested site and concluded the repo root
    was itself a site — so building the repo copied its whole tree.
    """
    (tmp_path / "README.md").write_text("# A code repo\n", encoding="utf-8")
    demo = tmp_path / "sandbox" / "demo"
    demo.mkdir(parents=True)
    (demo / "_config.yml").write_text("title: Demo\n", encoding="utf-8")
    (demo / "index.md").write_text("---\ntitle: Home\n---\nHi\n", encoding="utf-8")

    with pytest.raises(StaticError, match="does not look like a dolmen site"):
        Builder.from_source(tmp_path)


def test_an_empty_config_is_still_a_site(tmp_path: Path):
    """The escape hatch the error message promises has to actually work."""
    (tmp_path / "_config.yml").write_text("", encoding="utf-8")
    (tmp_path / "index.md").write_text("---\ntitle: Home\n---\nHi\n", encoding="utf-8")
    Builder.from_source(tmp_path).build()
    assert (tmp_path / "_site/index.html").is_file()


def test_a_nested_site_is_not_swallowed(site: Path):
    """A subdirectory with its own _config.yml is a separate site."""
    nested = site / "sandbox" / "other"
    nested.mkdir(parents=True)
    (nested / "_config.yml").write_text("title: Other\n", encoding="utf-8")
    (nested / "index.md").write_text("---\ntitle: Other Home\n---\nBody\n", encoding="utf-8")
    (nested / "_layouts").mkdir()
    (nested / "_layouts/thing.html").write_text("<p>{{ content }}</p>", encoding="utf-8")

    builder, _ = build(site)

    assert all("sandbox" not in str(d.relative_path) for d in builder.site.documents)
    assert not (site / "_site/sandbox").exists()
