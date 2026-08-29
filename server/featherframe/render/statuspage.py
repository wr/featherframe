"""On-demand status plate (button 3 on the frame).

Typeset in the plates' v3 idiom: the swash italic wordmark over a hedera,
engraved-capital labels, and old-style italic values. The device supplies its
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

    # The wordmark, exactly as the boot splash sets it, over a small hedera.
    title_font = typography.FONTS.get(theme.TITLE_SIZE, italic=True,
                                      weight=theme.TITLE_WEIGHT)
    title_baseline = theme.HEIGHT * 0.20
    typography.draw_title(draw, cx, title_baseline, "Featherframe", title_font,
                          theme.INK, theme.TITLE_SIZE * theme.TITLE_TRACKING)
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
    # old-style italic values on the right — the plates' label/value voice.
    y = title_baseline + 250
    label_size = 32
    value_feats = ["onum", "liga", "kern"] if typography.HAS_RAQM else None
    max_value_w = theme.WIDTH - (cx + 40) - theme.MARGIN_X
    for label, value in rows:
        lw = typography.engraved_width(label.upper(), label_size,
                                       theme.KEY_TRACKING)
        typography.draw_engraved(draw, cx - 40 - lw / 2, y, label.upper(),
                                 label_size, theme.INK_MEDIUM, theme.KEY_TRACKING)
        size = 46
        value_font = typography.FONTS.get(size, italic=True, weight=500)
        while value_font.getlength(value) > max_value_w and size > 32:
            size -= 2
            value_font = typography.FONTS.get(size, italic=True, weight=500)
        while value_font.getlength(value) > max_value_w and len(value) > 4:
            value = value[:-2].rstrip() + "…"
        draw.text((cx + 40, y), value, font=value_font, fill=theme.INK,
                  anchor="ls", features=value_feats)
        y += 112

    # "as of" footer in the old date-line voice: time, hedera, date.
    date_font = typography.FONTS.get(theme.DATE_SIZE, italic=True,
                                     weight=theme.DATE_WEIGHT)
    tracking_px = theme.DATE_SIZE * theme.DATE_TRACKING
    hour = now.hour % 12 or 12
    stamp = f"{hour}:{now.minute:02d} {'am' if now.hour < 12 else 'pm'}"
    datestr = f"{now.day} {now.strftime('%B')}"
    if typography.HAS_RAQM:
        gap = theme.DATE_SIZE * theme.DATE_ORNAMENT_GAP
        orn_w = date_font.getlength(theme.DATE_ORNAMENT)
        t_w = sum(date_font.getlength(c, features=["smcp", "onum"]) for c in stamp) +             tracking_px * max(0, len(stamp) - 1)
        d_w = sum(date_font.getlength(c, features=["onum"]) for c in datestr) +             tracking_px * max(0, len(datestr) - 1)
        x = cx - (t_w + gap + orn_w + gap + d_w) / 2
        typography.draw_ot_tracked(draw, x + t_w / 2, y + 60, stamp, date_font,
                                   theme.INK_MEDIUM, theme.TIME_FEATURES, tracking_px)
        x += t_w + gap
        draw.text((x, y + 60), theme.DATE_ORNAMENT, font=date_font,
                  fill=theme.INK_MEDIUM, anchor="ls")
        x += orn_w + gap
        typography.draw_ot_tracked(draw, x + d_w / 2, y + 60, datestr, date_font,
                                   theme.INK_MEDIUM, theme.DATE_FEATURES, tracking_px)
    else:
        draw.text((cx, y + 60), f"as of {stamp} · {datestr}", font=date_font,
                  fill=theme.INK_MEDIUM, anchor="ms")
    return field
