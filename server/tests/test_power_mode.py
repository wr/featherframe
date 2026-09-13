"""Power model and wake interval served to the device (W-736 / W-456).

The config page is the one place either is set. Both ride every /api/frame
response as X-Power-Mode and X-Wake-Minutes — a 304 and a 503 included, since
a frame that is up to date or waiting for its first bird must still learn
that it has been switched to deep sleep.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, load_config, save_config
from featherframe.db import Database


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_MDNS", "0")
    from featherframe.app import app
    with TestClient(app) as c:
        yield c


def _seed_frame(client) -> str:
    svc = client.app.state.service
    svc._frame_bytes = b"FFF1" + bytes(12)
    svc._etag = "abc123"
    return svc._etag


# -- config ----------------------------------------------------------------

def test_power_mode_defaults_to_awake():
    assert Config().power_mode == "awake"


def test_power_mode_rejects_unknown_values():
    assert Config(power_mode="hibernate").power_mode == "awake"


def test_power_mode_round_trips(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    save_config(db, Config(power_mode="sleep", wake_interval_minutes=30))
    cfg = load_config(db)
    assert (cfg.power_mode, cfg.wake_interval_minutes) == ("sleep", 30)


# -- headers ---------------------------------------------------------------

def _set(client, **fields):
    """Persist through the service so the tick thread's reload can't undo it."""
    svc = client.app.state.service
    cfg = Config(**{**svc.config.to_dict(), **fields})
    svc.update_config(cfg)


def test_headers_on_200_and_304(client):
    etag = _seed_frame(client)
    _set(client, power_mode="sleep", wake_interval_minutes=30)

    r = client.get("/api/frame")
    assert r.status_code == 200
    assert r.headers["x-power-mode"] == "sleep"
    assert r.headers["x-wake-minutes"] == "30"

    r = client.get("/api/frame", headers={"If-None-Match": f'"{etag}"'})
    assert r.status_code == 304
    assert r.headers["x-power-mode"] == "sleep"
    assert r.headers["x-wake-minutes"] == "30"


def test_headers_on_503_before_first_bird(client):
    svc = client.app.state.service
    svc._frame_bytes, svc._etag = None, None
    r = client.get("/api/frame")
    assert r.status_code == 503
    assert r.headers["x-power-mode"] == "awake"
    assert r.headers["x-wake-minutes"] == str(svc.config.wake_interval_minutes)


def test_headers_on_button_views(client):
    svc = client.app.state.service
    _set(client, dither="none", power_mode="sleep")  # dither: keep the render cheap
    r = client.get("/api/frame", params={"view": "status"})
    assert r.status_code == 200
    assert r.headers["x-power-mode"] == "sleep"


# -- settings form -----------------------------------------------------------

def test_settings_form_sets_power_mode(client):
    svc = client.app.state.service
    form = {k: str(v) for k, v in svc.config.to_dict().items()
            if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    form["power_mode"] = "sleep"
    form["wake_interval_minutes"] = "60"
    r = client.post("/settings", data=form, follow_redirects=False)
    assert r.status_code in (200, 303)
    assert svc.config.power_mode == "sleep"
    assert svc.config.wake_interval_minutes == 60


# -- frame card follows the power model ----------------------------------------

def test_overdue_threshold_follows_power_mode():
    from datetime import datetime, timedelta
    from featherframe.service import DeviceStatus, frame_card
    now = datetime(2026, 9, 13, 12, 0, 0)
    dev = DeviceStatus(last_checkin=(now - timedelta(minutes=8)).isoformat())
    # Deep sleep on a 15 min interval: 8 min is one skipped wake, not overdue.
    asleep = frame_card(dev, 15, now, power_mode="sleep")
    assert asleep["overdue"] is False
    assert asleep["overdue_text"] == "Overdue — wakes every 15 min"
    # Always awake polls every 15 s: 8 min of silence is an outage.
    awake = frame_card(dev, 15, now, power_mode="awake")
    assert awake["overdue"] is True
    assert awake["overdue_text"] == "Overdue — checks in every few seconds"


def test_wake_interval_row_hidden_unless_deep_sleep(client):
    svc = client.app.state.service
    _set(client, power_mode="awake")
    html = client.get("/").text
    assert 'class="reveal collapsed" id="wake-field" hidden' in html
    _set(client, power_mode="sleep")
    html = client.get("/").text
    assert 'class="reveal" id="wake-field" >' in html


# -- device poll interval (W-775) --------------------------------------------

def test_device_poll_seconds_served_and_clamped(client):
    etag = _seed_frame(client)
    _set(client, device_poll_seconds=3)
    r = client.get("/api/frame", headers={"If-None-Match": f'"{etag}"'})
    assert r.status_code == 304
    assert r.headers["x-poll-seconds"] == "3"
    assert Config(device_poll_seconds=0).device_poll_seconds == 2
    assert Config(device_poll_seconds=999).device_poll_seconds == 60
    assert Config(poll_interval_seconds=2).poll_interval_seconds == 2


def test_check_every_row_shown_only_when_awake(client):
    _set(client, power_mode="awake")
    html = client.get("/").text
    assert 'class="reveal" id="poll-field" >' in html
    assert 'class="reveal collapsed" id="wake-field" hidden' in html
    _set(client, power_mode="sleep")
    html = client.get("/").text
    assert 'class="reveal collapsed" id="poll-field" hidden' in html
