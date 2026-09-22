"""Several frames on one server. There is no primary and no "added" frame
(W-833): every kit that is on is drawn for the same way, from the same
pictures, finished for its own panel, showing plates or the collage. An EE03 on
plates and an EE02 on the collage is the case it exists for."""
from __future__ import annotations

import struct
from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe.render import pipeline
from tests._fixtures import create_birds_db, make_row
from tests._frames import connect, frame_bytes

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
    svc.reload_config = lambda: None
    rows = []
    for i, (c, s) in enumerate(SPECIES):
        rows += [make_row(NOW - timedelta(minutes=40 - i * 10 + j), c, s, 0.9) for j in range(3)]
    svc.source.db_path = str(create_birds_db(tmp_path / "birds.db", rows))
    app.state.service = svc
    svc.tick()
    client = TestClient(app)
    assert connect(client, EE03).status_code == 200   # the first frame is let in by itself
    return client


def _add(client):
    assert client.get("/api/frame", headers=EE02).status_code == 403     # it has to be answered first
    r = client.post("/api/frames", data={"id": EE02["X-Device-Id"], "action": "add"})
    assert r.json()["ok"]
    client.app.state.service.tick()


def _row(svc, frame_id: str) -> dict:
    return [f for f in svc.frames_list() if f["id"] == frame_id][0]


def _fff_size(body: bytes) -> tuple[int, int]:
    assert body[:4] == b"FFF1"
    return struct.unpack_from("<HH", body, 6)     # "<4sBBHHB5x": magic, version, bpp, w, h


def test_a_second_kit_is_added_beside_the_first_not_instead_of_it(client):
    svc = client.app.state.service
    first = EE03["X-Device-Id"]
    wall = (frame_bytes(svc, first), _row(svc, first)["card"]["last_checkin_iso"])
    _add(client)
    ids = [f["id"] for f in svc.frames_list() if f["status"] == "on"]
    assert ids == [first, EE02["X-Device-Id"]]
    assert not [f for f in svc.frames_list() if f["status"] == "asking"]
    # The first frame's bytes and its check-in record are what they were.
    assert (frame_bytes(svc, first), _row(svc, first)["card"]["last_checkin_iso"]) == wall
    assert client.get("/api/frame", headers=EE03).content == wall[0]


def test_the_colour_kit_gets_the_collage_for_its_own_panel(client):
    """An EE02 starts as its panel does on a fresh install: on the collage,
    native portrait 1200x1600, inks, rotation 0 — while the EE03 shows plates."""
    svc = client.app.state.service
    _add(client)
    r = client.get("/api/frame", headers=EE02)
    assert r.status_code == 200 and r.headers["x-ff-rotation"] == "0"
    assert sorted(_fff_size(r.content)) == [1200, 1600]
    assert sorted(_fff_size(frame_bytes(svc, EE03["X-Device-Id"]))) == [1404, 1872]
    assert svc._meta["mode"] == "single" and svc.pictures["collage"].etag
    row = _row(svc, EE02["X-Device-Id"])
    assert row["shows"] == "collage" and row["card"]["battery_percent"] == 71
    assert row["details"]["firmware"] == "1.9.0"
    # It 304s on its own ETag, and the other frame's telemetry is its own.
    etag = r.headers["etag"]
    assert client.get("/api/frame", headers={**EE02, "If-None-Match": etag}).status_code == 304
    assert _row(svc, EE03["X-Device-Id"])["card"]["battery_percent"] != 71


