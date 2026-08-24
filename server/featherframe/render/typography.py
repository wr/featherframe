"""Type. This is where a hobby project becomes an heirloom, per the spec.

We use EB Garamond (OFL) as a variable font. When libraqm is present we shape
real OpenType features — swash italics for the common name, true small caps
with old-style figures for the scientific name and date. When it is absent
(some Pis ship without it) we fall back to *faux* small caps: lowercase drawn
as smaller capitals. Both read like a museum plate; the OT path just adds the
swashes and old-style figures.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

from PIL import ImageDraw, ImageFont, features as _pil_features

from .. import paths
from . import theme

_FONTS = paths.fonts_dir()
_ROMAN = _FONTS / "EBGaramond[wght].ttf"
_ITALIC = _FONTS / "EBGaramond-Italic[wght].ttf"

# Real OpenType shaping (swashes, small caps, old-style figures) needs libraqm.
HAS_RAQM = bool(_pil_features.check("raqm"))
_LAYOUT = ImageFont.Layout.RAQM if HAS_RAQM else ImageFont.Layout.BASIC


class FontBook:
    """Caches sized/weighted font instances so we load the TTFs once."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}

    def get(self, size: int, italic: bool = False, weight: int = 400) -> ImageFont.FreeTypeFont:
        key = ("i" if italic else "r", int(size), int(weight))
        font = self._cache.get(key)
        if font is None:
            path = _ITALIC if italic else _ROMAN
            font = ImageFont.truetype(str(path), int(size), layout_engine=_LAYOUT)
            try:
                font.set_variation_by_axes([weight])
            except Exception:
                pass  # non-variable fallback: size-only
            self._cache[key] = font
        return font


# One shared book for the process.
FONTS = FontBook()


# -- OpenType (RAQM) drawing ----------------------------------------------
def _feat(features: Sequence[str]) -> Optional[list[str]]:
    return list(features) if HAS_RAQM else None


def ot_length(font: ImageFont.FreeTypeFont, text: str,
              features: Sequence[str]) -> float:
    return font.getlength(text, features=_feat(features))


def draw_ot(draw: ImageDraw.ImageDraw, x: float, baseline_y: float, text: str,
            font: ImageFont.FreeTypeFont, fill: int, features: Sequence[str],
            anchor: str = "ls") -> None:
    """Draw one shaped run (ligatures, swashes, kerning intact). No tracking."""
    draw.text((x, baseline_y), text, font=font, fill=fill, anchor=anchor,
              features=_feat(features))


def draw_ot_tracked(draw: ImageDraw.ImageDraw, center_x: float, baseline_y: float,
                    text: str, font: ImageFont.FreeTypeFont, fill: int,
                    features: Sequence[str], tracking_px: float) -> float:
    """Centered, letter-spaced run. Each glyph is shaped on its own so features
    like ``smcp``/``onum`` still apply; contextual joins are dropped, which is
    invisible once the letters are tracked apart. Returns the drawn width."""
    feats = _feat(features)
    widths = [font.getlength(ch, features=feats) for ch in text]
    total = sum(widths) + tracking_px * max(0, len(text) - 1)
    x = center_x - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, baseline_y), ch, font=font, fill=fill, anchor="ls",
                  features=feats)
        x += w + tracking_px
    return total


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


def _when_parts(when: datetime) -> tuple[str, str]:
    """Lowercase (date, time) parts so ``smcp`` renders them as small caps and
    ``onum`` gives the figures their old-style shapes."""
    month = when.strftime("%B").lower()
    hour = when.hour % 12 or 12
    ampm = "am" if when.hour < 12 else "pm"
    return (f"{when.day} {month} {when.year}", f"{hour}:{when.minute:02d} {ampm}")


