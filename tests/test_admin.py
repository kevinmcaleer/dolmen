"""The HTTP API behind the build front end.

These routes write files, so the path checks matter as much as the happy paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dolmen.scaffold import create_site
from dolmen.server import DevSite, create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    create_site(tmp_path / "site", title="Test")
    site = DevSite(tmp_path / "site")
    site.build()
    client = TestClient(create_app(site, live_reload=False, admin=True))
    client.source = tmp_path / "site"
    return client


def test_front_end_page_is_served(client):
    response = client.get("/_dolmen/")
    assert response.status_code == 200
    assert "monaco" in response.text.lower()


def test_front_end_assets_are_served(client):
    assert client.get("/_dolmen/assets/app.js").status_code == 200
    assert client.get("/_dolmen/assets/app.css").status_code == 200


def test_asset_route_refuses_traversal(client):
    assert client.get("/_dolmen/assets/../app.py").status_code in {403, 404}


def test_tree_lists_source_files_with_their_kind(client):
    payload = client.get("/_dolmen/api/tree").json()
    paths = {e["path"]: e for e in payload["entries"]}
    assert "index.md" in paths
    assert paths["index.md"]["editable"] is True
    assert paths["_layouts/default.html"]["special"] is True
    assert not any(p.startswith("_site") for p in paths)


def test_meta_reports_layouts_and_collections(client):
    payload = client.get("/_dolmen/api/meta").json()
    assert payload["title"] == "Test"
    assert "default" in payload["layouts"]
    assert "posts" in payload["collections"]


def test_read_a_file_returns_text_and_front_matter(client):
    payload = client.get("/_dolmen/api/file", params={"path": "about.md"}).json()
    assert payload["has_front_matter"] is True
    assert payload["metadata"]["title"] == "About"
    assert payload["url"] == "/about.html"


def test_read_a_missing_file_is_404(client):
    assert client.get("/_dolmen/api/file", params={"path": "nope.md"}).status_code == 404


def test_read_refuses_a_binary_file(client, tmp_path):
    (tmp_path / "site/logo.png").write_bytes(b"\x89PNG")
    response = client.get("/_dolmen/api/file", params={"path": "logo.png"})
    assert response.status_code == 415


def test_read_refuses_a_path_outside_the_site(client):
    response = client.get("/_dolmen/api/file", params={"path": "../../etc/passwd"})
    assert response.status_code == 400
    assert "outside" in response.json()["error"]


def test_write_saves_and_rebuilds(client, tmp_path):
    response = client.post(
        "/_dolmen/api/file",
        json={"path": "about.md", "text": "---\ntitle: About\n---\n\nEdited.\n"},
    )
    assert response.status_code == 200
    assert response.json() == {"saved": True, "error": None}
    assert "Edited." in (tmp_path / "site/_site/about.html").read_text(encoding="utf-8")


def test_write_refuses_a_path_outside_the_site(client, tmp_path):
    outside = tmp_path / "escaped.md"
    response = client.post(
        "/_dolmen/api/file", json={"path": "../escaped.md", "text": "x"}
    )
    assert response.status_code == 400
    assert not outside.exists()


def test_create_a_post(client, tmp_path):
    payload = client.post(
        "/_dolmen/api/new",
        json={"title": "My New Post", "collection": "posts", "layout": "post"},
    ).json()
    assert payload["path"].startswith("_posts/")
    assert payload["path"].endswith("-my-new-post.md")
    assert (tmp_path / "site" / payload["path"]).is_file()


def test_create_a_page(client, tmp_path):
    payload = client.post(
        "/_dolmen/api/new", json={"title": "Contact", "collection": "pages"}
    ).json()
    assert payload["path"] == "contact.md"
    assert (tmp_path / "site/contact.md").is_file()


def test_create_refuses_to_clobber(client):
    body = {"title": "Dup", "collection": "pages"}
    assert client.post("/_dolmen/api/new", json=body).status_code == 200
    assert client.post("/_dolmen/api/new", json=body).status_code == 409


def test_rebuild_endpoint(client):
    payload = client.post("/_dolmen/api/build").json()
    assert payload["ok"] is True
    assert payload["documents"] >= 1


def test_delete_a_file(client, tmp_path):
    assert (tmp_path / "site/about.md").is_file()
    response = client.request("DELETE", "/_dolmen/api/file", json={"path": "about.md"})
    assert response.status_code == 200
    assert not (tmp_path / "site/about.md").exists()


def test_delete_refuses_a_path_outside_the_site(client):
    response = client.request(
        "DELETE", "/_dolmen/api/file", json={"path": "../../etc/passwd"}
    )
    assert response.status_code == 400


def test_upload_rejects_a_non_image(client):
    response = client.post(
        "/_dolmen/api/upload", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 415


def test_upload_stores_an_image_and_returns_markdown(client, tmp_path):
    png = _tiny_png()
    response = client.post(
        "/_dolmen/api/upload", files={"file": ("logo.png", png, "image/png")}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["url"] == "/assets/img/logo.png"
    assert payload["markdown"] == "![logo](/assets/img/logo.png)"
    assert (tmp_path / "site/assets/img/logo.png").is_file()


def test_upload_does_not_clobber_an_existing_file(client, tmp_path):
    png = _tiny_png()
    first = client.post("/_dolmen/api/upload", files={"file": ("a.png", png, "image/png")})
    second = client.post("/_dolmen/api/upload", files={"file": ("a.png", png, "image/png")})
    assert first.json()["url"] == "/assets/img/a.png"
    assert second.json()["url"] == "/assets/img/a-1.png"


def _tiny_png() -> bytes:
    """A 1x1 PNG, so the uploader has something Pillow can actually open."""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
