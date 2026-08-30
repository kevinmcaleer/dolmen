"""Structural facts about a site: template usage, include parameters, data shape."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dolmen.builder import Builder
from dolmen.scaffold import create_site
from dolmen.structure import data_files, dump_sequence, includes, layouts, read_sequence


@pytest.fixture
def built(tmp_path: Path):
    root = tmp_path / "site"
    create_site(root, title="Test")
    builder = Builder.from_source(root)
    builder.build()
    return builder, root


def by_name(uses):
    return {use.name: use for use in uses}


# -- layouts -----------------------------------------------------------------


def test_layouts_list_the_documents_that_use_them(built):
    builder, root = built
    found = by_name(layouts(builder.site, builder.config))
    assert "post" in found and "page" in found and "default" in found
    assert any("_posts/" in path for path in found["post"].used_by)


def test_a_layouts_own_layout_counts_as_a_use(built):
    builder, root = built
    found = by_name(layouts(builder.site, builder.config))
    # page and post both declare `layout: default`.
    assert "_layouts/page.html" in found["default"].used_by
    assert "_layouts/post.html" in found["default"].used_by


def test_an_unused_layout_reports_zero(built):
    builder, root = built
    (root / "_layouts/orphan.html").write_text("{{ content }}", encoding="utf-8")
    builder = Builder.from_source(root)
    builder.build()
    found = by_name(layouts(builder.site, builder.config))
    assert found["orphan"].used_by == []


def test_layout_front_matter_is_exposed(built):
    builder, root = built
    found = by_name(layouts(builder.site, builder.config))
    assert found["page"].metadata.get("layout") == "default"


# -- includes ----------------------------------------------------------------


def test_includes_report_who_calls_them(built):
    builder, root = built
    found = by_name(includes(builder.site, builder.config))
    assert "header.html" in found
    assert "_layouts/default.html" in found["header.html"].used_by


def test_include_parameters_are_the_names_it_reads(built):
    builder, root = built
    (root / "_includes/card.html").write_text(
        '{{ include.title }} {{ include["cols"] }} {{ include.link }}', encoding="utf-8"
    )
    builder = Builder.from_source(root)
    builder.build()
    found = by_name(includes(builder.site, builder.config))
    assert found["card.html"].parameters == ["cols", "link", "title"]


def test_includes_called_from_a_document_are_counted(built):
    builder, root = built
    (root / "uses.md").write_text(
        "---\ntitle: Uses\n---\n\n{% include header.html title='x' %}\n", encoding="utf-8"
    )
    builder = Builder.from_source(root)
    builder.build()
    found = by_name(includes(builder.site, builder.config))
    assert "uses.md" in found["header.html"].used_by


# -- data files --------------------------------------------------------------


def test_data_files_are_classified_by_shape(built):
    builder, root = built
    (root / "_data/settings.yml").write_text("theme: dark\n", encoding="utf-8")
    found = {f.name: f for f in data_files(builder.config)}
    assert found["navigation.yml"].shape == "sequence"
    assert found["settings.yml"].shape == "mapping"


def test_sequence_columns_are_in_first_seen_order(built):
    builder, root = built
    (root / "_data/nav2.yml").write_text(
        "- name: A\n  link: /a\n- link: /b\n  name: B\n  icon: star\n", encoding="utf-8"
    )
    found = {f.name: f for f in data_files(builder.config)}
    assert found["nav2.yml"].columns == ["name", "link", "icon"]


# -- minimal diffs -----------------------------------------------------------


def test_dump_sequence_keeps_key_order(tmp_path: Path):
    """Acceptance criterion: reordering the nav must not reflow the file."""
    rows = [{"name": "Home", "link": "/"}, {"name": "Blog", "link": "/blog/"}]
    text = dump_sequence(rows)
    assert text.index("name: Home") < text.index("link: /")
    assert "{" not in text, "block style keeps the file diffable"


def test_reordering_changes_only_the_order(tmp_path: Path):
    path = tmp_path / "navigation.yml"
    original = (
        "- name: Home\n  link: /\n"
        "- name: Blog\n  link: /blog/\n"
        "- name: About\n  link: /about\n"
    )
    path.write_text(original, encoding="utf-8")

    rows = read_sequence(path)
    rows.insert(0, rows.pop(2))                    # move About to the top
    path.write_text(dump_sequence(rows), encoding="utf-8")

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == [
        {"name": "About", "link": "/about"},
        {"name": "Home", "link": "/"},
        {"name": "Blog", "link": "/blog/"},
    ]
    # Same lines, different order — no reflow, no requoting.
    assert sorted(path.read_text(encoding="utf-8").splitlines()) == sorted(
        original.splitlines()
    )


def test_read_sequence_rejects_a_mapping(tmp_path: Path):
    path = tmp_path / "settings.yml"
    path.write_text("theme: dark\n", encoding="utf-8")
    assert read_sequence(path) is None


def test_read_sequence_survives_broken_yaml(tmp_path: Path):
    path = tmp_path / "bad.yml"
    path.write_text("- [oops\n", encoding="utf-8")
    assert read_sequence(path) is None
