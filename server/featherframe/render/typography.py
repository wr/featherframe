"""Type. This is where a hobby project becomes an heirloom, per the spec.

Two faces. EB Garamond (OFL, variable) carries the italic voice: the swash
common name, the date line, the corner mark. Adorn Engraved carries the
letterspaced capitals: the scientific name and the collage key. When libraqm
is present we shape real OpenType features — swashes, small caps, old-style
figures. When it is absent (some Pis ship without it) the Garamond runs fall
back to *faux* small caps: lowercase drawn as smaller capitals. The engraved
capitals need no shaping, so they render the same either way.
"""
from __future__ import annotations

import re

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
_ENGRAVED = _FONTS / "Adorn-Engraved.ttf"

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


# Characters Adorn Engraved's cmap lacks, mapped to glyphs it has (the okina
# in Hawaiian bird names would otherwise vanish into a bare .notdef advance).
_ENGRAVED_SUBS = str.maketrans({"ʻ": "'", "ʼ": "'"})


@lru_cache(maxsize=8)
def engraved(size: int) -> Optional[ImageFont.FreeTypeFont]:
    """Adorn Engraved at `size`. A static all-caps face: no variation axes,
    no features, so it renders identically with or without libraqm. Returns
    None when the file is absent — the explicit is_file() check matters,
    because PIL would otherwise silently load a same-named system font."""
    if not _ENGRAVED.is_file():
        return None
    try:
        return ImageFont.truetype(str(_ENGRAVED), int(size), layout_engine=_LAYOUT)
    except OSError:
        return None


def draw_engraved(draw: ImageDraw.ImageDraw, center_x: float, baseline_y: float,
                  text: str, size: int, fill: int,
                  tracking: float = theme.SUBTITLE_TRACKING) -> float:
    """Centered engraved capitals with letter-spacing. Falls back to Garamond
    faux small caps if the engraved face is missing, so a bad deploy degrades
    instead of freezing the panel. Returns the drawn width."""
    text = text.translate(_ENGRAVED_SUBS)
    font = engraved(size)
    if font is None:
        return draw_smallcaps(draw, center_x, baseline_y, text, FONTS, size,
                              fill, tracking, weight_caps=520, weight_small=520)
    tracking_px = size * tracking
    total = tracked_width(font, text, tracking_px)
    x = center_x - total / 2
    for ch in text:
        draw.text((x, baseline_y), ch, font=font, fill=fill, anchor="ls")
        x += _len(font, ch) + tracking_px
    return total


def engraved_width(text: str, size: int,
                   tracking: float = theme.SUBTITLE_TRACKING) -> float:
    text = text.translate(_ENGRAVED_SUBS)
    font = engraved(size)
    if font is None:
        plan = smallcaps_plan(text, FONTS, size, 520, 520)
        return smallcaps_width(plan, size * tracking)
    return tracked_width(font, text, size * tracking)


# -- OpenType (RAQM) drawing ----------------------------------------------
def _feat(features: Sequence[str]) -> Optional[list[str]]:
    return list(features) if HAS_RAQM else None


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


# -- the swash italic title -------------------------------------------------
# Sequences kept whole while tracking, so their ligatures still form.
_LIG_GLUE = re.compile(r"f+[bhijklt]?|Th")


def _title_segments(text: str) -> list[tuple[int, str]]:
    """(start index, run) pairs: single characters, except ligature sequences."""
    segs, i = [], 0
    while i < len(text):
        m = _LIG_GLUE.match(text, i)
        if m and m.end() - i > 1:
            segs.append((i, text[i:m.end()]))
            i = m.end()
        else:
            segs.append((i, text[i]))
            i += 1
    return segs


def _title_features(text: str) -> list[str]:
    """Title features, with "swsh" range-disabled on the capitals that should
    keep their plain form (the J's swash overwhelms short names)."""
    feats = list(theme.TITLE_FEATURES)
    for i, ch in enumerate(text):
        if ch in theme.TITLE_NO_SWASH:
            feats.append(f"swsh[{i}:{i + 1}]=0")
    return feats


def title_width(font: ImageFont.FreeTypeFont, text: str, tracking_px: float) -> float:
    segs = _title_segments(text)
    return font.getlength(text, features=_feat(_title_features(text))) + \
        tracking_px * max(0, len(segs) - 1)


