"""Type. This is where a hobby project becomes an heirloom, per the spec.

We use EB Garamond (OFL) as a variable font: real italics from the italic
face, and *faux* small caps synthesised by drawing lowercase letters as
smaller capitals. Faux small caps (rather than the OpenType ``smcp`` feature)
is a deliberate portability choice — applying OT features through Pillow needs
libraqm, which isn't reliably present on a Pi. Done carefully, with the right
size ratio and tracking, it reads like a real museum plate.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import ImageDraw, ImageFont

from .. import paths
from . import theme

_FONTS = paths.fonts_dir()
_ROMAN = _FONTS / "EBGaramond[wght].ttf"
_ITALIC = _FONTS / "EBGaramond-Italic[wght].ttf"


class FontBook:
    """Caches sized/weighted font instances so we load the TTFs once."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}

    def get(self, size: int, italic: bool = False, weight: int = 400) -> ImageFont.FreeTypeFont:
        key = ("i" if italic else "r", int(size), int(weight))
        font = self._cache.get(key)
        if font is None:
            path = _ITALIC if italic else _ROMAN
            font = ImageFont.truetype(str(path), int(size))
            try:
                font.set_variation_by_axes([weight])
            except Exception:
                pass  # non-variable fallback: size-only
            self._cache[key] = font
        return font


# One shared book for the process.
FONTS = FontBook()


# -- low-level drawing -----------------------------------------------------
def _len(font: ImageFont.FreeTypeFont, s: str) -> float:
    return font.getlength(s)


def tracked_width(font: ImageFont.FreeTypeFont, text: str, tracking_px: float) -> float:
    if not text:
        return 0.0
    return sum(_len(font, ch) for ch in text) + tracking_px * (len(text) - 1)


def draw_tracked(draw: ImageDraw.ImageDraw, center_x: float, baseline_y: float,
                 text: str, font: ImageFont.FreeTypeFont, fill: int,
                 tracking_px: float) -> None:
    """Draw `text` centered on center_x, sitting on baseline_y, with letter
    spacing. Anchor 'ls' keeps a common baseline for mixed sizes."""
    total = tracked_width(font, text, tracking_px)
    x = center_x - total / 2
    for ch in text:
        draw.text((x, baseline_y), ch, font=font, fill=fill, anchor="ls")
        x += _len(font, ch) + tracking_px


def smallcaps_plan(text: str, book: FontBook, size: int, weight_caps: int,
                   weight_small: int) -> list[tuple[str, ImageFont.FreeTypeFont]]:
    """Turn a string into (glyph, font) pairs for faux small caps: uppercase
    letters + non-letters at full size, lowercase drawn as smaller capitals."""
    full = book.get(size, weight=weight_caps)
    small = book.get(max(1, round(size * theme.SMALLCAP_RATIO)), weight=weight_small)
    plan = []
    for ch in text:
        if ch.isalpha() and ch.islower():
            plan.append((ch.upper(), small))
        else:
            plan.append((ch, full))
    return plan


def smallcaps_width(plan, tracking_px: float) -> float:
    if not plan:
        return 0.0
    w = sum(_len(font, ch) for ch, font in plan)
    return w + tracking_px * (len(plan) - 1)


def draw_smallcaps(draw: ImageDraw.ImageDraw, center_x: float, baseline_y: float,
                   text: str, book: FontBook, size: int, fill: int,
                   tracking: float = theme.NAME_TRACKING,
                   weight_caps: int = 600, weight_small: int = 620) -> float:
    """Centered faux small caps on a baseline. Returns the drawn width."""
    plan = smallcaps_plan(text, book, size, weight_caps, weight_small)
    tracking_px = size * tracking
    total = smallcaps_width(plan, tracking_px)
    x = center_x - total / 2
    for ch, font in plan:
        draw.text((x, baseline_y), ch, font=font, fill=fill, anchor="ls")
        x += _len(font, ch) + tracking_px
    return total


# -- date formatting -------------------------------------------------------
def format_when(when: datetime) -> str:
    month = when.strftime("%B")
    hour = when.hour % 12 or 12
    ampm = "AM" if when.hour < 12 else "PM"
    return f"{when.day} {month} {when.year}  ·  {hour}:{when.minute:02d} {ampm}"


# -- caption block ---------------------------------------------------------
def caption_block(draw: ImageDraw.ImageDraw, center_x: float, top_y: float,
                  common_name: str, scientific_name: str, when: Optional[datetime],
                  book: FontBook = FONTS, meta_override: Optional[str] = None) -> float:
    """Render the museum caption: common name (small caps), scientific name
    (italic), hairline rule, then date/time. Returns the bottom y."""
    # Common name — the anchor of the block.
    name_font = book.get(theme.NAME_SIZE, weight=600)
    ascent, _ = name_font.getmetrics()
    baseline = top_y + ascent
    draw_smallcaps(draw, center_x, baseline, common_name, book,
                   theme.NAME_SIZE, theme.INK, theme.NAME_TRACKING)

    # Scientific name — italic, sentence case as given (Genus species).
    sci_font = book.get(theme.SCI_SIZE, italic=True, weight=460)
    baseline += theme.NAME_SIZE * 0.30 + theme.SCI_SIZE
    draw.text((center_x, baseline), scientific_name, font=sci_font,
              fill=theme.INK, anchor="ms")

    # Hairline rule.
    rule_y = baseline + theme.SCI_SIZE * 0.55 + 34
    half = theme.RULE_WIDTH / 2
    draw.rectangle([center_x - half, rule_y, center_x + half, rule_y + theme.RULE_THICKNESS - 1],
                   fill=theme.RULE)

    # Date / time.
    meta = meta_override if meta_override is not None else (format_when(when) if when else "")
    if meta:
        meta_baseline = rule_y + 30 + theme.META_SIZE
        draw_smallcaps(draw, center_x, meta_baseline, meta, book, theme.META_SIZE,
                       theme.INK_SOFT, theme.META_TRACKING, weight_caps=500, weight_small=520)
        return meta_baseline
    return rule_y + theme.RULE_THICKNESS


def plate_number_mark(draw: ImageDraw.ImageDraw, ordinal: int,
                      book: FontBook = FONTS) -> None:
    """Engraved 'No. 47' in the top-right corner, counting unique species seen."""
    text = f"No. {ordinal}"
    font = book.get(theme.PLATE_NO_SIZE, weight=520)
    tracking_px = theme.PLATE_NO_SIZE * theme.PLATE_NO_TRACKING
    total = tracked_width(font, text, tracking_px)
    x_right = theme.WIDTH - theme.MARGIN_X
    baseline = theme.MARGIN_TOP - 24
    # right-align: start so the text ends at x_right
    x = x_right - total
    for ch in text:
        draw.text((x, baseline), ch, font=font, fill=theme.INK_SOFT, anchor="ls")
        x += _len(font, ch) + tracking_px


def wrap_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if _len(font, trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines
