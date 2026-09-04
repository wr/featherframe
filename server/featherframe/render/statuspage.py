"""On-demand status plate (button 3 on the frame).

Typeset in the plates' idiom: the script wordmark over a hedera,
engraved-capital labels, and script values. The device supplies its
own numbers (battery, Wi-Fi) via request headers; the server adds what it
knows (last bird, counts).
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

    # The wordmark, exactly as the boot splash sets it (the plate title's
    # script and auto-fit size), over a small hedera — Garamond's, the script
    # has none.
    title_baseline = theme.HEIGHT * 0.20
    typography.draw_script(field, cx, title_baseline, "Featherframe",
                           typography.fit_script_title("Featherframe", theme.CONTENT_W),
                           theme.INK, stroke=theme.TITLE_STROKE)
    hedera_font = typography.FONTS.get(36, italic=True, weight=500)
    draw.text((cx, title_baseline + 92), theme.DATE_ORNAMENT, font=hedera_font,
              fill=theme.INK_MEDIUM, anchor="ms")

    if info.last_common and info.last_when:
        when = info.last_when
        stamp = (when.strftime("%-I:%M %p").lower() if when.date() == now.date()
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

    # Two columns about the center line: engraved-capital labels on the left,
    # script values on the right — the plates' Latin-name / legend voice.
    y = title_baseline + 250
    label_size = 32
    max_value_w = theme.WIDTH - (cx + 40) - theme.MARGIN_X
    for label, value in rows:
        lw = typography.engraved_width(label.upper(), label_size,
                                       theme.KEY_TRACKING)
        typography.draw_engraved(draw, cx - 40 - lw / 2, y, label.upper(),
                                 label_size, theme.INK_MEDIUM, theme.KEY_TRACKING)
        size = theme.STATUS_VALUE_SIZE
        while typography.script_width(value, size) > max_value_w and size > 30:
            size -= 2
        while typography.script_width(value, size) > max_value_w and len(value) > 4:
            value = value[:-2].rstrip() + "…"
        typography.draw_script(field, cx + 40, y, value, size, theme.INK,
                               stroke=theme.LEGEND_STROKE, anchor="ls")
        y += 112

    # "as of" footer in the corner marks' voice: "8:14 pm · 4 September".
    hour = now.hour % 12 or 12
    stamp = f"{hour}:{now.minute:02d} {'am' if now.hour < 12 else 'pm'}"
    footer = f"{stamp} {theme.CORNER_SEP} {now.day} {now.strftime('%B')}"
    typography.draw_script(field, cx, y + 60, footer, theme.STATUS_FOOT_SIZE,
                           theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE)
    return field
