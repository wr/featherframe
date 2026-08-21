"""The on-demand status plate (frame button 3) renders at panel size with the
device-supplied numbers, and degrades cleanly when nothing is known."""
from __future__ import annotations

from datetime import datetime

from featherframe.render import theme
from featherframe.render.statuspage import StatusInfo, render_status, _wifi_words


def test_renders_panel_sized_grayscale():
    img = render_status(StatusInfo(
        battery_voltage=4.01, battery_percent=78, wifi_rssi=-61,
        last_common="American Goldfinch", last_when=datetime(2026, 8, 21, 16, 13),
        species_today=7, species_all_time=121,
        server_label="birdnet-pi", wake_minutes=15))
    assert img.mode == "L"
    assert img.size == (theme.WIDTH, theme.HEIGHT)


def test_renders_with_nothing_known():
    img = render_status(StatusInfo())
    assert img.size == (theme.WIDTH, theme.HEIGHT)


def test_wifi_words_buckets():
    assert _wifi_words(None) == "Unknown"
    assert _wifi_words(-50).startswith("Excellent")
    assert _wifi_words(-60).startswith("Good")
    assert _wifi_words(-70).startswith("Fair")
    assert _wifi_words(-85).startswith("Weak")
