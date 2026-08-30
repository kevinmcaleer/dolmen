"""Site validation — the checks behind the problems panel.

A panel that is always on screen must not cry wolf, so these tests care as much
about what is *not* reported as about what is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dolmen.builder import Builder
from dolmen.scaffold import create_site
from dolmen.validate import validate


def check(source: Path):
    builder = Builder.from_source(source)
    result = builder.build()
    return validate(builder.site, builder.config, build_warnings=result.warnings)


@pytest.fixture
def clean_site(tmp_path: Path) -> Path:
    create_site(tmp_path / "site", title="Test")
    return tmp_path / "site"


def rules(report) -> list[str]:
    return [p.rule for p in report.problems]


# -- the scaffold must be clean ----------------------------------------------


def test_a_new_site_has_no_problems(clean_site: Path):
    """Regression: the scaffold shipped a nav link to a /blog/ that never existed."""
    report = check(clean_site)
    assert report.total == 0, [p.title for p in report.problems]


# -- broken links and images -------------------------------------------------


def test_broken_internal_link(clean_site: Path):
    (clean_site / "linky.md").write_text(
        "---\ntitle: Linky\n---\n\n[nope](/missing/page/)\n", encoding="utf-8"
    )
    report = check(clean_site)
    assert "broken-link" in rules(report)
    problem = next(p for p in report.problems if p.rule == "broken-link")
    assert "/missing/page/" in problem.title
    assert problem.file == "linky.md"
    assert problem.why


def test_working_internal_link_is_not_reported(clean_site: Path):
    (clean_site / "linky.md").write_text(
        "---\ntitle: Linky\n---\n\n[about](/about.html) and [home](/)\n", encoding="utf-8"
    )
    assert "broken-link" not in rules(check(clean_site))


def test_external_links_are_never_checked(clean_site: Path):
    (clean_site / "linky.md").write_text(
        "---\ntitle: Linky\n---\n\n"
        "[x](https://example.com/nope) [y](mailto:a@b.c) [z](#anchor)\n",
        encoding="utf-8",
    )
    assert "broken-link" not in rules(check(clean_site))


def test_missing_image_is_reported_as_an_asset(clean_site: Path):
    (clean_site / "pic.md").write_text(
        "---\ntitle: Pic\n---\n\n![alt](/assets/img/absent.png)\n", encoding="utf-8"
    )
    report = check(clean_site)
    assert "missing-asset" in rules(report)


def test_present_image_is_not_reported(clean_site: Path):
    images = clean_site / "assets/img"
    images.mkdir(parents=True, exist_ok=True)
    (images / "there.png").write_bytes(b"\x89PNG")
    (clean_site / "pic.md").write_text(
        "---\ntitle: Pic\n---\n\n![alt](/assets/img/there.png)\n", encoding="utf-8"
    )
    assert "missing-asset" not in rules(check(clean_site))


# -- wiki links --------------------------------------------------------------


def test_unresolved_wikilink(clean_site: Path):
    (clean_site / "w.md").write_text(
        "---\ntitle: W\n---\n\nSee [[No Such Page]].\n", encoding="utf-8"
    )
    report = check(clean_site)
    assert "broken-wikilink" in rules(report)


def test_resolved_wikilink_is_not_reported(clean_site: Path):
    (clean_site / "w.md").write_text(
        "---\ntitle: W\n---\n\nSee [[About]].\n", encoding="utf-8"
    )
    assert "broken-wikilink" not in rules(check(clean_site))


# -- layouts, includes, front matter -----------------------------------------


def test_missing_layout(clean_site: Path):
    (clean_site / "l.md").write_text(
        "---\ntitle: L\nlayout: ghost\n---\nBody\n", encoding="utf-8"
    )
    report = check(clean_site)
    assert "missing-layout" in rules(report)
    problem = next(p for p in report.problems if p.rule == "missing-layout")
    assert problem.severity == "error"
    assert problem.line == 3


def test_missing_include(clean_site: Path):
    (clean_site / "i.md").write_text(
        "---\ntitle: I\n---\n{% include ghost.html %}\n", encoding="utf-8"
    )
    report = check(clean_site)
    assert "missing-include" in rules(report)


def test_a_specific_error_replaces_the_generic_build_warning(clean_site: Path):
    """Regression: a missing layout was reported twice — once generically."""
    (clean_site / "l.md").write_text(
        "---\ntitle: L\nlayout: ghost\n---\nBody\n", encoding="utf-8"
    )
    report = check(clean_site)
    for_file = [p for p in report.problems if p.file == "l.md" and p.severity == "error"]
    assert len(for_file) == 1, [p.title for p in for_file]


def test_missing_title(clean_site: Path):
    (clean_site / "untitled.md").write_text("---\nlayout: page\n---\nBody\n", encoding="utf-8")
    report = check(clean_site)
    assert "missing-front-matter" in rules(report)


def test_post_without_a_date_in_front_matter_or_filename(clean_site: Path):
    (clean_site / "_posts/undated.md").write_text(
        "---\ntitle: Undated\n---\nBody\n", encoding="utf-8"
    )
    report = check(clean_site)
    missing = [p for p in report.problems if p.rule == "missing-front-matter"]
    assert any("date" in p.title for p in missing)


# -- code fences -------------------------------------------------------------


def test_unknown_code_language(clean_site: Path):
    (clean_site / "c.md").write_text(
        "---\ntitle: C\n---\n\n```pyton\nx = 1\n```\n", encoding="utf-8"
    )
    report = check(clean_site)
    assert "unknown-code-language" in rules(report)
    assert next(p for p in report.problems if p.rule == "unknown-code-language").severity == "info"


def test_known_language_is_not_reported(clean_site: Path):
    (clean_site / "c.md").write_text(
        "---\ntitle: C\n---\n\n```python\nx = 1\n```\n", encoding="utf-8"
    )
    assert "unknown-code-language" not in rules(check(clean_site))


def test_closing_fence_does_not_match_the_next_paragraph(clean_site: Path):
    """Regression: `\\s*` spanned newlines, so the word after a closing fence
    was read as a language — `Wiki links resolve...` became language `Wiki`."""
    (clean_site / "c.md").write_text(
        "---\ntitle: C\n---\n\n```python\nx = 1\n```\n\nWiki links resolve by title.\n",
        encoding="utf-8",
    )
    assert "unknown-code-language" not in rules(check(clean_site))


def test_convention_fences_are_not_languages(clean_site: Path):
    (clean_site / "c.md").write_text(
        "---\ntitle: C\n---\n\n```mermaid\ngraph TD;\n```\n\n```text\nplain\n```\n",
        encoding="utf-8",
    )
    assert "unknown-code-language" not in rules(check(clean_site))


# -- the report --------------------------------------------------------------


def test_report_counts_and_worst_severity(clean_site: Path):
    (clean_site / "l.md").write_text(
        "---\ntitle: L\nlayout: ghost\n---\nBody\n", encoding="utf-8"
    )
    (clean_site / "c.md").write_text(
        "---\ntitle: C\n---\n\n```pyton\nx=1\n```\n", encoding="utf-8"
    )
    report = check(clean_site)
    assert report.worst == "error"
    assert report.errors >= 1
    assert report.infos >= 1
    assert report.total == len(report.problems)


def test_problems_are_sorted_worst_first(clean_site: Path):
    (clean_site / "l.md").write_text(
        "---\ntitle: L\nlayout: ghost\n---\nBody\n", encoding="utf-8"
    )
    (clean_site / "c.md").write_text(
        "---\ntitle: C\n---\n\n```pyton\nx=1\n```\n", encoding="utf-8"
    )
    severities = [p.severity for p in check(clean_site).sorted()]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s])


def test_clean_report_has_no_worst_severity(clean_site: Path):
    report = check(clean_site)
    assert report.worst is None
    assert report.to_dict()["problems"] == []


def test_every_problem_explains_why_it_matters(clean_site: Path):
    (clean_site / "l.md").write_text(
        "---\nlayout: ghost\n---\n\n[x](/nope/) [[Missing]]\n\n```pyton\nx=1\n```\n",
        encoding="utf-8",
    )
    report = check(clean_site)
    assert report.total > 0
    for problem in report.problems:
        assert problem.why, f"{problem.rule} has no explanation"
        assert problem.title and problem.message
