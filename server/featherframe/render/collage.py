"""Daily collage: a grid of the day's most-frequent species.

2 columns, up to 3 rows (so 2x2 or 2x3 per the spec). Each cell is a mini-plate:
the bird art, its common name in small caps, and the day's detection count as
"x14". A quiet title band up top carries the date and the day's totals. Species
we have no plate for get a typographic mini-cell — still never a wrong bird.

If the caller only has one species for the day, it should render a single frame
instead; collage assumes two or more.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as ddate
from typing import Optional

from PIL import Image, ImageChops, ImageDraw

from . import theme, typography
from .compose import _fit, _new_field
from .provider import ArtProvider


@dataclass
class CollageCell:
    common_name: str
    scientific_name: str
    count: int


def _paste_art(field: Image.Image, art: Image.Image, box: tuple[int, int, int, int],
               v_align: float = 0.5) -> None:
    bl, bt, br, bb = box
    fitted = _fit(art, br - bl, bb - bt)
    x = bl + (br - bl - fitted.width) // 2
    y = bt + int((bb - bt - fitted.height) * v_align)
    region = field.crop((bl, bt, br, bb))
    layer = Image.new("L", region.size, 255)
    layer.paste(fitted, (x - bl, y - bt))
    field.paste(ImageChops.darker(region, layer), (bl, bt))


def _grid(n: int) -> tuple[int, int]:
    """(cols, rows). 2 columns; up to 3 rows; caps at 6 cells."""
    n = min(n, 6)
    cols = 1 if n == 1 else 2
    rows = math.ceil(n / cols)
    return cols, rows


def _title_band(field: Image.Image, when: ddate, title: str) -> int:
    """One italic header line — "Sightings ~ August 27" — swash, tightened,
    auto-fit to the content width. Returns the y where the art may begin."""
    draw = ImageDraw.Draw(field)
    cx = theme.WIDTH / 2
    text = f"{title} ~ {when.strftime('%B')} {when.day}"
    if typography.HAS_RAQM:
        size = theme.COLLAGE_TITLE_SIZE
        while size > 60:
            font = typography.FONTS.get(size, italic=True, weight=theme.TITLE_WEIGHT)
            if typography.title_width(font, text,
                                      size * theme.TITLE_TRACKING) <= theme.CONTENT_W:
                break
            size -= 3
        typography.draw_title(draw, cx, theme.COLLAGE_TITLE_BASELINE, text, font,
                              theme.INK, size * theme.TITLE_TRACKING)
    else:
        size = typography.fit_smallcaps_size(text, typography.FONTS, 58, 0.12,
                                             theme.CONTENT_W)
        typography.draw_smallcaps(draw, cx, theme.COLLAGE_TITLE_BASELINE, text,
                                  typography.FONTS, size, theme.INK, 0.12)
    return theme.COLLAGE_ART_TOP


def _fit_key(entries: list[str], max_w: float) -> tuple[int, list[list[str]]]:
    """(font size, entry lines) for the key: the widest line must fit the
    sheet. Long BirdNET names (hyphenated warblers and swallows) would
    otherwise clip silently at the panel edges. Tries fewer lines at larger
    sizes first, then one entry per line, then scales below the size floor —
    the fit is a guarantee, not a preference."""
    if not entries:
        return theme.KEY_SIZES[-1], []

    def width(chunk: list[str], size: int) -> float:
        gap = size * theme.KEY_ENTRY_GAP
        return sum(typography.engraved_width(e, size, theme.KEY_TRACKING)
                   for e in chunk) + gap * (len(chunk) - 1)

    for size in theme.KEY_SIZES:
        for lines in range(1, len(entries) + 1):
            per = -(-len(entries) // lines)  # ceil
            chunks = [c for c in (entries[i * per:(i + 1) * per]
                                  for i in range(lines)) if c]
            if max(width(c, size) for c in chunks) <= max_w:
                return size, chunks
    # Even one entry per line overflows at the floor: scale to the widest.
    size = theme.KEY_SIZES[-1]
    widest = max(width([e], size) for e in entries)
    size = max(12, math.floor(size * max_w / widest))
    return size, [[e] for e in entries]


def _draw_key(draw: ImageDraw.ImageDraw, key_size: int,
              key_lines: list[list[str]], bottom: int = theme.KEY_BOTTOM) -> int:
    """Engraved-caps key lines, bottom-anchored and centered, each line's
    entries separated by a wide gap. `bottom` is the last baseline's height
    above the panel edge. Returns the key's ink top."""
    if not key_lines:
        return theme.HEIGHT - theme.MARGIN_BOTTOM
    cx = theme.WIDTH / 2
    line_h = round(key_size * theme.KEY_LINE_H)
    gap = key_size * theme.KEY_ENTRY_GAP
    first_baseline = theme.HEIGHT - bottom - (len(key_lines) - 1) * line_h
    for li, chunk in enumerate(key_lines):
        widths = [typography.engraved_width(e, key_size, theme.KEY_TRACKING)
                  for e in chunk]
        total = sum(widths) + gap * (len(chunk) - 1)
        x = cx - total / 2
        baseline = first_baseline + li * line_h
        for entry, w in zip(chunk, widths):
            typography.draw_engraved(draw, x + w / 2, baseline, entry, key_size,
                                     theme.INK, theme.KEY_TRACKING)
            x += w + gap
    return first_baseline - round(key_size * theme.ENGRAVED_CAP)


