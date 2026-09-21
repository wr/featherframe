"""The page's Health card (W-587, per frame since W-833): a frame's health
at a glance.

All the reasoning is server-side — frame_card() turns the recorded check-in
into ready-to-print strings plus an overdue flag, so the template just prints.
Overdue means the device has missed two consecutive wake intervals.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from jinja2 import Environment, FileSystemLoader

from featherframe import paths
from featherframe.service import FeatherframeService, frame_card
from tests._frames import FRAME_ID, add_kit, device, health

NOW = datetime(2026, 8, 28, 9, 0, 0)


def _dev(minutes_ago: float, **kw) -> dict:
    """One frame\'s report, `minutes_ago` old — what its row holds."""
    then = NOW - timedelta(minutes=minutes_ago)
    return {"last_checkin": then.isoformat(timespec="seconds"), **kw}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    add_kit(service)
    yield service


def _checkin(svc, **telemetry) -> None:
    """A check-in from the one kit, as /api/frame records it."""
    svc._record_checkin(FRAME_ID, telemetry)


# -- the pure computation ---------------------------------------------------
def test_never_seen():
    card = frame_card({}, 15, NOW)
    assert card["seen"] is False
    assert card["overdue"] is False
    assert card["last_seen"] is None


def test_garbled_checkin_counts_as_never_seen():
    card = frame_card({"last_checkin": "not-a-date"}, 15, NOW)
    assert card["seen"] is False


def test_fresh_checkin():
    dev = _dev(7, battery_voltage=3.95, battery_percent=72, last_result="304")
    card = frame_card(dev, 15, NOW)
    assert card["seen"] is True
    assert card["overdue"] is False
    assert card["last_seen"] == "7 min ago"
    assert card["battery"].startswith("3.95 V · 72%")   # "· on battery" etc. follows (W-694)
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
def test_status_carries_a_health_block_per_frame(svc):
    card = [f for f in svc.status()["frames"]["list"] if f["id"] == FRAME_ID][0]["card"]
    assert card["seen"] is False
    assert card["expected_minutes"] == svc.config.wake_interval_minutes


def test_device_checkin_flows_to_card(svc):
    _checkin(svc, user_agent="esp32-featherframe", battery_voltage=3.95,
             battery_percent=72, wifi_rssi=-61)
    card = health(svc)
    assert card["seen"] is True
    assert card["overdue"] is False
    assert card["last_seen"] == "just now"
    assert card["battery"].startswith("3.95 V · 72%")   # "· on battery" etc. follows (W-694)
    assert card["wifi_rssi"] == -61


# -- the page renders all three states --------------------------------------
def test_battery_critical_under_ten_percent():
    # W-736: low (the red bar) starts at 20 %; critical (the page banner) is
    # the last stretch before the firmware's own 3.45 V hold.
    low = frame_card(_dev(1, battery_voltage=3.58, battery_percent=18), 15, NOW)
    assert low["battery_low"] is True and low["battery_critical"] is False
    assert frame_card(_dev(1, battery_voltage=3.50, battery_percent=9), 15, NOW)["battery_critical"] is True
    assert frame_card(_dev(1, battery_voltage=3.44), 15, NOW)["battery_critical"] is True
    # No pack, or no reading at all: nothing to charge.
    assert frame_card(_dev(1, battery_voltage=0.4), 15, NOW)["battery_critical"] is False
    assert frame_card(_dev(1), 15, NOW)["battery_critical"] is False
    assert frame_card({}, 15, NOW)["battery_critical"] is False


def test_battery_critical_follows_the_panels_hold():
    # The colour panel's warning is a 30 s full refresh, so its hold starts
    # higher; the banner must not wait for a voltage that frame never reports.
    from featherframe import panels
    dev = _dev(1, battery_voltage=3.52, battery_percent=14)
    assert frame_card(dev, 15, NOW)["battery_critical"] is False
    assert frame_card(dev, 15, NOW, critical_volts=panels.EE02.low_battery_volts)["battery_critical"] is True


