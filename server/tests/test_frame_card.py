"""The config page's frame card (W-587): wall-frame health at a glance.

All the reasoning is server-side — frame_card() turns the recorded check-in
into ready-to-print strings plus an overdue flag, so the template just prints.
Overdue means the device has missed two consecutive wake intervals.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from jinja2 import Environment, FileSystemLoader

from featherframe import paths
from featherframe.service import DeviceStatus, FeatherframeService, frame_card

NOW = datetime(2026, 8, 28, 9, 0, 0)


def _dev(minutes_ago: float, **kw) -> DeviceStatus:
    then = NOW - timedelta(minutes=minutes_ago)
    return DeviceStatus(last_checkin=then.isoformat(timespec="seconds"), **kw)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    yield FeatherframeService()


# -- the pure computation ---------------------------------------------------
def test_never_seen():
    card = frame_card(DeviceStatus(), 15, NOW)
    assert card["seen"] is False
    assert card["overdue"] is False
    assert card["last_seen"] is None


def test_garbled_checkin_counts_as_never_seen():
    card = frame_card(DeviceStatus(last_checkin="not-a-date"), 15, NOW)
    assert card["seen"] is False


def test_fresh_checkin():
    dev = _dev(7, battery_voltage=3.95, battery_percent=72, last_result="304")
    card = frame_card(dev, 15, NOW)
    assert card["seen"] is True
    assert card["overdue"] is False
    assert card["last_seen"] == "7 min ago"
    assert card["battery"] == "3.95 V · 72%"
    assert card["served"] == "up to date (304)"


def test_overdue_after_two_wake_intervals():
    assert frame_card(_dev(30), 15, NOW)["overdue"] is False  # boundary holds
    late = frame_card(_dev(31), 15, NOW)
    assert late["overdue"] is True
    assert late["expected_minutes"] == 15
    assert late["seen"] is True


def test_served_words():
    assert frame_card(_dev(1, last_result="frame"), 15, NOW)["served"] == "new frame"
    assert frame_card(_dev(1, last_result="collage"), 15, NOW)["served"] == "collage view"
    assert frame_card(_dev(1), 15, NOW)["served"] is None


def test_battery_without_percent():
    card = frame_card(_dev(1, battery_voltage=4.1), 15, NOW)
    assert card["battery"] == "4.10 V"
    assert frame_card(_dev(1), 15, NOW)["battery"] is None


def test_relative_times():
    assert frame_card(_dev(0.5), 15, NOW)["last_seen"] == "just now"
    assert frame_card(_dev(90), 15, NOW)["last_seen"] == "1 h ago"
    assert frame_card(_dev(60 * 24 * 3), 15, NOW)["last_seen"] == "3 days ago"


# -- through the service ----------------------------------------------------
def test_status_exposes_frame_card(svc):
    card = svc.status()["frame_card"]
    assert card["seen"] is False
    assert card["expected_minutes"] == svc.config.wake_interval_minutes


def test_device_checkin_flows_to_card(svc):
    svc.get_frame(None, "esp32-featherframe", 3.95, 72, wifi_rssi=-61)
    card = svc.status()["frame_card"]
    assert card["seen"] is True
    assert card["overdue"] is False
    assert card["last_seen"] == "just now"
    assert card["battery"] == "3.95 V · 72%"
    assert card["wifi_rssi"] == -61


# -- the page renders all three states --------------------------------------
def _render_page(svc) -> str:
    env = Environment(loader=FileSystemLoader(str(paths.templates_dir())),
                      autoescape=True)
    return env.get_template("index.html").render(
        status=svc.status(), config=svc.config, version="test", generated=[])


def test_page_never_seen(svc):
    assert "connected yet" in _render_page(svc)


def test_page_fresh(svc):
    svc.get_frame(None, "esp32-featherframe", 3.95, 72, wifi_rssi=-61)
    html = _render_page(svc)
    assert "just now" in html
    assert "3.95 V · 72%" in html
    assert "connected yet" not in html
    assert "overdue — expected" not in html


def test_page_overdue(svc):
    late = datetime.now() - timedelta(minutes=svc.config.wake_interval_minutes * 2 + 5)
    svc.device = DeviceStatus(last_checkin=late.isoformat(timespec="seconds"),
                              battery_voltage=3.6, battery_percent=31,
                              last_result="304")
    html = _render_page(svc)
    assert f"overdue — expected every {svc.config.wake_interval_minutes} min" in html
