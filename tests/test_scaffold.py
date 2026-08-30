"""`dolmen new` — the site it writes must build cleanly, including under --strict.

This is the example every user copies first, so a template idiom that only works
in lenient mode is a bug in the scaffold, not in strict mode.
"""

from __future__ import annotations

from pathlib import Path

from dolmen.builder import Builder
from dolmen.scaffold import create_site


def test_scaffolded_site_builds(tmp_path: Path):
    create_site(tmp_path / "site", title="Test")
    result = Builder.from_source(tmp_path / "site").build()
    assert result.warnings == []
    assert (tmp_path / "site/_site/index.html").is_file()
    assert (tmp_path / "site/_site/about.html").is_file()


def test_scaffolded_site_builds_under_strict(tmp_path: Path):
    """Regression: `page.description or site.description` raised on StrictUndefined."""
    create_site(tmp_path / "site", title="Test")
    result = Builder.from_source(tmp_path / "site", strict=True).build()
    assert result.warnings == []


def test_scaffold_refuses_a_non_empty_directory(tmp_path: Path):
    import pytest

    from dolmen.exceptions import StaticError

    (tmp_path / "existing.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(StaticError):
        create_site(tmp_path)


def test_scaffold_force_writes_into_a_non_empty_directory(tmp_path: Path):
    (tmp_path / "existing.txt").write_text("hi", encoding="utf-8")
    create_site(tmp_path, force=True)
    assert (tmp_path / "_config.yml").is_file()
    assert (tmp_path / "existing.txt").is_file()

def test_no_template_syntax_leaks_into_the_output(tmp_path: Path):
    """Regression: a Jinja2 comment `{# … #}` survived the switch to Liquid and
    rendered as literal text on every page of every new site.

    Liquid silently passes through anything it does not recognise as a tag, so
    a syntax error from another template language is invisible to the build and
    only shows up on the page. Assert the output is clean.
    """
    create_site(tmp_path / "site", title="Test")
    Builder.from_source(tmp_path / "site").build()

    leaked = []
    for page in (tmp_path / "site/_site").rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for marker in ("{#", "#}", "{%", "%}", "{{", "}}"):
            if marker in text:
                leaked.append(f"{page.name} contains {marker!r}")
    assert not leaked, leaked
