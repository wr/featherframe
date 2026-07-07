"""Single-detection composition: one bird, museum-plate styling.

Field + fitted bird art (seamlessly darken-composited so the plate's paper melts
into our field) + caption block + optional 'No. NN' corner mark. When the
provider has no art, we render a typographic fallback plate instead — never a
wrong bird.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PIL import Image, ImageChops, ImageDraw

from . import theme, typography
from .provider import ArtProvider


@dataclass
class SingleSpec:
    common_name: str
    scientific_name: str
    when: Optional[datetime] = None
    plate_number: Optional[int] = None   # all-time species ordinal -> "No. NN"
    first_seen: Optional[str] = None      # 'YYYY-MM-DD', for the fallback plate


def _new_field() -> Image.Image:
    return Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)


def _fit(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    scale = min(box_w / img.width, box_h / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    return img.resize((w, h), Image.LANCZOS)


def _place_art(field: Image.Image, art: Image.Image, box: tuple[int, int, int, int],
               v_align: float = 0.48) -> None:
    """Fit `art` into `box` and darken-composite it onto `field` so near-white
    paper blends into the field and only the ink shows (no paste seam)."""
    bl, bt, br, bb = box
    fitted = _fit(art, br - bl, bb - bt)
    x = bl + (br - bl - fitted.width) // 2
    y = bt + int((bb - bt - fitted.height) * v_align)
    # Build a white layer the size of the field, drop the art in, then darken.
    layer = Image.new("L", field.size, 255)
    layer.paste(fitted, (x, y))
    field.paste(ImageChops.darker(field.crop((0, 0, *field.size)), layer), (0, 0))


def render_single(spec: SingleSpec, provider: ArtProvider,
                  show_plate_number: bool = True) -> Image.Image:
    art = provider.artwork(spec.common_name, spec.scientific_name)
    if art is None:
        return render_fallback(spec, show_plate_number=show_plate_number)

    field = _new_field()
    draw = ImageDraw.Draw(field)

    caption_top = theme.HEIGHT - theme.MARGIN_BOTTOM - theme.CAPTION_BLOCK_H
    art_box = (theme.MARGIN_X, theme.MARGIN_TOP,
               theme.WIDTH - theme.MARGIN_X, caption_top - theme.CAPTION_GAP)
    # Composites read better filling the frame; single subjects sit slightly high.
    _place_art(field, art.image, art_box, v_align=0.5 if art.composite else 0.44)

    typography.caption_block(draw, theme.WIDTH / 2, caption_top,
                             spec.common_name, spec.scientific_name, spec.when)

    if show_plate_number and spec.plate_number:
        typography.plate_number_mark(draw, spec.plate_number)
    return field


def render_fallback(spec: SingleSpec, show_plate_number: bool = True) -> Image.Image:
    """Typographic plate for a species we have no illustration for.

    Just the name, set large and well, with 'First recorded <date>' beneath.
    Honest and quiet — the museum's way of saying 'no plate for this one'.
    """
    field = _new_field()
    draw = ImageDraw.Draw(field)
    cx = theme.WIDTH / 2

    # A pair of thin rules top and bottom of the type gives it a plate-like frame.
    block_top = theme.HEIGHT * 0.34
    typography.draw_smallcaps(draw, cx, block_top, spec.common_name,
                              typography.FONTS, 104, theme.INK, theme.NAME_TRACKING)

    sci_font = typography.FONTS.get(58, italic=True, weight=460)
    sci_baseline = block_top + 92
    draw.text((cx, sci_baseline), spec.scientific_name, font=sci_font,
              fill=theme.INK, anchor="ms")

    # hairline
    rule_y = sci_baseline + 74
    half = theme.RULE_WIDTH / 2
    draw.rectangle([cx - half, rule_y, cx + half, rule_y + theme.RULE_THICKNESS - 1],
                   fill=theme.RULE)

    when = spec.first_seen or (spec.when.strftime("%Y-%m-%d") if spec.when else None)
    if when:
        try:
            d = datetime.strptime(when, "%Y-%m-%d")
            pretty = f"First recorded {d.day} {d.strftime('%B')} {d.year}"
        except ValueError:
            pretty = f"First recorded {when}"
        typography.draw_smallcaps(draw, cx, rule_y + 54, pretty, typography.FONTS,
                                  theme.META_SIZE, theme.INK_SOFT, theme.META_TRACKING,
                                  weight_caps=500, weight_small=520)

    if show_plate_number and spec.plate_number:
        typography.plate_number_mark(draw, spec.plate_number)
    return field
