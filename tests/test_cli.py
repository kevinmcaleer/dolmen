"""The `dolmen` command."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from dolmen.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "dolmen" in result.output


def test_new_then_build(runner, tmp_path: Path):
    site = tmp_path / "site"
    assert runner.invoke(main, ["new", str(site)]).exit_code == 0
    assert (site / "_config.yml").is_file()

    result = runner.invoke(main, ["build", "--source", str(site)])
    assert result.exit_code == 0
    assert "built" in result.output
    assert (site / "_site/index.html").is_file()


def test_new_refuses_a_non_empty_directory(runner, tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = runner.invoke(main, ["new", str(tmp_path)])
    assert result.exit_code == 1
    assert "not empty" in result.output


def test_new_force_overrides_that(runner, tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert runner.invoke(main, ["new", str(tmp_path), "--force"]).exit_code == 0


def test_build_to_a_custom_destination(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    out = tmp_path / "public"
    result = runner.invoke(main, ["build", "--source", str(site), "--destination", str(out)])
    assert result.exit_code == 0
    assert (out / "index.html").is_file()


def test_build_quiet_prints_nothing_on_success(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    result = runner.invoke(main, ["build", "--source", str(site), "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_build_reports_a_template_error_without_a_traceback(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    (site / "broken.md").write_text(
        "---\ntitle: Broken\nlayout: nope\n---\nBody\n", encoding="utf-8"
    )
    result = runner.invoke(main, ["build", "--source", str(site)])
    assert "warning:" in result.output
    assert "Traceback" not in result.output


def test_strict_turns_a_warning_into_a_failure(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    (site / "broken.md").write_text("---\ntitle: [oops\n---\nBody\n", encoding="utf-8")
    result = runner.invoke(main, ["build", "--source", str(site), "--strict"])
    assert result.exit_code == 1
    assert "error:" in result.output


def test_doctor_reports_a_clean_site(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    result = runner.invoke(main, ["doctor", "--source", str(site)])
    assert "no problems found" in result.output


def test_doctor_lists_problems_with_a_reason(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    (site / "bad.md").write_text("---\ntitle: [oops\n---\n", encoding="utf-8")
    result = runner.invoke(main, ["doctor", "--source", str(site)])
    assert "error:" in result.output
    assert "why:" in result.output, "every problem explains why it matters"
    assert "1 error(s)" in result.output
    assert result.exit_code == 1


def test_doctor_reports_a_broken_link(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    (site / "linky.md").write_text(
        "---\ntitle: Linky\n---\n\n[nowhere](/missing/page/)\n", encoding="utf-8"
    )
    result = runner.invoke(main, ["doctor", "--source", str(site)])
    assert "/missing/page/" in result.output
    # Warnings alone are not a failure unless --strict.
    assert result.exit_code == 0
    assert runner.invoke(main, ["doctor", "--source", str(site), "--strict"]).exit_code == 1


def test_clean_removes_the_output(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    runner.invoke(main, ["build", "--source", str(site)])
    assert (site / "_site").is_dir()

    result = runner.invoke(main, ["clean", "--source", str(site)])
    assert "removed" in result.output
    assert not (site / "_site").exists()


def test_clean_on_a_site_never_built(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    result = runner.invoke(main, ["clean", "--source", str(site)])
    assert "nothing to remove" in result.output


def test_drafts_flag_includes_drafts(runner, tmp_path: Path):
    site = tmp_path / "site"
    runner.invoke(main, ["new", str(site)])
    drafts = site / "_drafts"
    drafts.mkdir()
    (drafts / "wip.md").write_text("---\ntitle: WIP\n---\nBody\n", encoding="utf-8")

    runner.invoke(main, ["build", "--source", str(site)])
    assert not list((site / "_site").rglob("wip*"))

    runner.invoke(main, ["build", "--source", str(site), "--drafts"])
    assert list((site / "_site").rglob("*wip*"))
