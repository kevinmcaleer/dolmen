"""The Jekyll-compatible filters and the layout/include model."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from dolmen.exceptions import RenderError
from dolmen.markdown import MarkdownRenderer
from dolmen.templating import Templating


@pytest.fixture
def templating(tmp_path: Path) -> Templating:
    (tmp_path / "_layouts").mkdir()
    (tmp_path / "_includes").mkdir()
    return Templating(
        tmp_path,
        markdown=MarkdownRenderer(),
        baseurl="/sub",
        url="https://example.com",
    )


def render(templating: Templating, source: str, **context) -> str:
    return templating.render_string(source, context)


def test_relative_url_adds_the_baseurl(templating):
    assert render(templating, "{{ '/a.css' | relative_url }}") == "/sub/a.css"


def test_relative_url_leaves_absolute_urls_alone(templating):
    assert render(templating, "{{ 'https://x.com/a' | relative_url }}") == "https://x.com/a"


def test_absolute_url_prefixes_the_site_url(templating):
    assert render(templating, "{{ '/a' | absolute_url }}") == "https://example.com/sub/a"


def test_date_filter_uses_strftime(templating):
    out = render(templating, "{{ d | date('%Y-%m') }}", d=dt.date(2026, 8, 5))
    assert out == "2026-08"


def test_date_filter_accepts_an_iso_string(templating):
    assert render(templating, "{{ '2026-08-05' | date('%d %b %Y') }}") == "05 Aug 2026"


def test_where_filter_matches_a_key(templating):
    items = [{"n": 1, "kind": "a"}, {"n": 2, "kind": "b"}]
    assert render(templating, "{{ items | where('kind', 'b') | map(attribute='n') | list }}",
                  items=items) == "[2]"


def test_where_matches_inside_a_list_value(templating):
    items = [{"tags": ["x", "y"]}, {"tags": ["z"]}]
    assert render(templating, "{{ (items | where('tags', 'x')) | length }}", items=items) == "1"


def test_where_exp_evaluates_an_expression(templating):
    items = [{"n": 1}, {"n": 5}]
    out = render(templating, "{{ (items | where_exp('i', 'i.n > 2')) | length }}", items=items)
    assert out == "1"


def test_group_by(templating):
    items = [{"k": "a"}, {"k": "a"}, {"k": "b"}]
    out = render(templating, "{{ (items | group_by('k')) | length }}", items=items)
    assert out == "2"


def test_array_to_sentence_string(templating):
    assert render(templating, "{{ ['a','b','c'] | array_to_sentence_string }}") == "a, b, and c"


def test_slugify_and_strip_html(templating):
    assert render(templating, "{{ 'Hello, World!' | slugify }}") == "hello-world"
    assert render(templating, "{{ '<b>hi</b>' | strip_html }}") == "hi"


def test_markdownify(templating):
    assert "<strong>hi</strong>" in render(templating, "{{ '**hi**' | markdownify }}")


def test_truncatewords(templating):
    assert render(templating, "{{ 'a b c d' | truncatewords(2) }}") == "a b..."


def test_include_receives_parameters_as_include_dot(templating, tmp_path):
    (tmp_path / "_includes/card.html").write_text("[{{ include.title }}]", encoding="utf-8")
    assert render(templating, "{{ include('card.html', title='Hi') }}") == "[Hi]"


def test_include_inherits_the_callers_context(templating, tmp_path):
    (tmp_path / "_includes/card.html").write_text("{{ site.title }}", encoding="utf-8")
    out = render(templating, "{{ include('card.html') }}", site={"title": "Test"})
    assert out == "Test"


def test_missing_include_is_reported(templating):
    with pytest.raises(RenderError, match="nope.html"):
        render(templating, "{{ include('nope.html') }}")


def test_layouts_wrap_rather_than_inherit(templating, tmp_path):
    (tmp_path / "_layouts/inner.html").write_text(
        "---\nlayout: outer\n---\n<i>{{ content }}</i>", encoding="utf-8"
    )
    (tmp_path / "_layouts/outer.html").write_text("<o>{{ content }}</o>", encoding="utf-8")
    assert templating.render_layout("inner", "BODY", {}) == "<o><i>BODY</i></o>"


def test_layout_loop_is_detected(templating, tmp_path):
    layouts = tmp_path / "_layouts"
    (layouts / "a.html").write_text("---\nlayout: b\n---\n{{ content }}", encoding="utf-8")
    (layouts / "b.html").write_text("---\nlayout: a\n---\n{{ content }}", encoding="utf-8")
    with pytest.raises(RenderError, match="layout loop"):
        templating.render_layout("a", "BODY", {})


def test_missing_layout_names_the_layout(templating):
    with pytest.raises(RenderError, match="nope"):
        templating.render_layout("nope", "BODY", {})
