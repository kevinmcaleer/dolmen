"""Wiki-link resolution, backlinks, headings and collisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from dolmen.builder import Builder
from dolmen.links import anchor_for, build_index, heading_ids, split_target
from dolmen.scaffold import create_site


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    create_site(root, title="Wiki")
    (root / "guide.md").write_text(
        "---\ntitle: Getting Started\n---\n\n## Installing\n\nA\n\n## Configuring\n\nB\n",
        encoding="utf-8",
    )
    (root / "uses.md").write_text(
        "---\ntitle: Uses\n---\n\nSee [[Getting Started]] and [[Getting Started|the guide]].\n",
        encoding="utf-8",
    )
    return root


def indexed(root: Path):
    builder = Builder.from_source(root)
    builder.build()
    return builder, build_index(builder.site)


def test_split_target():
    assert split_target("Page") == ("Page", None)
    assert split_target("Page#Section") == ("Page", "Section")
    assert split_target("  Page  #  Section  ") == ("Page", "Section")


def test_resolves_by_title(wiki: Path):
    builder, _ = indexed(wiki)
    html = (wiki / "_site/uses.html").read_text(encoding="utf-8")
    assert '<a href="/guide.html" class="wikilink">Getting Started</a>' in html


def test_label_overrides_the_display_text(wiki: Path):
    indexed(wiki)
    html = (wiki / "_site/uses.html").read_text(encoding="utf-8")
    assert '>the guide</a>' in html


def test_resolves_by_slug_and_filename(wiki: Path):
    (wiki / "uses.md").write_text(
        "---\ntitle: Uses\n---\n\n[[getting-started]] and [[guide]]\n", encoding="utf-8"
    )
    _, index = indexed(wiki)
    links = index.outgoing["uses.md"]
    assert all(not link.is_broken for link in links), [link.target for link in links]


def test_heading_target_resolves_to_an_anchor(wiki: Path):
    (wiki / "uses.md").write_text(
        "---\ntitle: Uses\n---\n\n[[Getting Started#Configuring]]\n", encoding="utf-8"
    )
    indexed(wiki)
    html = (wiki / "_site/uses.html").read_text(encoding="utf-8")
    assert 'href="/guide.html#configuring"' in html


def test_heading_anchors_use_the_same_slugify_as_links():
    from dolmen.markdown import MarkdownRenderer

    html = MarkdownRenderer().render("## v1.2 Notes\n")
    assert anchor_for("v1.2 Notes") in heading_ids(html)


def test_backlinks_point_the_other_way(wiki: Path):
    builder, index = indexed(wiki)
    guide = next(d for d in builder.site.documents if d.title == "Getting Started")
    assert [d.title for d in index.backlinks(guide)] == ["Uses"]


def test_backlinks_are_deduplicated(wiki: Path):
    (wiki / "uses.md").write_text(
        "---\ntitle: Uses\n---\n\n[[Getting Started]] twice: [[Getting Started]]\n",
        encoding="utf-8",
    )
    builder, index = indexed(wiki)
    guide = next(d for d in builder.site.documents if d.title == "Getting Started")
    assert len(index.backlinks(guide)) == 1


def test_backlinks_span_posts_and_collections(wiki: Path):
    (wiki / "_posts/2026-01-01-p.md").write_text(
        "---\ntitle: A Post\n---\n\n[[Getting Started]]\n", encoding="utf-8"
    )
    projects = wiki / "_projects"
    projects.mkdir()
    (projects / "thing.md").write_text(
        "---\ntitle: A Project\n---\n\n[[Getting Started]]\n", encoding="utf-8"
    )
    builder, index = indexed(wiki)
    guide = next(d for d in builder.site.documents if d.title == "Getting Started")
    assert {d.title for d in index.backlinks(guide)} == {"Uses", "A Post", "A Project"}


def test_backlinks_are_exposed_to_templates(wiki: Path):
    (wiki / "_layouts/page.html").write_text(
        "---\nlayout: default\n---\n{{ content }}\n"
        "{% for b in page.backlinks %}<i>{{ b.title }}</i>{% endfor %}",
        encoding="utf-8",
    )
    indexed(wiki)
    assert "<i>Uses</i>" in (wiki / "_site/guide.html").read_text(encoding="utf-8")


def test_broken_links_carry_their_line_number(wiki: Path):
    (wiki / "uses.md").write_text(
        "---\ntitle: Uses\n---\n\nline one\n\n[[Totally Missing]]\n", encoding="utf-8"
    )
    _, index = indexed(wiki)
    broken = index.broken()
    assert len(broken) == 1
    document, link = broken[0]
    assert document.title == "Uses"
    assert link.line == 7, "line must be in the file, front matter included"


def test_ambiguous_titles_resolve_stably_and_are_recorded(wiki: Path):
    (wiki / "one.md").write_text("---\ntitle: Twin\n---\nA\n", encoding="utf-8")
    (wiki / "two.md").write_text("---\ntitle: Twin\n---\nB\n", encoding="utf-8")
    (wiki / "uses.md").write_text(
        "---\ntitle: Uses\n---\n\n[[Twin]]\n", encoding="utf-8"
    )
    _, index = indexed(wiki)
    assert "twin" in index.ambiguous
    assert len(index.ambiguous["twin"]) == 2
    # Resolution is by collection then path, so it is arbitrary but repeatable.
    link = index.outgoing["uses.md"][0]
    assert link.resolved is not None
    assert str(link.resolved.relative_path) == "one.md"


def test_an_unused_duplicate_title_is_not_reported(wiki: Path):
    (wiki / "one.md").write_text("---\ntitle: Twin\n---\nA\n", encoding="utf-8")
    (wiki / "two.md").write_text("---\ntitle: Twin\n---\nB\n", encoding="utf-8")
    _, index = indexed(wiki)
    assert index.ambiguous
    assert not index.incoming_for_title("Twin")


def test_link_lines_are_file_lines_not_body_lines(wiki: Path):
    """Regression: the panel jumped to the wrong line by the height of the
    front matter, because the index counted lines from the body."""
    (wiki / "uses.md").write_text(
        "---\ntitle: Uses\nlayout: page\n---\n\n[[Totally Missing]]\n", encoding="utf-8"
    )
    _, index = indexed(wiki)
    _, link = index.broken()[0]

    lines = (wiki / "uses.md").read_text(encoding="utf-8").splitlines()
    assert "[[Totally Missing]]" in lines[link.line - 1]
