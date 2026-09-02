"""W-688: the config page has an icon, and /favicon.ico stops 404ing."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe import paths
from featherframe.service import FeatherframeService


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    app.state.service = FeatherframeService()
    return TestClient(app, raise_server_exceptions=False)


def test_favicon_ico_is_served_with_a_days_cache(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/x-icon"
    assert "max-age=86400" in r.headers["cache-control"]
    assert r.content[:4] == b"\x00\x00\x01\x00"          # ICO header


def test_icon_files_exist_and_are_the_committed_sizes():
    from PIL import Image
    static = paths.static_dir()
    with Image.open(static / "favicon-32.png") as im:
        assert im.size == (32, 32)
    with Image.open(static / "favicon-192.png") as im:
        assert im.size == (192, 192)
    with Image.open(static / "apple-touch-icon.png") as im:
        assert im.size == (180, 180) and im.mode == "RGB"   # iOS: opaque, square


def test_page_links_the_icon_set(client):
    html = client.get("/").text
    assert '<link rel="icon" href="/favicon.ico"' in html
    assert 'href="/static/favicon-32.png"' in html
    assert '<link rel="apple-touch-icon"' in html
    assert client.get("/static/favicon-32.png").status_code == 200
