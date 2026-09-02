"""W-694: USB vs battery inferred from the voltage trend, the battery log
behind it, and the endpoint that feeds the Frame card's trend line."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe.db import Database
from featherframe.service import FeatherframeService, power_state


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


NOW = datetime(2026, 9, 2, 10, 0, 0)


def _hist(*pairs):
    """[(minutes_ago, volts), ...] -> battery_log-shaped rows, oldest first."""
    rows = [{"at": (NOW - timedelta(minutes=m)).isoformat(timespec="seconds"), "voltage": v}
            for m, v in pairs]
    return sorted(rows, key=lambda r: r["at"])


def test_full_and_flat_is_on_usb():
    st = power_state(_hist((80, 4.20), (60, 4.20), (30, 4.21), (10, 4.20), (0, 4.20)), NOW)
    assert st["state"] == "usb" and st["text"] == "on USB"


def test_sustained_climb_is_charging():
    st = power_state(_hist((38, 3.90), (32, 3.91), (28, 3.93), (22, 3.94), (18, 3.96),
                           (12, 3.97), (8, 3.99), (2, 4.00)), NOW)
    assert st["state"] == "charging"


def test_step_then_flat_is_recovery_not_charging():
    # A cell relaxing after a heavy load rises once and then holds; that is
    # not a charger.
    st = power_state(_hist((38, 3.90), (32, 3.90), (28, 3.97), (22, 3.97), (18, 3.97),
                           (12, 3.97), (8, 3.97), (2, 3.97)), NOW)
    assert st["state"] == "battery"


def test_live_readings_decide_usb_within_minutes():
    # The log still says 4.08 (last row 4 min ago) but the frame has been
    # reporting 4.23 for the last two minutes: that is USB, now.
    live = _hist((2, 4.23), (1.5, 4.22), (1, 4.23), (0.5, 4.23), (0, 4.23))
    st = power_state(_hist((34, 4.08), (29, 4.08), (24, 4.08), (19, 4.08), (14, 4.08), (9, 4.08), (4, 4.08)),
                     NOW, live)
    assert st["state"] == "usb"
    # ...and the moment it is unplugged the live median drops it again.
    live2 = _hist((2, 4.23), (1.5, 4.09), (1, 4.06), (0.5, 4.09), (0, 4.06))
    st2 = power_state(_hist((34, 4.08), (4, 4.23)), NOW, live2)
    assert st2["state"] != "usb"


def test_falling_or_flat_below_full_is_on_battery():
    assert power_state(_hist((80, 4.05), (60, 4.04), (30, 4.03), (0, 4.02)), NOW)["state"] == "battery"
    assert power_state(_hist((80, 3.90), (60, 3.90), (30, 3.91), (0, 3.90)), NOW)["state"] == "battery"


def test_noise_does_not_read_as_charging():
    # ±30 mV wander between check-ins (the calibrated ADC path) is not a charge.
    st = power_state(_hist((80, 3.90), (70, 3.93), (60, 3.88), (50, 3.92), (30, 3.89),
                           (20, 3.93), (10, 3.90), (0, 3.92)), NOW)
    assert st["state"] == "battery"


def test_no_baseline_falls_back_to_level():
    assert power_state(_hist((5, 4.21), (0, 4.20)), NOW)["state"] == "usb"
    assert power_state(_hist((5, 3.95), (0, 3.94)), NOW)["state"] == "battery"


def test_card_shows_the_live_median_not_the_flicker(client, svc):
    for v, p in (("4.09", "89"), ("4.06", "86"), ("4.09", "89"), ("4.06", "86"), ("4.09", "89")):
        client.get("/api/frame", headers={"X-Battery-Voltage": v, "X-Battery-Percent": p})
    card = client.get("/api/status").json()["frame_card"]
    assert card["battery"].startswith("4.09 V · 89%")
    body = client.get("/api/battery").json()
    assert body["items"][-1]["voltage"] == pytest.approx(4.09)   # the line ends on the shown value


def test_stale_or_empty_history_is_unknown():
    assert power_state([], NOW)["state"] == "unknown"
    assert power_state(_hist((45, 4.20)), NOW)["state"] == "unknown"
    assert power_state([{"at": "garbage", "voltage": "x"}], NOW)["state"] == "unknown"


def test_battery_log_downsamples_and_prunes(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    t0 = NOW
    assert db.log_battery(t0.isoformat(), 4.0, 80) is True
    assert db.log_battery((t0 + timedelta(seconds=15)).isoformat(), 4.0, 80) is False
    assert db.log_battery((t0 + timedelta(minutes=5)).isoformat(), 3.99, 79) is True
    old = (t0 - timedelta(days=8)).isoformat()
    db._conn.execute("INSERT INTO battery_log(at, voltage, percent) VALUES(?,?,?)", (old, 3.5, 20))
    db.log_battery((t0 + timedelta(minutes=10)).isoformat(), 3.98, 78)
    rows = db.battery_history((t0 - timedelta(days=30)).isoformat())
    assert [r["voltage"] for r in rows] == [4.0, 3.99, 3.98]   # the 8-day-old row is gone


def test_checkin_logs_battery_and_card_says_power(client, svc):
    client.get("/api/frame", headers={"X-Battery-Voltage": "4.21", "X-Battery-Percent": "100"})
    rows = svc.db.battery_history("2000-01-01")
    assert len(rows) == 1 and rows[0]["voltage"] == pytest.approx(4.21)
    card = client.get("/api/status").json()["frame_card"]
    assert card["power"]["state"] == "usb"
    assert card["battery"].endswith("on USB")
    assert card["battery_low"] is False


def test_no_pack_is_not_logged(client, svc):
    client.get("/api/frame", headers={"X-Battery-Voltage": "0.03", "X-Battery-Percent": "0"})
    assert svc.db.battery_history("2000-01-01") == []


def test_battery_endpoint_shape(client, svc):
    client.get("/api/frame", headers={"X-Battery-Voltage": "3.95", "X-Battery-Percent": "70"})
    body = client.get("/api/battery?hours=24").json()
    assert body["hours"] == 24 and body["usb_v"] == pytest.approx(4.19)
    assert body["items"][0]["voltage"] == pytest.approx(3.95)
    assert body["power"]["state"] in ("battery", "unknown", "usb", "charging")
    assert client.get("/api/battery?hours=99999").json()["hours"] == 24 * 7


def test_power_row_hides_percent_on_usb_and_shows_it_on_battery(client, svc):
    from featherframe.app import templates
    def page():
        return client.get("/").text
    client.get("/api/frame", headers={"X-Battery-Voltage": "4.21", "X-Battery-Percent": "100"})
    html = page()
    assert "<dt>Power source</dt>" in html
    usb = html.split('id="pw-usb"')[1].split(">")[0]
    wrap = html.split('id="fc-batt-wrap"')[1].split(">")[0]
    assert "hidden" not in usb and "hidden" in wrap          # USB: icon + word, no percent
    # A fresh service with a mid-charge cell and no history reads as on battery.
    svc.db._conn.execute("DELETE FROM battery_log"); svc.db._conn.commit()
    svc._battery_live = []
    client.get("/api/frame", headers={"X-Battery-Voltage": "3.90", "X-Battery-Percent": "65"})
    html = page()
    usb = html.split('id="pw-usb"')[1].split(">")[0]
    wrap = html.split('id="fc-batt-wrap"')[1].split(">")[0]
    assert "hidden" in usb and "hidden" not in wrap and "65%" in html


def test_settings_post_without_show_battery_is_fine(client, svc):
    r = client.post("/settings", data={"mode": "single", "wake_interval_minutes": "15"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "show_battery" not in svc.config.to_dict()
