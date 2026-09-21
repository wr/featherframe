"""Several frames on one server (W-832). The active frame keeps the resident
frame and the device card; an ADDED frame is a second kit drawn from the same
pictures, finished for its own panel, showing plates or the collage. An EE03 on
plates and an EE02 on the collage is the case it exists for."""
from __future__ import annotations

import struct
from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe.render import pipeline
from tests._fixtures import create_birds_db, make_row

NOW = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
SPECIES = [("Northern Cardinal", "Cardinalis cardinalis"), ("Blue Jay", "Cyanocitta cristata"),
           ("American Goldfinch", "Spinus tristis")]
EE03 = {"X-Device-Id": "AA:AA:AA:00:00:03", "X-Panel": "ED103TC2 1404x1872 gray16",
        "X-Board": "XIAO ESP32-S3 Plus + EE03"}
EE02 = {"X-Device-Id": "BB:BB:BB:00:00:02", "X-Panel": "T133A01 1200x1600 spectra6",
        "X-Board": "XIAO ESP32-S3 Plus + EE02", "X-Battery-Voltage": "3.95",
        "X-Battery-Percent": "71", "X-Wifi-Rssi": "-60", "X-FF-Version": "1.9.0"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc._clock = lambda: NOW
    svc.config.quiet_hours_mode = "off"
    svc.config.mode = "single"
    svc.reload_config = lambda: None
    rows = []
    for i, (c, s) in enumerate(SPECIES):
        rows += [make_row(NOW - timedelta(minutes=40 - i * 10 + j), c, s, 0.9) for j in range(3)]
    svc.source.db_path = str(create_birds_db(tmp_path / "birds.db", rows))
    app.state.service = svc
    svc.tick()
    client = TestClient(app)
    assert client.get("/api/frame", headers=EE03).status_code == 200     # the first frame takes the seat
    return client


def _add(client):
    assert client.get("/api/frame", headers=EE02).status_code == 403     # it has to be answered first
    r = client.post("/api/frames", data={"id": EE02["X-Device-Id"], "action": "add"})
    assert r.json()["ok"]
    client.app.state.service.tick()


def _fff_size(body: bytes) -> tuple[int, int]:
    assert body[:4] == b"FFF1"
    return struct.unpack_from("<HH", body, 6)     # "<4sBBHHB5x": magic, version, bpp, w, h


def test_a_second_kit_is_added_beside_the_first_not_instead_of_it(client):
    svc = client.app.state.service
    wall = (svc._etag, svc._frame_bytes, svc.config.panel, svc.status()["device"]["last_checkin"])
    _add(client)
    view = svc.frames_view()
    assert view["active"]["id"] == EE03["X-Device-Id"] and not view["pending"]
    assert [f["id"] for f in view["added"]] == [EE02["X-Device-Id"]]
    # The wall's frame, panel and card are what they were.
    assert (svc._etag, svc._frame_bytes, svc.config.panel,
            svc.status()["device"]["last_checkin"]) == wall
    assert client.get("/api/frame", headers=EE03).content == wall[1]


def test_the_colour_kit_gets_the_collage_for_its_own_panel(client):
    """An EE02 starts as its panel does on a fresh install: on the collage,
    native portrait 1200x1600, inks, rotation 0 — while the wall shows plates."""
    svc = client.app.state.service
    _add(client)
    r = client.get("/api/frame", headers=EE02)
    assert r.status_code == 200 and r.headers["x-ff-rotation"] == "0"
    assert sorted(_fff_size(r.content)) == [1200, 1600]
    assert sorted(_fff_size(svc._frame_bytes)) == [1404, 1872]
    assert svc._meta["mode"] == "single" and svc.pictures["collage"].etag
    card = svc.frames_view()["added"][0]
    assert card["shows"] == "collage" and card["battery_percent"] == 71 and card["fw_version"] == "1.9.0"
    # It 304s on its own ETag, and the device card is still the wall's.
    etag = r.headers["etag"]
    assert client.get("/api/frame", headers={**EE02, "If-None-Match": etag}).status_code == 304
    assert svc.status()["device"]["battery_percent"] != 71


def test_its_settings_are_its_own(client):
    svc = client.app.state.service
    _add(client)
    before = client.get("/api/frame", headers=EE02).headers["etag"]
    fid = EE02["X-Device-Id"]
    r = client.post(f"/api/frames/{fid}", json={"shows": "plates", "panel_rotation": 180,
                                                "power_mode": "sleep", "name": "Study"})
    assert r.json()["ok"]
    svc.tick()
    r = client.get("/api/frame", headers=EE02)
    assert r.headers["etag"] != before and r.headers["x-ff-rotation"] == "180"
    assert r.headers["x-power-mode"] == "sleep"
    card = svc.frames_view()["added"][0]
    assert (card["shows"], card["rotation"], card["name"]) == ("plates", 180, "Study")
    # A rotation its panel cannot do is refused by the same rules as the page's.
    client.post(f"/api/frames/{fid}", json={"panel_rotation": 90})
    assert svc.frames_view()["added"][0]["rotation"] in (0, 180)
    # The household's config never moved.
    assert svc.config.panel == "ee03" and svc.config.mode == "single"
    assert svc.pictures["collage"].etag is None      # nobody shows it any more


def test_nothing_is_redrawn_until_its_picture_changes(client):
    svc = client.app.state.service
    _add(client)
    svc.tick()                 # a colour kit's first tick also asks for the colour twin
    drawn = dict(svc._added)
    svc.tick(); svc.tick()
    assert svc._added == drawn


def test_forgetting_it_takes_its_files_and_its_picture_with_it(client, tmp_path):
    svc = client.app.state.service
    _add(client)
    added = tmp_path / "data" / "frames" / "added"
    assert len(list(added.glob("*.fff"))) == 1
    client.post("/api/frames", data={"id": EE02["X-Device-Id"], "action": "forget"})
    svc.tick()
    assert list(added.glob("*")) == [] and svc._added == {}
    assert svc.pictures["collage"].etag is None
    assert client.get("/api/frame", headers=EE02).status_code == 403     # it asks again


def test_each_board_gets_its_own_firmware(client, tmp_path):
    data = tmp_path / "data"
    (data / "firmware.bin").write_bytes(b"\xe9" + b"..XIAO ESP32-S3 Plus + EE03.." * 4)
    (data / "firmware-ee02.bin").write_bytes(b"\xe9" + b"..XIAO ESP32-S3 Plus + EE02.." * 5)
    a = client.get("/api/firmware", headers={"X-Board": EE03["X-Board"]})
    b = client.get("/api/firmware", headers={"X-Board": EE02["X-Board"]})
    assert a.status_code == b.status_code == 200 and a.content != b.content
    assert b"EE02" in b.content and b"EE03" in a.content
    # Up to date is still up to date, per board.
    assert client.get("/api/firmware", headers={"X-Board": EE02["X-Board"],
                                                "X-Firmware-MD5": b.headers["x-md5"]}).status_code == 304
    # A board nobody built for is refused, as before.
    assert client.get("/api/firmware", headers={"X-Board": "Some Other Board"}).status_code == 404


def test_the_page_offers_to_add_it_and_then_lists_it(client):
    client.get("/api/frame", headers=EE02)
    html = client.get("/").text
    assert 'data-frame-action="add"' in html and "Replace the current frame" in html
    _add(client)
    html = client.get("/").text
    assert 'data-frame-action="add"' not in html
    row = html.split(f'data-added-frame="{EE02["X-Device-Id"]}"')[1].split("</li>")[0]
    assert "Spectra 6" in row and 'data-af="shows"' in row and 'data-af="panel_rotation"' in row
    # Its panel's own rotations, not the wall's.
    assert 'value="180"' in row and 'value="90"' not in row
    # Nothing shared is offered per frame.
    for shared in ("quiet_hours", "species_blocklist", "detection_backend", "imagegen"):
        assert shared not in row


def test_with_two_frames_the_first_is_named_too(client):
    """Its vitals sit at the top of the card; unnamed, the card reads as if
    the added frame were the only one."""
    assert "fc-active-name" not in client.get("/").text.split("<body")[1]    # one frame: the card as it was
    _add(client)
    body = client.get("/").text.split("<body")[1]
    named = body.split('class="fc-active-name"')[1].split("</div>\n        {% endif %}")[0][:600]
    assert "EE03" in named and "Plates" in named
    assert ">Frames<" in body


def test_both_frames_are_laid_out_the_same_way(client):
    """Name, Power and Wi-Fi tiles, Details, then settings: a second kit is
    not a lesser kind of row."""
    _add(client)
    client.get("/api/frame", headers=EE02)                 # it reports battery and Wi-Fi
    body = client.get("/").text.split("<body")[1]
    row = body.split(f'data-added-frame="{EE02["X-Device-Id"]}"')[1].split("</li>")[0]
    assert 'class="vitals' in row and ">Power<" in row and ">Wi-Fi<" in row
    assert "71%" in row and "Good" in row and "<summary>Details</summary>" in row
    assert "1.9.0" in row and EE02["X-Board"] in row
