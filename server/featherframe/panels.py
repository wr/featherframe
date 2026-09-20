"""The panels Featherframe can drive. One server instance drives one panel
(`config.panel`); everything panel-specific the render needs lives here.

Both panels are 3:4 and hang portrait, so the art is always composed on the
theme's 1404x1872 sheet and scaled to the panel at the finish — the layout and
the type never fork.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Panel:
    key: str
    name: str
    width: int                  # portrait, as the frame hangs
    height: int
    color: bool                 # six-ink Spectra vs 16-level gray
    rotations: tuple[int, ...]  # valid config.panel_rotation values; first is the default
    refresh_seconds: int        # a full refresh, roughly: how long the glass is busy
    dither: str                 # what config.dither "auto" means here
    # The firmware's low-battery hold for this panel (FF_LOW_BATT_V in
    # ff_config.h; a test keeps the two equal). Under it the frame goes silent,
    # so it is also where the page's "Charge the frame." banner must be up.
    low_battery_volts: float = 3.45


# Seeed EE03: E Ink ED103TC2 10.3", IT8951. Native canvas is landscape and
# setRotation() is a no-op, so the frame is rotated server-side (90 or 270).
# Blue-noise: 16 grays leave little to diffuse, and it is vectorised (Pi-friendly).
EE03 = Panel("ee03", 'EE03 · 10.3" gray', 1404, 1872, False, (90, 270), 2, "bluenoise")

# Seeed EE02: E Ink Spectra 6 13.3" (T133A01). Native canvas is portrait; 180
# is the frame hung the other way up. No partial refresh.
# Stucki: with only six inks, diffusion holds the engraving lines and grains
# far tighter than the ordered mix (judged side by side on the glass, 19 Sep
# 2026). It is a per-pixel Python loop — seconds on a PC, minutes on a Pi
# Zero — which a panel that takes 30 s to refresh can afford.
EE02 = Panel("ee02", 'EE02 · 13.3" Spectra 6 colour', 1200, 1600, True, (0, 180), 30, "stucki",
             # Its warning is a 30 s six-ink full refresh, not a sub-second pill:
             # hold 0.1 V earlier so the cell still has the headroom to paint it.
             low_battery_volts=3.55)

PANELS = {p.key: p for p in (EE03, EE02)}

# The settings whose right value depends on the panel (and on the mat in front
# of it): what "use this panel's defaults" resets after a panel swap.
PANEL_SETTINGS = ("gray_mode", "color_saturation", "dither", "panel_rotation",
                  "mat_inset_pct", "mat_offset_x_px", "mat_offset_y_px")
DEFAULT = EE03


def from_report(reported: str | None) -> Panel | None:
    """The panel a device's X-Panel header names ("ED103TC2 1404x1872 gray16",
    "T133A01 1200x1600 spectra6"), or None when it names none we know."""
    text = str(reported or "").lower()
    if "t133a01" in text or "spectra" in text:
        return EE02
    if "ed103tc2" in text:
        return EE03
    return None


def get(key: str) -> Panel:
    return PANELS.get(str(key or "").lower(), DEFAULT)