def render_generated_collage(art: Image.Image, cells: list[CollageCell],
                             when: Optional[ddate] = None, total_detections: int = 0,
                             title: str = "The Day in Review",
                             note: Optional[str] = None) -> Image.Image:
    """The generated composite sheet: title band, the one generated artwork
    where the grid would be, and a key matching the sheet's figure numerals —
    '1. Species ×count' in prominence order. A `note` (the gone-quiet
    footnote) lifts the key so the two never share the bottom margin."""
    when = when or ddate.today()
    field = _new_field()
    draw = ImageDraw.Draw(field)

    art_top = _title_band(field, when, title)

    entries = [f"{i}. {c.common_name.upper()}" for i, c in enumerate(cells, start=1)]
    key_size, key_lines = _fit_key(entries, theme.WIDTH - 2 * 60)
    key_top = _draw_key(draw, key_size, key_lines,
                        bottom=theme.KEY_BOTTOM + (theme.NOTE_CLEAR if note else 0))
    art_bottom = key_top - theme.KEY_ART_GAP
    _paste_art(field, art,
               (theme.MARGIN_X, art_top, theme.WIDTH - theme.MARGIN_X, art_bottom),
               v_align=0.5)
    if note:
        typography.note_line(draw, note)
    return field


def render_collage(cells: list[CollageCell], provider: ArtProvider,
                   when: Optional[ddate] = None, total_detections: int = 0,
                   title: str = "A Day in the Garden",
                   note: Optional[str] = None) -> Image.Image:
    when = when or ddate.today()
    cells = cells[:6]
    cols, rows = _grid(len(cells))

    field = _new_field()
    draw = ImageDraw.Draw(field)

    # -- grid --------------------------------------------------------------
    grid_top = _title_band(field, when, title)
    grid_bottom = theme.HEIGHT - theme.MARGIN_BOTTOM
    grid_left = theme.MARGIN_X
    grid_right = theme.WIDTH - theme.MARGIN_X
    gutter_x, gutter_y = 70, 56
    cell_w = (grid_right - grid_left - gutter_x * (cols - 1)) / cols
    cell_h = (grid_bottom - grid_top - gutter_y * (rows - 1)) / rows
    caption_h = 96  # reserved at the bottom of each cell for name + count

    for i, cell in enumerate(cells):
        r, c = divmod(i, cols)
        x0 = grid_left + c * (cell_w + gutter_x)
        y0 = grid_top + r * (cell_h + gutter_y)
        art_box = (int(x0), int(y0), int(x0 + cell_w), int(y0 + cell_h - caption_h))
        ccx = x0 + cell_w / 2

        art = provider.artwork(cell.common_name, cell.scientific_name)
        if art is not None:
            _paste_art(field, art.image, art_box, v_align=0.5)
        else:
            # typographic mini: just the italic scientific name, centered in art box
            sci_font = typography.FONTS.get(40, italic=True, weight=460)
            midy = (art_box[1] + art_box[3]) / 2
            draw.text((ccx, midy), cell.scientific_name, font=sci_font,
                      fill=theme.INK_SOFT, anchor="mm")

        # cell caption: common name (small caps) + count
        name_baseline = y0 + cell_h - caption_h + 46
        typography.draw_smallcaps(draw, ccx, name_baseline, cell.common_name,
                                  typography.FONTS, 34, theme.INK, 0.06)
        count_font = typography.FONTS.get(30, weight=560)
        draw.text((ccx, name_baseline + 40), f"×{cell.count}", font=count_font,
                  fill=theme.INK_SOFT, anchor="ms")

    # The grid stops at MARGIN_BOTTOM, well above the note's baseline.
    if note:
        typography.note_line(draw, note)
    return field
