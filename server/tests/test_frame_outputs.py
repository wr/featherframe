"""Every frame owns its settings and its output (W-833 step 2b).

There is no primary kit any more. A frame's bytes are the picture it shows,
finished with its own config, drawn in the tick and kept as one file. Two kits
of the same panel with different settings get different bytes; an EE03 on
plates and an EE02 on the collage get different pictures. The upgrade from the
single-frame build must not repaint anything.
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta

import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe import frames as frames_mod
from featherframe import paths
from featherframe.config import Config
from featherframe.db import Database
from featherframe.render import framebuffer, pipeline, theme
from tests._fixtures import create_birds_db, make_row
from tests._frames import EE02_PANEL, EE03_PANEL, add_kit, connect, device

NOW = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
SPECIES = [("Northern Cardinal", "Cardinalis cardinalis"), ("Blue Jay", "Cyanocitta cristata"),
           ("American Goldfinch", "Spinus tristis")]
EE03 = {"X-Device-Id": "AA:AA:AA:00:00:03", "X-Panel": EE03_PANEL}
EE02 = {"X-Device-Id": "BB:BB:BB:00:00:02", "X-Panel": EE02_PANEL}


def _heard(path):
    rows = []
    for i, (c, s) in enumerate(SPECIES):
        rows += [make_row(NOW - timedelta(minutes=40 - i * 10 + j), c, s, 0.9) for j in range(3)]
    return str(create_birds_db(path, rows))


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
    add_kit(svc, EE02["X-Device-Id"], panel=EE02_PANEL, power_mode="awake",
            device_poll_seconds=7)
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


# -- the upgrade from the single-frame build -----------------------------------
def _legacy_install(tmp_path, monkeypatch, panel: str, mode: str, rotation: int,
                    sheet: Image.Image, settings=None):
    """A DB and a data dir exactly as the build before this one left them."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    pipeline.DITHER_OVERRIDE = "none"
    cfg = Config.from_dict({"panel": panel, "mode": mode, "panel_rotation": rotation,
                            **(settings or {})})
    result = pipeline.render_image(sheet, cfg, "collage" if mode == "collage" else "single",
                                   "Blue Jay")
    frames_dir = paths.frames_dir()
    (frames_dir / "current.fff").write_bytes(result.frame)
    result.preview.save(frames_dir / "current.png")
    sheet.save(frames_dir / "current_sheet.png")
    db = Database()
    db.set("config", cfg.to_dict())
    db.set("current_frame", {"etag": result.etag, "mode": cfg.mode, "label": "Blue Jay",
                             "species_key": None if mode == "collage" else "cyanocitta cristata",
                             "rendered_at": NOW.isoformat(timespec="seconds")})
    reported = {"ee02": EE02_PANEL, "ee03": EE03_PANEL}[panel]
    db.set("frames", {"active": "AA:BB", "known": {
        "AA:BB": {"id": "AA:BB", "status": "active", "panel": reported, "ip": "10.0.1.10",
                  "first_seen": "2026-09-01T09:00:00", "last_seen": "2026-09-20T09:00:00",
                  "device": {"battery_voltage": 3.9, "battery_percent": 64,
                             "last_checkin": "2026-09-20T09:00:00", "fw_version": "1.9.0"}}}})
    return db, result, cfg


def _start(db):
    from featherframe.service import FeatherframeService
    svc = FeatherframeService(db)
    svc._clock = lambda: NOW
    return svc


@pytest.mark.parametrize("panel,mode,rotation,rgb", [
    ("ee03", "single", 90, False),
    ("ee03", "collage", 270, False),
    ("ee02", "collage", 0, True),
])
def test_the_migrated_frame_is_served_the_very_same_bytes(tmp_path, monkeypatch,
                                                          panel, mode, rotation, rgb):
    """Pixel identity across the upgrade: the wall must not repaint because
    the server learned a new way to name what it already had."""
    sheet = Image.new("RGB" if rgb else "L", (theme.WIDTH, theme.HEIGHT),
                      (200, 120, 60) if rgb else 200)
    db, result, cfg = _legacy_install(tmp_path, monkeypatch, panel, mode, rotation, sheet)
    svc = _start(db)
    assert svc._out["AA:BB"]["etag"] == result.etag
    assert svc._output_bytes("AA:BB") == result.frame
    assert svc._etag == result.etag                 # the picture is named by those bytes
    # And a tick does not redraw it: the settings and the picture are the same.
    svc.tick()
    assert svc._output_bytes("AA:BB") == result.frame
    # Re-finishing the adopted sheet with the frame's own config is byte-identical,
    # which is what makes the adoption safe in the first place.
    with Image.open(svc.pictures[svc._shown].sheet_path) as kept:
        kept.load()
    again = pipeline.render_image(kept, svc.frame_config(svc.frames.get("AA:BB")),
                                  "single", "Blue Jay")
    assert again.frame == result.frame


