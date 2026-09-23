"""The panels Featherframe can drive. One server instance drives one panel
(`config.panel`); everything panel-specific the render needs lives here.

Both known panels are 3:4 and hang portrait, so the art is always composed on
the theme's 1404x1872 sheet and scaled to the panel at the finish — the layout
and the type never fork. A frame also describes its panel as facts (W-813:
X-Panel-Width / -Height / -Format / -Rotations), so a panel this file has never
heard of is drawn for from the report alone (`from_report`, `custom`); one that
is not 3:4 gets the same sheet, letterboxed on the paper field.
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
    dither: str                 # the dither this panel's frames are drawn with
    # The firmware's low-battery hold for this panel (FF_LOW_BATT_V in
    # ff_config.h; a test keeps the two equal). Under it the frame goes silent,
    # so it is also where the page's "Charge the frame." banner must be up.
    low_battery_volts: float = 3.45
    # What the wire nibbles/bits mean (X-Panel-Format): gray16 | gray2 | mono |
    # spectra6. `color` is spectra6.
    fmt: str = "gray16"
    # Set when the frame reported a format this server cannot draw: the render
    # falls back to 16-level gray at the right size and the page says so.
    unknown_format: str = ""
    # A fresh config's mode here. A refresh that takes half a minute makes a
    # plate per detection a poor fit: the colour panel starts on the collage.
    mode: str = "single"
    # The firmware's floor between two repaints it was not asked for by a
    # button (FF_MIN_REPAINT_MS, ff_config.h; a test keeps them equal): a
    # full-refresh panel holds a change that lands inside it (W-841).
    min_repaint_s: int = 180

    @property
    def known(self) -> bool:
        return self.key in PANELS

    @property
    def native(self) -> tuple[int, int]:
        """The canvas as the firmware pushes it (what X-Panel-Width/-Height
        report, and the FFF header carries)."""
        if self.rotations[0] in (90, 270):
            return self.height, self.width
        return self.width, self.height


# Seeed EE03: E Ink ED103TC2 10.3", IT8951. Native canvas is landscape and
# setRotation() is a no-op, so the frame is rotated server-side (90 or 270).
# Blue-noise: 16 grays leave little to diffuse, and it is vectorised (Pi-friendly).
EE03 = Panel("ee03", 'EE03 · 10.3" gray', 1404, 1872, False, (90, 270), 2, "bluenoise",
             min_repaint_s=0)

# The name is shown on the page, whose copy is US English ("color").
# Seeed EE02: E Ink Spectra 6 13.3" (T133A01). Native canvas is portrait; 180
# is the frame hung the other way up. No partial refresh.
# Stucki: with only six inks, diffusion holds the engraving lines and grains
# far tighter than the ordered mix (judged side by side on the glass, 19 Sep
# 2026). It is a per-pixel Python loop — seconds on a PC, minutes on a Pi
# Zero — which a panel that takes 30 s to refresh can afford.
EE02 = Panel("ee02", 'EE02 · 13.3" Spectra 6 color', 1200, 1600, True, (0, 180), 30, "stucki",
             # Its warning is a 30 s six-ink full refresh, not a sub-second pill:
             # hold 0.1 V earlier so the cell still has the headroom to paint it.
             low_battery_volts=3.55, fmt="spectra6", mode="collage")

PANELS = {p.key: p for p in (EE03, EE02)}

# The settings whose right value depends on the panel (and on the mat in front
# of it): what "use this panel's defaults" resets after a panel swap.
PANEL_SETTINGS = ("mode", "panel_rotation",
                  "mat_inset_pct", "mat_offset_x_px", "mat_offset_y_px")
DEFAULT = EE03


FORMATS = ("gray16", "gray2", "mono", "spectra6")
CUSTOM_PREFIX = "custom:"
_MIN_SIDE, _MAX_SIDE = 64, 4096


def _by_name(reported: str | None) -> Panel | None:
    text = str(reported or "").lower()
    # "spectra" alone is not the EE02: another Spectra 6 panel reports its own size.
    if "t133a01" in text or ("spectra" in text and "1200x1600" in text):
        return EE02
    if "ed103tc2" in text:
        return EE03
    return None


def _int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _rotations(value, native_w: int, native_h: int) -> tuple[int, ...]:
    """The rotations the firmware accepts, first = default. They must share an
    axis (0/180 or 90/270): the other pair is another native size, which the
    firmware rejects. Missing or junk: a landscape canvas hangs portrait."""
    seen: list[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        rot = _int(part) if part.strip().lstrip("-").isdigit() else -1
        if rot in (0, 90, 180, 270) and rot not in seen:
            seen.append(rot)
    seen = [r for r in seen if r % 180 == seen[0] % 180]
    if not seen:
        seen = [90, 270] if native_w > native_h else [0, 180]
    return tuple(seen)


def custom(width, height, fmt, rotations=None, name: str | None = None) -> Panel | None:
    """A panel built from a frame's own report (native canvas, wire format,
    accepted rotations), or None when the size is not one."""
    w, h = _int(width), _int(height)
    if not (_MIN_SIDE <= w <= _MAX_SIDE and _MIN_SIDE <= h <= _MAX_SIDE):
        return None
    rots = _rotations(rotations, w, h)
    wire = str(fmt or "").strip().lower()[:24]
    wire = "".join(c for c in wire if c.isalnum() or c in "-_") or "gray16"
    drawn = wire if wire in FORMATS else "gray16"
    key = f"{CUSTOM_PREFIX}{w}x{h}:{wire}:{','.join(str(r) for r in rots)}"
    pw, ph = (h, w) if rots[0] in (90, 270) else (w, h)
    color = drawn == "spectra6"
    label = str(name or "").strip()[:60]
    shown = f"{label} · {w}×{h} {wire}" if label and f"{w}x{h}" not in label else (label or f"{w}×{h} {wire}")
    return Panel(key, shown, pw, ph, color, rots, 30 if color else 2,
                 "stucki" if color else "bluenoise", fmt=drawn,
                 mode="collage" if color else "single",
                 unknown_format="" if wire in FORMATS else wire,
                 # The generic firmware build (FF_GENERIC_PANEL) says "Battery low"
                 # with a full-screen refresh, so it holds where the EE02 does.
                 low_battery_volts=EE02.low_battery_volts)


def _from_key(key: str) -> Panel | None:
    parts = key[len(CUSTOM_PREFIX):].split(":")
    if len(parts) != 3 or "x" not in parts[0]:
        return None
    w, _, h = parts[0].partition("x")
    return custom(w, h, parts[1], parts[2])


def from_report(reported: str | None, facts: dict | None = None) -> Panel | None:
    """The panel a device reports. A name we know ("ED103TC2 1404x1872 gray16",
    "T133A01 1200x1600 spectra6") keeps its curated entry; any other is built
    from the facts it sent (`w`, `h`, `fmt`, `rot`: the X-Panel-* headers).
    None when it names none we know and sent no usable size."""
    panel = _by_name(reported)
    if panel is not None:
        return panel
    facts = facts or {}
    return custom(facts.get("w"), facts.get("h"), facts.get("fmt"), facts.get("rot"),
                  name=reported)


def get(key: str) -> Panel:
    key = str(key or "").lower()
    if key.startswith(CUSTOM_PREFIX):
        return _from_key(key) or DEFAULT
    return PANELS.get(key, DEFAULT)
