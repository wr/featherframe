"""A frame on the collage checks in when the collage is next redrawn (W-833):
its own Update interval is neither read nor offered, and its checks follow the
collage's clock — the interval after the last draw by day, the start of quiet
hours, midnight, or the end of quiet hours at night — so a kit polling once
every few hours never lags the collage by hours of its own."""
from __future__ import annotations

from datetime import datetime

import pytest
from starlette.testclient import TestClient

from featherframe import service as service_mod
from featherframe.config import save_config
from featherframe.render import pipeline
from tests._frames import EE03_PANEL, FRAME_ID, add_kit

HEAD = {"X-Device-Id": FRAME_ID, "X-Panel": EE03_PANEL}
TRMNL = {"ID": "CC:CC:CC:00:00:02", "Model": "x", "Width": "1872", "Height": "1404"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start, svc.config.quiet_hours_end = "22:00", "06:00"
    svc.config.collage_interval_hours = 4
    save_config(svc.db, svc.config)     # a tick reloads the household from the DB
    svc._clock = lambda: datetime(2026, 9, 21, 12, 0)
    app.state.service = svc
    return TestClient(app)


def _drawn_at(svc, when: datetime):
    """As if the collage had last been drawn then (after any tick, which
    would draw it now)."""
    svc.pictures["collage"].meta["collage_at"] = when.isoformat(timespec="seconds")
    svc.pictures["collage"].etag = svc.pictures["collage"].etag or "c0ffee"


def _headers(client, svc, drawn: datetime):
    add_kit(svc, shows="collage", device_poll_seconds=5, wake_interval_minutes=15)
    svc.tick()
    _drawn_at(svc, drawn)
    return client.get("/api/frame", headers=HEAD).headers


def test_on_plates_the_intervals_are_the_owners(client):
    svc = client.app.state.service
    add_kit(svc, shows="plates", device_poll_seconds=5, wake_interval_minutes=15)
    svc.tick()
    h = client.get("/api/frame", headers=HEAD).headers
    assert h["x-poll-seconds"] == "5" and h["x-wake-minutes"] == "15"


def test_on_the_collage_the_checks_land_just_after_the_next_redraw(client):
    svc = client.app.state.service
    h = _headers(client, svc, datetime(2026, 9, 21, 10, 0))   # due again at 14:00
    expect = 2 * 3600 + service_mod.COLLAGE_CHECK_MARGIN_S
    assert h["x-poll-seconds"] == str(expect)
    assert h["x-wake-minutes"] == str(-(-expect // 60))


def test_quiet_hours_and_midnight_come_first_when_they_are_sooner(client):
    svc = client.app.state.service
    svc._clock = lambda: datetime(2026, 9, 21, 21, 0)
    _drawn_at(svc, datetime(2026, 9, 21, 20, 0))          # due at 00:00, but quiet starts at 22:00
    assert svc.collage_next_at(svc._clock()) == datetime(2026, 9, 21, 22, 0)
    svc.config.quiet_hours_mode = "off"
    svc.config.collage_interval_hours = 24
    save_config(svc.db, svc.config)
    _drawn_at(svc, datetime(2026, 9, 21, 3, 0))           # due 03:00 tomorrow, but the day ends first
    assert svc.collage_next_at(svc._clock()) == datetime(2026, 9, 22, 0, 0)


def test_in_quiet_hours_the_next_check_is_the_morning(client):
    svc = client.app.state.service
    svc._clock = lambda: datetime(2026, 9, 21, 23, 30)
    _drawn_at(svc, datetime(2026, 9, 21, 22, 0))
    svc.db.set("quiet_collage_for", "2026-09-21")       # the nightly one is up
    assert svc.collage_next_at(svc._clock()) == datetime(2026, 9, 22, 6, 0)
    # Before the nightly draw there is nothing to wait for: come back soon.
    svc.db.set("quiet_collage_for", "2026-09-20")
    assert svc.collage_next_at(svc._clock()) is None
    assert svc.frame_intervals(add_kit(svc, shows="collage")) == (service_mod.COLLAGE_CHECK_FLOOR_S, 1)


def test_a_check_that_comes_before_the_draw_has_landed_tries_again_soon(client):
    svc = client.app.state.service
    h = _headers(client, svc, datetime(2026, 9, 21, 7, 0))    # overdue since 11:00
    assert h["x-poll-seconds"] == str(service_mod.COLLAGE_CHECK_FLOOR_S)


def test_a_trmnl_on_the_collage_follows_it_too(client):
    svc = client.app.state.service
    client.get("/api/setup", headers=TRMNL)
    svc.answer_frame(TRMNL["ID"], "add")
    svc.update_frame(TRMNL["ID"], {"shows": "collage"})
    svc.tick()
    _drawn_at(svc, datetime(2026, 9, 21, 11, 0))
    row = svc.frames.get(TRMNL["ID"])
    assert svc.viewer_refresh_seconds(row) == 3 * 3600 + service_mod.COLLAGE_CHECK_MARGIN_S
    svc.update_frame(TRMNL["ID"], {"shows": "plates"})
    assert svc.viewer_refresh_seconds(svc.frames.get(TRMNL["ID"])) == 900


def test_the_row_is_locked_on_the_collage_and_quotes_the_collages_interval(client):
    svc = client.app.state.service
    add_kit(svc, shows="collage", device_poll_seconds=5)
    row = client.get("/").text.split(f'data-frame="{FRAME_ID}"')[1].split("</li>")[0]
    assert '<div class="frow locked" data-fr-interval>' in row
    assert "Set by the collage’s update interval." in row
    assert 'data-fr-collage-every >Every 4 hours<' in row
    svc.update_frame(FRAME_ID, {"shows": "plates"})
    row = client.get("/").text.split(f'data-frame="{FRAME_ID}"')[1].split("</li>")[0]
    assert '<div class="frow" data-fr-interval>' in row and "Set by the collage" not in row
