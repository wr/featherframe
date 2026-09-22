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
from tests._frames import FRAME_ID, add_kit, seed_frame


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_MDNS", "0")
    from featherframe.app import app
    with TestClient(app) as c:
        yield c


HEAD = {"X-Device-Id": FRAME_ID}


def _seed_frame(client) -> str:
    """One kit with something already on its glass."""
    return seed_frame(client.app.state.service)


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
    """The frame's own settings live on its row (W-833); the household's rest
    goes through the service so the tick thread's reload can't undo it."""
    svc = client.app.state.service
    own = {k: v for k, v in fields.items()
           if k in ("power_mode", "wake_interval_minutes", "device_poll_seconds",
                    "panel_rotation", "mat_inset_pct", "shows")}
    rest = {k: v for k, v in fields.items() if k not in own}
    if rest:
        svc.update_config(Config(**{**svc.config.to_dict(), **rest}))
    if own:
        if svc.frames.get(FRAME_ID) is None:
            add_kit(svc)
        svc.update_frame(FRAME_ID, own)


def test_headers_on_200_and_304(client):
    etag = _seed_frame(client)
    _set(client, power_mode="sleep", wake_interval_minutes=30)

    r = client.get("/api/frame", headers=HEAD)
    assert r.status_code == 200
    assert r.headers["x-power-mode"] == "sleep"
    assert r.headers["x-wake-minutes"] == "30"

    r = client.get("/api/frame", headers={**HEAD, "If-None-Match": f'"{etag}"'})
    assert r.status_code == 304
    assert r.headers["x-power-mode"] == "sleep"
    assert r.headers["x-wake-minutes"] == "30"


def test_headers_on_503_before_first_bird(client):
    svc = client.app.state.service
    add_kit(svc)
    r = client.get("/api/frame", headers=HEAD)
    assert r.status_code == 503
    assert r.headers["x-power-mode"] == "awake"
    assert r.headers["x-wake-minutes"] == str(
        svc.frame_config(svc.frames.get(FRAME_ID)).wake_interval_minutes)


def test_headers_on_button_views(client):
    _seed_frame(client)
    _set(client, power_mode="sleep")
    r = client.get("/api/frame", params={"view": "status"}, headers=HEAD)
    assert r.status_code == 200
    assert r.headers["x-power-mode"] == "sleep"


# -- the frame's own row is the only place it is set --------------------------

def test_the_frames_endpoint_sets_power_mode(client):
    svc = client.app.state.service
    add_kit(svc)
    r = client.post(f"/api/frames/{FRAME_ID}",
                    json={"power_mode": "sleep", "wake_interval_minutes": 60})
    assert r.json()["ok"]
    cfg = svc.frame_config(svc.frames.get(FRAME_ID))
    assert (cfg.power_mode, cfg.wake_interval_minutes) == ("sleep", 60)
    assert svc.frames.get(FRAME_ID)["set"]["power_mode"] == "sleep"


def test_the_household_form_no_longer_takes_them(client):
    """W-833: the power model is a frame's, so /settings must not move it."""
    svc = client.app.state.service
    add_kit(svc)
    before = svc.frame_config(svc.frames.get(FRAME_ID)).power_mode
    r = client.post("/settings", data={"power_mode": "sleep",
                                       "wake_interval_minutes": "60",
                                       "quiet_hours_mode": "custom"},
                    follow_redirects=False)
    assert r.status_code in (200, 303)
    assert svc.frame_config(svc.frames.get(FRAME_ID)).power_mode == before
    assert "power_mode" not in svc.frames.get(FRAME_ID)["set"]


# -- frame card follows the power model ----------------------------------------

def test_overdue_threshold_follows_power_mode():
    from datetime import datetime, timedelta
    from featherframe.service import frame_card
    now = datetime(2026, 9, 13, 12, 0, 0)
    dev = {"last_checkin": (now - timedelta(minutes=8)).isoformat()}
    # Deep sleep on a 15 min interval: 8 min is one skipped wake, not overdue.
    asleep = frame_card(dev, 15, now, power_mode="sleep")
    assert asleep["overdue"] is False
    assert asleep["overdue_text"] == "Overdue — wakes every 15 min"
    # Always awake polls every 15 s: 8 min of silence is an outage.
    awake = frame_card(dev, 15, now, power_mode="awake")
    assert awake["overdue"] is True
    assert awake["overdue_text"] == "Overdue — checks in every few seconds"


def _row(client) -> str:
    body = client.get("/").text.split("<body")[1]
    return body.split(f'data-frame="{FRAME_ID}"')[1].split("\n    </li>")[0]


def test_wake_interval_row_hidden_unless_deep_sleep(client):
    _set(client, power_mode="awake")
    assert '<span data-fr-swap="sleep" hidden>' in _row(client)
    _set(client, power_mode="sleep")
    assert '<span data-fr-swap="sleep" >' in _row(client)


# -- device poll interval (W-775) --------------------------------------------

def test_device_poll_seconds_served_and_clamped(client):
    etag = _seed_frame(client)
    _set(client, device_poll_seconds=3)
    r = client.get("/api/frame", headers={**HEAD, "If-None-Match": f'"{etag}"'})
    assert r.status_code == 304
    assert r.headers["x-poll-seconds"] == "3"
    assert Config(device_poll_seconds=0).device_poll_seconds == 2
    assert Config(device_poll_seconds=999999).device_poll_seconds == 86400


def test_one_update_interval_swaps_its_options_with_the_power_model(client):
    """Same label, same place: seconds while awake, minutes in deep sleep."""
    _set(client, power_mode="awake")
    row = _row(client)
    assert row.count(">Update interval<") == 1
    assert '<span data-fr-swap="awake" >' in row
    assert '<span data-fr-swap="sleep" hidden>' in row
    assert 'data-f="device_poll_seconds"' in row and 'data-f="wake_interval_minutes"' in row
    _set(client, power_mode="sleep")
    assert '<span data-fr-swap="awake" hidden>' in _row(client)
