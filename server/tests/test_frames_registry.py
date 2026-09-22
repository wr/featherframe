"""One registry for every screen (W-833). Every frame — a kit, a TRMNL, a
tablet — is one row shape behind `frames.FrameRegistry`, and what a screen can
be asked for is derived from what it reported, never typed per model.

What needs its own test here: the capability rules, and that any frame is
renamed through the one endpoint.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe import frames, panels
from tests._frames import connect


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
    assert not caps["colour"]
    assert "look" not in caps and "dark_quiet" not in caps
    assert caps["has_battery"] and not caps["needs_size"]


def test_a_colour_kit_is_colour_and_hangs_the_other_way_up():
    caps = frames.capabilities(_kit("T133A01 1200x1600 spectra6"))
    assert caps["colour"] and caps["rotations"] == (0, 180)
    assert not caps["has_battery"]               # it has said nothing about a battery yet


def test_a_custom_panel_answers_from_its_own_facts():
    row = _kit("GDEY075 DIY", {"w": "800", "h": "480", "fmt": "spectra6", "rot": "90,270"})
    caps = frames.capabilities(row)
    assert caps["rotations"] == (90, 270) and caps["colour"] and caps["mat"]


def test_a_kit_that_has_not_reported_yet_is_drawn_for_the_default_panel(monkeypatch):
    """Older firmware names no panel. It gets the default one — or whatever
    FEATHERFRAME_PANEL seeded this install with."""
    row = frames.new_row("legacy", "kit", "2026-09-20T09:00:00", frames.ON)
    assert frames.capabilities(row)["rotations"] == panels.DEFAULT.rotations
    assert frames.panel_for(row) is panels.DEFAULT
    monkeypatch.setenv("FEATHERFRAME_PANEL", "ee02")
    assert frames.capabilities(row)["rotations"] == (0, 180)
    assert frames.capabilities(row)["colour"]


def test_a_trmnl_that_reported_its_size_needs_nothing_from_the_owner():
    row = frames.new_row("AA:BB:CC:DD:EE:01", "trmnl", "2026-09-20T09:00:00", frames.ON)
    row["reported"] = {"model": "x", "width": 1872, "height": 1404, "battery_volts": 4.0}
    caps = frames.capabilities(row)
    assert not caps["needs_size"] and caps["has_battery"]
    assert caps["rotations"] == (0, 90, 180, 270)     # turned in the render, not by the glass
    assert not caps["mat"] and not caps["power"] and not caps["colour"]
    assert "look" not in caps and "dark_quiet" not in caps


def test_a_trmnl_that_reported_no_size_has_to_be_told():
    row = frames.new_row("AA:BB:CC:DD:EE:03", "trmnl", "2026-09-20T09:00:00", frames.ON)
    row["reported"] = {"battery_percent": 80}
    assert frames.capabilities(row)["needs_size"]
    assert frames.capabilities(row)["has_battery"]


def test_a_page_is_lit_turns_itself_and_has_no_battery():
    row = frames.new_row("AA:BB:CC:DD:EE:02", "page", "2026-09-20T09:00:00", frames.ON)
    row["reported"] = {"width": 2048, "height": 1536, "model": "iPad"}
    caps = frames.capabilities(row)
    assert caps["rotations"] == () and caps["colour"]
    assert "look" not in caps and "dark_quiet" not in caps
    assert not caps["mat"] and not caps["power"] and not caps["has_battery"]
    assert not caps["needs_size"] and caps["renamable"] and caps["shows"]


# -- renaming any frame ------------------------------------------------------------
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
    assert connect(client, EE03).status_code == 200
    assert client.get("/api/frame", headers=EE02).status_code == 403
    client.post("/api/frames", data={"id": EE02["X-Device-Id"], "action": "add"},
                headers=SAME_ORIGIN)
    client.get("/api/setup", headers=TRMNL)
    return client


def _name(client, frame_id: str, name: str):
    return client.post(f"/api/frames/{frame_id}", json={"name": name}, headers=SAME_ORIGIN)


def _row(svc, frame_id: str) -> dict:
    return [f for f in svc.frames_list() if f["id"] == frame_id][0]


def test_every_frame_can_be_named(client):
    """There is no primary kit to be named apart from the rest (W-833): the
    first kit is renamed through the one endpoint, like any other frame."""
    svc = client.app.state.service
    fid = EE03["X-Device-Id"]
    assert _row(svc, fid)["name"] == ""
    assert _name(client, fid, "Hallway").json()["ok"]
    assert _row(svc, fid)["name"] == "Hallway" and "EE03" in _row(svc, fid)["what"]
    # The page shows the name where it showed the panel, and only once named.
    assert "Hallway" in client.get("/").text
    assert _name(client, fid, "  ").json()["ok"]
    assert _row(svc, fid)["name"] == ""


def test_an_added_frame_is_still_renamed_with_the_rest_of_its_settings(client):
    svc = client.app.state.service
    r = client.post(f"/api/frames/{EE02['X-Device-Id']}",
                    json={"name": "Study", "panel_rotation": 180}, headers=SAME_ORIGIN)
    assert r.json()["ok"]
    card = _row(svc, EE02["X-Device-Id"])
    assert card["name"] == "Study" and card["settings"]["rotation"] == 180


def test_a_viewer_is_renamed_on_the_one_endpoint(client):
    svc = client.app.state.service
    assert _name(client, TRMNL["ID"], "Desk").json()["ok"]
    assert svc.frames.get(TRMNL["ID"])["set"]["name"] == "Desk"
    assert _row(svc, TRMNL["ID"])["name"] == "Desk"


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
    assert _row(svc, EE03["X-Device-Id"])["transport"] == "kit"
