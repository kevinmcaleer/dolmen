from __future__ import annotations

import pytest

from dolmen import frontmatter
from dolmen.exceptions import FrontMatterError


def test_splits_front_matter_from_body():
    doc = frontmatter.split("---\ntitle: Hi\n---\nBody text\n")
    assert doc.has_front_matter
    assert doc.metadata == {"title": "Hi"}
    assert doc.content == "Body text\n"


def test_file_without_front_matter_is_left_alone():
    doc = frontmatter.split("# Just markdown\n")
    assert not doc.has_front_matter
    assert doc.metadata == {}
    assert doc.content == "# Just markdown\n"


def test_empty_front_matter_block():
    doc = frontmatter.split("---\n---\nBody\n")
    assert doc.has_front_matter
    assert doc.metadata == {}


def test_fence_must_be_at_the_very_start():
    doc = frontmatter.split("\n---\ntitle: Hi\n---\nBody\n")
    assert not doc.has_front_matter


def test_triple_dash_inside_the_body_is_not_a_fence():
    doc = frontmatter.split("---\ntitle: Hi\n---\nBefore\n\n---\n\nAfter\n")
    assert doc.metadata == {"title": "Hi"}
    assert "Before" in doc.content and "After" in doc.content


def test_invalid_yaml_raises():
    with pytest.raises(FrontMatterError):
        frontmatter.split("---\ntitle: [unclosed\n---\nBody\n")


def test_non_mapping_front_matter_raises():
    with pytest.raises(FrontMatterError):
        frontmatter.split("---\n- a\n- b\n---\nBody\n")


def test_dump_round_trips_and_keeps_key_order():
    text = frontmatter.dump({"title": "Hi", "layout": "post"}, "\nBody\n")
    assert text.startswith("---\ntitle: Hi\nlayout: post\n---\n")
    assert frontmatter.split(text).metadata == {"title": "Hi", "layout": "post"}
