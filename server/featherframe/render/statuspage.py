"""On-demand status plate (button 3 on the frame).

Typeset in the same museum idiom as the species plates: small caps, hairline
rules, generous margins. The device supplies its own numbers (battery, Wi-Fi)
via request headers; the server adds what it knows (last bird, counts).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from . import theme, typography


@dataclass
class StatusInfo:
    battery_voltage: Optional[float] = None
    battery_percent: Optional[int] = None
    wifi_rssi: Optional[int] = None          # dBm, e.g. -61
    last_common: Optional[str] = None
    last_when: Optional[datetime] = None
    species_today: int = 0
    species_all_time: int = 0
    server_label: str = ""
    wake_minutes: int = 15


def _wifi_words(rssi: Optional[int]) -> str:
    if rssi is None:
        return "Unknown"
    if rssi >= -55:
        return f"Excellent ({rssi} dBm)"
    if rssi >= -67:
        return f"Good ({rssi} dBm)"
    if rssi >= -75:
        return f"Fair ({rssi} dBm)"
    return f"Weak ({rssi} dBm)"


def _battery_words(info: StatusInfo) -> str:
    if info.battery_percent is None:
        return "Unknown"
    s = f"{info.battery_percent}%"
    if info.battery_voltage is not None:
        s += f" ({info.battery_voltage:.2f} V)"
    return s


def render_status(info: StatusInfo, now: Optional[datetime] = None) -> Image.Image:
    now = now or datetime.now()
    field = Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)
    draw = ImageDraw.Draw(field)
    cx = theme.WIDTH / 2

    top = theme.HEIGHT * 0.22
    typography.draw_smallcaps(draw, cx, top, "Featherframe", typography.FONTS,
                              104, theme.INK, theme.NAME_TRACKING)

    half = theme.RULE_WIDTH / 2
    rule_y = top + 74
    draw.rectangle([cx - half, rule_y, cx + half, rule_y + theme.RULE_THICKNESS - 1],
                   fill=theme.RULE)

    if info.last_common and info.last_when:
        when = info.last_when
        stamp = (when.strftime("%-I:%M %p") if when.date() == now.date()
                 else f"{when.day} {when.strftime('%b')}")
        last_bird = f"{info.last_common} · {stamp}"
    else:
        last_bird = info.last_common or "None yet"

    rows = [
        ("Battery", _battery_words(info)),
        ("Wi-Fi", _wifi_words(info.wifi_rssi)),
        ("Last bird", last_bird),
        ("Species today", str(info.species_today)),
        ("Species all time", str(info.species_all_time)),
        ("Checks in every", f"{info.wake_minutes} minutes"),
        ("Server", info.server_label or "—"),
    ]

    y = rule_y + 150
    label_font = typography.FONTS.get(40, weight=560)
    max_value_w = theme.WIDTH - (cx + 40) - theme.MARGIN_X
    for label, value in rows:
        draw.text((cx - 40, y), label.upper(), font=label_font,
                  fill=theme.INK_SOFT, anchor="rs")
        # Shrink long values to fit the column; ellipsize as a last resort.
        size = 46
        value_font = typography.FONTS.get(size, weight=430)
        while value_font.getlength(value) > max_value_w and size > 32:
            size -= 2
            value_font = typography.FONTS.get(size, weight=430)
        while value_font.getlength(value) > max_value_w and len(value) > 4:
            value = value[:-2].rstrip() + "…"
        draw.text((cx + 40, y), value, font=value_font, fill=theme.INK, anchor="ls")
        y += 110

    typography.draw_smallcaps(draw, cx, y + 60,
                              f"As of {typography.format_when(now)}",
                              typography.FONTS, theme.META_SIZE, theme.INK_SOFT,
                              theme.META_TRACKING, weight_caps=500, weight_small=520)
    return field