def test_its_settings_are_its_own(client):
    svc = client.app.state.service
    _add(client)
    before = client.get("/api/frame", headers=EE02).headers["etag"]
    fid = EE02["X-Device-Id"]
    r = client.post(f"/api/frames/{fid}", json={"shows": "plates", "rotation": 180,
                                                "power_mode": "sleep", "name": "Study"})
    assert r.json()["ok"]
    svc.tick()
    r = client.get("/api/frame", headers=EE02)
    assert r.headers["etag"] != before and r.headers["x-ff-rotation"] == "180"
    assert r.headers["x-power-mode"] == "sleep"
    row = _row(svc, fid)
    assert (row["shows"], row["settings"]["rotation"], row["name"]) == ("plates", 180, "Study")
    # A rotation its panel cannot do is refused by the same rules as the page's.
    client.post(f"/api/frames/{fid}", json={"rotation": 90})
    assert _row(svc, fid)["settings"]["rotation"] in (0, 180)
    # The other frame's settings never moved.
    first = svc.frame_config(svc.frames.get(EE03["X-Device-Id"]))
    assert first.panel == "ee03" and first.panel_rotation == 90
    assert svc.pictures["collage"].etag is None      # nobody shows it any more


def test_nothing_is_redrawn_until_its_picture_changes(client):
    svc = client.app.state.service
    _add(client)
    svc.tick()                 # a colour kit's first tick also asks for the colour twin
    drawn = dict(svc._out)
    svc.tick(); svc.tick()
    assert svc._out == drawn


def test_forgetting_it_takes_its_files_and_its_picture_with_it(client, tmp_path):
    svc = client.app.state.service
    _add(client)
    out = tmp_path / "data" / "frames" / "out"
    assert len(list(out.glob("*.fff"))) == 2      # one per frame
    client.post("/api/frames", data={"id": EE02["X-Device-Id"], "action": "forget"})
    svc.tick()
    assert len(list(out.glob("*.fff"))) == 1 and EE02["X-Device-Id"] not in svc._out
    assert svc.pictures["collage"].etag is None
    assert client.get("/api/frame", headers=EE02).status_code == 403     # it asks again


def test_remove_on_the_row_forgets_it_too(client):
    """The Frames card's Remove is the same answer, posted to the frame."""
    svc = client.app.state.service
    _add(client)
    fid = EE02["X-Device-Id"]
    assert client.post(f"/api/frames/{fid}", json={"forget": True}).json()["ok"]
    assert svc.frames.get(fid) is None and fid not in svc._out
    assert client.get("/api/frame", headers=EE02).status_code == 403


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
    assert 'data-frame-action="add"' in html
    # There is no current frame, so there is nothing to replace.
    assert "Replace the current frame" not in html
    _add(client)
    html = client.get("/").text
    assert 'data-frame-action="add"' not in html
    row = html.split('data-frame="%s"' % EE02["X-Device-Id"])[1].split(chr(10) + "    </li>")[0]
    assert "Spectra 6" in row and 'data-f="shows"' in row and 'data-f="rotation"' in row
    # Its panel's own rotations, not the other frame's.
    assert 'value="180"' in row and 'value="90"' not in row
    # Nothing shared is offered per frame.
    for shared in ("quiet_hours", "species_blocklist", "detection_backend", "imagegen"):
        assert shared not in row


def test_both_frames_are_the_same_row(client):
    """Name, Content, Rotation, Power, Advanced, Details, Save, Remove: the
    first kit is not a different kind of thing from the second."""
    _add(client)
    client.get("/api/frame", headers=EE02)                 # it reports its Wi-Fi
    body = client.get("/").text.split("<body")[1]
    rows = [body.split('data-frame="%s"' % h["X-Device-Id"])[1].split(chr(10) + "    </li>")[0]
            for h in (EE03, EE02)]
    for row in rows:
        for want in ('data-f="name"', 'data-f="shows"', 'data-f="rotation"',
                     'data-f="power_mode"', 'data-f="mat_inset_pct"',
                     'data-fr-action="save"', 'data-fr-action="forget"'):
            assert want in row
        # …and each carries its own health on the row, over its own Details.
        for want in ('data-h="dot"', 'data-h="seen-text"',
                     'data-h="wifi-wrap"', "<span>Details</span>", ">Frame ID<"):
            assert want in row, want
    ee02 = rows[1]
    assert "Good" in ee02 and "1.9.0" in ee02 and EE02["X-Board"] in ee02
