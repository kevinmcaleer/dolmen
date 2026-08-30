"""The Liquid environment and the Jekyll compatibility layer on top of it."""

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


# -- core Liquid still works -------------------------------------------------


def test_assign_and_output(templating):
    assert render(templating, "{% assign x = 'hi' %}{{ x }}") == "hi"


def test_capture(templating):
    assert render(templating, "{% capture y %}ab{% endcapture %}{{ y }}") == "ab"


def test_for_loop_with_forloop_object(templating):
    assert render(templating, "{% for i in (1..3) %}{{ forloop.index0 }}{% endfor %}") == "012"


def test_hyphenated_assign(templating):
    """kevsrobots' default.html does `{% assign bg-col = 'bg-dark' %}`."""
    assert render(templating, "{% assign bg-col = 'bg-dark' %}{{ bg-col }}") == "bg-dark"


def test_unknown_variable_renders_empty_not_an_error(templating):
    assert render(templating, "[{{ nope }}]") == "[]"


# -- Jekyll's URL filters ----------------------------------------------------


def test_relative_url_adds_the_baseurl(templating):
    assert render(templating, "{{ '/a.css' | relative_url }}") == "/sub/a.css"


def test_relative_url_leaves_absolute_urls_alone(templating):
    assert render(templating, "{{ 'https://x.com/a' | relative_url }}") == "https://x.com/a"


def test_absolute_url_prefixes_the_site_url(templating):
    assert render(templating, "{{ '/a' | absolute_url }}") == "https://example.com/sub/a"


# -- date filters ------------------------------------------------------------


def test_date_filter_uses_strftime(templating):
    assert render(templating, "{{ d | date: '%Y-%m' }}", d=dt.date(2026, 8, 5)) == "2026-08"


def test_date_to_string(templating):
    out = render(templating, "{{ d | date_to_string }}", d=dt.date(2026, 8, 5))
    assert out == "05 Aug 2026"


def test_date_to_xmlschema(templating):
    out = render(templating, "{{ d | date_to_xmlschema }}", d=dt.date(2026, 8, 5))
    assert out.startswith("2026-08-05T")


# -- collection filters ------------------------------------------------------


def test_where_filter_matches_a_key(templating):
    items = [{"n": 1, "kind": "a"}, {"n": 2, "kind": "b"}]
    out = render(templating, "{{ items | where: 'kind', 'b' | map: 'n' | join: ',' }}",
                 items=items)
    assert out == "2"


def test_where_exp_evaluates_a_liquid_expression(templating):
    items = [{"n": 1, "t": "a"}, {"n": 5, "t": "b"}]
    out = render(templating, "{{ items | where_exp: 'i', 'i.n > 2' | map: 't' | join: ',' }}",
                 items=items)
    assert out == "b"


def test_where_exp_supports_contains(templating):
    items = [{"tags": ["x"], "t": "a"}, {"tags": ["y"], "t": "b"}]
    out = render(
        templating,
        '{{ items | where_exp: "i", "i.tags contains \'x\'" | map: "t" | join: "," }}',
        items=items,
    )
    assert out == "a"


def test_group_by(templating):
    items = [{"k": "a"}, {"k": "a"}, {"k": "b"}]
    assert render(templating, "{{ items | group_by: 'k' | size }}", items=items) == "2"


def test_sort_by(templating):
    items = [{"n": 3}, {"n": 1}]
    assert render(templating, "{{ items | sort_by: 'n' | map: 'n' | join: ',' }}",
                  items=items) == "1,3"


def test_array_to_sentence_string(templating):
    out = render(templating, "{{ list | array_to_sentence_string }}", list=["a", "b", "c"])
    assert out == "a, b, and c"


# -- text filters ------------------------------------------------------------


def test_slugify(templating):
    assert render(templating, "{{ 'Hello, World!' | slugify }}") == "hello-world"


def test_markdownify(templating):
    assert "<strong>hi</strong>" in render(templating, "{{ '**hi**' | markdownify }}")


def test_number_of_words(templating):
    assert render(templating, "{{ 'a b c' | number_of_words }}") == "3"


def test_xml_escape(templating):
    assert render(templating, "{{ '<a>' | xml_escape }}") == "&lt;a&gt;"


def test_jsonify(templating):
    assert render(templating, "{{ d | jsonify }}", d={"a": 1}) == '{"a": 1}'


# -- Jekyll's include tag ----------------------------------------------------


def test_include_receives_parameters_as_include_dot(templating, tmp_path):
    (tmp_path / "_includes/card.html").write_text("[{{ include.title }}]", encoding="utf-8")
    assert render(templating, '{% include card.html title="Hi" %}') == "[Hi]"


def test_include_takes_several_parameters(templating, tmp_path):
    (tmp_path / "_includes/card.html").write_text(
        '[{{ include.title }}|{{ include["cols"] }}]', encoding="utf-8"
    )
    assert render(templating, '{% include card.html title="Hi" cols=3 %}') == "[Hi|3]"


def test_include_resolves_a_bare_word_as_a_variable(templating, tmp_path):
    (tmp_path / "_includes/card.html").write_text("[{{ include.title }}]", encoding="utf-8")
    out = render(templating, "{% include card.html title=post.title %}",
                 post={"title": "From a variable"})
    assert out == "[From a variable]"


def test_include_inherits_the_callers_context(templating, tmp_path):
    (tmp_path / "_includes/card.html").write_text("{{ site.title }}", encoding="utf-8")
    assert render(templating, "{% include card.html %}", site={"title": "Test"}) == "Test"


def test_include_without_parameters(templating, tmp_path):
    (tmp_path / "_includes/plain.html").write_text("PLAIN", encoding="utf-8")
    assert render(templating, "{% include plain.html %}") == "PLAIN"


def test_missing_include_is_reported(templating):
    with pytest.raises(RenderError, match="nope"):
        render(templating, "{% include nope.html %}")


# -- layouts -----------------------------------------------------------------


def test_layouts_wrap_rather_than_inherit(templating, tmp_path):
    layouts = tmp_path / "_layouts"
    (layouts / "inner.html").write_text(
        "---\nlayout: outer\n---\n<i>{{ content }}</i>", encoding="utf-8"
    )
    (layouts / "outer.html").write_text("<o>{{ content }}</o>", encoding="utf-8")
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


# -- the documented incompatibility ------------------------------------------


def test_reserved_words_need_bracket_access(templating, tmp_path):
    """python-liquid reserves `cols`, so Jekyll's `include.cols` will not parse.

    This is the one known Jekyll incompatibility; it is documented in the README
    and the migration guide, and asserted here so a future upstream fix is noticed.
    """
    (tmp_path / "_includes/g.html").write_text("{{ include.cols }}", encoding="utf-8")
    with pytest.raises(RenderError):
        render(templating, "{% include g.html cols=3 %}")

    (tmp_path / "_includes/g2.html").write_text('{{ include["cols"] }}', encoding="utf-8")
    assert render(templating, "{% include g2.html cols=3 %}") == "3"
