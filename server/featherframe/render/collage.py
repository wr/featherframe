"""Daily collage: the day's most-frequent species on one sheet.

Two sheets, one setting. The grid (`render_collage`) lays up to six plates out
2 columns wide; the generated sheet (`render_generated_collage`) carries one
painted scene. Both are set the same way below the art: the date in the
engraved capitals, then a numbered key ("1. BLUE JAY") packed into columns
along the bottom, and a small figure numeral on each figure. Species we have no
plate for get a typographic mini-cell — still never a wrong bird.

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
from .compose import _fit, _new_field, merge_color, new_color_layer
from .provider import ArtProvider


@dataclass
class CollageCell:
    common_name: str
    scientific_name: str
    count: int

    @property
    def species_key(self) -> str:
        """What this cell is OF, whatever the day's tally says about it."""
        return (self.scientific_name or self.common_name).strip().lower()


def same_species(a: list, b: list) -> bool:
    """Do two cell lists name the same species? The counts move all day, and
    with them the order, but the figures on a sheet do not: a sheet is of a
    set of species, and its key numbers them as painted (W-859)."""
    return sorted(c.species_key for c in a) == sorted(c.species_key for c in b)


def _paste_art(field: Image.Image, art: Image.Image, box: tuple[int, int, int, int],
               v_align: float = 0.5) -> None:
    bl, bt, br, bb = box
    fitted = _fit(art, br - bl, bb - bt)
    x = bl + (br - bl - fitted.width) // 2
    y = bt + int((bb - bt - fitted.height) * v_align)
    region = field.crop((bl, bt, br, bb))
    layer = Image.new(field.mode, region.size, 255 if field.mode == "L" else (255, 255, 255))
    layer.paste(fitted, (x - bl, y - bt))
    field.paste(ImageChops.darker(region, layer), (bl, bt))


def _grid(n: int) -> tuple[int, int]:
    """(cols, rows) for n plates: the fewest columns whose rows do not run past
    one more than the columns, so the cells stay near a plate's own upright
    shape on the 3:4 sheet. 6 -> 2x3, 12 -> 3x4, 20 -> 4x5."""
    if n <= 1:
        return 1, 1
    cols = 2
    while math.ceil(n / cols) > cols + 1:
        cols += 1
    return cols, math.ceil(n / cols)


def _fit_key(entries: list[str], max_w: float,
             max_h: Optional[int] = None,
             sizes: tuple[int, ...] = theme.KEY_SIZES,
             max_rows: Optional[int] = None) -> tuple[int, list[list[str]]]:
    """(font size, rows) for the key. Rows are filled column-major — entry
    k+1 sits under entry k — so a long day's key reads down each column like
    a plate key. The widest row must fit `max_w`: long BirdNET names
    (hyphenated warblers and swallows) would otherwise clip silently at the
    panel edges. With `max_h` the block's ink height must fit too, so a
    thirty-species night can't push the art off the sheet. Tries fewer
    columns at larger sizes first, then more columns, then scales below the
    size floor — the fit is a guarantee, not a preference. `max_rows` is a
    preference: a layout within it wins over any deeper one, so a long key
    widens into another column before it deepens; if nothing fits within
    it, the search runs again without it."""
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

    for row_cap in ((max_rows, None) if max_rows else (None,)):
        for size in sizes:
            for cols in range(1, len(entries) + 1):
                rows = rows_for(cols)
                if row_cap is not None and len(rows) > row_cap:
                    continue
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
                    max_h=theme.SHEET_KEY_MAX_H, sizes=theme.SHEET_KEY_SIZES,
                    max_rows=theme.SHEET_KEY_MAX_ROWS)


def _key_ink_top(key_size: int, key_rows: list[list[str]], bottom: int) -> int:
    """Where a key drawn with `_draw_key` would put its ink top, without drawing."""
    if not key_rows:
        return theme.HEIGHT - theme.MARGIN_BOTTOM
    line_h = round(key_size * theme.KEY_LINE_H)
    first_baseline = theme.HEIGHT - bottom - (len(key_rows) - 1) * line_h
    return first_baseline - round(key_size * theme.ENGRAVED_CAP)


def sheet_date_text(when: ddate) -> str:
    """The sheet's date, as the key is set: "WEDNESDAY, SEPTEMBER 2, 2026"."""
    return f"{when.strftime('%A, %B')} {when.day}, {when.year}".upper()


def _sheet_date_baseline(key_size: int, key_rows: list[list[str]], bottom: int) -> int:
    """The date line sits one open line above the key's first baseline."""
    key_ink_top = _key_ink_top(key_size, key_rows, bottom)
    first_baseline = key_ink_top + round(key_size * theme.ENGRAVED_CAP)
    return first_baseline - round(key_size * theme.SHEET_DATE_GAP)


def sheet_date_baseline(cells: list[CollageCell], note: bool = False) -> int:
    key_size, key_rows = sheet_key(cells, note)
    return _sheet_date_baseline(key_size, key_rows,
                                theme.KEY_BOTTOM + (theme.NOTE_CLEAR if note else 0))


def sheet_art_box(cells: list[CollageCell], note: bool = False) -> tuple[int, int, int, int]:
    """The art box on the generated sheet for these cells: the top margin
    down to the date line above the key, full width less the sheet margins.
    There is no header."""
    key_size, key_rows = sheet_key(cells, note)
    bottom = theme.KEY_BOTTOM + (theme.NOTE_CLEAR if note else 0)
    date_ink_top = (_sheet_date_baseline(key_size, key_rows, bottom)
                    - round(key_size * theme.ENGRAVED_CAP))
    art_bottom = date_ink_top - theme.SHEET_KEY_ART_GAP
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