# -- caption block ---------------------------------------------------------
def caption_block(draw: ImageDraw.ImageDraw, center_x: float, top_y: float,
                  common_name: str, scientific_name: str, when: Optional[datetime],
                  book: FontBook = FONTS, meta_override: Optional[str] = None) -> float:
    """Render the museum caption: common name (swash italic), scientific name
    (small caps), hairline rule, then date/time. Returns the bottom y."""
    if not HAS_RAQM:
        return _caption_block_faux(draw, center_x, top_y, common_name,
                                   scientific_name, when, book, meta_override)

    # Common name — swash italic, auto-fit to the content width.
    size = theme.TITLE_SIZE
    while size > 60:
        title_font = book.get(size, italic=True, weight=theme.TITLE_WEIGHT)
        if ot_length(title_font, common_name, theme.TITLE_FEATURES) <= theme.CONTENT_W:
            break
        size -= 3
    ascent, _ = title_font.getmetrics()
    baseline = top_y + int(ascent * 0.92)
    draw_ot(draw, center_x, baseline, common_name, title_font, theme.INK,
            theme.TITLE_FEATURES, anchor="ms")

    # Scientific name — italic small caps with old-style figures.
    sci_font = book.get(theme.SUBTITLE_SIZE, italic=True, weight=theme.SUBTITLE_WEIGHT)
    baseline += size * 0.28 + theme.SUBTITLE_SIZE
    draw_ot_tracked(draw, center_x, baseline, scientific_name.lower(), sci_font,
                    theme.INK_MEDIUM, theme.SUBTITLE_FEATURES,
                    theme.SUBTITLE_SIZE * theme.SUBTITLE_TRACKING)

    # Hairline rule.
    rule_y = baseline + theme.SUBTITLE_SIZE * 0.60 + 34
    half = theme.RULE_WIDTH / 2
    draw.rectangle([center_x - half, rule_y, center_x + half, rule_y + theme.RULE_THICKNESS - 1],
                   fill=theme.RULE)

    # Date / time, split around a floral fleuron.
    date_font = book.get(theme.DATE_SIZE, italic=True, weight=theme.DATE_WEIGHT)
    tracking_px = theme.DATE_SIZE * theme.DATE_TRACKING
    meta_baseline = rule_y + 47 + theme.DATE_SIZE
    if meta_override is not None:
        if meta_override:
            draw_ot_tracked(draw, center_x, meta_baseline, meta_override.lower(),
                            date_font, theme.INK_MEDIUM, theme.DATE_FEATURES, tracking_px)
        else:
            return rule_y + theme.RULE_THICKNESS
    elif when:
        date_str, time_str = _when_parts(when)
        feats = _feat(theme.DATE_FEATURES)
        gap = theme.DATE_SIZE * 0.7
        orn_w = date_font.getlength(theme.DATE_ORNAMENT)
        date_w = sum(date_font.getlength(c, features=feats) for c in date_str) + \
            tracking_px * max(0, len(date_str) - 1)
        time_w = sum(date_font.getlength(c, features=feats) for c in time_str) + \
            tracking_px * max(0, len(time_str) - 1)
        total = date_w + gap + orn_w + gap + time_w
        x = center_x - total / 2
        draw_ot_tracked(draw, x + date_w / 2, meta_baseline, date_str, date_font,
                        theme.INK_MEDIUM, theme.DATE_FEATURES, tracking_px)
        x += date_w + gap
        draw.text((x, meta_baseline), theme.DATE_ORNAMENT, font=date_font,
                  fill=theme.INK_MEDIUM, anchor="ls")
        x += orn_w + gap
        draw_ot_tracked(draw, x + time_w / 2, meta_baseline, time_str, date_font,
                        theme.INK_MEDIUM, theme.DATE_FEATURES, tracking_px)
    else:
        return rule_y + theme.RULE_THICKNESS
    return meta_baseline


def _caption_block_faux(draw: ImageDraw.ImageDraw, center_x: float, top_y: float,
                        common_name: str, scientific_name: str,
                        when: Optional[datetime], book: FontBook,
                        meta_override: Optional[str]) -> float:
    """No-libraqm fallback: the original faux small-caps caption."""
    name_font = book.get(theme.NAME_SIZE, weight=600)
    ascent, _ = name_font.getmetrics()
    baseline = top_y + ascent
    draw_smallcaps(draw, center_x, baseline, common_name, book,
                   theme.NAME_SIZE, theme.INK, theme.NAME_TRACKING)

    sci_font = book.get(theme.SCI_SIZE, italic=True, weight=460)
    baseline += theme.NAME_SIZE * 0.30 + theme.SCI_SIZE
    draw.text((center_x, baseline), scientific_name, font=sci_font,
              fill=theme.INK, anchor="ms")

    rule_y = baseline + theme.SCI_SIZE * 0.55 + 34
    half = theme.RULE_WIDTH / 2
    draw.rectangle([center_x - half, rule_y, center_x + half, rule_y + theme.RULE_THICKNESS - 1],
                   fill=theme.RULE)

    meta = meta_override if meta_override is not None else (format_when(when) if when else "")
    if meta:
        meta_baseline = rule_y + 30 + theme.META_SIZE
        draw_smallcaps(draw, center_x, meta_baseline, meta, book, theme.META_SIZE,
                       theme.INK_MEDIUM, theme.META_TRACKING, weight_caps=500, weight_small=520)
        return meta_baseline
    return rule_y + theme.RULE_THICKNESS


def plate_number_mark(draw: ImageDraw.ImageDraw, ordinal: int,
                      book: FontBook = FONTS) -> None:
    """Engraved '№ 47' in the top-right corner, counting unique species seen.
    Italic numero + old-style figures, aligned to the top and right margins."""
    x_right = theme.WIDTH - theme.MARGIN_X
    if HAS_RAQM:
        text = f"{theme.PLATE_NO_NUMERO} {ordinal}"
        font = book.get(theme.PLATE_NO_SIZE, italic=True, weight=theme.DATE_WEIGHT)
        feats = _feat(theme.PLATE_NO_FEATURES)
        # Align the glyph's ink top (not the ascender line) to the top margin:
        # getbbox's top is the ascender->ink gap for this string.
        top_gap = font.getbbox(text, features=feats)[1]
        draw.text((x_right, theme.MARGIN_TOP - top_gap), text, font=font,
                  fill=theme.INK_MEDIUM, anchor="ra", features=feats)
        return
    # Faux fallback: plain "No. 47".
    text = f"No. {ordinal}"
    font = book.get(theme.PLATE_NO_SIZE, weight=520)
    tracking_px = theme.PLATE_NO_SIZE * theme.PLATE_NO_TRACKING
    total = tracked_width(font, text, tracking_px)
    x = x_right - total
    baseline = theme.MARGIN_TOP + theme.PLATE_NO_SIZE
    for ch in text:
        draw.text((x, baseline), ch, font=font, fill=theme.INK_MEDIUM, anchor="ls")
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
