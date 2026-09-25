"""Every frame owns its settings and its output (W-833).

There is no primary kit. A frame's bytes are the picture it shows, finished
with its own config, drawn in the tick and kept as one file. Two kits of the
same panel with different settings get different bytes; an EE03 on plates and
an EE02 on the collage get different pictures.
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta

import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe import frames as frames_mod
from featherframe.db import Database
from featherframe.render import framebuffer, pipeline
from tests._fixtures import create_birds_db, make_row
from tests._frames import EE02_PANEL, EE03_PANEL, add_kit, connect, device, pin_today

NOW = datetime(2026, 9, 22, 12, 0, 0)
SPECIES = [("Northern Cardinal", "Cardinalis cardinalis"), ("Blue Jay", "Cyanocitta cristata"),
           ("American Goldfinch", "Spinus tristis")]
EE03 = {"X-Device-Id": "AA:AA:AA:00:00:03", "X-Panel": EE03_PANEL}
EE02 = {"X-Device-Id": "BB:BB:BB:00:00:02", "X-Panel": EE02_PANEL}


def _heard(path):
    rows = []
    for i, (c, s) in enumerate(SPECIES):
        rows += [make_row(NOW - timedelta(minutes=40 - i * 10 + j), c, s, 0.9) for j in range(3)]
    return str(create_birds_db(path, rows))


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    pin_today(monkeypatch, NOW)     # the collage's day is NOW's, not the runner's


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    service = FeatherframeService()
    service._clock = lambda: NOW
    service.config.quiet_hours_mode = "off"
    service.reload_config = lambda: None
    service.source.db_path = _heard(tmp_path / "birds.db")
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app)


def _size(body: bytes) -> tuple:
    assert body[:4] == b"FFF1"
    return struct.unpack_from("<HH", body, 6)


# -- two frames, two outputs ---------------------------------------------------
def test_two_kits_of_the_same_panel_with_different_settings_get_different_bytes(svc):
    a = add_kit(svc, "AA:00", panel_rotation=90, mat_inset_pct=4.0)
    b = add_kit(svc, "BB:00", panel_rotation=270, mat_inset_pct=0.0)
    svc.tick()
    first, second = svc._out["AA:00"], svc._out["BB:00"]
    assert first["etag"] != second["etag"]
    assert svc._output_bytes("AA:00") != svc._output_bytes("BB:00")
    # Each is exactly what the pipeline packs for THAT frame's config.
    sheet_path = svc.picture_for(frames_mod.shows_of(a), NOW).sheet_path
    with Image.open(sheet_path) as sheet:
        sheet.load()
    for row, state in ((a, first), (b, second)):
        want = pipeline.render_image(sheet, svc.frame_config(row), "single", "")
        assert svc._output_bytes(str(row["id"])) == want.frame
        assert state["etag"] == want.etag


def test_each_frame_carries_its_own_headers(client, svc):
    add_kit(svc, EE03["X-Device-Id"], panel_rotation=270, power_mode="sleep",
            wake_interval_minutes=45)
    # On plates: the EE02's own default is the collage, whose checks follow
    # the collage's redraws rather than the row's interval (test_collage_checks).
    add_kit(svc, EE02["X-Device-Id"], panel=EE02_PANEL, power_mode="awake",
            device_poll_seconds=7, shows="plates")
    svc.tick()
    a = client.get("/api/frame", headers=EE03)
    b = client.get("/api/frame", headers=EE02)
    assert (a.headers["x-ff-rotation"], a.headers["x-power-mode"],
            a.headers["x-wake-minutes"]) == ("270", "sleep", "45")
    assert (b.headers["x-ff-rotation"], b.headers["x-power-mode"],
            b.headers["x-poll-seconds"]) == ("0", "awake", "7")
    assert a.headers["etag"] != b.headers["etag"]
    # Each 304s on its own ETag and nothing else's.
    assert client.get("/api/frame", headers={**EE03, "If-None-Match": a.headers["etag"]
                                             .strip('"')}).status_code == 304
    assert client.get("/api/frame", headers={**EE02, "If-None-Match": a.headers["etag"]
                                             .strip('"')}).status_code == 200


def test_an_ee03_on_plates_beside_an_ee02_on_the_collage(client, svc):
    add_kit(svc, EE03["X-Device-Id"], shows="plates")
    add_kit(svc, EE02["X-Device-Id"], panel=EE02_PANEL, shows="collage")
    svc.tick()
    assert svc.pictures["plates"].etag and svc.pictures["collage"].etag
    gray = client.get("/api/frame", headers=EE03)
    colour = client.get("/api/frame", headers=EE02)
    assert sorted(_size(gray.content)) == [1404, 1872]
    assert sorted(_size(colour.content)) == [1200, 1600]
    _, _, _, _, _, flags = framebuffer.HEADER.unpack_from(colour.content, 0)
    assert flags == framebuffer.FLAG_INKS
    # The colour kit's picture is the collage, so a new plate is not its news.
    before = colour.headers["etag"]
    svc._render_single(_det(), NOW, reason="test")
    svc.tick()
    assert client.get("/api/frame", headers=EE02).headers["etag"] == before
    assert client.get("/api/frame", headers=EE03).headers["etag"] != gray.headers["etag"]


def test_a_colour_kit_beside_a_gray_one_gets_the_pictures_colour_twin(svc):
    add_kit(svc, EE03["X-Device-Id"], shows="plates")
    add_kit(svc, EE02["X-Device-Id"], panel=EE02_PANEL, shows="plates")
    svc.tick()
    plates = svc.pictures["plates"]
    assert plates.has_color()                    # composed because a colour kit shows it
    assert svc._output_bytes(EE03["X-Device-Id"]) != svc._output_bytes(EE02["X-Device-Id"])


def test_nothing_is_redrawn_until_its_picture_or_its_settings_change(svc):
    add_kit(svc, "AA:00")
    svc.tick()
    drawn = dict(svc._out)
    svc.tick(); svc.tick()
    assert svc._out == drawn
    svc.update_frame("AA:00", {"mat_inset_pct": 1.5})
    svc.tick()
    assert svc._out["AA:00"]["etag"] != drawn["AA:00"]["etag"]


def test_nothing_renders_in_a_request_handler(client, svc):
    """Every render happens in the tick. A kit that has just been let in is
    503 until one runs — never a render on the request thread."""
    add_kit(svc, "AA:00")
    svc.tick()
    before = dict(svc._out)
    for _ in range(3):
        client.get("/api/frame", headers={"X-Device-Id": "AA:00", "X-Panel": EE03_PANEL})
    client.get("/")
    client.get("/api/status")
    assert svc._out == before
    assert client.get("/api/frame", headers={"X-Device-Id": "CC:00",
                                             "X-Panel": EE03_PANEL}).status_code == 403


def test_every_kit_asks_first_including_the_one_on_a_fresh_install(client, svc):
    """W-833: no screen connects by itself. The first kit on an empty server
    asks exactly as the second one does, and is served once it is added."""
    svc.tick()
    first = client.get("/api/frame", headers=EE03)
    assert first.status_code == 403 and first.headers["x-ff-frame"] == "pending"
    assert svc.frames.get(EE03["X-Device-Id"])["status"] == frames_mod.ASKING
    assert connect(client, EE03).status_code == 200
    assert svc.frames.get(EE03["X-Device-Id"])["status"] == frames_mod.ON
    r = client.get("/api/frame", headers=EE02)
    assert r.status_code == 403 and r.headers["x-ff-frame"] == "pending"
    assert svc.frames.get(EE02["X-Device-Id"])["status"] == frames_mod.ASKING
    client.post("/api/frames", data={"id": EE02["X-Device-Id"], "action": "add"})
    svc.tick()
    assert client.get("/api/frame", headers=EE02).status_code == 200


def test_a_legacy_frame_keeps_its_output_when_it_learns_its_id(client, svc):
    svc.tick()
    legacy = connect(client, {"X-Panel": EE03_PANEL})
    assert legacy.status_code == 200
    named = client.get("/api/frame", headers=EE03)
    assert named.status_code == 200 and named.content == legacy.content
    assert named.headers["etag"] == legacy.headers["etag"]
    assert svc.LEGACY_FRAME not in svc._out


# -- telemetry, per frame ------------------------------------------------------
def test_each_frames_battery_history_and_low_state_are_its_own(client, svc):
    add_kit(svc, EE03["X-Device-Id"])
    add_kit(svc, EE02["X-Device-Id"], panel=EE02_PANEL)
    svc.tick()
    client.get("/api/frame", headers={**EE03, "X-Battery-Voltage": "4.21",
                                      "X-Battery-Percent": "100"})
    client.get("/api/frame", headers={**EE02, "X-Battery-Voltage": "3.50",
                                      "X-Battery-Percent": "8"})
    gray = svc.db.battery_history("2000-01-01", EE03["X-Device-Id"])
    colour = svc.db.battery_history("2000-01-01", EE02["X-Device-Id"])
    assert [round(r["voltage"], 2) for r in gray] == [4.21]
    assert [round(r["voltage"], 2) for r in colour] == [3.50]
    cards = {f["id"]: f["card"] for f in svc.status()["frames"]["list"]}
    assert cards[EE03["X-Device-Id"]]["battery_critical"] is False
    assert cards[EE02["X-Device-Id"]]["battery_critical"] is True    # 3.50 < the EE02's hold
    assert svc.battery_view(24, EE02["X-Device-Id"])["items"][0]["percent"] == 8
    assert svc.battery_view(24, EE03["X-Device-Id"])["items"][0]["percent"] == 100


def test_the_frames_list_is_one_shape_for_a_kit_a_trmnl_and_a_page(client, svc):
    add_kit(svc, EE03["X-Device-Id"])
    svc.tick()
    client.get("/api/frame", headers={**EE03, "X-Battery-Voltage": "3.95",
                                      "X-Battery-Percent": "70", "X-Wifi-Rssi": "-60"})
    client.get("/api/display", headers={"ID": "AA:BB:CC:DD:EE:01", "Model": "x",
                                        "Width": "1872", "Height": "1404",
                                        "Battery-Voltage": "4.02"})
    client.get("/api/view/state?viewer=PAGE-IPAD&w=600&h=800&device=iPad")
    for viewer in ("AA:BB:CC:DD:EE:01", "PAGE-IPAD"):   # the owner adds them
        assert svc.answer_frame(viewer, "add")
    listed = {f["id"]: f for f in svc.status()["frames"]["list"]}
    assert set(listed) == {EE03["X-Device-Id"], "AA:BB:CC:DD:EE:01", "PAGE-IPAD"}
    keys = {"id", "name", "title", "transport", "status", "shows", "picture_etag",
            "output_etag", "capabilities", "settings", "reported", "last_seen",
            "last_seen_text", "card"}
    for row in listed.values():
        assert keys <= set(row)
        assert row["status"] == frames_mod.ON and row["title"]
        assert set(row["settings"]) == set(listed[EE03["X-Device-Id"]]["settings"])
    kit = listed[EE03["X-Device-Id"]]
    assert kit["transport"] == "kit" and kit["output_etag"] == svc._out[kit["id"]]["etag"]
    assert kit["settings"]["power_mode"] == "awake" and kit["capabilities"]["mat"]
    assert kit["card"]["battery_percent"] == 70
    trmnl = listed["AA:BB:CC:DD:EE:01"]
    assert trmnl["transport"] == "trmnl" and trmnl["output_etag"] is None
    assert trmnl["settings"]["format"] == "gray16" and not trmnl["capabilities"]["mat"]
    assert trmnl["card"]["battery"].startswith("4.02 V")
    page = listed["PAGE-IPAD"]
    assert page["transport"] == "page" and page["settings"]["format"] == "color"
    assert page["capabilities"]["colour"] and page["card"]["battery"] is None


# -- a server with nothing yet ---------------------------------------------------
def _start(db):
    from featherframe.service import FeatherframeService
    svc = FeatherframeService(db)
    svc._clock = lambda: NOW
    return svc


def test_a_fresh_install_lets_its_first_kit_in(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    pipeline.DITHER_OVERRIDE = "none"
    svc = _start(Database())
    assert svc.frames.all() == {} and svc._out == {}   # nothing to start from
    svc.source.db_path = str(tmp_path / "missing.db")
    svc._render_welcome(NOW, False)
    # Every kit asks, the first one included; the owner answers for one of them.
    assert svc.admit_frame("AA:BB", EE03_PANEL, None, None) == frames_mod.ASKING
    assert svc.admit_frame("CC:DD", EE03_PANEL, None, None) == frames_mod.ASKING
    assert svc.answer_frame("AA:BB", "add")
    assert svc.admit_frame("AA:BB", EE03_PANEL, None, None) == frames_mod.ON
    svc.tick()
    assert svc.get_frame("AA:BB", None)[0] == 200
    assert svc.get_frame("CC:DD", None)[0] == 503       # not served until it is answered for


def _det():
    from featherframe.sources import Detection
    return Detection(rowid=-1, date=NOW.strftime("%Y-%m-%d"), time=NOW.strftime("%H:%M:%S"),
                     common_name="Tufted Titmouse", scientific_name="Baeolophus bicolor",
                     confidence=0.95)


# -- the mat guide ---------------------------------------------------------------
def test_the_mat_guide_is_a_two_pixel_line_at_the_compositions_edge(svc):
    """`mat_guide` draws a black line just inside the composition, inset and
    offset included, so the mat can be set against it; off, nothing changes."""
    plain = add_kit(svc, "AA:00", mat_inset_pct=4.0, mat_offset_x_px=10, mat_guide=False)
    guided = add_kit(svc, "BB:00", mat_inset_pct=4.0, mat_offset_x_px=10, mat_guide=True)
    svc.tick()
    assert svc._output_bytes("AA:00") != svc._output_bytes("BB:00")
    sheet_path = svc.picture_for(frames_mod.shows_of(plain), NOW).sheet_path
    with Image.open(sheet_path) as sheet:
        sheet.load()
    off = pipeline.render_image(sheet, svc.frame_config(plain), "single", "").preview
    on = pipeline.render_image(sheet, svc.frame_config(guided), "single", "").preview
    w, h = on.size
    sw, sh = round(w * 0.92), round(h * 0.92)
    x0, y0 = (w - sw) // 2 + 10, (h - sh) // 2
    px = on.load()
    # The line: two pixels deep along every edge of the shrunk composition.
    assert px[x0, y0 + sh // 2] == 0 and px[x0 + 1, y0 + sh // 2] == 0
    assert px[x0 + sw - 1, y0 + sh // 2] == 0 and px[x0 + sw // 2, y0] == 0
    assert px[x0 + sw // 2, y0 + sh - 1] == 0
    # Just outside it is the mat ring, and the plain render has no line.
    assert px[x0 - 1, y0 + sh // 2] != 0
    assert off.load()[x0, y0 + sh // 2] != 0
    # The row saves it like any other setting, and the output follows.
    assert svc.update_frame("AA:00", {"mat_guide": True})
    assert svc.frames.get("AA:00")["set"]["mat_guide"] is True
    svc.tick()
    assert svc._output_bytes("AA:00") == svc._output_bytes("BB:00")
