"""Hosted mode (W-844): this server as one household's, its data dir kept
behind a front door (W-843) that answers the frames while it sleeps. The front
door here is a fake with the same internal API, in memory."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, Request, Response
from starlette.testclient import TestClient

from featherframe import frames as frames_mod
from featherframe import hosted
from featherframe.render import pipeline
from tests._frames import EE03_BOARD, EE03_PANEL, FRAME_ID, add_kit, give_output, seed_frame

NOW = datetime(2026, 9, 22, 12, 0, 0)
KIT = {"X-Device-Id": FRAME_ID, "X-Panel": EE03_PANEL, "X-Board": EE03_BOARD}


def front_door():
    """The household's front door, as far as this server can tell."""
    door = FastAPI()
    door.state.files, door.state.states, door.state.queue, door.state.calls = {}, [], [], []

    def ok(request):
        return request.headers.get("authorization") == "Bearer k"

    @door.get("/h/files")
    async def listing(request: Request):
        assert ok(request)
        return {"files": {k: hashlib.sha256(v).hexdigest() for k, v in door.state.files.items()}}

    @door.api_route("/h/files/{rel:path}", methods=["GET", "PUT", "DELETE"])
    async def one(request: Request, rel: str):
        assert ok(request)
        door.state.calls.append((request.method, rel))
        if request.method == "GET":
            return Response(door.state.files[rel])
        if request.method == "PUT":
            body = await request.body()
            assert request.headers["x-sha256"] == hashlib.sha256(body).hexdigest()
            door.state.files[rel] = body
            return Response(status_code=204)
        door.state.files.pop(rel, None)
        return Response(status_code=204)

    @door.post("/h/state")
    async def state(request: Request):
        door.state.states.append(await request.json())
        return {"ok": True}

    @door.post("/h/checkins/take")
    async def take():
        q, door.state.queue = door.state.queue, []
        return {"checkins": q}

    return door


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    door = front_door()
    link = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=TestClient(door))
    return door, link, tmp_path / "data"


def _service():
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc._clock = lambda: NOW
    svc.reload_config = lambda: None
    return svc


def test_the_data_dir_comes_down_and_only_what_changed_goes_back(env):
    door, link, data = env
    door.state.files = {"generated/blue-jay.png": b"plate", "frames/out/x.fff": b"FFF1old"}
    assert link.pull() == 2
    assert (data / "generated" / "blue-jay.png").read_bytes() == b"plate"
    assert link.pull() == 0                                   # already here
    (data / "frames" / "out" / "x.fff").write_bytes(b"FFF1new")
    (data / "frames" / "pictures").mkdir(parents=True)
    (data / "frames" / "pictures" / "sheet.png").write_bytes(b"sheet")
    (data / "generated" / "blue-jay.png").unlink()
    (data / "frames" / "views").mkdir(parents=True)
    (data / "frames" / "views" / "cache.png").write_bytes(b"a cache, not state")
    door.state.calls.clear()
    assert link.push() == 3
    assert sorted(door.state.calls) == [("DELETE", "generated/blue-jay.png"),
                                        ("PUT", "frames/out/x.fff"),
                                        ("PUT", "frames/pictures/sheet.png")]
    door.state.calls.clear()
    assert link.push() == 0 and door.state.calls == []


def test_a_path_from_the_front_door_stays_inside_the_data_dir(env):
    door, link, data = env
    door.state.files = {"../escape.txt": b"no", "ok.txt": b"yes"}
    link.pull()
    assert (data / "ok.txt").exists() and not (data.parent / "escape.txt").exists()


def test_the_database_travels_as_a_snapshot_and_comes_back_whole(env, tmp_path, monkeypatch):
    door, link, data = env
    svc = _service()
    seed_frame(svc, etag="one")
    link.push()
    assert hosted.DB_NAME in door.state.files
    # A new Container: an empty disk, the same household.
    fresh = tmp_path / "fresh"
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(fresh))
    link2 = hosted.HostedLink("http://door/h", "k", fresh, session=TestClient(door))
    link2.pull()
    svc2 = _service()
    assert svc2.frames.get(FRAME_ID)["status"] == frames_mod.ON


