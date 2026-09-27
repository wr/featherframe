"""Every frame is approved on the server, and a waiting frame says so (W-833).

A screen that finds this server on the LAN is not the owner saying yes. Every
frame of every transport — the first kit on a fresh install included — starts
`asking`, shows on its own glass that it is waiting, and takes the picture only
once the owner answers "Add" in the Frames card.
"""
from __future__ import annotations

import io
from datetime import datetime

import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe import viewers
from featherframe.render import pipeline, theme, welcome
from tests._frames import EE03_PANEL, approve

KIT = {"X-Device-Id": "AA:AA:AA:00:00:03", "X-Panel": EE03_PANEL}
TRMNL = {"ID": "CC:CC:CC:00:00:01", "Model": "x", "Width": "1872", "Height": "1404"}
PAGE = "/api/view/state?viewer=PAGE-1A2B3C4D&w=1536&h=2048&device=iPad"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    svc._clock = lambda: datetime(2026, 9, 20, 12, 0)   # daytime: the waiting refresh is not the quiet one
    svc._render_welcome(svc._clock(), False)
    app.state.service = svc
    return TestClient(app)


def _image(client, body):
    r = client.get(body["image_url"].split("http://testserver")[1])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(r.content))


# -- the kit ------------------------------------------------------------------
def test_the_first_kit_on_a_fresh_install_asks_like_any_other(client):
    svc = client.app.state.service
    assert svc.frames.all() == {}
    r = client.get("/api/frame", headers=KIT)
    assert r.status_code == 403 and r.headers["x-ff-frame"] == "pending"
    assert svc.frames.get(KIT["X-Device-Id"])["status"] == "asking"
    approve(svc, KIT["X-Device-Id"])
    svc.tick()
    assert client.get("/api/frame", headers=KIT).status_code == 200


# -- the kiosk page -----------------------------------------------------------
def test_a_page_is_told_it_is_waiting_and_which_screen_it_is(client):
    svc = client.app.state.service
    state = client.get(PAGE).json()
    assert state["waiting"] is True and state["image"] is None
    assert state["id"] == "PAGE-1A2B3C4D"[-6:]
    assert state["poll"] == viewers.PAGE_POLL_SECONDS
    # It keeps asking, and the next poll after the owner answers is the picture.
    approve(svc, "PAGE-1A2B3C4D")
    state = client.get(PAGE).json()
    assert not state.get("waiting") and state["image"]


def test_an_ignored_page_shows_the_same_waiting_screen(client):
    svc = client.app.state.service
    client.get(PAGE)
    assert svc.answer_frame("PAGE-1A2B3C4D", "ignore")
    assert client.get(PAGE).json()["waiting"] is True


def test_the_page_says_the_one_thing_to_do_about_it(client):
    html = client.get("/view").text
    assert "Add this frame on the Featherframe webapp" in html
    assert 'id="wait-id"' in html and "state.waiting" in html
    # Old iPads run it: no arrow functions, no let/const, no fetch.
    script = html.split("<script>")[1]
    assert "=>" not in script and "const " not in script and "fetch(" not in script


# -- TRMNL --------------------------------------------------------------------
def test_a_trmnl_gets_its_key_then_the_waiting_image(client):
    svc = client.app.state.service
    assert client.get("/api/setup", headers=TRMNL).json()["api_key"]
    body = client.get("/api/display", headers=TRMNL).json()
    assert body["status"] == 0 and body["refresh_rate"] == 300
    assert body["refresh_rate"] == viewers.WAITING_REFRESH_SECONDS
    assert body["filename"] == "waiting-1872x1404-gray16-90"
    # Sized, formatted and turned for that device, like any other view.
    img = _image(client, body)
    assert img.size == (1872, 1404)
    assert np.asarray(img.convert("L")).min() < 128      # there is type on it
    assert svc.frames.get(TRMNL["ID"])["status"] == "asking"
    # Added: the picture, on the ordinary refresh.
    approve(svc, TRMNL["ID"])
    after = client.get("/api/display", headers=TRMNL).json()
    assert after["filename"].startswith(svc.current_etag())
    assert after["refresh_rate"] == viewers.REFRESH_SECONDS


def test_an_ignored_trmnl_keeps_the_waiting_image_and_asks_rarely(client):
    svc = client.app.state.service
    client.get("/api/display", headers=TRMNL)
    assert svc.answer_frame(TRMNL["ID"], "ignore")
    body = client.get("/api/display", headers=TRMNL).json()
    assert body["filename"].startswith("waiting-")
    assert body["refresh_rate"] == viewers.IGNORED_REFRESH_SECONDS
    assert _image(client, body).size == (1872, 1404)


def test_the_waiting_plate_carries_the_wordmark_and_the_sentence(client):
    assert welcome.WAITING_LINE == "ADD THIS FRAME ON THE FEATHERFRAME WEBAPP"
    sheet = welcome.render_waiting("00:01")
    assert sheet.size == (theme.WIDTH, theme.HEIGHT)
    # Two screens waiting side by side are told apart by their short id.
    other = welcome.render_waiting("00:02")
    assert not np.array_equal(np.asarray(sheet), np.asarray(other))


def test_the_waiting_plate_is_drawn_once_and_kept(client, tmp_path):
    """A screen still asking must not cost a full render on every poll."""
    from featherframe import paths
    client.get("/api/display", headers=TRMNL)
    for _ in range(3):
        client.get("/api/display", headers=TRMNL)
        _image(client, client.get("/api/display", headers=TRMNL).json())
    kept = list(paths.views_dir().glob("waiting-*.png"))
    assert len(kept) == 1


# -- nothing is drawn for a screen nobody answered for -------------------------
def test_a_screen_that_is_only_asking_makes_no_picture_wanted(client):
    svc = client.app.state.service
    client.get("/api/display", headers=TRMNL)
    client.post(f"/api/frames/{TRMNL['ID']}", json={"shows": "collage"})
    assert svc._kinds_shown() == {"plates"}       # the server's own fallback, not its ask
    approve(svc, TRMNL["ID"])
    assert svc._kinds_shown() == {"collage"}


# -- the card ------------------------------------------------------------------
def test_the_card_offers_to_add_a_screen_of_any_transport(client):
    client.get("/api/frame", headers=KIT)
    client.get("/api/display", headers=TRMNL)
    client.get(PAGE)
    card = client.get("/").text.split('id="frames-card"')[1].split("</section>")[0]
    assert card.count("wants to connect") == 3
    for fid in (KIT["X-Device-Id"], TRMNL["ID"], "PAGE-1A2B3C4D"):
        assert f'data-frame-action="add" data-frame-id="{fid}"' in card
        assert f'data-frame-action="ignore" data-frame-id="{fid}"' in card
    # Its short id and its address, so two tablets can be told apart.
    assert "testclient" in card
