"""The system voice on the glass (W-741).

A message is not a plate legend. Whatever the frame has to *tell* the owner
(nothing heard, source unreachable, waiting for the first bird, this sheet
was imagined) is set the way the firmware's own screens set it: Inter on a
black rounded pill, an outlined pill with a slashed icon for an error, the
setup card's black box for a block of lines. The geometry here mirrors
`firmware/tools/screens/bake_screens.py` so a server-drawn pill and a baked
toast read as one family on the same panel.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from PIL import ImageDraw, ImageFont

from .. import paths
from . import theme, typography

_SANS = paths.fonts_dir() / "Inter-Medium.otf"
_SANS_SEMIBOLD = paths.fonts_dir() / "Inter-SemiBold.otf"

# The firmware's toast: PILL_H 82, text 34, side padding 30 (bake_screens.py).
PILL_H, PILL_PAD, PILL_TEXT = 82, 30, 34
# Where the firmware rests its pills: the toast band over the bottom margin,
# and the "Trying again in 5 min" line beneath it (TOAST_Y / RETRY_BASELINE).
TOAST_Y, RETRY_BASELINE, RETRY_TEXT = 1648, 1776, 28
# The footer note between the corner marks: the same pill, two-thirds size.
NOTE_H, NOTE_PAD, NOTE_TEXT = 52, 22, 24
CARD_RADIUS = 24
OUTLINE_W = 5


@lru_cache(maxsize=16)
def sans(size: int, weight: str = "medium") -> ImageFont.FreeTypeFont:
    path = _SANS_SEMIBOLD if weight == "semibold" and _SANS_SEMIBOLD.exists() else _SANS
    return ImageFont.truetype(str(path), int(size))


def sans_width(text: str, size: int) -> float:
    return sans(size).getlength(text)


# -- icons, ported from the bake so the two sides match ------------------------
def _slash(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, ink: int, paper: int) -> None:
    d.line([(cx - s, cy + s), (cx + s, cy - s)], fill=paper, width=max(4, int(s * 0.7)))
    d.line([(cx - s, cy + s), (cx + s, cy - s)], fill=ink, width=max(2, int(s * 0.3)))


def wifi_glyph(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, ink: int) -> None:
    for rad in (s, s * 0.62, s * 0.30):
        d.arc([cx - rad, cy - rad, cx + rad, cy + rad], 225, 315,
              fill=ink, width=max(2, int(s * 0.12)))
    d.ellipse([cx - s * 0.07, cy - s * 0.07, cx + s * 0.07, cy + s * 0.07], fill=ink)


def cloud_slash(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, ink: int, paper: int) -> None:
    lobes = [(cx - 0.52 * s, cy + 0.10 * s, 0.38 * s),
             (cx + 0.02 * s, cy - 0.18 * s, 0.52 * s),
             (cx + 0.55 * s, cy + 0.12 * s, 0.36 * s)]
    for (x, y, r) in lobes:
        d.ellipse([x - r, y - r, x + r, y + r], fill=ink)
    d.rounded_rectangle([cx - 0.72 * s, cy + 0.05 * s, cx + 0.80 * s, cy + 0.48 * s],
                        radius=int(0.2 * s), fill=ink)
    _slash(d, cx, cy, s * 0.95, ink, paper)


def wifi_slash(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, ink: int, paper: int) -> None:
    wifi_glyph(d, cx, cy + s * 0.55, s, ink)
    _slash(d, cx, cy, s * 0.78, ink, paper)


# -- the pill ----------------------------------------------------------------
def pill(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str, *,
         h: int = PILL_H, pad: int = PILL_PAD, size: int = PILL_TEXT,
         style: str = "solid", icon: Optional[str] = None,
         max_w: Optional[float] = None, weight: str = "medium",
         tracking: float = 0.0) -> tuple[float, float]:
    """One line in a rounded pill centred on (cx, cy). `style` "solid" is the
    firmware's black toast (white type); "outline" is its error pill (paper,
    ink outline, slashed icon). Shrinks the type rather than clip when wider
    than `max_w`. `tracking` is extra letter-spacing as a fraction of the
    size, for a short label in capitals. Returns the pill's x-extent."""
    ink, paper = theme.INK, theme.FIELD
    icon_slot = int(h * 0.68) if icon else 0
    gap = int(pad * 0.6) if icon else 0
    while True:
        fnt = sans(size, weight)
        track = tracking * size
        tw = fnt.getlength(text) + track * max(0, len(text) - 1)
        w = pad + icon_slot + gap + tw + pad + 4
        if max_w is None or w <= max_w or size <= 16:
            break
        size -= 1
    x0 = cx - w / 2
    box = [x0, cy - h / 2, x0 + w, cy + h / 2]
    if style == "outline":
        d.rounded_rectangle(box, radius=h / 2, fill=paper, outline=ink,
                            width=max(2, round(OUTLINE_W * h / PILL_H)))
        fg, bg = ink, paper
    else:
        d.rounded_rectangle(box, radius=h / 2, fill=ink)
        fg, bg = paper, ink
    x = x0 + pad
    if icon:
        icx, s = x + icon_slot / 2, h * 0.27
        if icon == "wifi":
            wifi_slash(d, icx, cy, s, fg, bg)
        else:
            cloud_slash(d, icx, cy, s, fg, bg)
        x += icon_slot + gap
    cap = fnt.getbbox("H")
    baseline = cy + (cap[3] - cap[1]) / 2
    if track:
        for ch in text:
            d.text((x + 2, baseline), ch, font=fnt, fill=fg, anchor="ls")
            x += fnt.getlength(ch) + track
    else:
        d.text((x + 2, baseline), text, font=fnt, fill=fg, anchor="ls")
    return x0, x0 + w


