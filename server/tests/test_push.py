"""Push, don't poll (W-841). A kit on USB holds a socket to /api/frame/push
and is told when its next GET /api/frame would answer differently; the GET
itself is unchanged. Only a frame that is on is kept on a socket."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from featherframe import frames as frames_mod
from featherframe.render import pipeline
from tests._frames import FRAME_ID, EE03_BOARD, EE03_PANEL, add_kit, give_output, seed_frame

NOW = datetime(2026, 9, 22, 12, 0, 0)
KIT = {"X-Device-Id": FRAME_ID, "X-Panel": EE03_PANEL, "X-Board": EE03_BOARD}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc._clock = lambda: NOW
    svc.reload_config = lambda: None
    app.state.service = svc
    return TestClient(app)


def _svc(client):
    return client.app.state.service


def test_a_frame_on_push_is_told_its_output_at_once_and_again_when_it_changes(client):
    svc = _svc(client)
    seed_frame(svc, etag="one", power_mode="awake")
    with client.websocket_connect("/api/frame/push", headers=KIT) as ws:
        first = ws.receive_json()
        assert first["etag"] == "one" and first["power"] == "awake" and first["ota"] is False
        assert svc.push.connected(FRAME_ID)
        give_output(svc, etag="two")
        svc.push.notify()
        assert ws.receive_json()["etag"] == "two"
    assert not svc.push.connected(FRAME_ID)


def test_a_settings_save_reaches_the_frame_without_a_tick(client):
    svc = _svc(client)
    seed_frame(svc, power_mode="awake")
    with client.websocket_connect("/api/frame/push", headers=KIT) as ws:
        rot = ws.receive_json()["rotation"]
        other = 270 if rot == 90 else 90
        assert client.post(f"/api/frames/{FRAME_ID}", json={"rotation": other}).json()["ok"]
        assert ws.receive_json()["rotation"] == other
        # USB to battery: the frame hears it, fetches, and goes to sleep.
        client.post(f"/api/frames/{FRAME_ID}", json={"power_mode": "sleep"})
        assert ws.receive_json()["power"] == "sleep"


def test_a_tick_wakes_the_sockets_even_when_it_fails(client, monkeypatch):
    svc = _svc(client)
    woken = []
    monkeypatch.setattr(svc.push, "notify", lambda fid=None: woken.append(fid))
    monkeypatch.setattr(svc, "_tick", lambda: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        svc.tick()
    assert woken == [None]


@pytest.mark.parametrize("status", [frames_mod.ASKING, frames_mod.IGNORED, None])
def test_only_a_frame_that_is_on_is_kept(client, status):
    svc = _svc(client)
    if status:
        add_kit(svc, status=status)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/frame/push", headers=KIT) as ws:
            ws.receive_json()
    assert not svc.push.connected(FRAME_ID)


def test_no_id_and_a_foreign_page_are_refused(client):
    seed_frame(_svc(client))
    for headers in ({"X-Panel": EE03_PANEL}, {**KIT, "Origin": "http://evil.example"}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/frame/push", headers=headers) as ws:
                ws.receive_json()


def test_a_frame_removed_from_the_page_is_let_go(client):
    svc = _svc(client)
    seed_frame(svc)
    with client.websocket_connect("/api/frame/push", headers=KIT) as ws:
        ws.receive_json()
        assert client.post(f"/api/frames/{FRAME_ID}", json={"forget": True}).json()["ok"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_a_pending_firmware_update_is_part_of_the_message(client, monkeypatch):
    svc = _svc(client)
    seed_frame(svc)
    monkeypatch.setattr(svc, "firmware_view", lambda row: {"pending": True})
    assert svc.push_message(FRAME_ID)["ota"] is True


def test_the_heartbeat_it_names_is_what_overdue_is_measured_against(client):
    svc = _svc(client)
    seed_frame(svc, power_mode="awake")
    r = client.get("/api/frame", headers={**KIT, "X-FF-Push": "900"})
    assert r.status_code == 200
    row = svc.frames.get(FRAME_ID)
    assert row["told_s"] == 900
    assert frames_mod.reported_of(row)["push_s"] == 900
    view = [f for f in svc.frames_list() if f["id"] == FRAME_ID][0]
    assert view["settings"]["push"] is True
    # Twelve minutes on, a 3 s poller would be overdue; a 15 min heartbeat is not.
    svc._clock = lambda: NOW + timedelta(minutes=12)
    assert svc.frame_health(svc.frames.get(FRAME_ID))["overdue"] is False
    # A socket that is down again: it polls, and says so.
    client.get("/api/frame", headers={**KIT, "X-FF-Push": "0"})
    view = [f for f in svc.frames_list() if f["id"] == FRAME_ID][0]
    assert view["settings"]["push"] is False


def test_an_open_socket_is_being_heard_from(client):
    svc = _svc(client)
    seed_frame(svc, power_mode="awake")
    client.get("/api/frame", headers=KIT)
    svc._clock = lambda: NOW + timedelta(hours=2)
    assert svc.frame_health(svc.frames.get(FRAME_ID))["overdue"] is True
    with client.websocket_connect("/api/frame/push", headers=KIT) as ws:
        ws.receive_json()
        card = svc.frame_health(svc.frames.get(FRAME_ID))
        assert card["overdue"] is False and card["last_seen"] == "just now"


def _queued(svc):
    return [f for f in svc.frames_list() if f["id"] == FRAME_ID][0]["queued_s"]


def test_a_change_the_colour_panel_must_hold_is_shown_as_queued(client):
    """The EE02 paints at most once per FF_MIN_REPAINT_MS, counted from the end
    of its last paint: a change told to it inside that is queued, not lost."""
    from tests._frames import EE02_BOARD, EE02_PANEL
    svc = _svc(client)
    seed_frame(svc, panel=EE02_PANEL, etag="one", power_mode="awake")
    ee02 = {**KIT, "X-Panel": EE02_PANEL, "X-Board": EE02_BOARD, "X-FF-Push": "900"}
    assert client.get("/api/frame", headers=ee02).status_code == 200    # painting "one" from NOW
    assert _queued(svc) is None                                          # nothing new yet
    give_output(svc, etag="two")
    svc._clock = lambda: NOW + timedelta(seconds=40)
    # 30 s of paint plus the 180 s floor, 40 s of it gone.
    assert _queued(svc) == 30 + 180 - 40
    svc._clock = lambda: NOW + timedelta(seconds=215)
    assert _queued(svc) is None                                          # it is fetching it now


def test_the_gray_panel_never_holds_a_change(client):
    svc = _svc(client)
    seed_frame(svc, etag="one", power_mode="awake")
    client.get("/api/frame", headers={**KIT, "X-FF-Push": "900"})
    give_output(svc, etag="two")
    assert _queued(svc) is None


def test_the_repaint_floor_is_the_firmwares():
    import re
    from pathlib import Path
    from featherframe import panels
    src = (Path(__file__).resolve().parents[2] / "firmware" / "include" / "ff_config.h").read_text()
    ms = int(re.search(r"#define FF_MIN_REPAINT_MS\s+(\d+)UL", src).group(1))
    assert ms == panels.EE02.min_repaint_s * 1000
    assert panels.custom(800, 480, "gray16", "90,270").min_repaint_s * 1000 == ms   # generic build: full refresh


def test_a_save_inside_the_floor_is_queued_before_it_is_drawn(client):
    """The page says so as it saves: the next tick draws it, the panel holds it."""
    from tests._frames import EE02_BOARD, EE02_PANEL
    svc = _svc(client)
    seed_frame(svc, panel=EE02_PANEL, power_mode="awake")
    # What the output was drawn from: here, just the one setting that is a pixel.
    svc._output_src = lambda row, now: f"mat_guide={frames_mod.settings_of(row).get('mat_guide')}"
    svc._out[FRAME_ID]["src"] = svc._output_src(svc.frames.get(FRAME_ID), NOW)
    ee02 = {**KIT, "X-Panel": EE02_PANEL, "X-Board": EE02_BOARD, "X-FF-Push": "900"}
    assert client.get("/api/frame", headers=ee02).status_code == 200
    assert _queued(svc) is None
    svc._clock = lambda: NOW + timedelta(seconds=60)
    out = client.post(f"/api/frames/{FRAME_ID}", json={"mat_guide": True}).json()
    mine = [f for f in out["frames"] if f["id"] == FRAME_ID][0]
    assert mine["queued_s"] == 30 + 180 - 60
    # A name is not a pixel: nothing to hold.
    svc._out[FRAME_ID]["src"] = svc._output_src(svc.frames.get(FRAME_ID), NOW)   # the tick drew it
    out = client.post(f"/api/frames/{FRAME_ID}", json={"name": "Hall"}).json()
    assert [f for f in out["frames"] if f["id"] == FRAME_ID][0]["queued_s"] is None
