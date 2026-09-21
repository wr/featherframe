"""One registry for every screen (W-833, step 1). The three stores that grew
separately — the `frames` kv, the `viewers` kv, and the active frame's settings
in Config — become one row shape behind `frames.FrameRegistry`, and what a
screen can be asked for is derived from what it reported, never typed per model.

Behaviour must not change here, so most of the proof is the other 600-odd tests
still passing. What is new and needs its own: the migration, the capability
rules, renaming any frame, and that the legacy keys are dead once migrated.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe import frames, panels
from featherframe.config import Config
from featherframe.db import Database


# -- migration ----------------------------------------------------------------
LEGACY_FRAMES = {
    "active": "AA:AA:AA:00:00:03",
    "known": {
        "AA:AA:AA:00:00:03": {
            "id": "AA:AA:AA:00:00:03", "status": "active", "ip": "10.0.1.10",
            "panel": "ED103TC2 1404x1872 gray16", "board": "XIAO ESP32-S3 Plus + EE03",
            "first_seen": "2026-09-01T09:00:00", "last_seen": "2026-09-20T09:00:00",
            "device": {"battery_voltage": 3.9, "battery_percent": 64, "fw_version": "1.9.0"}},
        "BB:BB:BB:00:00:02": {
            "id": "BB:BB:BB:00:00:02", "status": "added", "ip": "10.0.1.11",
            "panel": "T133A01 1200x1600 spectra6", "board": "XIAO ESP32-S3 Plus + EE02",
            "set": {"shows": "collage", "panel_rotation": 180, "name": "Study"},
            "device": {"battery_percent": 71, "last_result": "frame"},
            "first_seen": "2026-09-10T09:00:00", "last_seen": "2026-09-20T08:00:00"},
        "CC:CC:CC:00:00:01": {
            "id": "CC:CC:CC:00:00:01", "status": "pending", "panel": "GDEY075 DIY",
            "facts": {"w": "800", "h": "480", "fmt": "gray16", "rot": "90,270"},
            "first_seen": "2026-09-19T09:00:00", "last_seen": "2026-09-19T09:00:00"},
        "DD:DD:DD:00:00:00": {
            "id": "DD:DD:DD:00:00:00", "status": "ignored", "panel": "mystery panel",
            "first_seen": "2026-09-18T09:00:00", "last_seen": "2026-09-18T09:00:00"},
    },
}
LEGACY_VIEWERS = {
    "AA:BB:CC:DD:EE:01": {
        "id": "AA:BB:CC:DD:EE:01", "kind": "trmnl", "token": "abc123", "ip": "10.0.1.20",
        "reported": {"model": "x", "width": 1872, "height": 1404, "battery_volts": 4.0},
        "set": {"rotation": 90}, "first_seen": "2026-09-05T09:00:00",
        "last_seen": "2026-09-20T07:00:00"},
    "AA:BB:CC:DD:EE:02": {
        "id": "AA:BB:CC:DD:EE:02", "kind": "page", "token": "def456",
        "reported": {"width": 2048, "height": 1536, "model": "iPad"},
        "set": {"name": "Kitchen", "dark_quiet": False},
        "first_seen": "2026-09-06T09:00:00", "last_seen": "2026-09-20T07:30:00"},
}


@pytest.fixture
def migrated(tmp_path) -> Database:
    db = Database(str(tmp_path / "ff.db"))
    db.set("frames", LEGACY_FRAMES)
    db.set("viewers", LEGACY_VIEWERS)
    frames.FrameRegistry(db).migrate()
    return db


def test_the_active_frame_becomes_the_primary_kit(migrated):
    row = frames.FrameRegistry(migrated).primary()
    assert row["id"] == "AA:AA:AA:00:00:03"
    assert (row["transport"], row["status"], row["primary"]) == ("kit", frames.ON, True)
    assert frames.seat(row) == "active"
    # Panel, board and telemetry lived in three places on the old row; they are
    # all just what the device reported.
    rep = frames.reported_of(row)
    assert rep["board"] == "XIAO ESP32-S3 Plus + EE03" and rep["battery_percent"] == 64
    assert frames.panel_of(row) is panels.EE03
    assert row["ip"] == "10.0.1.10" and row["first_seen"] == "2026-09-01T09:00:00"


def test_an_added_frame_keeps_its_choices_and_its_telemetry(migrated):
    row = frames.FrameRegistry(migrated).get("BB:BB:BB:00:00:02")
    assert (row["status"], row["primary"]) == (frames.ON, False)
    assert frames.seat(row) == "added"
    assert row["set"] == {"shows": "collage", "panel_rotation": 180, "name": "Study"}
    assert frames.name_of(row) == "Study"
    assert frames.reported_of(row)["battery_percent"] == 71
    assert frames.panel_of(row) is panels.EE02


def test_pending_and_ignored_frames_keep_their_answer(migrated):
    reg = frames.FrameRegistry(migrated)
    asking = reg.get("CC:CC:CC:00:00:01")
    assert asking["status"] == frames.ASKING and frames.seat(asking) == "pending"
    assert frames.panel_of(asking).key == "custom:800x480:gray16:90,270"
    ignored = reg.get("DD:DD:DD:00:00:00")
    assert ignored["status"] == frames.IGNORED and frames.seat(ignored) == "ignored"
    assert frames.panel_of(ignored) is None      # it named no panel we know
    assert [r["id"] for r in reg.added()] == ["BB:BB:BB:00:00:02"]


def test_viewers_become_frames_fed_another_way(migrated):
    reg = frames.FrameRegistry(migrated)
    trmnl = reg.get("AA:BB:CC:DD:EE:01")
    assert (trmnl["transport"], trmnl["status"], trmnl["primary"]) == ("trmnl", frames.ON, False)
    assert trmnl["token"] == "abc123" and trmnl["ip"] == "10.0.1.20"
    assert trmnl["reported"]["model"] == "x" and trmnl["set"] == {"rotation": 90}
    page = reg.get("AA:BB:CC:DD:EE:02")
    assert page["transport"] == "page" and page["set"]["name"] == "Kitchen"
    assert {r["id"] for r in reg.by_transport("trmnl", "page")} == set(LEGACY_VIEWERS)
    assert len(reg.all()) == 6


def test_migrating_again_changes_nothing(migrated):
    reg = frames.FrameRegistry(migrated)
    before = reg.all()
    reg.rename("AA:AA:AA:00:00:03", "Hallway")
    frames.FrameRegistry(migrated).migrate()
    after = frames.FrameRegistry(migrated).all()
    assert after != before                       # the rename is still there
    assert frames.name_of(after["AA:AA:AA:00:00:03"]) == "Hallway"


def test_a_fresh_install_migrates_to_an_empty_registry(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    reg = frames.FrameRegistry(db)
    reg.migrate()
    assert reg.all() == {} and reg.primary() is None and reg.added() == []
    assert db.get("frames") is None and db.get("viewers") is None


def test_junk_in_the_legacy_stores_does_not_stop_the_migration(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    db.set("frames", {"active": "X", "known": "not a dict"})
    db.set("viewers", ["not", "a", "dict"])
    frames.FrameRegistry(db).migrate()
    assert frames.FrameRegistry(db).all() == {}


# -- the capability rules -------------------------------------------------------
def _kit(panel=None, facts=None, **reported) -> dict:
    row = frames.new_row("AA:AA:AA:00:00:03", "kit", "2026-09-20T09:00:00", frames.ON)
    row["reported"] = {k: v for k, v in
                       dict(panel=panel, facts=facts, **reported).items() if v is not None}
    return row


def test_a_gray_kit_has_a_mat_a_power_model_and_its_panels_rotations():
    caps = frames.capabilities(_kit("ED103TC2 1404x1872 gray16", battery_voltage=3.9))
    assert caps["rotations"] == (90, 270) and caps["mat"] and caps["power"]
    assert caps["renamable"] and caps["shows"]
    assert not caps["colour"] and not caps["look"] and not caps["dark_quiet"]
    assert caps["has_battery"] and not caps["needs_size"]


def test_a_colour_kit_is_colour_and_hangs_the_other_way_up():
    caps = frames.capabilities(_kit("T133A01 1200x1600 spectra6"))
    assert caps["colour"] and caps["rotations"] == (0, 180)
    assert not caps["has_battery"]               # it has said nothing about a battery yet


def test_a_custom_panel_answers_from_its_own_facts():
    row = _kit("GDEY075 DIY", {"w": "800", "h": "480", "fmt": "spectra6", "rot": "90,270"})
    caps = frames.capabilities(row)
    assert caps["rotations"] == (90, 270) and caps["colour"] and caps["mat"]


def test_a_kit_that_has_not_reported_yet_falls_back_to_the_config():
    """The wall frame on a fresh install: its panel is still in the config."""
    row = frames.new_row("legacy", "kit", "2026-09-20T09:00:00", frames.ON)
    cfg = Config(panel="ee02").sanitize()
    assert frames.capabilities(row, cfg)["rotations"] == (0, 180)
    assert frames.capabilities(row, cfg)["colour"]
    assert frames.capabilities(row)["rotations"] == panels.DEFAULT.rotations


def test_a_trmnl_that_reported_its_size_needs_nothing_from_the_owner():
    row = frames.new_row("AA:BB:CC:DD:EE:01", "trmnl", "2026-09-20T09:00:00", frames.ON)
    row["reported"] = {"model": "x", "width": 1872, "height": 1404, "battery_volts": 4.0}
    caps = frames.capabilities(row)
    assert not caps["needs_size"] and caps["has_battery"]
    assert caps["rotations"] == (0, 90, 180, 270)     # turned in the render, not by the glass
    assert not caps["mat"] and not caps["power"] and not caps["colour"]
    assert not caps["dark_quiet"] and not caps["look"]


def test_a_trmnl_that_reported_no_size_has_to_be_told():
    row = frames.new_row("AA:BB:CC:DD:EE:03", "trmnl", "2026-09-20T09:00:00", frames.ON)
    row["reported"] = {"battery_percent": 80}
    assert frames.capabilities(row)["needs_size"]
    assert frames.capabilities(row)["has_battery"]


def test_a_page_is_lit_turns_itself_and_has_no_battery():
    row = frames.new_row("AA:BB:CC:DD:EE:02", "page", "2026-09-20T09:00:00", frames.ON)
    row["reported"] = {"width": 2048, "height": 1536, "model": "iPad"}
    caps = frames.capabilities(row)
    assert caps["rotations"] == () and caps["colour"] and caps["look"] and caps["dark_quiet"]
    assert not caps["mat"] and not caps["power"] and not caps["has_battery"]
    assert not caps["needs_size"] and caps["renamable"] and caps["shows"]


# -- renaming, and the legacy keys after the migration -----------------------------
EE03 = {"X-Device-Id": "AA:AA:AA:00:00:03", "X-Panel": "ED103TC2 1404x1872 gray16",
        "X-Board": "XIAO ESP32-S3 Plus + EE03"}
EE02 = {"X-Device-Id": "BB:BB:BB:00:00:02", "X-Panel": "T133A01 1200x1600 spectra6",
        "X-Board": "XIAO ESP32-S3 Plus + EE02"}
TRMNL = {"ID": "AA:BB:CC:DD:EE:01", "Model": "og", "Width": "800", "Height": "480"}
SAME_ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    app.state.service = svc
    svc._render_welcome(svc._clock(), False)
    client = TestClient(app)
    assert client.get("/api/frame", headers=EE03).status_code == 200
    assert client.get("/api/frame", headers=EE02).status_code == 403
    client.post("/api/frames", data={"id": EE02["X-Device-Id"], "action": "add"},
                headers=SAME_ORIGIN)
    client.get("/api/setup", headers=TRMNL)
    return client


def _name(client, frame_id: str, name: str):
    return client.post(f"/api/frames/{frame_id}", json={"name": name}, headers=SAME_ORIGIN)


def test_the_wall_frame_can_be_named_too(client):
    svc = client.app.state.service
    assert svc.frames_view()["active"]["name"] == ""
    assert _name(client, EE03["X-Device-Id"], "Hallway").json()["ok"]
    view = svc.frames_view()
    assert view["active"]["name"] == "Hallway" and "EE03" in view["active"]["panel_name"]
    # The page shows the name where it showed the panel, and only once named.
    assert "Hallway" in client.get("/").text
    assert _name(client, EE03["X-Device-Id"], "  ").json()["ok"]
    assert svc.frames_view()["active"]["name"] == ""


def test_an_added_frame_is_still_renamed_with_the_rest_of_its_settings(client):
    svc = client.app.state.service
    r = client.post(f"/api/frames/{EE02['X-Device-Id']}",
                    json={"name": "Study", "panel_rotation": 180}, headers=SAME_ORIGIN)
    assert r.json()["ok"]
    card = svc.frames_view()["added"][0]
    assert card["name"] == "Study" and card["rotation"] == 180


def test_a_viewer_is_renamed_by_either_route(client):
    svc = client.app.state.service
    assert _name(client, TRMNL["ID"], "Desk").json()["ok"]
    assert svc.viewers.get(TRMNL["ID"])["set"]["name"] == "Desk"
    r = client.post(f"/api/viewers/{TRMNL['ID']}", json={"name": "Bench"}, headers=SAME_ORIGIN)
    assert r.json()["viewer"]["name"] == "Bench"
    assert client.get("/api/viewers").json()["viewers"][0]["name"] == "Bench"


def test_renaming_a_frame_nobody_has_heard_of_is_a_404(client):
    assert _name(client, "ZZ:ZZ:ZZ:ZZ:ZZ:ZZ", "Nowhere").status_code == 404


def test_a_viewer_claiming_a_frames_id_does_not_take_its_seat(client):
    """One registry means one id space. The kit is being served; an unasked
    HTTP request must not unseat it."""
    svc = client.app.state.service
    before = svc.frames.get(EE03["X-Device-Id"])
    r = client.get("/api/setup", headers={"ID": EE03["X-Device-Id"], "Model": "og"})
    assert r.status_code == 200                      # it is still answered
    assert svc.frames.get(EE03["X-Device-Id"]) == before
    assert svc.frames_view()["active"]["id"] == EE03["X-Device-Id"]
    assert svc.viewers.get(EE03["X-Device-Id"]) is None


@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    """The upgrade an owner actually runs: a DB whose rows were written by the
    build before this one."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    db = Database(str(tmp_path / "ff.db"))
    db.set("frames", LEGACY_FRAMES)
    db.set("viewers", LEGACY_VIEWERS)
    svc = FeatherframeService(db)
    svc.source.db_path = str(tmp_path / "missing.db")
    app.state.service = svc
    svc._render_welcome(svc._clock(), False)
    return TestClient(app)


