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


def _fit_key(entries: list[str], max_w: float,
             max_h: Optional[int] = None,
             sizes: tuple[int, ...] = theme.KEY_SIZES) -> tuple[int, list[list[str]]]:
    """(font size, rows) for the key. Rows are filled column-major — entry
    k+1 sits under entry k — so a long day's key reads down each column like
    a plate key. The widest row must fit `max_w`: long BirdNET names
    (hyphenated warblers and swallows) would otherwise clip silently at the
    panel edges. With `max_h` the block's ink height must fit too, so a
    thirty-species night can't push the art off the sheet. Tries fewer
    columns at larger sizes first, then more columns, then scales below the
    size floor — the fit is a guarantee, not a preference."""
    if not entries:
        return sizes[-1], []

    def rows_for(cols: int) -> list[list[str]]:
        per = -(-len(entries) // cols)  # ceil
        columns = [c for c in (entries[i * per:(i + 1) * per] for i in range(cols)) if c]
        return [[c[r] for c in columns if r < len(c)] for r in range(per)]

    def width(rows: list[list[str]], size: int) -> float:
        ncols = max(len(r) for r in rows)
        col_w = [max(typography.engraved_width(r[c], size, theme.KEY_TRACKING)
                     for r in rows if c < len(r)) for c in range(ncols)]
        return sum(col_w) + size * theme.KEY_ENTRY_GAP * (ncols - 1)

    def height(rows: list[list[str]], size: int) -> int:
        return (len(rows) - 1) * round(size * theme.KEY_LINE_H) + size

    def fits(rows: list[list[str]], size: int) -> bool:
        return width(rows, size) <= max_w and (max_h is None or height(rows, size) <= max_h)

    for size in sizes:
        for cols in range(1, len(entries) + 1):
            rows = rows_for(cols)
            if fits(rows, size):
                return size, rows
    # Nothing fits at the floor: for each column count, the largest size that
    # satisfies both bounds; keep the layout that stays largest.
    floor = sizes[-1]
    best = (0.0, [])
    for cols in range(1, len(entries) + 1):
        rows = rows_for(cols)
        size = floor * max_w / width(rows, floor)
        if max_h is not None:
            size = min(size, max_h / (theme.KEY_LINE_H * (len(rows) - 1) + 1))
        if size > best[0]:
            best = (size, rows)
    return max(12, math.floor(best[0])), best[1]


def _draw_key(draw: ImageDraw.ImageDraw, key_size: int,
              key_rows: list[list[str]], bottom: int = theme.KEY_BOTTOM) -> int:
    """Engraved-caps key, bottom-anchored and centered. A single column reads
    centered line by line; a packed key becomes aligned columns, each entry
    flush to its column's left edge, the block centered as a whole. `bottom`
    is the last baseline's height above the panel edge. Returns the key's
    ink top."""
    if not key_rows:
        return theme.HEIGHT - theme.MARGIN_BOTTOM
    cx = theme.WIDTH / 2
    line_h = round(key_size * theme.KEY_LINE_H)
    gap = key_size * theme.KEY_ENTRY_GAP
    first_baseline = theme.HEIGHT - bottom - (len(key_rows) - 1) * line_h
    ncols = max(len(r) for r in key_rows)
    col_w = [max(typography.engraved_width(r[c], key_size, theme.KEY_TRACKING)
                 for r in key_rows if c < len(r)) for c in range(ncols)]
    x0 = cx - (sum(col_w) + gap * (ncols - 1)) / 2
    for li, row in enumerate(key_rows):
        baseline = first_baseline + li * line_h
        x = x0
        for c, entry in enumerate(row):
            w = typography.engraved_width(entry, key_size, theme.KEY_TRACKING)
            typography.draw_engraved(draw, cx if ncols == 1 else x + w / 2, baseline,
                                     entry, key_size, theme.INK, theme.KEY_TRACKING)
            x += col_w[c] + gap
    return first_baseline - round(key_size * theme.ENGRAVED_CAP)


def sheet_key(cells: list[CollageCell], note: bool = False) -> tuple[int, list[list[str]]]:
    """The generated sheet's key: small engraved caps, packed into columns
    before it grows tall, so the art keeps the sheet."""
    entries = [f"{i}. {c.common_name.upper()}" for i, c in enumerate(cells, start=1)]
    return _fit_key(entries, theme.WIDTH - 2 * theme.SHEET_MARGIN_X,
                    max_h=theme.SHEET_KEY_MAX_H, sizes=theme.SHEET_KEY_SIZES)


def _key_ink_top(key_size: int, key_rows: list[list[str]], bottom: int) -> int:
    """Where a key drawn with `_draw_key` would put its ink top, without drawing."""
    if not key_rows:
        return theme.HEIGHT - theme.MARGIN_BOTTOM
    line_h = round(key_size * theme.KEY_LINE_H)
    first_baseline = theme.HEIGHT - bottom - (len(key_rows) - 1) * line_h
    return first_baseline - round(key_size * theme.ENGRAVED_CAP)


def sheet_art_box(cells: list[CollageCell], note: bool = False) -> tuple[int, int, int, int]:
    """The art box on the generated sheet for these cells: the top margin
    down to the key, full width less the sheet margins. There is no header."""
    key_size, key_rows = sheet_key(cells, note)
    bottom = theme.KEY_BOTTOM + (theme.NOTE_CLEAR if note else 0)
    art_bottom = _key_ink_top(key_size, key_rows, bottom) - theme.SHEET_KEY_ART_GAP
    return (theme.SHEET_MARGIN_X, theme.SHEET_MARGIN_TOP,
            theme.WIDTH - theme.SHEET_MARGIN_X, art_bottom)


def sheet_art_size(cells: list[CollageCell]) -> tuple[int, int]:
    """The size to generate the sheet's art at: the art box's own aspect, so
    the image fills the box instead of leaving bare paper down both sides.
    Multiples of 16 (the image API's grid); the key's height decides it, so a
    five-species night gets a taller image than a twenty-four-species one."""
    left, top, right, bottom = sheet_art_box(cells)
    w = theme.SHEET_GEN_W
    h = round(w * (bottom - top) / (right - left) / 16) * 16
    return w, h


def render_generated_collage(art: Image.Image, cells: list[CollageCell],
                             when: Optional[ddate] = None, total_detections: int = 0,
                             title: str = "The Day in Review",
                             note: Optional[str] = None) -> Image.Image:
    """The generated composite sheet: the one generated artwork from the top
    margin down, and a small key matching the sheet's figure numerals —
    '1. Species' in prominence order — packed along the bottom. No header:
    the art is the sheet. `when` and `title` are accepted for the caller's
    convenience and print nothing. A `note` (the gone-quiet footnote) lifts
    the key so the two never share the bottom margin."""
    field = _new_field()
    draw = ImageDraw.Draw(field)

    key_size, key_rows = sheet_key(cells, bool(note))
    key_top = _draw_key(draw, key_size, key_rows,
                        bottom=theme.KEY_BOTTOM + (theme.NOTE_CLEAR if note else 0))
    art_bottom = key_top - theme.SHEET_KEY_ART_GAP
    _paste_art(field, art,
               (theme.SHEET_MARGIN_X, theme.SHEET_MARGIN_TOP,
                theme.WIDTH - theme.SHEET_MARGIN_X, art_bottom),
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
