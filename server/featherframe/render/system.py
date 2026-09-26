"""The system voice on the glass (W-741).

A message is not a plate legend. Whatever the frame has to *tell* the owner
(nothing heard, source unreachable, waiting for the first bird, this sheet
was imagined) is set the way the firmware's own screens set it: Inter on a
black rounded pill (a slashed icon on it for an error: one style), the
setup card's black box for a block of lines. The geometry here mirrors
`firmware/tools/screens/bake_screens.py` so a server-drawn pill and a baked
toast read as one family on the same panel.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .. import paths
from . import theme, typography

_SANS = paths.fonts_dir() / "Inter-Medium.otf"
_SANS_SEMIBOLD = paths.fonts_dir() / "Inter-SemiBold.otf"

# One pill, in one place (25 Sep 2026): on the footer line between the corner
# marks, where the firmware bakes its toasts and error pills too
# (bake_screens.py scales these by its reference mat). A line that goes with a
# pill ("Trying again shortly") sits over it.
NOTE_H, NOTE_PAD, NOTE_TEXT = 52, 22, 24
NOTE_CY = theme.MARKS_BASELINE - 9          # the script marks' x-height centre
RETRY_BASELINE, RETRY_TEXT = NOTE_CY - NOTE_H / 2 - 20, NOTE_TEXT
CARD_RADIUS = 24


@lru_cache(maxsize=16)
def sans(size: int, weight: str = "medium") -> ImageFont.FreeTypeFont:
    path = _SANS_SEMIBOLD if weight == "semibold" and _SANS_SEMIBOLD.exists() else _SANS
    return ImageFont.truetype(str(path), int(size))


def sans_width(text: str, size: int) -> float:
    return sans(size).getlength(text)


# -- icons -------------------------------------------------------------------
# One drawer for every icon on a pill, the server's and the bake's
# (bake_screens.py calls it), so both sides match. Each is drawn four times
# over and cut to ink or paper: at pill size a thin arc or a slash's casing
# otherwise breaks up, and a two-tone icon never dithers.
_ICON_SS = 4


def _glyph(d: ImageDraw.ImageDraw, kind: str, c: float, s: float) -> None:
    cx = cy = c
    if kind == "wifi":
        oy = cy + s * 0.78                        # the fan's point
        for rad in (s * 1.45, s * 0.92):
            d.arc([cx - rad, oy - rad, cx + rad, oy + rad], 222, 318, fill=0, width=int(s * 0.3))
        r = s * 0.24
        d.ellipse([cx - r, oy - r * 2.1, cx + r, oy - r * 0.1], fill=0)
    elif kind == "cloud":
        for (x, y, r) in ((-0.55, 0.12, 0.40), (0.0, -0.2, 0.55), (0.58, 0.14, 0.38)):
            d.ellipse([cx + (x - r) * s, cy + (y - r) * s, cx + (x + r) * s, cy + (y + r) * s], fill=0)
        d.rounded_rectangle([cx - 0.78 * s, cy + 0.05 * s, cx + 0.85 * s, cy + 0.52 * s],
                            radius=0.24 * s, fill=0)
    elif kind == "battery":                       # nearly empty: one sliver of charge
        w, h, lw = s * 2.0, s * 1.1, s * 0.2
        x0, y0 = cx - w / 2 - s * 0.1, cy - h / 2
        d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=s * 0.25, outline=0, width=int(lw))
        d.rounded_rectangle([x0 + w + s * 0.05, cy - h * 0.24, x0 + w + s * 0.3, cy + h * 0.24],
                            radius=s * 0.08, fill=0)
        g = lw + s * 0.14
        d.rectangle([x0 + g, y0 + g, x0 + g + w * 0.16, y0 + h - g], fill=0)
    if kind in ("wifi", "cloud"):                 # the slash, cased in paper
        a, b = (cx - s * 0.9, cy - s * 0.9), (cx + s * 0.9, cy + s * 0.9)
        d.line([a, b], fill=255, width=int(s * 0.5))
        d.line([a, b], fill=0, width=int(s * 0.2))


def draw_icon(im: Image.Image, kind: str, cx: float, cy: float, s: float,
         ink: int = 0) -> None:
    """A slashed "wifi" or "cloud", or a low "battery", in `ink`, centred on
    (cx, cy), `s` its half-size. What the glyph leaves (the slash's casing) is
    not drawn at all, so the pill's own colour shows through."""
    half = int(s * 1.5) + 2
    big = Image.new("L", (half * 2 * _ICON_SS,) * 2, 255)
    _glyph(ImageDraw.Draw(big), kind, half * _ICON_SS, s * _ICON_SS)
    mask = big.resize((half * 2,) * 2, Image.LANCZOS).point(lambda v: 255 if v < 128 else 0)
    x, y = int(round(cx)) - half, int(round(cy)) - half
    fill = ink if im.mode == "L" else (ink,) * len(im.getbands())
    im.paste(fill, (x, y, x + half * 2, y + half * 2), mask)


ICON_OF_PILL = 0.33                               # an icon's half-size, of the pill's height


# -- the pill ----------------------------------------------------------------
def pill(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str, *,
         h: int = NOTE_H, pad: int = NOTE_PAD, size: int = NOTE_TEXT,
         icon: Optional[str] = None,
         max_w: Optional[float] = None, weight: str = "medium",
         tracking: float = 0.0) -> tuple[float, float]:
    """One line in a black rounded pill centred on (cx, cy), white type: the
    firmware's toast. An error carries a slashed `icon`. Shrinks the type rather than clip when wider
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
    d.rounded_rectangle(box, radius=h / 2, fill=ink)
    fg, bg = paper, ink
    x = x0 + pad
    if icon:
        draw_icon(d._image, icon, x + icon_slot / 2, cy, h * ICON_OF_PILL, fg)
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
    """The footer note between the date and plate marks, on the marks' own
    line: the black pill, with a slashed cloud when the source is
    unreachable (`kind` "outage")."""
    pill(d, theme.WIDTH / 2, NOTE_CY, text,
         icon="cloud" if kind == "outage" else None, max_w=max_w)


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