def draw_title(draw: ImageDraw.ImageDraw, center_x: float, baseline_y: float,
               text: str, font: ImageFont.FreeTypeFont, fill: int,
               tracking_px: float) -> None:
    """Centered swash italic with tightened tracking. Runs are placed by
    "whole minus suffix" widths so cross-run kerning survives; ligature
    sequences stay glued so their glyphs still form."""
    segs = _title_segments(text)
    full = font.getlength(text, features=_feat(_title_features(text)))
    total = full + tracking_px * max(0, len(segs) - 1)
    x0 = center_x - total / 2
    for k, (i, seg) in enumerate(segs):
        suffix = text[i:]
        x = full - font.getlength(suffix, features=_feat(_title_features(suffix)))
        seg_feats = list(theme.TITLE_FEATURES)
        if seg in theme.TITLE_NO_SWASH:
            seg_feats.append("-swsh")
        draw.text((x0 + x + k * tracking_px, baseline_y), seg, font=font,
                  fill=fill, anchor="ls", features=_feat(seg_feats))


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


def fit_smallcaps_size(text: str, book: FontBook, start: int, tracking: float,
                       max_w: float, floor: int = 42) -> int:
    """Largest faux-small-caps size (from `start` down) whose run fits `max_w`.
    Long BirdNET names (hyphenated warblers and swallows) clip at a fixed size."""
    size = start
    while size > floor:
        plan = smallcaps_plan(text, book, size, 600, 620)
        if smallcaps_width(plan, size * tracking) <= max_w:
            break
        size -= 3
    return size


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
    """(date, time) runs: the date keeps its case ("17 May 2026" in true
    italic), the time's lowercase am/pm becomes small caps under ``smcp``;
    ``onum`` gives the figures their old-style shapes."""
    month = when.strftime("%B")
    hour = when.hour % 12 or 12
    ampm = "am" if when.hour < 12 else "pm"
    return (f"{when.day} {month} {when.year}", f"{hour}:{when.minute:02d} {ampm}")


# -- caption block ---------------------------------------------------------
def caption_block(draw: ImageDraw.ImageDraw, center_x: float, top_y: float,
                  common_name: str, scientific_name: str,
                  book: FontBook = FONTS) -> float:
    """Render the museum caption: common name (swash italic) over the
    scientific name (engraved capitals), nothing else — the detection date
    lives in the corner (time_corner_mark). Returns the sci baseline."""
    if not HAS_RAQM:
        return _caption_block_faux(draw, center_x, top_y, common_name,
                                   scientific_name, book)

    # Common name — swash italic, tightened, auto-fit to the content width.
    # top_y is the cap top, so the block starts exactly where the ink does.
    size = theme.TITLE_SIZE
    while size > 60:
        title_font = book.get(size, italic=True, weight=theme.TITLE_WEIGHT)
        if title_width(title_font, common_name,
                       size * theme.TITLE_TRACKING) <= theme.CONTENT_W:
            break
        size -= 3
    baseline = top_y + round(size * theme.TITLE_CAP)
    draw_title(draw, center_x, baseline, common_name, title_font, theme.INK,
               size * theme.TITLE_TRACKING)

    # Scientific name — engraved capitals, letterspaced.
    baseline += round(size * theme.CAPTION_SCI_DROP)
    sci_size = theme.SUBTITLE_SIZE
    while sci_size > 24 and engraved_width(scientific_name.upper(),
                                           sci_size) > theme.CONTENT_W:
        sci_size -= 1
    draw_engraved(draw, center_x, baseline, scientific_name.upper(), sci_size,
                  theme.INK_MEDIUM)
    return baseline


def _corner_parts(when: datetime) -> tuple[str, str]:
    """("1 Sep", "8:14 am") for the corner mark: day + abbreviated month, then
    the time. The date is always shown so a three-day-old plate reads as
    three days old, not as this morning's."""
    _, clock = _when_parts(when)
    return f"{when.day} {when.strftime('%b')}", clock


def time_corner_mark(draw: ImageDraw.ImageDraw, when: datetime,
                     book: FontBook = FONTS) -> None:
    """Italic old-style date and time ("1 Sep · 8:14 am", small-cap am) at the
    top-left margin, mirroring the № mark. Repeats of a species DO re-render
    (the owner wants the clock to move with the bird), and a held or quiet
    frame can sit for days — so the mark always carries the date: a stale
    plate must look stale. The date keeps its case; the time takes the
    small-cap am/pm, so the two are shaped as separate runs."""
    date_text, clock = _corner_parts(when)
    font = book.get(theme.PLATE_NO_SIZE, italic=True, weight=theme.PLATE_NO_WEIGHT)
    if HAS_RAQM:
        date_feats = _feat(theme.DATE_FEATURES)
        time_feats = _feat(theme.TIME_FEATURES)
        mid = f" {theme.CORNER_SEP} "
        # Align the run's ink top (the taller of the two runs) to the top margin.
        top_gap = min(font.getbbox(date_text + mid, features=date_feats)[1],
                      font.getbbox(clock, features=time_feats)[1])
        y = theme.MARGIN_TOP - top_gap
        draw.text((theme.MARGIN_X, y), date_text + mid, font=font,
                  fill=theme.INK_MEDIUM, anchor="la", features=date_feats)
        x = theme.MARGIN_X + font.getlength(date_text + mid, features=date_feats)
        draw.text((x, y), clock, font=font, fill=theme.INK_MEDIUM, anchor="la",
                  features=time_feats)
        return
    draw.text((theme.MARGIN_X, theme.MARGIN_TOP),
              f"{date_text} {theme.CORNER_SEP} {clock}", font=font,
              fill=theme.INK_MEDIUM, anchor="la")


