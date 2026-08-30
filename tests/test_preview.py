"""Live preview of an unsaved buffer, and the reload behaviour around it."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dolmen.scaffold import create_site
from dolmen.server import STYLE_SUFFIXES, DevSite, ReloadChannel, create_app


@pytest.fixture
def dev_site(tmp_path: Path) -> DevSite:
    create_site(tmp_path / "site", title="Test")
    site = DevSite(tmp_path / "site")
    site.build()
    return site


@pytest.fixture
def client(dev_site: DevSite) -> TestClient:
    client = TestClient(create_app(dev_site, live_reload=False, admin=True))
    client.site = dev_site
    return client


# -- rendering an unsaved buffer ---------------------------------------------


def test_preview_renders_the_buffer_through_the_full_pipeline(dev_site: DevSite):
    html, error = dev_site.render_preview(
        dev_site.source / "about.md",
        "---\ntitle: About\n---\n\n# Typed just now\n\n**bold**\n",
    )
    assert error is None
    assert "Typed just now" in html
    assert "<strong>bold</strong>" in html
    assert html.lstrip().startswith("<!DOCTYPE"), "the layout chain must run"
    assert "site-header" in html, "includes must run"


def test_preview_writes_nothing(dev_site: DevSite):
    source = dev_site.source / "about.md"
    before = source.read_text(encoding="utf-8")
    built = (dev_site.source / "_site/about.html").read_text(encoding="utf-8")

    dev_site.render_preview(source, "---\ntitle: About\n---\n\nUNSAVED\n")

    assert source.read_text(encoding="utf-8") == before
    assert (dev_site.source / "_site/about.html").read_text(encoding="utf-8") == built


def test_preview_restores_the_site_model(dev_site: DevSite):
    """A preview must not leak into the next build."""
    document = next(d for d in dev_site.last_site.documents if d.title == "About")
    before = document.body

    dev_site.render_preview(dev_site.source / "about.md", "---\ntitle: About\n---\n\nX\n")

    assert document.body == before


def test_preview_reports_a_template_error_instead_of_raising(dev_site: DevSite):
    html, error = dev_site.render_preview(
        dev_site.source / "about.md", "---\ntitle: About\n---\n{% if %}\n"
    )
    assert error is not None
    assert html == ""


def test_preview_uses_the_front_matter_in_the_buffer(dev_site: DevSite):
    html, error = dev_site.render_preview(
        dev_site.source / "about.md", "---\ntitle: Renamed In Buffer\n---\n\nBody\n"
    )
    assert error is None
    assert "Renamed In Buffer" in html


def test_preview_endpoint(client):
    response = client.post(
        "/_dolmen/api/preview",
        json={"path": "about.md", "text": "---\ntitle: About\n---\n\nHELLO PREVIEW\n"},
    )
    assert response.status_code == 200
    assert "HELLO PREVIEW" in response.json()["html"]


def test_preview_endpoint_refuses_a_path_outside_the_site(client):
    response = client.post(
        "/_dolmen/api/preview", json={"path": "../../etc/passwd", "text": "x"}
    )
    assert response.status_code == 400


def test_preview_endpoint_returns_the_error_not_a_500(client):
    response = client.post(
        "/_dolmen/api/preview",
        json={"path": "about.md", "text": "---\ntitle: A\n---\n{% if %}\n"},
    )
    assert response.status_code == 200
    assert response.json()["error"]


# -- reload behaviour --------------------------------------------------------


def test_reload_channel_reports_css_only_builds():
    channel = ReloadChannel()
    queue = channel.subscribe()
    queue.get_nowait()                       # the initial version

    channel.publish(css_only=True)
    assert queue.get_nowait() == {"version": 1, "cssOnly": True}

    channel.publish()
    assert queue.get_nowait() == {"version": 2, "cssOnly": False}


def test_style_suffixes_cover_the_usual_stylesheets():
    assert ".css" in STYLE_SUFFIXES and ".scss" in STYLE_SUFFIXES


def test_live_reload_script_preserves_scroll_and_swaps_css(dev_site: DevSite):
    with TestClient(create_app(dev_site, live_reload=True, admin=False)) as client:
        body = client.get("/").text
    assert "sessionStorage" in body, "scroll position must survive a reload"
    assert "window.scrollY" in body
    assert "cssOnly" in body, "a stylesheet change must not force a full reload"
    assert 'link[rel="stylesheet"]' in body
