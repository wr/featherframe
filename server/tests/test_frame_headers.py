"""What rides on every /api/frame response, the quiet-hours window, and which
saves repaint the glass. (The parts of the old dark-mode tests that were never
about dark mode; W-821 removed the rest.)"""
from __future__ import annotations

from datetime import date, time as dtime

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, _sun_window


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The real app wired to a real service, without the lifespan's scheduler
    thread — tests set the resident frame directly."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    app.state.service = svc
    return TestClient(app)


def _seed_frame(client) -> str:
    svc = client.app.state.service
    svc._frame_bytes = b"FFF1" + bytes(12)
    svc._etag = "abc123"
    return svc._etag


def test_fielded_firmware_is_told_not_to_invert(client):
    """Dark mode is gone, but firmware that still reads X-FF-Invert keeps its
    last value in NVS unless told otherwise: a 304 says "0" too."""
    etag = _seed_frame(client)
    assert client.get("/api/frame").headers["x-ff-invert"] == "0"
    r = client.get("/api/frame", headers={"If-None-Match": f'"{etag}"'})
    assert r.status_code == 304 and r.headers["x-ff-invert"] == "0"


def test_a_stored_dark_mode_is_dropped_and_the_page_has_no_control(client):
    assert "dark_mode" not in Config.from_dict({"dark_mode": "quiet"}).to_dict()
    assert 'name="dark_mode"' not in client.get("/").text


def test_frame_response_carries_the_rotation(client):
    # The firmware turns its baked boot screens and pills to match (X-FF-Rotation),
    # on a 304 too, so a frame flipped on the page is right at its next boot.
    etag = _seed_frame(client)
    svc = client.app.state.service
    svc.config.panel_rotation = 270
    assert client.get("/api/frame").headers["x-ff-rotation"] == "270"
    r = client.get("/api/frame", headers={"If-None-Match": f'"{etag}"'})
    assert r.status_code == 304 and r.headers["x-ff-rotation"] == "270"


# Checkboxes that default on: omitting one from the form would turn it off.
_BASE_FORM = {"quiet_hours_mode": "custom", "imagegen_enabled": "on", "collage_generated": "on"}


def test_only_a_render_setting_repaints_on_save(client, monkeypatch):
    svc = client.app.state.service
    calls = []
    monkeypatch.setattr(svc, "rerender_current", lambda: calls.append(1))
    client.post("/settings", data={**_BASE_FORM, "panel_rotation": "270"}, follow_redirects=False)
    assert svc.config.panel_rotation == 270 and len(calls) == 1
    client.post("/settings", data={**_BASE_FORM, "panel_rotation": "270"}, follow_redirects=False)
    assert len(calls) == 1                               # nothing changed
    client.post("/settings", data={**_BASE_FORM, "panel_rotation": "270",
                                   "wake_interval_minutes": "30"}, follow_redirects=False)
    assert svc.config.wake_interval_minutes == 30 and len(calls) == 1


def test_sun_window_is_a_seasonal_night():
    sunset_s, sunrise_s = _sun_window(date(2026, 6, 21))   # summer solstice
    sunset_w, sunrise_w = _sun_window(date(2026, 12, 21))  # winter solstice
    # The night window wraps midnight: evening sunset later than morning sunrise.
    assert sunset_s > sunrise_s and sunset_w > sunrise_w
    # Longer summer days -> later sunset, earlier sunrise than winter.
    assert sunset_s > sunset_w
    assert sunrise_s < sunrise_w


def test_quiet_hours_sun_mode_uses_window():
    c = Config(quiet_hours_mode="sun")
    # 2 a.m. is always before sunrise at the default latitude; midday never is.
    assert c.in_quiet_hours(dtime(2, 0)) is True
    assert c.in_quiet_hours(dtime(12, 0)) is False


def test_legacy_quiet_hours_enabled_migrates():
    # Pre-mode configs stored only quiet_hours_enabled.
    assert Config.from_dict({"quiet_hours_enabled": False}).quiet_hours_mode == "off"
    assert Config.from_dict({"quiet_hours_enabled": True}).quiet_hours_mode == "custom"


def test_a_frame_left_inverted_by_the_old_dark_mode_is_redrawn_once(client, monkeypatch):
    svc = client.app.state.service
    svc._frame_bytes = b"FFF1" + bytes(12)
    svc._meta = {"mode": "single", "label": "Blue Jay", "species_key": "cyanocitta cristata",
                 "dark": True}
    calls = []
    monkeypatch.setattr(svc, "rerender_current", lambda: calls.append(1))
    svc.tick(); svc.tick()
    assert calls == [1] and "dark" not in svc._meta
