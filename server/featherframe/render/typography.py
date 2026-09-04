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

import functools
import logging

import re

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFont, features as _pil_features

from .. import paths
from . import theme

log = logging.getLogger("featherframe.typography")
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


# -- script caption (W-708) -------------------------------------------------
# The caption's voice is a monoline script. The file comes from the box's data
# dir first (data/fonts/script.ttf — a licensed face that must not ship in the
# public repo), then the bundled open-licence Ms Madi, and if neither loads,
# Garamond italic, so a missing font degrades the look, never the frame.
_SCRIPT_BUNDLED = _FONTS / "MsMadi-Regular.ttf"
_SCRIPT_DATA_NAME = "script.ttf"


def script_font_path() -> Path:
    for cand in (paths.data_dir() / "fonts" / _SCRIPT_DATA_NAME, _SCRIPT_BUNDLED):
        if cand.exists():
            return cand
    return _ITALIC


@functools.lru_cache(maxsize=None)
def script_font(size: int) -> ImageFont.FreeTypeFont:
    path = script_font_path()
    if path != _ITALIC:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            log.warning("script font %s failed to load; using Garamond italic", path)
    return FONTS.get(size, italic=True, weight=500)


def script_width(text: str, size: int) -> float:
    return script_font(size).getlength(text)


def draw_script(field: Image.Image, x: float, baseline: float, text: str, size: int,
                fill: int, stroke: float = 0.0, anchor: str = "ms") -> float:
    """Draw `text` in the script onto `field` (an 'L' image), darken-composited
    so it sits on art as well as on paper. `stroke` is a synthetic bold — the
    script has one weight and reads thin on e-ink — in pixels added to every
    edge; fractional strokes come from a 4x supersample. `anchor` is the
    horizontal anchor at x: "ms" centred, "ls" left, "rs" right. Returns the
    text width."""
    font = script_font(size)
    width = font.getlength(text)
    left = {"ms": x - width / 2, "ls": x, "rs": x - width}[anchor]
    if stroke <= 0:
        ImageDraw.Draw(field).text((left, baseline), text, font=font, fill=fill, anchor="ls")
        return width
    ss = 4
    big = script_font(size * ss)
    pad = int(size * 0.6) * ss
    w = int(big.getlength(text)) + 2 * pad
    h = int(size * ss * 1.7)
    layer = Image.new("L", (w, h), 255)
    ImageDraw.Draw(layer).text((pad, size * ss * 1.2), text, font=big, fill=fill, anchor="ls",
                               stroke_width=round(stroke * ss), stroke_fill=fill)
    small = layer.resize((w // ss, h // ss), Image.LANCZOS)
    ox, oy = int(round(left - pad / ss)), int(round(baseline - size * 1.2))
    region = field.crop((ox, oy, ox + small.width, oy + small.height))
    field.paste(ImageChops.darker(region, small), (ox, oy))
    return width


def fit_script_title(text: str, max_w: float) -> int:
    size = theme.SCRIPT_TITLE_SIZE
    while size > theme.SCRIPT_TITLE_MIN and script_width(text, size) > max_w:
        size -= 2
    return size


def caption(field: Image.Image, top_y: float, common_name: str, scientific_name: str,
            lines: list[str]) -> float:
    """The script caption: title (auto-fit), engraved Latin name with its
    period, then `lines` — the plate legend, plus any extra line the caller
    adds — in the small script. `top_y` is the title's ink top. Returns the
    last baseline drawn."""
    cx = theme.WIDTH / 2
    size = fit_script_title(common_name, theme.CONTENT_W)
    baseline = top_y + round(size * theme.SCRIPT_TITLE_ASCENT)
    draw_script(field, cx, baseline, common_name, size, theme.INK, stroke=theme.TITLE_STROKE)
    baseline += theme.TITLE_TO_LATIN
    latin = scientific_name.upper() + theme.LATIN_PERIOD
    sci_size = theme.SUBTITLE_SIZE
    while sci_size > 24 and engraved_width(latin, sci_size) > theme.CONTENT_W:
        sci_size -= 1
    draw_engraved(ImageDraw.Draw(field), cx, baseline, latin, sci_size, theme.INK_MEDIUM)
    first = True
    for line in lines:
        baseline += theme.LATIN_TO_LEGEND if first else theme.LEGEND_PITCH
        first = False
        size = theme.LEGEND_SIZE
        while size > 18 and script_width(line, size) > theme.CONTENT_W:
            size -= 1
        draw_script(field, cx, baseline, line, size, theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE)
    return baseline


def _corner_parts(when: datetime) -> tuple[str, str]:
    """("1 Sep", "8:14 am") for the date mark: day + abbreviated month, then
    the time. The date is always shown so a three-day-old plate reads as
    three days old, not as this morning's."""
    _, clock = _when_parts(when)
    return f"{when.day} {when.strftime('%b')}", clock


def date_text(when: datetime) -> str:
    date_part, clock = _corner_parts(when)
    return f"{date_part} {theme.CORNER_SEP} {clock}"


def date_mark(field: Image.Image, when: datetime) -> float:
    """"4 Sep · 11:34 am" in the small script, tucked into the bottom-left
    corner. Repeats of a species DO re-render (the owner wants the clock to
    move with the bird), and a held or quiet frame can sit for days — so the
    mark always carries the date: a stale plate must look stale. Returns
    the mark's width."""
    return draw_script(field, theme.CORNER_INSET, theme.MARKS_BASELINE, date_text(when),
                       theme.CORNER_SIZE, theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE,
                       anchor="ls")


def date_mark_max_width() -> float:
    return script_width(f"30 Sep {theme.CORNER_SEP} 12:44 pm", theme.CORNER_SIZE)


def plate_number_mark(field: Image.Image, ordinal: int) -> float:
    """"No. 47" in the bottom-right corner, counting unique species seen."""
    return draw_script(field, theme.WIDTH - theme.CORNER_INSET, theme.MARKS_BASELINE,
                       f"{theme.PLATE_NO_PREFIX} {ordinal}", theme.CORNER_SIZE,
                       theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE, anchor="rs")


def plate_number_max_width() -> float:
    return script_width(f"{theme.PLATE_NO_PREFIX} 888", theme.CORNER_SIZE)


def note_line(field: Image.Image, text: str, max_w: float = theme.CONTENT_W) -> None:
    """One small script line centred on the corner marks' baseline — the
    gone-quiet note. Shrinks rather than clips if wider than `max_w` (the room
    between the marks)."""
    size = theme.NOTE_SIZE
    while size > theme.NOTE_MIN_SIZE and script_width(text, size) > max_w:
        size -= 1
    draw_script(field, theme.WIDTH / 2, theme.MARKS_BASELINE, text, size, theme.INK_MEDIUM,
                stroke=theme.LEGEND_STROKE)


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