def _bottom_block(field: Image.Image, draw: ImageDraw.ImageDraw, cells: list[CollageCell],
                  when: ddate, note: Optional[str] = None,
                  note_kind: Optional[str] = None) -> tuple[int, int, int, int]:
    """How BOTH collages are set below the art: the date, spaced wide in the
    engraved capitals, on its own line over a numbered key ('1. BLUE JAY') in
    prominence order, packed into columns along the bottom. A `note` (the
    gone-quiet footnote) lifts the key so the two never share the bottom
    margin. Returns the art box the block leaves — there is no header, so the
    art runs from the top margin down to it."""
    bottom = theme.KEY_BOTTOM + (theme.NOTE_CLEAR if note else 0)
    key_size, key_rows = sheet_key(cells, bool(note))
    _draw_key(draw, key_size, key_rows, bottom=bottom)
    date_baseline = _sheet_date_baseline(key_size, key_rows, bottom)
    typography.draw_engraved(draw, theme.WIDTH / 2, date_baseline, sheet_date_text(when),
                             key_size, theme.INK, theme.SHEET_DATE_TRACKING)
    if note:
        typography.note_line(field, note, kind=note_kind)
    return sheet_art_box(cells, bool(note))


def _figure_numeral(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                    number: int) -> None:
    """The grid's answer to the painted sheet's figure numerals: the same
    engraved digit, at the head of the cell, keyed to the same bottom key."""
    size = theme.COLLAGE_FIGURE_SIZE
    width = typography.engraved_width(str(number), size, theme.KEY_TRACKING)
    typography.draw_engraved(draw, box[0] + width / 2, box[1] + round(size * theme.ENGRAVED_CAP),
                             str(number), size, theme.INK, theme.KEY_TRACKING)


def render_generated_collage(art: Image.Image, cells: list[CollageCell],
                             when: Optional[ddate] = None, total_detections: int = 0,
                             note: Optional[str] = None,
                             note_kind: Optional[str] = None) -> Image.Image:
    """The generated composite sheet: the one generated artwork from the top
    margin down, over the shared date line and numbered key. No header: the
    art is the sheet, and its figure numerals are painted into it."""
    when = when or ddate.today()
    field = _new_field()
    draw = ImageDraw.Draw(field)
    # A colour sheet (colour panel) goes on its own layer under the gray type.
    layer = new_color_layer() if art.mode == "RGB" else None

    box = _bottom_block(field, draw, cells, when, note, note_kind)
    _paste_art(layer if layer is not None else field, art, box, v_align=0.5)
    return merge_color(field, layer) if layer is not None else field


def render_collage(cells: list[CollageCell], provider: ArtProvider,
                   when: Optional[ddate] = None, total_detections: int = 0,
                   note: Optional[str] = None,
                   note_kind: Optional[str] = None, color: bool = False) -> Image.Image:
    """The free grid: every plate it is handed, filling the art area the
    generated sheet's art fills, each with its figure numeral, over the same
    date line and numbered key. The two sheets are one thing set two ways,
    and the owner's species limit (the caller's cut) applies to both."""
    when = when or ddate.today()
    cols, rows = _grid(len(cells))

    field = _new_field()
    draw = ImageDraw.Draw(field)
    layer = new_color_layer() if color else None   # colour panel: see compose.merge_color

    # -- grid, inside the art box the key leaves ---------------------------
    grid_left, grid_top, grid_right, grid_bottom = _bottom_block(
        field, draw, cells, when, note, note_kind)
    # Gutters thin out as the grid fills, so a long day's plates keep their size.
    gutter_x, gutter_y = max(24, 140 // cols), max(20, 112 // cols)
    cell_w = (grid_right - grid_left - gutter_x * (cols - 1)) / cols
    cell_h = (grid_bottom - grid_top - gutter_y * (rows - 1)) / rows

    for i, cell in enumerate(cells):
        r, c = divmod(i, cols)
        x0 = grid_left + c * (cell_w + gutter_x)
        y0 = grid_top + r * (cell_h + gutter_y)
        art_box = (int(x0), int(y0), int(x0 + cell_w), int(y0 + cell_h))
        ccx = x0 + cell_w / 2

        # The numeral goes down first: the art is composited darker over it,
        # so it survives wherever the plate's paper is.
        _figure_numeral(draw, art_box, i + 1)
        art = provider.artwork(cell.common_name, cell.scientific_name)
        pair = art.color_pair() if (art is not None and color) else None
        if pair is not None:
            _paste_art(layer, pair[1], art_box, v_align=0.5)
        elif art is not None:
            _paste_art(field, art.image, art_box, v_align=0.5)
        else:
            # Typographic mini: the Latin name in the plates' engraved capitals,
            # centred in the art box.
            sci = cell.scientific_name.upper()
            sci_size = theme.SUBTITLE_SIZE
            while sci_size > 20 and typography.engraved_width(sci, sci_size) > cell_w - 40:
                sci_size -= 1
            midy = (art_box[1] + art_box[3]) / 2
            typography.draw_engraved(draw, ccx, midy + sci_size * theme.ENGRAVED_CAP / 2,
                                     sci, sci_size, theme.INK_SOFT)

    return merge_color(field, layer) if color else field