def first_recorded_line(draw: ImageDraw.ImageDraw, center_x: float, baseline_y: float,
                        text: str = "First recorded today", book: FontBook = FONTS) -> None:
    """The "first recorded ..." annotation under the scientific name: the
    corner date's italic voice (old-style figures) in the full ink, a pair of
    hederae either side in the medium ink. Centred on the text, not the run,
    so it lines up with the caption whatever the ornaments' widths."""
    size = theme.FIRST_LINE_SIZE
    if not HAS_RAQM:
        # Faux: upright small caps at the same weight, no ornaments (the
        # hedera is an OpenType glyph we can't rely on shaping).
        draw_smallcaps(draw, center_x, baseline_y, text, book, size - 6, theme.INK,
                       theme.META_TRACKING, weight_caps=500, weight_small=520)
        return
    font = book.get(size, italic=True, weight=theme.FIRST_LINE_WEIGHT)
    feats = _feat(theme.FIRST_LINE_FEATURES)
    width = font.getlength(text, features=feats)
    draw.text((center_x, baseline_y), text, font=font, fill=theme.INK, anchor="ms",
              features=feats)
    left, right = theme.FIRST_LINE_ORNAMENTS
    gap = size * theme.FIRST_LINE_ORNAMENT_GAP
    if left:
        draw.text((center_x - width / 2 - gap, baseline_y), left, font=font,
                  fill=theme.INK_MEDIUM, anchor="rs")
    if right:
        draw.text((center_x + width / 2 + gap, baseline_y), right, font=font,
                  fill=theme.INK_MEDIUM, anchor="ls")


def _caption_block_faux(draw: ImageDraw.ImageDraw, center_x: float, top_y: float,
                        common_name: str, scientific_name: str,
                        book: FontBook = FONTS) -> float:
    """caption_block without RAQM: faux small caps stand in for the swash."""
    name_size = fit_smallcaps_size(common_name, book, theme.NAME_SIZE,
                                   theme.NAME_TRACKING, theme.CONTENT_W)
    draw_smallcaps(draw, center_x, top_y + name_size, common_name, book,
                   name_size, theme.INK, theme.NAME_TRACKING)
    baseline = top_y + name_size + round(name_size * theme.CAPTION_SCI_DROP)
    draw_engraved(draw, center_x, baseline, scientific_name.upper(),
                  theme.SUBTITLE_SIZE, theme.INK_MEDIUM)
    return baseline

def plate_number_mark(draw: ImageDraw.ImageDraw, ordinal: int,
                      book: FontBook = FONTS) -> None:
    """Engraved '№ 47' in the top-right corner, counting unique species seen.
    Italic numero + old-style figures, aligned to the top and right margins."""
    x_right = theme.WIDTH - theme.MARGIN_X
    if HAS_RAQM:
        text = f"{theme.PLATE_NO_NUMERO} {ordinal}"
        font = book.get(theme.PLATE_NO_SIZE, italic=True, weight=theme.PLATE_NO_WEIGHT)
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


def note_line(draw: ImageDraw.ImageDraw, text: str, book: FontBook = FONTS) -> None:
    """One italic footnote centred in the bottom margin, in the medium ink —
    the gone-quiet note. Sits on its own fixed baseline near the panel's
    bottom edge so it clears the caption above it whatever the title's size;
    shrinks rather than clips if the text is ever wider than the content."""
    size = theme.NOTE_SIZE
    feats = _feat(theme.NOTE_FEATURES)
    font = book.get(size, italic=True, weight=theme.NOTE_WEIGHT)
    while size > 18 and font.getlength(text, features=feats) > theme.CONTENT_W:
        size -= 2
        font = book.get(size, italic=True, weight=theme.NOTE_WEIGHT)
    draw.text((theme.WIDTH / 2, theme.NOTE_BASELINE), text, font=font,
              fill=theme.INK_MEDIUM, anchor="ms", features=feats)


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