def test_the_migration_moves_settings_telemetry_and_the_battery_log(tmp_path, monkeypatch):
    sheet = Image.new("L", (theme.WIDTH, theme.HEIGHT), 200)
    db, _result, cfg = _legacy_install(
        tmp_path, monkeypatch, "ee03", "collage", 270, sheet,
        settings={"mat_inset_pct": 3.5, "mat_offset_x_px": -10, "power_mode": "sleep",
                  "wake_interval_minutes": 45, "device_poll_seconds": 9})
    db.log_battery("2026-09-20T08:00:00", 3.88, 62)          # written before frames had ids
    svc = _start(db)
    row = svc.frames.get("AA:BB")
    assert frames_mod.LEGACY_PRIMARY not in row
    assert row["set"]["shows"] == "collage"                  # from the old config.mode
    assert (row["set"]["mat_inset_pct"], row["set"]["mat_offset_x_px"]) == (3.5, -10)
    assert (row["set"]["power_mode"], row["set"]["wake_interval_minutes"]) == ("sleep", 45)
    assert row["set"]["panel_rotation"] == 270 and row["set"]["device_poll_seconds"] == 9
    # Its telemetry is on its row, and the battery log is its own.
    assert frames_mod.reported_of(row)["battery_percent"] == 64
    assert svc.db.battery_history("2000-01-01", "AA:BB")[0]["voltage"] == pytest.approx(3.88)
    assert svc.db.battery_history("2000-01-01") == []
    assert device(svc, "AA:BB").fw_version == "1.9.0"
    # Everything the frame is drawn with now comes off its row.
    cfg = svc.frame_config(row)
    assert (cfg.panel_rotation, cfg.mat_inset_pct, cfg.power_mode) == (270, 3.5, "sleep")


def test_migrating_again_changes_nothing(tmp_path, monkeypatch):
    sheet = Image.new("L", (theme.WIDTH, theme.HEIGHT), 200)
    db, result, _cfg = _legacy_install(tmp_path, monkeypatch, "ee03", "single", 90, sheet)
    svc = _start(db)
    before = (svc.frames.all(), dict(svc._out))
    # The owner turns it the other way up; a restart must not undo that.
    svc.update_frame("AA:BB", {"panel_rotation": 270})
    again = _start(Database())
    assert again.frames.get("AA:BB")["set"]["panel_rotation"] == 270
    assert again._output_bytes("AA:BB") == result.frame
    assert before[0] != again.frames.all()


def test_a_fresh_install_migrates_to_nothing_and_lets_its_first_kit_in(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    pipeline.DITHER_OVERRIDE = "none"
    svc = _start(Database())
    assert svc.frames.all() == {} and svc._out == {}
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


def test_a_frame_that_never_named_its_panel_keeps_the_one_the_config_knew(tmp_path,
                                                                          monkeypatch):
    """Firmware too old to send X-Panel: the config knew which panel that
    frame has, and it must go on being drawn for it."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    pipeline.DITHER_OVERRIDE = "none"
    db = Database()
    db.set("config", Config.from_dict({"panel": "ee02", "mode": "collage"}).to_dict())
    db.set("frames", {"active": "AA:BB", "known": {"AA:BB": {"id": "AA:BB", "status": "active"}}})
    svc = _start(db)
    assert svc.frame_config(svc.frames.get("AA:BB")).panel == "ee02"
    # Its own report still wins the moment it sends one.
    svc.admit_frame("AA:BB", EE03_PANEL, None, None)
    assert svc.frame_config(svc.frames.get("AA:BB")).panel == "ee03"


def test_a_legacy_id_frame_migrates_in_place(tmp_path, monkeypatch):
    """Firmware from before X-Device-Id was the frame called "legacy"; its
    settings and its framebuffer move onto that row like any other."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    pipeline.DITHER_OVERRIDE = "none"
    sheet = Image.new("L", (theme.WIDTH, theme.HEIGHT), 180)
    cfg = Config.from_dict({"panel": "ee03", "mode": "single", "panel_rotation": 270})
    result = pipeline.render_image(sheet, cfg, "single", "Blue Jay")
    (paths.frames_dir() / "current.fff").write_bytes(result.frame)
    sheet.save(paths.frames_dir() / "current_sheet.png")
    db = Database()
    db.set("config", cfg.to_dict())
    db.set("current_frame", {"etag": result.etag, "mode": "single", "label": "Blue Jay",
                             "species_key": "cyanocitta cristata"})
    db.set("frames", {"active": "legacy", "known": {
        "legacy": {"id": "legacy", "status": "active", "first_seen": "2026-09-01T09:00:00"}}})
    svc = _start(db)
    assert svc.frames.get("legacy")["set"]["panel_rotation"] == 270
    assert svc._output_bytes("legacy") == result.frame
    svc.tick()
    assert svc._output_bytes("legacy") == result.frame


def _det():
    from featherframe.sources import Detection
    return Detection(rowid=-1, date=NOW.strftime("%Y-%m-%d"), time=NOW.strftime("%H:%M:%S"),
                     common_name="Tufted Titmouse", scientific_name="Baeolophus bicolor",
                     confidence=0.95)