def note_pill(d: ImageDraw.ImageDraw, text: str, kind: Optional[str], max_w: float) -> None:
    """The footer note between the date and № marks, on the marks' own
    line: a solid pill for information ("nothing heard"), an outlined
    slashed one for a fault (the source is unreachable)."""
    cy = theme.MARKS_BASELINE - 9          # the script marks' x-height centre
    if kind == "outage":
        pill(d, theme.WIDTH / 2, cy, text, h=NOTE_H, pad=NOTE_PAD, size=NOTE_TEXT,
             style="outline", icon="cloud", max_w=max_w)
    else:
        pill(d, theme.WIDTH / 2, cy, text, h=NOTE_H, pad=NOTE_PAD, size=NOTE_TEXT,
             style="solid", max_w=max_w)


# -- the card ----------------------------------------------------------------
def _card_metrics(lines, pad_x, pad_y, gap, max_w):
    fonts = []
    for text, size, weight in lines:
        f = typography.FONTS.get(size, weight=weight)
        while f.getlength(text) > max_w - 2 * pad_x and size > 20:
            size -= 2
            f = typography.FONTS.get(size, weight=weight)
        fonts.append(f)
    widest = max(f.getlength(t) for (t, _, _), f in zip(lines, fonts))
    heights = [f.getbbox("Hg")[3] - f.getbbox("Hg")[1] for f in fonts]
    w = widest + 2 * pad_x
    h = pad_y * 2 + sum(heights) + gap * (len(lines) - 1)
    return fonts, heights, w, h


def card_size(lines: list[tuple[str, int, int]], *, pad_x: int = 64, pad_y: int = 54,
              gap: int = 26, max_w: float = theme.CONTENT_W) -> tuple[float, float]:
    """(width, height) the card will take, so a caller can centre it."""
    _, _, w, h = _card_metrics(lines, pad_x, pad_y, gap, max_w)
    return w, h


def card(d: ImageDraw.ImageDraw, cx: float, top: float,
         lines: list[tuple[str, int, int]], *, pad_x: int = 64, pad_y: int = 54,
         gap: int = 26, max_w: float = theme.CONTENT_W) -> float:
    """The setup card's black box holding `lines` of (text, size, weight) in
    the plate's Garamond, reversed and semibold as the firmware sets it (light
    type loses weight on e-ink). Fits its widest line. Returns the bottom."""
    fonts, heights, w, h = _card_metrics(lines, pad_x, pad_y, gap, max_w)
    x0, y0 = cx - w / 2, top
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=CARD_RADIUS, fill=theme.INK)
    y = y0 + pad_y
    for (text, _, _), f, lh in zip(lines, fonts, heights):
        d.text((cx, y + lh / 2), text, font=f, fill=theme.FIELD, anchor="mm")
        y += lh + gap
    return y0 + h


def line(d: ImageDraw.ImageDraw, cx: float, baseline: float, text: str,
         size: int = 28, fill: Optional[int] = None) -> None:
    """A plain Inter line, the firmware's "Trying again in 5 min" voice."""
    d.text((cx, baseline), text, font=sans(size), fill=theme.INK_MEDIUM if fill is None else fill,
           anchor="ms")
