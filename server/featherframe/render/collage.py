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


def render_collage(cells: list[CollageCell], provider: ArtProvider,
                   when: Optional[ddate] = None, total_detections: int = 0,
                   title: str = "A Day in the Garden") -> Image.Image:
    when = when or ddate.today()
    cells = cells[:6]
    cols, rows = _grid(len(cells))

    field = _new_field()
    draw = ImageDraw.Draw(field)
    cx = theme.WIDTH / 2

    # -- title band --------------------------------------------------------
    title_font_baseline = theme.MARGIN_TOP + 44
    typography.draw_smallcaps(draw, cx, title_font_baseline, title,
                              typography.FONTS, 58, theme.INK, 0.12)
    # date + totals subtitle
    d = when
    date_str = f"{d.day} {d.strftime('%B')} {d.year}"
    n_species = len([c for c in cells])
    sub = f"{date_str}   ·   {n_species} species"
    if total_detections:
        sub += f"   ·   {total_detections} detections"
    typography.draw_smallcaps(draw, cx, title_font_baseline + 52, sub,
                              typography.FONTS, theme.META_SIZE, theme.INK_SOFT,
                              theme.META_TRACKING, weight_caps=500, weight_small=520)
    rule_y = title_font_baseline + 92
    half = theme.RULE_WIDTH / 2
    draw.rectangle([cx - half, rule_y, cx + half, rule_y + theme.RULE_THICKNESS - 1],
                   fill=theme.RULE)

    # -- grid --------------------------------------------------------------
    grid_top = rule_y + 60
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

    return field
