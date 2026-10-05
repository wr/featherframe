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
    yield door, link, tmp_path / "data"
    hosted.activate(None)


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
    assert link.pull() == 1                                   # the illustration waits to be read
    hosted.activate(link)
    assert hosted.local(data / "generated" / "blue-jay.png").read_bytes() == b"plate"
    assert link.pull() == 0                                   # already here
    (data / "frames" / "out" / "x.fff").write_bytes(b"FFF1new")
    (data / "frames" / "pictures").mkdir(parents=True)
    (data / "frames" / "pictures" / "sheet.png").write_bytes(b"sheet")
    (data / "generated" / "blue-jay.png").unlink()
    (data / "plate-library").mkdir(parents=True)
    (data / "plate-library" / "lib.png").write_bytes(b"a cache, not state")
    (data / "frames" / "views").mkdir(parents=True)
    (data / "frames" / "views" / "v.png").write_bytes(b"a viewer's image: the front door serves it")
    door.state.calls.clear()
    assert link.push() == 4
    assert sorted(door.state.calls) == [("DELETE", "generated/blue-jay.png"),
                                        ("PUT", "frames/out/x.fff"),
                                        ("PUT", "frames/pictures/sheet.png"),
                                        ("PUT", "frames/views/v.png")]
    door.state.calls.clear()
    assert link.push() == 0 and door.state.calls == []


def _write(data, rel, body):
    (data / rel).parent.mkdir(parents=True, exist_ok=True)
    (data / rel).write_bytes(body)


def test_small_files_travel_as_a_few_archives(env, tmp_path):
    """W-985: a start fetched ~85 small files one request apiece."""
    door, link, data = env
    link.pull()
    _write(data, "generated/blue-jay.json", b"{}")
    _write(data, "generated/thumbs/blue-jay.jpg", b"thumb")
    _write(data, "bluenoise64.npy", b"noise")
    _write(data, "frames/history/0123456789abcdef.png", b"past")
    _write(data, "frames/out/x.fff", b"FFF1")                    # the front door reads it
    _write(data, "frames/pictures/plates/sheet.png", b"sheet")   # changes with every picture
    _write(data, "big.npy", b"x" * (hosted.BUNDLE_MAX_BYTES + 1))
    door.state.calls.clear()
    link.push()
    assert sorted(door.state.calls) == [("PUT", "big.npy"), ("PUT", "bundles/files.tar"),
                                        ("PUT", "bundles/history.tar"), ("PUT", "frames/out/x.fff"),
                                        ("PUT", "frames/pictures/plates/sheet.png")]
    # A new Container gets them all back, one request per archive.
    fresh = tmp_path / "fresh"
    link2 = hosted.HostedLink("http://door/h", "k", fresh, session=TestClient(door))
    door.state.calls.clear()
    link2.pull()
    assert sorted(c for c in door.state.calls if c[1].startswith("bundles/")) == [
        ("GET", "bundles/files.tar"), ("GET", "bundles/history.tar")]
    assert len(door.state.calls) == 5
    for rel in ("generated/blue-jay.json", "generated/thumbs/blue-jay.jpg", "bluenoise64.npy",
                "frames/history/0123456789abcdef.png", "big.npy", "frames/out/x.fff"):
        assert (fresh / rel).read_bytes() == (data / rel).read_bytes()
    # A new picture's history PNG sends that archive again, not the rest.
    _write(fresh, "frames/history/fedcba9876543210.png", b"newer")
    door.state.calls.clear()
    assert link2.push() == 1
    assert door.state.calls == [("PUT", "bundles/history.tar")]
    door.state.calls.clear()
    assert link2.push() == 0 and door.state.calls == []
    # A file removed leaves its archive; an archive left empty goes.
    (fresh / "frames/history/0123456789abcdef.png").unlink()
    (fresh / "frames/history/fedcba9876543210.png").unlink()
    link2.push()
    assert "bundles/history.tar" not in door.state.files


def test_files_sent_one_by_one_before_are_sent_in_an_archive_and_removed(env):
    """A household from before W-985: its small files are each their own
    object. They come down as before, then go back as an archive, and the
    single copies are removed after it."""
    door, link, data = env
    door.state.files = {"generated/blue-jay.json": b"{}", "collages/2026-10-01.json": b"[]"}
    link.pull()
    door.state.calls.clear()
    link.push()
    assert door.state.calls[0] == ("PUT", "bundles/files.tar")
    assert sorted(door.state.calls[1:]) == [("DELETE", "collages/2026-10-01.json"),
                                            ("DELETE", "generated/blue-jay.json")]
    assert set(door.state.files) == {"bundles/files.tar"}


