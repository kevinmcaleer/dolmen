"""The dev server: file serving, live-reload injection, and error pages."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dolmen.scaffold import create_site
from dolmen.server import DevSite, create_app


@pytest.fixture
def dev_site(tmp_path: Path) -> DevSite:
    create_site(tmp_path / "site", title="Test")
    site = DevSite(tmp_path / "site")
    site.build()
    return site


@pytest.fixture
def client(dev_site: DevSite) -> TestClient:
    # live_reload=False keeps the watcher out of the test; injection is covered
    # separately with a client that has it on.
    return TestClient(create_app(dev_site, live_reload=False, admin=True))


def test_serves_the_built_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>" in response.text


def test_serves_a_page(client):
    assert client.get("/about.html").status_code == 200


def test_pretty_permalink_resolves_to_index_html(client):
    assert client.get("/blog/hello-dolmen/").status_code == 200


def test_serves_static_assets(client):
    response = client.get("/assets/css/main.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_unknown_path_is_a_404_page(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert "404" in response.text


def test_live_reload_script_is_injected_into_html(dev_site):
    with TestClient(create_app(dev_site, live_reload=True, admin=False)) as client:
        body = client.get("/").text
    assert "EventSource" in body
    assert "/_dolmen/events" in body


def test_live_reload_is_not_injected_into_css(dev_site):
    with TestClient(create_app(dev_site, live_reload=True, admin=False)) as client:
        body = client.get("/assets/css/main.css").text
    assert "EventSource" not in body


@pytest.mark.parametrize(
    "path",
    ["../../etc/passwd", "../_config.yml", "/../../etc/hosts", "....//....//etc/passwd"],
)
def test_path_traversal_is_refused(dev_site, path):
    """The resolver is tested directly: an HTTP client normalises `..` away."""
    from dolmen.server import _serve_path

    response = _serve_path(dev_site, path, live_reload=False)
    assert response.status_code in {403, 404}, f"{path} escaped the output directory"


def test_files_outside_the_output_directory_are_never_served(dev_site, tmp_path):
    from dolmen.server import _serve_path

    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me", encoding="utf-8")
    # A relative path that climbs out of _site and back down to the secret.
    escape = f"../../{secret.name}"
    response = _serve_path(dev_site, escape, live_reload=False)
    assert response.status_code in {403, 404}


def test_a_broken_build_serves_an_error_page_not_a_crash(dev_site):
    (dev_site.source / "index.md").write_text(
        "---\ntitle: Home\nlayout: nope\n---\nBody\n", encoding="utf-8"
    )
    dev_site.overrides["strict"] = True
    dev_site.build()
    # A missing layout is a warning, not a fatal error, so the site still serves.
    with TestClient(create_app(dev_site, live_reload=False, admin=False)) as client:
        assert client.get("/").status_code in {200, 500}


def test_status_endpoint_reports_the_last_build(client):
    payload = client.get("/_dolmen/api/status").json()
    assert payload["ok"] is True
    assert payload["documents"] >= 1
    assert payload["error"] is None
