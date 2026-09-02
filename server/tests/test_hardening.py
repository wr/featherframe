"""Adversarial-review regressions: hostile or unlucky input must never take the
server (or the tick thread) down, and the decision loop must not burn renders
or show a stale bird after an outage. Each test pins one finding.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config
from featherframe.service import FeatherframeService
from featherframe.sources.base import Detection
from tests._fixtures import create_birds_db, make_row


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    service.config.dither = "none"
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


def _det(rowid, common, sci, conf=0.9):
    return Detection(rowid=rowid, date="2026-09-01", time="08:00:00",
                     common_name=common, scientific_name=sci, confidence=conf)


# -- device headers ----------------------------------------------------------
def test_frame_endpoint_survives_non_finite_telemetry(client, svc):
    # int(float("inf")) raises OverflowError, which _to_int did not catch.
    r = client.get("/api/frame", headers={"X-Battery-Percent": "inf",
                                          "X-Wifi-RSSI": "-inf",
                                          "X-Boot-Count": "1e999"})
    assert r.status_code in (200, 503)
    assert svc.device.battery_percent is None
    assert svc.device.wifi_rssi is None
    assert svc.device.boot_count is None


def test_nan_battery_voltage_does_not_poison_status(client, svc):
    # A NaN persisted into device_status makes every /api/status 500
    # (JSONResponse refuses NaN) — until the device sends a real number.
    r = client.get("/api/frame", headers={"X-Battery-Voltage": "nan"})
    assert r.status_code in (200, 503)
    assert svc.device.battery_voltage is None
    assert client.get("/api/status").status_code == 200
    assert client.get("/").status_code == 200


def test_poisoned_device_status_heals_on_start(tmp_path, monkeypatch):
    # A DB row written by an older build (json.dumps emits NaN) must not keep
    # /api/status broken across restarts.
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.db import Database
    db = Database()
    db.set("device_status", {"battery_voltage": float("nan"), "battery_percent": 9999,
                             "wifi_rssi": float("inf"), "last_wake": "w" * 5000})
    service = FeatherframeService(db=db)
    assert service.device.battery_voltage is None
    assert service.device.battery_percent is None
    assert service.device.wifi_rssi is None
    assert len(service.device.last_wake) <= 120
    from featherframe.app import app
    app.state.service = service
    assert TestClient(app, raise_server_exceptions=False).get("/api/status").status_code == 200


def test_telemetry_outside_plausible_range_is_dropped(client, svc):
    client.get("/api/frame", headers={"X-Battery-Voltage": "9.5",
                                      "X-Battery-Percent": "9999",
                                      "X-Wifi-RSSI": "40"})
    assert svc.device.battery_voltage is None
    assert svc.device.battery_percent is None
    assert svc.device.wifi_rssi is None
    client.get("/api/frame", headers={"X-Battery-Voltage": "3.87",
                                      "X-Battery-Percent": "62",
                                      "X-Wifi-RSSI": "-61"})
    assert svc.device.battery_voltage == 3.87
    assert svc.device.battery_percent == 62
    assert svc.device.wifi_rssi == -61


def test_device_string_headers_are_bounded(client, svc):
    client.get("/api/frame", headers={"X-FF-Version": "v" * 5000,
                                      "X-Wake-Detail": "d" * 5000})
    assert len(svc.device.fw_version or "") <= 120
    assert len(svc.device.wake_detail or "") <= 120


# -- settings form -----------------------------------------------------------
def test_settings_post_survives_inf_and_nan(client, svc):
    r = client.post("/settings", data={"refresh_debounce_minutes": "inf",
                                       "confidence_threshold": "nan",
                                       "mat_inset_pct": "inf"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert svc.config.refresh_debounce_minutes == 15   # default kept
    assert svc.config.confidence_threshold == 0.7      # NaN is not a threshold
    assert svc.config.mat_inset_pct == 4.0


def test_invalid_quiet_hours_keep_the_stored_value(client, svc):
    svc.config.quiet_hours_start = "21:30"
    svc.config.quiet_hours_end = "05:45"
    svc.update_config(svc.config)
    r = client.post("/settings", data={"quiet_hours_start": "99:99",
                                       "quiet_hours_end": "7pm"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert svc.config.quiet_hours_start == "21:30"   # not "03:39"
    assert svc.config.quiet_hours_end == "05:45"
    # The stored blob is validated the same way.
    assert Config(quiet_hours_start="99:99").quiet_hours_start == "22:00"
    assert Config(quiet_hours_start="7:05").quiet_hours_start == "07:05"


def test_blocklist_is_bounded(client, svc):
    huge = "\n".join(f"{'x' * 300}{i}" for i in range(2000))   # ~600 KB
    r = client.post("/settings", data={"species_blocklist": huge},
                    follow_redirects=False)
    assert r.status_code == 303
    assert len(svc.config.species_blocklist) <= 500
    assert all(len(b) <= 64 for b in svc.config.species_blocklist)
    assert svc.config.is_blocked("x" * 64, "")   # truncated entries still match


def test_settings_post_with_file_part_does_not_500(client, svc):
    before = svc.config.birdnet_db_path
    r = client.post("/settings", data={"mode": "single"},
                    files={"birdnet_db_path": ("x.db", b"abc")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert isinstance(svc.config.birdnet_db_path, str)
    assert svc.config.birdnet_db_path == before


# -- the decision loop -------------------------------------------------------
class _PagedSource:
    """A source whose backlog is bigger than one new_since page."""

    def __init__(self, page_rows, tail_row, page_limit):
        self._page = page_rows
        self._tail = tail_row
        self._limit = page_limit

    def available(self):
        return True

    def max_rowid(self):
        return self._tail.rowid

    def new_since(self, cursor, min_confidence=0.0, limit=500):
        return [d for d in self._page if d.rowid > cursor][:min(limit, self._limit)]

    def latest_many(self, min_confidence=0.0, limit=25):
        return [self._tail]

    def latest(self, min_confidence=0.0, scan=25):
        return self._tail

    def species_ordinal(self, sci):
        return None

    def first_seen_date(self, sci):
        return None


def test_full_backlog_page_shows_the_newest_bird_not_the_oldest_chunk(svc, monkeypatch):
    # After an outage the first page since the cursor is full; rendering the
    # newest row OF THAT PAGE shows a bird from hours ago and then walks the
    # backlog one page per tick. Single mode promises the most recent bird.
    page = [_det(i, "Old Bird", f"old bird {i}") for i in range(1, 501)]
    tail = _det(900, "Newest Bird", "newest bird")
    svc.source = _PagedSource(page, tail, page_limit=500)
    svc._frame_bytes = b"resident"
    svc._set_cursor(0)
    svc._cursor_verified = True

    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason: rendered.append(det.common_name))
    svc._single_tick(datetime(2026, 9, 1, 8, 0, 0))

    assert rendered == ["Newest Bird"]
    assert svc._cursor() == 900


def test_collage_fallback_does_not_rerender_every_tick(svc, tmp_path, monkeypatch):
    # Collage mode with one species today: the fallback single render must
    # not repeat on every 20 s tick (a full-panel render each time on a Pi).
    base = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    rows = [make_row(base + timedelta(minutes=i), "Blue Jay", "Cyanocitta cristata", 0.9)
            for i in range(3)]
    svc.source.db_path = str(create_birds_db(tmp_path / "birds.db", rows))
    svc.config.mode = "collage"

    calls = []
    real = svc._render_single

    def counting(det, now, reason):
        calls.append(reason)
        real(det, now, reason)
    monkeypatch.setattr(svc, "_render_single", counting)

    now = datetime.now()
    svc._maybe_daytime_collage(now)
    svc._maybe_daytime_collage(now + timedelta(seconds=20))
    svc._maybe_daytime_collage(now + timedelta(seconds=40))
    assert calls == ["collage-fallback"]
    assert svc._meta.get("species_key") == "cyanocitta cristata"


def test_daytime_collage_tolerates_bad_collage_at(svc, monkeypatch):
    svc._meta = {"mode": "collage", "collage_at": "not-a-timestamp"}
    built = []
    monkeypatch.setattr(svc, "_build_collage", lambda *a, **k: built.append(a) or True)
    svc._maybe_daytime_collage(datetime.now())   # must not raise
    assert built


# -- quiet hours: the review date follows the ACTIVE window ------------------
def test_sun_mode_review_date_uses_sun_window_not_custom_fields(svc):
    # Custom fields left at a daytime window, but the active mode is "sun":
    # the sun window wraps midnight, so a 01:00 tick reviews yesterday.
    svc.config = Config(quiet_hours_mode="sun", quiet_hours_start="12:00",
                        quiet_hours_end="14:00")
    start, end = svc.config.quiet_window(date(2026, 6, 21))
    assert start > end                      # sunset -> sunrise wraps midnight
    assert svc._review_date(datetime(2026, 6, 22, 1, 0)) == date(2026, 6, 21)
    assert svc._review_date(datetime(2026, 6, 22, 21, 0)) == date(2026, 6, 22)


def test_custom_review_date_unchanged(svc):
    svc.config = Config(quiet_hours_mode="custom", quiet_hours_start="22:00",
                        quiet_hours_end="06:00")
    assert svc._review_date(datetime(2026, 8, 29, 0, 30)) == date(2026, 8, 28)
    assert svc._review_date(datetime(2026, 8, 29, 7, 0)) == date(2026, 8, 29)


# -- sources must not raise --------------------------------------------------
def test_birdnet_pi_bad_row_is_skipped_not_fatal(tmp_path):
    from featherframe.birdnet import BirdNetDB
    path = create_birds_db(tmp_path / "birds.db", [
        make_row(datetime(2026, 9, 1, 8, 0), "Blue Jay", "Cyanocitta cristata", 0.9)])
    conn = sqlite3.connect(path)
    # SQLite happily stores text in a FLOAT column; it sorts above every number
    # so it passes `Confidence >= ?` and then float() blows up in Python.
    conn.execute("INSERT INTO detections(Date, Time, Sci_Name, Com_Name, Confidence, File_Name) "
                 "VALUES ('2026-09-01', '08:01:00', 'Turdus migratorius', 'American Robin', "
                 "'oops', 'x.mp3')")
    conn.execute("INSERT INTO detections(Date, Time, Sci_Name, Com_Name, Confidence, File_Name) "
                 "VALUES ('2026-09-01', '08:02:00', 'Passer domesticus', 'House Sparrow', "
                 "0.95, 'y.mp3')")
    conn.commit()
    conn.close()
    src = BirdNetDB(str(path))
    got = src.new_since(0, 0.5)
    assert [d.common_name for d in got] == ["Blue Jay", "House Sparrow"]
    assert [d.common_name for d in src.latest_many(0.5)] == ["House Sparrow", "Blue Jay"]


def test_birdnet_go_summary_oddities_do_not_raise(monkeypatch):
    from featherframe.sources.birdnet_go import BirdNetGoSource
    src = BirdNetGoSource("http://x")
    monkeypatch.setattr(src, "_get", lambda path, params=None: [
        "not-a-dict",
        {"scientific_name": "A a", "first_heard": 12345},
        {"scientific_name": "B b", "first_heard": "2026-01-01T00:00:00Z"},
        {"scientific_name": "C c", "first_heard": None},
    ])
    assert src.all_time_species_count() == 3
    assert src.species_ordinal("B b") in (None, 1, 2)
    assert src.species_ordinal("nope") is None
    assert src.first_seen_date("A a") is None


def test_birdweather_top_species_only_answers_today(monkeypatch):
    from featherframe.sources.birdweather import BirdWeatherSource
    src = BirdWeatherSource("tok")
    monkeypatch.setattr(src, "_get", lambda path, params=None: [
        {"commonName": "Blue Jay", "scientificName": "Cyanocitta cristata", "detections": 5}])
    today = date.today()
    assert src.top_species_today(today)[0]["common"] == "Blue Jay"
    # period=day is today; yesterday's numbers are not this list.
    assert src.top_species_today(today - timedelta(days=1)) == []


def test_apprise_ingest_rejects_nonstring_and_bounds_names():
    from featherframe.sources.apprise_push import AppriseSource
    src = AppriseSource()
    assert src.ingest({"comname": {"a": 1}, "confidence": 0.9}) is None
    assert src.ingest({"comname": ["Blue Jay"], "sciname": 5}) is None
    det = src.ingest({"comname": "x" * 5000, "sciname": "y" * 5000, "confidence": "0.9"})
    assert det is not None
    assert len(det.common_name) <= 200 and len(det.scientific_name) <= 200


def test_apprise_malformed_persisted_item_is_dropped():
    from featherframe.sources.apprise_push import AppriseSource, _STORE_KEY

    class _KV:
        def __init__(self, v): self.v = v
        def get(self, k, d=None): return self.v if k == _STORE_KEY else d
        def set(self, k, v): self.v = v

    db = _KV({"counter": 2, "items": [
        {"id": 1, "date": "2026-09-01"},                       # missing keys
        {"id": 2, "date": "2026-09-01", "time": "08:00:00", "common": "Blue Jay",
         "scientific": "Cyanocitta cristata", "confidence": 0.9}]})
    src = AppriseSource(db=db)
    assert [d.rowid for d in src.new_since(0)] == [2]
    assert src.top_species_today(date(2026, 9, 1))[0]["common"] == "Blue Jay"


# -- generated cache: cooldown re-checked under the generation lock ----------
def test_queued_generation_respects_cooldown_set_while_waiting(tmp_path):
    from featherframe.render.genart import GeneratedArtProvider, ImageModel

    class Failing(ImageModel):
        name = "fail"

        def __init__(self):
            self.calls = 0

        def generate(self, prompt, size, refs):
            self.calls += 1
            raise RuntimeError("401")

    model = Failing()
    p = GeneratedArtProvider(model, cache_dir=tmp_path / "gen", refs=[])
    slug = "passer-domesticus"
    # Simulate a caller that passed the outer cooldown check, then the model
    # failed for this slug while it waited on the lock.
    p._failed_at[slug] = __import__("time").time()
    assert p._generate_to_cache(slug, "House Sparrow", "Passer domesticus") is False
    assert model.calls == 0


# -- second-pass review regressions -------------------------------------------
def test_backlog_keeps_cursor_when_latest_many_blips(svc, monkeypatch):
    # The tail jump must not happen before a candidate is in hand: latest_many
    # soft-fails to [] on a blip, and jumping first would swallow the backlog
    # and render nothing. Fall back to the page we already have.
    page = [_det(i, "Old Bird", f"old bird {i}") for i in range(1, 501)]
    tail = _det(900, "Newest Bird", "newest bird")
    src = _PagedSource(page, tail, page_limit=500)
    src.latest_many = lambda min_confidence=0.0, limit=25: []
    svc.source = src
    svc._frame_bytes = b"resident"
    svc._set_cursor(0)
    svc._cursor_verified = True

    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason: rendered.append(det.common_name))
    svc._single_tick(datetime(2026, 9, 1, 8, 0, 0))

    assert rendered == ["Old Bird"]          # something, not nothing
    assert svc._cursor() == 500              # the page, not the unseen tail


def test_torn_current_fff_is_not_served(tmp_path, monkeypatch):
    # A crash mid-write leaves a truncated frame; loading it under a fresh
    # ETag would hand the device a container it rejects on every wake.
    from featherframe import paths
    from featherframe.db import Database
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    db = Database()
    db.set("current_frame", {"etag": "deadbeefcafef00d", "mode": "single", "label": "x"})
    (paths.frames_dir() / "current.fff").write_bytes(b"FFF1" + b"\x00" * 200)
    service = FeatherframeService(db)
    assert service._frame_bytes is None
    assert service.current_etag() is None


def test_commit_writes_frame_atomically(svc, tmp_path):
    from featherframe import paths
    from featherframe.render.framebuffer import is_complete
    svc.force_test_detection("Northern Cardinal", "Cardinalis cardinalis")
    data = (paths.frames_dir() / "current.fff").read_bytes()
    assert is_complete(data)
    assert not (paths.frames_dir() / "current.fff.tmp").exists()


def test_non_ascii_apprise_token_is_403_not_500(client, svc):
    from featherframe.sources.apprise_push import AppriseSource
    svc.config.apprise_token = "pájaro"
    svc.source = AppriseSource(svc.db)
    bad = client.post("/api/ingest/apprise/p%C3%A1jara", json={"comname": "Blue Jay"})
    assert bad.status_code == 403
    good = client.post("/api/ingest/apprise/p%C3%A1jaro",
                       json={"comname": "Blue Jay", "sciname": "Cyanocitta cristata"})
    assert good.status_code == 200


def test_chunked_ingest_body_is_capped(client, svc):
    from featherframe.sources.apprise_push import AppriseSource
    svc.config.apprise_token = ""
    svc.source = AppriseSource(svc.db)

    def chunks():
        for _ in range(80):
            yield b"x" * 1024
    r = client.post("/api/ingest/apprise", content=chunks(),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_usb_only_unit_is_not_flagged_low_battery(client, svc):
    # An empty JST socket reads ~0 V; that is "no pack", not a flat one.
    client.get("/api/frame", headers={"X-Battery-Voltage": "0.031", "X-Battery-Percent": "0"})
    card = client.get("/api/status").json()["frame_card"]
    assert card["battery"] is None
    assert card["battery_low"] is False