def test_an_archive_wins_over_a_file_of_the_same_path(env, tmp_path):
    """A push stopped between sending an archive and removing the single
    copies it replaced leaves both: the archive is the newer."""
    door, link, data = env
    _write(data, "generated/blue-jay.json", b"new")
    link.pull()
    link.push()
    door.state.files["generated/blue-jay.json"] = b"old"
    fresh = tmp_path / "fresh"
    hosted.HostedLink("http://door/h", "k", fresh, session=TestClient(door)).pull()
    assert (fresh / "generated/blue-jay.json").read_bytes() == b"new"


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
    # Hosted, a frame joins only by pairing: an unknown one's check-in (one
    # queued just before its owner removed it) makes no "asking" row.
    assert svc.frames.get("CC:CC:CC:00:00:01") is None
    state = door.state.states[-1]
    assert "CC:CC:CC:00:00:01" not in state["frames"]
    mine = state["frames"][FRAME_ID]
    assert mine["etag"] == "one" and mine["file"] == "frames/out/AA_AA_AA_00_00_03.fff"
    assert mine["headers"]["X-Power-Mode"] == "awake" and mine["push"]["etag"] == "one"
    assert door.state.files[mine["file"]] == (data / mine["file"]).read_bytes()


def test_it_says_when_it_next_has_something_to_do(env):
    svc = _service()
    svc.config.quiet_hours_mode = "off"
    assert svc.next_wake_at() is None
    svc.config.quiet_hours_mode = "on"
    start, end = svc.quiet_window(NOW.date())
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
    start, _ = svc.quiet_window(NOW.date())
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
    svc.config.ingest_token = "t0k"
    assert svc.hosted_state()["source"] == {"kind": "apprise", "token": "t0k"}
    svc.config.detection_backend = "birdnet_go"
    assert svc.hosted_state()["source"] == {"kind": "birdnet_go", "token": "t0k"}


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


def test_a_new_frame_starts_the_way_up_it_already_hangs(env):
    """W-851: a frame moved from another server says which way up it hangs;
    its new row starts there, and a rotation its panel cannot do is not taken."""
    from fastapi.testclient import TestClient as TC
    from featherframe.app import app
    svc = _service()
    app.state.service, app.state.hosted = svc, None
    client = TC(app)
    for fid, rot, want in (("EE:EE:EE:00:00:01", "270", 270), ("EE:EE:EE:00:00:02", "180", 90)):
        client.get("/api/frame", headers={"X-Device-Id": fid, "X-Panel": EE03_PANEL, "X-FF-Rotation": rot})
        svc.answer_frame(fid, "add")
        assert svc.frame_config(svc.frames.get(fid)).panel_rotation == want
    # Its report never undoes the owner's choice afterwards.
    svc.update_frame("EE:EE:EE:00:00:01", {"rotation": 90})
    client.get("/api/frame", headers={"X-Device-Id": "EE:EE:EE:00:00:01", "X-Panel": EE03_PANEL, "X-FF-Rotation": "270"})
    assert svc.frame_config(svc.frames.get("EE:EE:EE:00:00:01")).panel_rotation == 90