def test_what_the_frames_said_while_it_slept_is_recorded_as_if_it_had_been_heard(env):
    door, link, data = env
    svc = _service()
    seed_frame(svc, etag="one", power_mode="awake")
    door.state.queue = [
        {"headers": {**KIT, "X-Battery-Voltage": "4.1", "X-Wifi-Rssi": "-58", "X-FF-Push": "900"},
         "ip": "10.0.0.9", "result": "304", "etag": "one", "at": "2026-09-22T11:58:00"},
        {"headers": {"X-Device-Id": "CC:CC:CC:00:00:01", "X-Panel": EE03_PANEL},
         "ip": "10.0.0.10", "result": "403", "at": "2026-09-22T11:59:00"},
    ]
    link.settle(svc)
    row = svc.frames.get(FRAME_ID)
    rep = frames_mod.reported_of(row)
    assert (rep["last_checkin"], rep["battery_voltage"], rep["wifi_rssi"], rep["push_s"]) == \
        ("2026-09-22T11:58:00", 4.1, -58, 900)
    assert row["told_s"] == 900
    assert svc.frames.get("CC:CC:CC:00:00:01")["status"] == frames_mod.ASKING   # asks to be added
    state = door.state.states[-1]
    assert state["frames"]["CC:CC:CC:00:00:01"] == {"status": "asking"}
    mine = state["frames"][FRAME_ID]
    assert mine["etag"] == "one" and mine["file"] == "frames/out/AA_AA_AA_00_00_03.fff"
    assert mine["headers"]["X-Power-Mode"] == "awake" and mine["push"]["etag"] == "one"
    assert door.state.files[mine["file"]] == (data / mine["file"]).read_bytes()


def test_it_says_when_it_next_has_something_to_do(env):
    svc = _service()
    svc.config.quiet_hours_mode = "off"
    assert svc.next_wake_at() is None
    svc.config.quiet_hours_mode = "on"
    start, end = svc.config.quiet_window(NOW.date())
    want = min(svc._next_time(NOW, start), svc._next_time(NOW, end))
    assert svc.next_wake_at() == want.isoformat(timespec="seconds")


def test_a_front_door_that_is_down_costs_nothing_but_a_retry(env, monkeypatch):
    door, link, data = env
    svc = _service()
    seed_frame(svc)

    def down(*a, **kw):
        raise hosted.requests.ConnectionError("down")

    monkeypatch.setattr(link, "take_checkins", down)
    link.settle(svc)                                  # logged, not raised
    assert door.state.states == []


def test_in_quiet_hours_there_is_nothing_to_look_for(env):
    svc = _service()
    svc.config.quiet_hours_mode = "off"
    assert svc.hosted_state()["poll"] is True
    svc.config.quiet_hours_mode = "on"
    start, _ = svc.config.quiet_window(NOW.date())
    svc._clock = lambda: datetime.combine(NOW.date(), start) + timedelta(minutes=5)
    assert svc.hosted_state()["poll"] is False


def test_only_a_hosted_server_has_a_wake(env):
    from featherframe.app import app
    app.state.service = _service()
    app.state.hosted = None
    assert TestClient(app).post("/api/hosted/run").status_code == 404


def test_the_front_door_is_told_where_news_comes_from(env):
    svc = _service()
    svc.config.detection_backend = "birdweather"
    svc.config.birdweather_station_id = "abc123"
    assert svc.hosted_state()["source"] == {"kind": "birdweather", "station": "abc123"}
    svc.config.detection_backend = "apprise"
    svc.config.apprise_token = "t0k"
    assert svc.hosted_state()["source"] == {"kind": "apprise", "token": "t0k"}


def test_a_frame_its_owner_paired_is_added_without_a_second_step(env):
    door, link, data = env
    svc = _service()
    door.state.queue = [{"headers": {"X-Device-Id": "DD:DD:DD:00:00:01", "X-Panel": EE03_PANEL},
                         "result": "403", "at": "2026-09-22T11:59:00", "add": True}]
    link.settle(svc)
    assert svc.frames.get("DD:DD:DD:00:00:01")["status"] == frames_mod.ON


def test_the_lobby_draws_a_code_for_the_panel_that_asks():
    """W-845: a frame no one has claimed gets its code, finished for its own
    panel — the EE03's native landscape, the EE02's portrait inks."""
    import struct
    from featherframe.lobby import render_pairing
    pipeline.DITHER_OVERRIDE = "none"
    gray, cfg = render_pairing("ABC-DEF", EE03_PANEL)
    assert struct.unpack_from("<HH", gray.frame, 6) == (1872, 1404) and cfg.panel_rotation in (90, 270)
    color, cfg2 = render_pairing("ABC-DEF", "T133A01 1200x1600 spectra6")
    assert struct.unpack_from("<HH", color.frame, 6) == (1200, 1600) and cfg2.panel == "ee02"
    odd, _ = render_pairing("ABC-DEF", "", {"w": "800", "h": "480", "fmt": "gray16", "rot": "90,270"})
    assert struct.unpack_from("<HH", odd.frame, 6) == (800, 480)
