"""TRMNL's bring-your-own-server protocol (W-824). A TRMNL, or an e-reader
running one of TRMNL's clients, pointed at this server is a viewer: it shows
what the frame shows and never becomes the frame. Request shapes are the
firmware's own (usetrmnl/trmnl-firmware, lib/trmnl/src/api-client/request_headers.cpp)."""
from __future__ import annotations

import io
from datetime import datetime
from urllib.parse import urlparse

import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe import viewers
from featherframe.render import pipeline, theme
from featherframe.render.pipeline import View

X = {"ID": "AA:BB:CC:DD:EE:01", "Model": "x", "Width": "1872", "Height": "1404",
     "FW-Version": "2.0.1", "Battery-Voltage": "4.02", "Percent-Charged": "88", "RSSI": "-58",
     "Refresh-Rate": "900", "Content-Type": "application/json"}
OG = {"ID": "AA:BB:CC:DD:EE:02", "Model": "og", "Width": "800", "Height": "480",
      "FW-Version": "1.6.9", "Battery-Voltage": "3.9", "RSSI": "-70"}
KOBO = {"ID": "AA:BB:CC:DD:EE:03", "Battery-Voltage": "4.0", "RSSI": "-61", "FW-Version": "kobo"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    svc._clock = lambda: datetime(2026, 9, 20, 12, 0)
    svc.config.quiet_hours_mode = "off"
    app.state.service = svc
    ramp = np.tile(np.linspace(0, 255, theme.WIDTH, dtype=np.uint8), (theme.HEIGHT, 1))
    svc._commit("plates", svc._clock(), sheet=Image.fromarray(ramp, mode="L"),
                mode="single", species_key=None, label="x")
    return TestClient(app)


def _image(client, body):
    url = urlparse(body["image_url"])
    r = client.get(url.path)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    return r.content


def test_setup_hands_a_new_device_its_key(client):
    r = client.get("/api/setup", headers={"ID": X["ID"], "Model": "x", "FW-Version": "2.0.1"})
    body = r.json()
    assert body["status"] == 200 and len(body["api_key"]) >= 16 and body["friendly_id"]
    # Asking again is the same device, the same key.
    assert client.get("/api/setup", headers={"ID": X["ID"]}).json()["api_key"] == body["api_key"]
    assert client.get("/api/setup").status_code == 404


def test_display_answers_in_the_firmwares_own_terms(client):
    svc = client.app.state.service
    body = client.get("/api/display", headers=X).json()
    assert body["status"] == 0 and body["update_firmware"] is False
    assert body["refresh_rate"] == viewers.REFRESH_SECONDS
    assert body["filename"].startswith(svc.current_etag())
    assert body["image_url"].startswith("http") and body["image_url"].endswith(".png")


def test_a_trmnl_x_gets_the_ee03s_picture_on_its_side(client):
    """1872x1404 of 16 grays is the EE03's glass: 4-bit PNG, the portrait
    plate turned into the landscape canvas."""
    png = _image(client, client.get("/api/display", headers=X).json())
    assert png[24] == 4
    assert Image.open(io.BytesIO(png)).size == (1872, 1404)
    assert viewers.view_of(client.app.state.service.viewers.get(X["ID"])) == View(1872, 1404, "gray16", 90)


def test_an_og_gets_two_bit_gray(client):
    png = _image(client, client.get("/api/display", headers=OG).json())
    assert png[24] == 2 and Image.open(io.BytesIO(png)).size == (800, 480)


def test_a_client_that_reports_no_size_gets_an_e_reader_page_until_the_owner_says(client):
    png = _image(client, client.get("/api/display", headers=KOBO).json())
    assert Image.open(io.BytesIO(png)).size == (1072, 1448)
    r = client.post(f"/api/viewers/{KOBO['ID']}", json={"width": 1264, "height": 1680,
                                                       "name": "Kitchen Kobo"})
    assert _row(r.json()["frames"], KOBO["ID"])["name"] == "Kitchen Kobo"
    png = _image(client, client.get("/api/display", headers=KOBO).json())
    assert Image.open(io.BytesIO(png)).size == (1264, 1680)


def test_the_owners_rotation_survives_check_ins_and_changes_the_filename(client):
    before = client.get("/api/display", headers=X).json()["filename"]
    client.post(f"/api/viewers/{X['ID']}", json={"rotation": 270})
    after = client.get("/api/display", headers=X).json()
    assert after["filename"] != before and after["filename"].endswith("-270")
    # Clearing the choice goes back to the default.
    client.post(f"/api/viewers/{X['ID']}", json={"rotation": ""})
    assert client.get("/api/display", headers=X).json()["filename"] == before


def _row(frames: list, frame_id: str) -> dict:
    return [f for f in frames if f["id"] == frame_id][0]


def _kits(svc) -> list:
    return [(f["id"], f["status"]) for f in svc.frames_list() if f["transport"] == "kit"]


def test_a_viewer_is_never_the_frame(client):
    svc = client.app.state.service
    before = (svc._etag, _kits(svc))
    client.get("/api/setup", headers=X)
    _image(client, client.get("/api/display", headers=X).json())
    assert (svc._etag, _kits(svc)) == before
    # It is a frame in the registry all the same, fed another way.
    listed = {f["id"]: f for f in svc.status()["frames"]["list"]}
    assert listed[X["ID"]]["transport"] == "trmnl"
    # And the frame's own endpoint is not opened by it: a kit asking there is
    # let in as the first kit, and served once a tick has drawn for it.
    from tests._frames import connect
    assert connect(client, {"X-Device-Id": "f0:0d"}).status_code == 200


def test_quiet_hours_let_a_viewer_sleep_longer(client):
    svc = client.app.state.service
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start, svc.config.quiet_hours_end = "11:00", "13:00"
    assert client.get("/api/display", headers=X).json()["refresh_rate"] == viewers.QUIET_REFRESH_SECONDS


def test_the_list_shows_what_each_viewer_is_and_never_its_key(client):
    client.get("/api/display", headers=X)
    client.get("/api/display", headers=OG)
    rows = client.get("/api/viewers").json()["viewers"]
    assert [r["id"] for r in rows] == [X["ID"], OG["ID"]]
    assert rows[0]["reported"]["battery_percent"] == 88 and rows[0]["view"]["format"] == "gray16"
    assert "token" not in rows[0] and "token" not in str(rows)
    assert client.post(f"/api/viewers/{OG['ID']}", json={"forget": True}).json()["ok"]
    assert len(client.get("/api/viewers").json()["viewers"]) == 1


def test_junk_from_the_lan_is_bounded(client):
    svc = client.app.state.service
    bad = {**X, "ID": "AA:BB:CC:DD:EE:09", "Width": "99999", "Battery-Voltage": "nan",
           "RSSI": "inf", "Model": "x" * 500}
    assert client.get("/api/display", headers=bad).status_code == 200
    row = svc.viewers.get("AA:BB:CC:DD:EE:09")
    assert "width" not in row["reported"] and "battery_volts" not in row["reported"]
    assert len(row["reported"]["model"]) <= 40
    assert client.get("/api/display", headers={"ID": "../../etc"}).status_code == 404
    for i in range(viewers.MAX_VIEWERS + 5):
        client.get("/api/display", headers={"ID": f"AA:00:00:00:00:{i:02X}"})
    assert len(svc.viewers.all()) == viewers.MAX_VIEWERS
    # A foreign page cannot rename or forget a viewer.
    r = client.post(f"/api/viewers/{X['ID']}", json={"forget": True},
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_the_log_is_taken_and_dropped(client):
    assert client.post("/api/log", headers={"ID": X["ID"]},
                       json={"logs": [{"message": "A test."}]}).status_code == 204


# -- the kiosk page (W-825) ------------------------------------------------------
def test_the_page_is_served_and_installs_as_an_app(client):
    html = client.get("/view").text
    assert "/api/view/state" in html and "apple-mobile-web-app-capable" in html
    # Old iPads run it: no arrow functions, no let/const, no fetch.
    script = html.split("<script>")[1]
    assert "=>" not in script and "const " not in script and "fetch(" not in script
    assert client.get("/view.webmanifest").json()["start_url"] == "/view"


def test_a_page_is_told_which_image_to_show_in_colour_at_its_own_size(client):
    r = client.get("/api/view/state?viewer=PAGE-1A2B3C4D&w=1536&h=2048&device=iPad").json()
    assert r["dark"] is False and r["poll"] == viewers.PAGE_POLL_SECONDS
    assert r["image"].endswith("-1536x2048-color-0.png")
    assert Image.open(io.BytesIO(client.get(r["image"]).content)).size == (1536, 2048)
    row = client.get("/api/viewers").json()["viewers"][0]
    assert row["kind"] == "page" and row["reported"]["model"] == "iPad" and row["dark_quiet"] is True


def test_a_big_screen_is_not_drawn_bigger_than_the_sheet_is_worth(client):
    r = client.get("/api/view/state?viewer=PAGE-1&w=2048&h=2732").json()
    assert "-1535x2048-color-0" in r["image"]   # the iPad Pro's 2048x2732, long side capped


def test_a_landscape_tablet_shows_the_plate_upright(client):
    r = client.get("/api/view/state?viewer=PAGE-2&w=2048&h=1536").json()
    assert r["image"].endswith("-2048x1536-color-0.png")


def test_a_lit_screen_goes_dark_in_quiet_hours_unless_the_owner_says_no(client):
    svc = client.app.state.service
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start, svc.config.quiet_hours_end = "11:00", "13:00"
    url = "/api/view/state?viewer=PAGE-3&w=1536&h=2048"
    assert client.get(url).json()["dark"] is True
    client.post("/api/viewers/PAGE-3", json={"dark_quiet": False})
    assert client.get(url).json()["dark"] is False
    # "Paper look" is the owner's format choice.
    client.post("/api/viewers/PAGE-3", json={"fmt": "gray256"})
    state = client.get(url).json()
    assert state["paper"] is True and "-gray256-" in state["image"]


def test_a_page_that_does_not_say_who_it_is_is_refused(client):
    assert client.get("/api/view/state?w=100&h=100").status_code == 400
    assert client.get("/api/view/state?viewer=PAGE-4&w=x&h=100").status_code == 400


# -- a viewer is a row in the Frames card, like every other frame (W-833) ---------
def test_the_frames_card_says_how_to_start_when_there_is_nothing(client):
    html = client.get("/").text
    assert 'id="frames-card"' in html
    assert "Nothing is showing plates yet" in html
    assert 'id="fr-view-url"' in html and "Featherframe-Setup" in html


def test_each_viewer_is_a_row_with_only_the_controls_its_screen_has(client):
    client.get("/api/display", headers=X)
    client.get("/api/display", headers=KOBO)
    client.get("/api/view/state?viewer=PAGE-1&w=1536&h=2048&device=iPad")
    client.post(f"/api/viewers/{X['ID']}", json={"name": "Hall TRMNL"})
    card = client.get("/").text.split('id="frames-card"')[1].split("</section>")[0]
    assert "Hall TRMNL" in card and "iPad" in card
    x, kobo, page = (card.split(f'data-frame="{i}"')[1].split(chr(10) + "    </li>")[0]
                     for i in (X["ID"], KOBO["ID"], "PAGE-1"))
    assert 'data-f="rotation"' in x and 'data-f="width"' not in x and 'data-f="dark_quiet"' not in x
    assert 'data-f="width"' in kobo                        # it never said how big it is
    assert 'data-f="dark_quiet"' in page and 'data-f="rotation"' not in page
    assert 'data-f="fmt"' in page and 'data-f="fmt"' not in x          # Look is a lit screen's
    # Every frame is named and every frame shows something.
    for row in (x, kobo, page):
        assert 'data-f="name"' in row and 'data-f="shows"' in row
        # …and nothing a viewer does not have.
        for absent in ('data-f="power_mode"', 'data-f="mat_inset_pct"'):
            assert absent not in row
    # Nothing shared is offered per frame.
    for shared in ("quiet_hours", "blocklist", "detection"):
        assert f'data-f="{shared}' not in card


def test_the_row_posts_what_the_api_takes(client):
    """The row sends every field as the form holds it: strings, a checkbox bool."""
    client.get("/api/view/state?viewer=PAGE-1&w=1536&h=2048&device=iPad")
    r = client.post("/api/frames/PAGE-1", json={"name": " Kitchen iPad ", "fmt": "gray256",
                                                "dark_quiet": False})
    v = _row(r.json()["frames"], "PAGE-1")
    assert v["name"] == "Kitchen iPad" and v["settings"]["format"] == "gray256"
    assert v["settings"]["dark_quiet"] is False
    client.get("/api/display", headers=KOBO)
    r = client.post(f"/api/frames/{KOBO['ID']}", json={"name": "", "rotation": "0",
                                                       "width": "1264", "height": "1680"})
    s = _row(r.json()["frames"], KOBO["ID"])["settings"]
    assert (s["width"], s["height"], s["format"], s["rotation"]) == (1264, 1680, "gray256", 0)


def test_a_page_is_never_given_a_panel_rotation(client):
    """Capability validation: a lit screen turns its own picture, so there is
    no rotation to set — and a post that tries is ignored, not obeyed."""
    svc = client.app.state.service
    client.get("/api/view/state?viewer=PAGE-1&w=1536&h=2048&device=iPad")
    r = client.post("/api/frames/PAGE-1", json={"rotation": 90, "panel_rotation": 90,
                                                "power_mode": "sleep", "mat_inset_pct": 9})
    assert r.json()["ok"]
    own = svc.frames.get("PAGE-1")["set"]
    assert "rotation" not in own and "power_mode" not in own and "mat_inset_pct" not in own