def test_firmware_hold_matches_the_panels():
    """`FF_LOW_BATT_V` (per panel, ff_config.h) is the page's critical voltage."""
    import re
    from featherframe import panels
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "firmware" / "include" / "ff_config.h").read_text()
    spectra, gray = re.search(
        r"#if FF_FULL_REFRESH\s*\n#define FF_LOW_BATT_V\s+([\d.]+)f.*?\n#else\s*\n#define FF_LOW_BATT_V\s+([\d.]+)f",
        src, re.S).groups()
    assert float(gray) == panels.EE03.low_battery_volts
    assert float(spectra) == panels.EE02.low_battery_volts
    # A reported panel runs the generic build, which is full-refresh too (W-819).
    assert float(spectra) == panels.custom(800, 480, "gray16", "90,270").low_battery_volts


def _render_page(svc) -> str:
    env = Environment(loader=FileSystemLoader(str(paths.templates_dir())),
                      autoescape=True)
    return env.get_template("index.html").render(
        status=svc.status(), config=svc.config, version="test", generated=[])


def test_page_never_seen(svc):
    # Unseen frame: the Frame card shows "never" with an off dot.
    assert "never" in _render_page(svc)


def test_page_fresh(svc):
    _checkin(svc, user_agent="esp32-featherframe", battery_voltage=3.95,
             battery_percent=72, wifi_rssi=-61)
    html = _render_page(svc)
    assert "just now" in html
    assert "72%" in html          # battery shown as percent only (W-607)
    assert "Overdue —" not in html


def test_page_overdue(svc):
    # the interval-based bar; awake is a fixed few minutes
    svc.update_frame(FRAME_ID, {"power_mode": "sleep"})
    minutes = svc.frame_config(svc.frames.get(FRAME_ID)).wake_interval_minutes
    late = datetime.now() - timedelta(minutes=minutes * 2 + 5)
    _checkin(svc, battery_voltage=3.6, battery_percent=31, last_result="304")
    svc.frames.save({**svc.frames.get(FRAME_ID),
                     "reported": {**svc.frames.get(FRAME_ID)["reported"],
                                  "last_checkin": late.isoformat(timespec="seconds")}})
    # The row says so with a badge of its own.
    assert svc.frame_health(svc.frames.get(FRAME_ID))["overdue"] is True
    assert '<span class="badge warn" data-h="overdue" >Overdue</span>' in _render_page(svc)


# -- device_extra plumbing + show_battery gating ----------------------------
def test_device_extra_recorded_from_get_frame(svc):
    # The optional device-reported headers must land on the frame\'s row so the
    # card can show firmware version, panel/board, wake, and counters.
    _checkin(svc, user_agent="ua", battery_voltage=3.9, battery_percent=60, wifi_rssi=-60,
             fw_version="2026.09.01+abc", sketch_md5="deadbeef", last_wake="timer",
             boot_count=3, refresh_count=7, panel="P", board="B")
    d = device(svc)
    assert d.fw_version == "2026.09.01+abc"
    assert (d.boot_count, d.refresh_count) == (3, 7)
    assert (d.panel, d.board, d.last_wake) == ("P", "B", "timer")


def test_battery_row_always_renders(svc):
    # The old "show battery" toggle is gone: the power state is inferred from
    # the voltage trend instead, so the tile is always meaningful.
    _checkin(svc, user_agent="ua", battery_voltage=3.9, battery_percent=60, wifi_rssi=-60)
    html = _render_page(svc)
    assert 'data-h="batt"' in html and "60%" in html
    assert 'name="show_battery"' not in html


def test_a_nearly_empty_cell_is_a_badge_on_that_frames_row(svc):
    """No full-width banner any more: the row it is about carries it."""
    _checkin(svc, user_agent="esp32-featherframe", battery_voltage=3.95, battery_percent=72)
    assert '<span class="badge bad" data-h="lowbatt" hidden>' in _render_page(svc)
    svc._battery_live.clear()
    _checkin(svc, battery_voltage=3.47, battery_percent=6, last_result="304")
    svc._battery_live.clear()
    svc.update_frame(FRAME_ID, {"name": "Hallway"})
    html = _render_page(svc)
    row = html.split('data-frame="%s"' % FRAME_ID)[1].split(chr(10) + "    </li>")[0]
    assert "Hallway" in row
    assert '<span class="badge bad" data-h="lowbatt" >Battery low</span>' in row
    assert "Charge Hallway." not in html