def test_a_new_frame_starts_with_the_mat_it_kept(env):
    """A frame removed and added again (here, or on another server) says the
    mat it kept in NVS; its new row starts there, clamped as the page clamps,
    and nothing it says afterwards undoes the owner's choice."""
    from fastapi.testclient import TestClient as TC
    from featherframe import frames as frames_mod
    from featherframe.app import app
    svc = _service()
    app.state.service, app.state.hosted = svc, None
    client = TC(app)
    fid = "EE:EE:EE:00:00:03"
    head = {"X-Device-Id": fid, "X-Panel": EE03_PANEL, "X-FF-Mat": "2.5,-8,999"}
    client.get("/api/frame", headers=head)
    svc.answer_frame(fid, "add")
    cfg = svc.frame_config(svc.frames.get(fid))
    assert (cfg.mat_inset_pct, cfg.mat_offset_x_px, cfg.mat_offset_y_px) == (2.5, -8, 120)
    assert "mat_inset_pct" in frames_mod.settings_of(svc.frames.get(fid))
    svc.update_frame(fid, {"mat_inset_pct": 1.0})
    client.get("/api/frame", headers=head)
    assert svc.frame_config(svc.frames.get(fid)).mat_inset_pct == 1.0
    # A default mat, the old default (no inset), or nonsense sets nothing of its own.
    for other, mat in (("EE:EE:EE:00:00:04", "4,0,0"), ("EE:EE:EE:00:00:05", "wide"),
                       ("EE:EE:EE:00:00:06", "0,0,0")):
        client.get("/api/frame", headers={**head, "X-Device-Id": other, "X-FF-Mat": mat})
        assert not any(k.startswith("mat_") for k in frames_mod.settings_of(svc.frames.get(other)))
    # The front door says it too, from the state it is handed.
    assert svc.hosted_state()["frames"][fid]["headers"]["X-FF-Mat"] == "1,-8,120"


def test_the_pairing_code_is_drawn_the_way_up_the_frame_hangs():
    from featherframe.lobby import render_pairing
    pipeline.DITHER_OVERRIDE = "none"
    up, cfg = render_pairing("ABC-DEF", EE03_PANEL, rotation=270)
    down, cfg2 = render_pairing("ABC-DEF", EE03_PANEL)
    assert (cfg.panel_rotation, cfg2.panel_rotation) == (270, 90) and up.frame != down.frame


# -- viewers (W-849) --------------------------------------------------------------
def _plates(svc):
    """Make the plates picture a plain sheet, as a render would."""
    from PIL import Image
    from featherframe.render import theme
    svc._commit("plates", NOW, sheet=Image.new("L", (theme.WIDTH, theme.HEIGHT), 200),
                mode="single", species_key=None, label="x")


def test_a_viewer_its_owner_paired_is_added_and_its_image_is_drawn_ahead(env):
    """A TRMNL paired on the hosted page: the front door hands its ask over
    with `add`, and the state it gets back names an image already drawn and
    pushed, so it can answer the device while this server sleeps."""
    door, link, data = env
    svc = _service()
    _plates(svc)
    door.state.queue = [{"id": "aa:bb:cc:00:00:01", "add": True, "at": "2026-09-22T11:59:00",
                         "viewer": {"transport": "trmnl",
                                    "headers": {"Model": "x", "Width": "1872", "Height": "1404",
                                                "Battery-Voltage": "4.0"}}}]
    link.settle(svc)
    row = svc.frames.get("AA:BB:CC:00:00:01")
    assert row["status"] == frames_mod.ON and row["transport"] == "trmnl"
    assert frames_mod.reported_of(row)["battery_volts"] == 4.0
    v = door.state.states[-1]["viewers"]["AA:BB:CC:00:00:01"]
    assert v["status"] == "on" and v["name"].startswith(svc.pictures["plates"].etag)
    assert v["file"] == f"frames/views/{v['name']}.png" and v["file"] in door.state.files
    assert v["paper"] is True and v["refresh"] > 0


def test_a_tablet_page_reports_its_size_through_the_front_door(env):
    door, link, data = env
    svc = _service()
    _plates(svc)
    door.state.queue = [{"id": "PAGE-0A1B2C3D", "add": True, "at": "2026-09-22T11:59:00",
                         "viewer": {"transport": "page", "w": "1536", "h": "2048", "device": "iPad"}}]
    link.settle(svc)
    row = svc.frames.get("PAGE-0A1B2C3D")
    assert row["transport"] == "page" and frames_mod.reported_of(row)["model"] == "iPad"
    v = door.state.states[-1]["viewers"]["PAGE-0A1B2C3D"]
    assert v["paper"] is False and v["name"].endswith("1536x2048-color-0")


def test_a_viewer_nobody_paired_is_not_recorded(env):
    """Hosted, a viewer joins only by pairing: a check-in from one this
    household does not have makes no row."""
    door, link, data = env
    svc = _service()
    _plates(svc)
    door.state.queue = [{"id": "AA:BB:CC:00:00:02", "at": "2026-09-22T11:59:00",
                         "viewer": {"transport": "trmnl", "headers": {}}}]
    link.settle(svc)
    assert svc.frames.get("AA:BB:CC:00:00:02") is None
    assert "AA:BB:CC:00:00:02" not in door.state.states[-1]["viewers"]