def test_a_migrated_install_keeps_serving_the_frame_it_was_serving(upgraded):
    """The wall frame must not be asked to connect again, and the second kit
    must not have to be added a second time."""
    svc = upgraded.app.state.service
    assert upgraded.get("/api/frame", headers=EE03).status_code == 200
    assert svc.frames_view()["active"]["id"] == EE03["X-Device-Id"]
    assert [f["name"] for f in svc.frames_view()["added"]] == ["Study"]
    # The added kit is served its own frame, not a 403: no tick has drawn it
    # yet, so it is 503 until one does — never "not this server's".
    assert upgraded.get("/api/frame", headers=EE02).status_code == 503
    svc.tick()
    assert upgraded.get("/api/frame", headers=EE02).status_code == 200
    assert len(upgraded.get("/api/viewers").json()["viewers"]) == 2


def test_nothing_writes_the_legacy_keys_after_the_migration(upgraded):
    """The registry is the only truth. The old blobs stay for a rollback, but
    check-ins, answers and renames must not touch them."""
    svc = upgraded.app.state.service
    assert svc.db.get("frames") == LEGACY_FRAMES and svc.db.get("viewers") == LEGACY_VIEWERS
    upgraded.get("/api/frame", headers=EE03)
    upgraded.get("/api/frame", headers=EE02)
    upgraded.get("/api/frame", headers={"X-Device-Id": "EE:EE:EE:00:00:09",
                                        "X-Panel": "ED103TC2 1404x1872 gray16"})
    upgraded.post("/api/frames", data={"id": "EE:EE:EE:00:00:09", "action": "ignore"},
                  headers=SAME_ORIGIN)
    upgraded.post("/api/frames", data={"id": "CC:CC:CC:00:00:01", "action": "forget"},
                  headers=SAME_ORIGIN)
    upgraded.get("/api/setup", headers=TRMNL)
    upgraded.get("/api/display", headers=TRMNL)
    upgraded.post(f"/api/viewers/{TRMNL['ID']}", json={"rotation": 270}, headers=SAME_ORIGIN)
    _name(upgraded, EE03["X-Device-Id"], "Hallway")
    assert svc.db.get("frames") == LEGACY_FRAMES and svc.db.get("viewers") == LEGACY_VIEWERS
    # And the registry recorded all of it.
    rows = svc.frames.all()
    assert frames.name_of(rows[EE03["X-Device-Id"]]) == "Hallway"
    assert rows["EE:EE:EE:00:00:09"]["status"] == frames.IGNORED
    assert "CC:CC:CC:00:00:01" not in rows
    assert rows[TRMNL["ID"]]["set"]["rotation"] == 270
    assert frames.reported_of(rows[EE02["X-Device-Id"]])["last_result"] in ("frame", "304")