def test_the_lobby_draws_a_viewers_code_at_its_own_size():
    import io
    from PIL import Image
    from featherframe.lobby import render_viewer_pairing
    png = render_viewer_pairing("ABC-DEF", {"model": "og", "width": 800, "height": 480})
    assert Image.open(io.BytesIO(png)).size == (800, 480)
    assert Image.open(io.BytesIO(render_viewer_pairing("", {}))).size == (1072, 1448)


@pytest.mark.parametrize("hosted", [False, True])
def test_a_hosted_page_offers_no_local_database_source(tmp_path, monkeypatch, hosted):
    """A hosted server has no BirdNET database beside it to read, so a
    household that never chose a source is shown BirdWeather."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from starlette.testclient import TestClient
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    monkeypatch.setattr(app.state, "hosted", object() if hosted else None, raising=False)
    html = TestClient(app).get("/").text
    assert ('<option value="custom"' in html) is not hosted
    assert ('value="birdweather" data-icon="ic-birdweather" selected' in html) is hosted
def test_a_wake_adds_a_paired_frame_before_it_draws(env, monkeypatch):
    """A pairing wakes the server; the frame it adds must be drawn for in that
    same wake, not the next one (up to five minutes later)."""
    from fastapi.testclient import TestClient as _TC
    from featherframe.app import app
    door, link, data = env
    svc = _service()
    _plates(svc)
    door.state.queue = [{"headers": {"X-Device-Id": "EE:EE:EE:00:00:01", "X-Panel": EE03_PANEL},
                         "result": "403", "at": "2026-09-22T11:59:00", "add": True}]
    monkeypatch.setattr(app.state, "service", svc, raising=False)
    monkeypatch.setattr(app.state, "hosted", link, raising=False)
    svc.after_tick.append(lambda: link.settle(svc))
    assert _TC(app).post("/api/hosted/run").status_code == 200
    entry = door.state.states[-1]["frames"]["EE:EE:EE:00:00:01"]
    assert entry["status"] == "on" and entry.get("etag") and entry["file"] in door.state.files


def test_the_pairing_screen_prints_when_its_code_expires():
    from featherframe.render import welcome
    bare = welcome.render_pairing("ABC-DEF")
    dated = welcome.render_pairing("ABC-DEF", expires="25 September, 10:32 am")
    # The date goes under the pairing line, and nothing above it moves.
    assert bare.crop((0, 0, bare.width, 1780)).tobytes() == dated.crop((0, 0, bare.width, 1780)).tobytes()
    assert bare.crop((0, 1780, bare.width, 1830)).getextrema() != dated.crop((0, 1780, bare.width, 1830)).getextrema()


def test_a_seed_sets_the_source_and_region_and_nothing_else(env):
    """W-888: the phone's setup queues the household's detection source and
    Region; only a hosted server takes it, and only those fields."""
    from featherframe.app import app
    svc = _service()
    app.state.service = svc
    app.state.hosted = None
    seed = {"detection_backend": "birdweather", "birdweather_station_id": "7033",
            "region": "europe", "imagegen_api_key": "sk-nope"}
    assert TestClient(app).post("/api/hosted/seed", json=seed).status_code == 404
    app.state.hosted = type("Link", (), {"settle": lambda self, svc, apply=True: None})()
    try:
        client = TestClient(app)
        assert client.post("/api/hosted/seed", json=seed).status_code == 200
        assert client.post("/api/hosted/seed", json=seed).status_code == 200   # a repeat is harmless
        assert client.post("/api/hosted/seed", json=["x"]).status_code == 400
        # Through the page's proxy it is not there.
        assert client.post("/api/hosted/seed", json={"region": "asia"},
                           headers={"X-FF-Hosted": "1"}).status_code == 404
    finally:
        app.state.hosted = None
    from featherframe.config import load_config
    saved = load_config(svc.db)           # this test's service does not reload
    assert saved.detection_backend == "birdweather"
    assert saved.birdweather_station_id == "7033"
    assert saved.region == "europe"
    assert saved.imagegen_api_key == ""


def test_the_pairing_screen_carries_the_setup_qr():
    """W-888, W-889: with a setup URL the pairing screen has a QR code of it
    under the wordmark, the code beside it, at whole-pixel modules."""
    import segno
    from featherframe.render import welcome
    url = "https://cloud.featherframe.app/setup/abcdef/0123456789ab"
    plain = welcome.render_pairing("ABC-DEF")
    scan = welcome.render_pairing("ABC-DEF", url=url)
    qr = welcome.setup_qr(url)
    x, y = welcome.setup_qr_origin("ABC-DEF", "", url)
    box = (x, y, x + qr.width, y + qr.height)
    drawn = [0 if v < 128 else 255 for v in scan.crop(box).getdata()]
    assert drawn == list(qr.getdata())
    assert list(plain.crop(box).getdata()) != drawn
    # The drawn modules are the symbol for this URL, one module every _QR_MODULE_PX.
    rows = [list(r) for r in segno.make(url, error="m", boost_error=False).matrix_iter(border=4)]
    m = welcome._QR_MODULE_PX
    sampled = [[qr.getpixel((c * m + m // 2, r * m + m // 2)) == 0 for c in range(len(rows))]
               for r in range(len(rows))]
    assert sampled == [[bool(v) for v in r] for r in rows]
    assert qr.width >= 400          # ~45 mm or more on the EE03's glass
    # Its modules clear of the 4 % mat (the white quiet zone may run under it).
    quiet = 4 * m
    assert y + qr.height - quiet < 1872 * 0.96 and x + quiet > 1404 * 0.04


# -- a start pulls only what a tick reads (W-915) ------------------------------
_LAZY_FILES = {
    "generated/blue-jay.png": b"illustration", "generated/blue-jay.json": b"{}",
    "generated/thumbs/blue-jay.jpg": b"thumb",
    "collages/2026-09-21.png": b"sheet", "collages/2026-09-21.json": b"{}",
    "frames/collage-days/2026-09-21.png": b"kept", "frames/collage-days/thumbs/2026-09-21.jpg": b"t",
    "frames/history/aaaaaaaaaaaaaaaa.png": b"small", "frames/history/aaaaaaaaaaaaaaaa.jpg": b"full",
    "firmware/0.2.10/ee03.bin": b"app",
    "frames/out/x.fff": b"FFF1", "frames/pictures/plates/sheet.png": b"sheet",
}
_WAITING = {"generated/blue-jay.png", "collages/2026-09-21.png", "frames/collage-days/2026-09-21.png",
            "frames/history/aaaaaaaaaaaaaaaa.jpg", "firmware/0.2.10/ee03.bin"}


def test_a_start_leaves_what_is_read_one_at_a_time_at_the_front_door(env):
    door, link, data = env
    door.state.files = dict(_LAZY_FILES)
    assert link.pull() == len(_LAZY_FILES) - len(_WAITING)
    for rel in _LAZY_FILES:
        assert (data / rel).exists() is (rel not in _WAITING), rel
    link.push()                    # the small files go back as archives (W-985)
    # Nothing changed: a push sends nothing, reads nothing back, and drops
    # nothing that waits at the front door.
    door.state.calls.clear()
    real_sha = hosted._sha
    try:
        hosted._sha = lambda p: pytest.fail(f"{p} hashed again")
        assert link.push() == 0 and door.state.calls == []
    finally:
        hosted._sha = real_sha
    assert _WAITING <= set(door.state.files)


def test_a_waiting_file_is_seen_fetched_and_removed_like_any_other(env):
    door, link, data = env
    door.state.files = dict(_LAZY_FILES)
    link.pull()
    hosted.activate(link)
    days = data / "frames" / "collage-days"
    assert hosted.exists(days / "2026-09-21.png") and not hosted.exists(days / "2026-09-20.png")
    assert [p.name for p in hosted.glob(days, "*.png")] == ["2026-09-21.png"]
    hist = data / "frames" / "history" / "aaaaaaaaaaaaaaaa.jpg"
    assert hosted.local(hist).read_bytes() == b"full"
    link.push()                    # the small files go back as archives (W-985)
    door.state.calls.clear()
    assert link.push() == 0 and door.state.calls == []        # fetched, not changed
    hosted.remove(days / "2026-09-21.png")                      # never fetched, removed
    hosted.remove(hist)                                         # fetched, removed
    link.push()
    assert sorted(door.state.calls) == [("DELETE", "frames/collage-days/2026-09-21.png"),
                                        ("DELETE", "frames/history/aaaaaaaaaaaaaaaa.jpg")]
    # A file written here over one that waited is the one kept.
    (data / "collages" / "2026-09-21.png").write_bytes(b"repainted")
    link.push()
    assert door.state.files["collages/2026-09-21.png"] == b"repainted"


def test_off_hosted_the_data_dir_is_the_disk(tmp_path):
    hosted.activate(None)
    f = tmp_path / "a.png"
    assert not hosted.exists(f) and hosted.local(f) == f and hosted.glob(tmp_path, "*.png") == []
    f.write_bytes(b"x")
    assert hosted.exists(f) and hosted.glob(tmp_path, "*.png") == [f]
    hosted.remove(f)
    assert not f.exists()


def _illustration_png() -> bytes:
    import io
    from PIL import Image
    im = Image.new("RGB", (400, 500), (250, 248, 240))
    for x in range(120, 280):
        for y in range(150, 350):
            im.putpixel((x, y), (40, 40, 40))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class _NoPurchase:
    name = "gpt-image-test"

    def generate(self, *a, **kw):
        pytest.fail("an illustration at the front door was bought again")


def test_an_illustration_at_the_front_door_is_drawn_never_bought_again(env):
    from featherframe.render import genart
    door, link, data = env
    door.state.files = {"generated/cyanocitta-cristata.png": _illustration_png(),
                        "generated/cyanocitta-cristata.json": b'{"slug": "cyanocitta-cristata"}'}
    link.pull()
    hosted.activate(link)
    gen = genart.GeneratedArtProvider(_NoPurchase())
    assert [m["slug"] for m in gen.cached_species()] == ["cyanocitta-cristata"]
    art = gen.artwork("Blue Jay", "Cyanocitta cristata")
    assert art is not None and art.generated
    assert (data / "generated" / "cyanocitta-cristata.png").exists()


def test_an_illustration_the_front_door_cannot_hand_over_is_not_bought(env, monkeypatch):
    from featherframe.render import genart
    door, link, data = env
    door.state.files = {"generated/cyanocitta-cristata.png": _illustration_png(),
                        "generated/cyanocitta-cristata.json": b'{"slug": "cyanocitta-cristata"}'}
    link.pull()
    hosted.activate(link)

    def down(*a, **kw):
        raise hosted.requests.ConnectionError("down")

    monkeypatch.setattr(link, "_fetch", down)
    gen = genart.GeneratedArtProvider(_NoPurchase())
    assert gen.artwork("Blue Jay", "Cyanocitta cristata") is None     # the fallback, this once
    link.push()
    assert "generated/cyanocitta-cristata.png" in door.state.files
    assert "generated/cyanocitta-cristata.json" in hosted._untar(door.state.files["bundles/files.tar"])


def test_a_detection_handed_over_by_a_wake_does_not_sync_on_its_own(env, monkeypatch):
    from featherframe.app import app
    door, link, data = env
    svc = _service()
    svc.config.detection_backend = "birdnet_go"
    settled = []
    monkeypatch.setattr(link, "settle", lambda *a, **kw: settled.append(1))
    monkeypatch.setattr(app.state, "service", svc, raising=False)
    monkeypatch.setattr(app.state, "hosted", link, raising=False)
    from featherframe.sources.pushed import PushedSource
    monkeypatch.setattr(svc, "source", PushedSource("birdnet_go", svc.db))
    client = TestClient(app)
    r = client.post(f"/api/ingest/birdnet-go/{svc.config.ingest_token}", json={"CommonName": "Blue Jay",
                                                    "ScientificName": "Cyanocitta cristata",
                                                    "Confidence": 0.9})
    assert r.status_code == 200 and settled == []
    # A change made on the page still reaches the front door at once.
    assert client.post("/api/ingest/token", headers={"X-FF-Hosted": "1"}).status_code < 400
    assert settled


def test_a_wake_ticks_and_syncs_once(env, monkeypatch):
    """W-985: the wake's tick syncs through its own hook; the request that
    asked for it does not sync again, and the scheduler does not tick on a
    clock of its own while the wake is up."""
    from featherframe.app import app
    door, link, data = env
    svc = _service()
    svc.ticks_itself = False
    ticks, settled = [], []
    real_tick = svc.tick
    monkeypatch.setattr(svc, "tick", lambda: (ticks.append(1), real_tick())[1])
    monkeypatch.setattr(link, "settle", lambda *a, **kw: settled.append(1))
    monkeypatch.setattr(link, "take", lambda *a, **kw: None)
    svc.after_tick.append(lambda: link.settle(svc))
    monkeypatch.setattr(app.state, "service", svc, raising=False)
    monkeypatch.setattr(app.state, "hosted", link, raising=False)
    svc._etag = "resident"                       # the start's own first picture is drawn
    svc.start()
    try:
        assert TestClient(app).post("/api/hosted/run").status_code == 200
        svc._stop.wait(0.2)
        assert ticks == [1] and settled == [1]
    finally:
        svc.stop(1)


# -- stopped only when idle (W-917) ------------------------------------------------
# The Container's activity timeout runs from the last request proxied, not
# from its end, and knows nothing of the server's own work: a tick longer than
# 30 s was stopped part way and lost, the nightly collage with it. The front
# door now asks the server whether it is working before it stops it.

def test_a_tick_is_work_until_what_it_drew_is_pushed(env):
    svc = _service()
    seen = []
    svc.after_tick.append(lambda: seen.append(svc.busy_for()))
    assert svc.busy_for() is None
    svc.tick()
    assert seen and seen[0] is not None     # the push after the tick is part of it
    assert svc.busy_for() is None


def test_a_background_task_is_work_while_it_runs(env):
    import threading
    svc = _service()
    go = threading.Event()
    svc._start_task("collage", go.wait, 5)
    for _ in range(50):
        if svc.busy_for() is not None:
            break
        threading.Event().wait(0.01)
    assert svc.busy_for() is not None
    go.set()
    for _ in range(100):
        if svc.busy_for() is None:
            break
        threading.Event().wait(0.01)
    assert svc.busy_for() is None


def test_the_front_door_can_ask_whether_the_server_is_working(env):
    import threading
    from featherframe.app import app
    door, link, data = env
    svc = _service()
    app.state.service, app.state.hosted = svc, link
    client = TestClient(app)
    assert client.get("/api/hosted/busy").json() == {"busy": False, "for_s": 0}
    go = threading.Event()
    svc._start_task("collage", go.wait, 5)
    for _ in range(50):
        if svc.busy_for() is not None:
            break
        threading.Event().wait(0.01)
    assert client.get("/api/hosted/busy").json()["busy"] is True
    go.set()
    app.state.hosted = None
    assert client.get("/api/hosted/busy").status_code == 404


def test_a_stop_waits_for_the_tick_under_way(env):
    """A stop that comes anyway (a deploy, the front door's cap) still lets
    the tick finish and push what it drew."""
    import threading
    svc = _service()
    pushed = []
    svc.after_tick.append(lambda: (threading.Event().wait(0.3), pushed.append(True)))
    ticking = threading.Thread(target=svc.tick)
    ticking.start()
    for _ in range(50):
        if svc.busy_for() is not None:
            break
        threading.Event().wait(0.01)
    svc.stop(wait_s=5)
    assert pushed == [True]
    ticking.join()


def test_a_failed_generation_waits_out_its_cooldown_across_a_restart(env):
    """A hosted server starts afresh on every wake: the cooldown after a
    failed paid generation is kept in the DB, not in memory."""
    from featherframe.config import save_config
    from featherframe.render.collage import CollageCell
    from featherframe.service import FeatherframeService

    class Refused:
        name, quality, last_usage, calls = "gpt-image-2.5-sunburst", "high", None, 0

        def generate(self, prompt, size, refs):
            Refused.calls += 1
            raise RuntimeError("HTTP 429: insufficient_quota")

    cells = [CollageCell("Blue Jay", "Cyanocitta cristata", 3),
             CollageCell("Carolina Wren", "Thryothorus ludovicianus", 2)]
    day = NOW.date()
    svc = FeatherframeService()
    svc.genart._model = Refused()
    svc.genart._refs = []
    assert svc.genart.day_composite(cells, day) is None
    assert Refused.calls == 1

    again = FeatherframeService()          # the next wake's Container
    again.genart._model = Refused()
    again.genart._refs = []
    assert again.genart.day_composite(cells, day) is None
    assert Refused.calls == 1              # not asked again inside its cooldown

    # A new key has not failed yet.
    again.config.imagegen_api_key = "sk-new"
    save_config(again.db, again.config)
    again.config.imagegen_api_key = "sk-old"
    again.reload_config()
    assert not again.genart._in_cooldown(f"collage-{day.isoformat()}")
