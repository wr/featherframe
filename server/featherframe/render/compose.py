"""Single-detection composition: one bird, museum-plate styling.

Field + full-bleed bird art (seamlessly darken-composited so the plate's paper
melts into our field) + the script caption (title, Latin name, the plate's own
legend lines) + the date and 'No. NN' marks in the bottom corners. When the
provider has no art, we render a typographic fallback plate instead — never a
wrong bird.

The art box runs to the panel's top and side edges (the mat inset in the
pipeline then scales the whole composition, so "full bleed" means to the mat
opening). A plate whose picture reaches its own edges (Snowy Owl) has its paper
border trimmed and is cover-fitted — it may lose some of its bottom, never its
top; a bird on bare paper is contain-fitted and centred, so Audubon's own
placement (kept by the symmetric crop in plate.py) carries through.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PIL import Image, ImageChops

from . import plate, theme, typography
from .provider import ArtProvider

# A plate whose outer border is this inked (fraction) is full-bleed art, not
# a bird on paper, and gets cover-fitted to the mat opening — but only if
# the fit crops at most COVER_MAX_LOSS of it on either axis. Audubon's
# landscape plates (a wader on a shoreline) would lose half their width to
# a cover-fit in the portrait box, so they are shown whole instead.
COVER_EDGE_INK = 0.25
COVER_MAX_LOSS = 0.25


@dataclass
class SingleSpec:
    common_name: str
    scientific_name: str
    when: Optional[datetime] = None
    plate_number: Optional[int] = None   # all-time species ordinal -> "No. NN"
    first_seen: Optional[str] = None      # 'YYYY-MM-DD', for the fallback plate
    # A species never heard before today. The service sets it from the
    # novelty class (not derived from first_seen here, because a source that
    # can't give a first-seen date still knows the class); the real plate
    # then carries "first recorded today" under the scientific name.
    first_ever: bool = False
    # One italic footnote in the bottom margin ("Nothing heard since 11:27 pm").
    # Set only by the gone-quiet alarm; None draws nothing.
    note: Optional[str] = None


def _new_field() -> Image.Image:
    return Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)


def _fit(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    scale = min(box_w / img.width, box_h / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    return img.resize((w, h), Image.LANCZOS)


def _composite(field: Image.Image, fitted: Image.Image, x: int, y: int) -> None:
    """Darken-composite `fitted` onto `field` so near-white paper blends into
    the field and only the ink shows (no paste seam)."""
    layer = Image.new("L", field.size, 255)
    layer.paste(fitted, (x, y))
    field.paste(ImageChops.darker(field, layer), (0, 0))


def _place_art(field: Image.Image, art: Image.Image, box: tuple[int, int, int, int],
               v_align: float = 0.5) -> None:
    """Contain-fit `art` into `box` (whole plate visible) and composite it."""
    bl, bt, br, bb = box
    fitted = _fit(art, br - bl, bb - bt)
    x = bl + (br - bl - fitted.width) // 2
    y = bt + int((bb - bt - fitted.height) * v_align)
    _composite(field, fitted, x, y)


def _place_cover(field: Image.Image, art: Image.Image, box: tuple[int, int, int, int],
                 v_bias: float = 0.0) -> None:
    """Cover-fit `art` to `box`: the box is filled and the overflow cropped —
    centred horizontally, and vertically by `v_bias` (0 keeps the top)."""
    bl, bt, br, bb = box
    bw, bh = br - bl, bb - bt
    scale = max(bw / art.width, bh / art.height)
    w, h = max(bw, round(art.width * scale)), max(bh, round(art.height * scale))
    fitted = art.resize((w, h), Image.LANCZOS)
    ox, oy = (w - bw) // 2, int((h - bh) * v_bias)
    _composite(field, fitted.crop((ox, oy, ox + bw, oy + bh)), bl, bt)


def _cover_loss(art: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Largest fraction of `art` (on either axis) a cover-fit to `box` crops."""
    bl, bt, br, bb = box
    bw, bh = br - bl, bb - bt
    scale = max(bw / art.width, bh / art.height)
    w, h = art.width * scale, art.height * scale
    return max((w - bw) / w, (h - bh) / h, 0.0)


def note_width() -> float:
    """Room for the footnote between the widest possible date and No. marks."""
    reserve = max(typography.date_mark_max_width(), typography.plate_number_max_width())
    return theme.WIDTH - 2 * (theme.CORNER_INSET + reserve + theme.NOTE_MARK_GAP)


def caption_height(n_lines: int, first_ever: bool = False) -> int:
    """Height reserved at the bottom for the caption: title ink top to the
    panel bottom, for `n_lines` legend lines (plus the first-recorded line)."""
    lines = n_lines + (1 if first_ever else 0)
    # With no legend the Latin name still keeps LATIN_BOTTOM_CLEAR of air
    # above the corner marks, so it never sits on them.
    below_latin = (theme.LATIN_TO_LEGEND + theme.LEGEND_PITCH * (lines - 1) if lines
                   else theme.LATIN_BOTTOM_CLEAR)
    return (round(theme.SCRIPT_TITLE_SIZE * theme.SCRIPT_TITLE_ASCENT) + theme.TITLE_TO_LATIN
            + below_latin + theme.CAPTION_BOTTOM)


FIRST_EVER_LINE = "First recorded today."


def render_single(spec: SingleSpec, provider: ArtProvider,
                  show_plate_number: bool = True) -> Image.Image:
    art = provider.artwork(spec.common_name, spec.scientific_name)
    if art is None:
        return render_fallback(spec, show_plate_number=show_plate_number)

    field = _new_field()

    lines = list(art.legend)
    if spec.first_ever:
        lines.append(FIRST_EVER_LINE)
    caption_top = theme.HEIGHT - caption_height(len(art.legend), spec.first_ever)
    art_box = (0, 0, theme.WIDTH, caption_top - theme.CAPTION_GAP)
    img = art.image
    # A composite is always shown whole (never a wrong bird); anything else
    # whose picture runs to its own edges fills the opening, unless that
    # would crop too much of it.
    cover = False
    if not art.composite and plate.edge_ink_fraction(img) > COVER_EDGE_INK:
        trimmed = plate.trim_paper(img)
        if _cover_loss(trimmed, art_box) <= COVER_MAX_LOSS:
            img, cover = trimmed, True
    if cover:
        _place_cover(field, img, art_box, v_bias=0.5 if img.width > img.height else 0.0)
    else:
        _place_art(field, img, art_box)

    typography.caption(field, caption_top, spec.common_name, spec.scientific_name, lines)
    if spec.when:
        typography.date_mark(field, spec.when)
    if show_plate_number and spec.plate_number:
        typography.plate_number_mark(field, spec.plate_number)
    if spec.note:
        typography.note_line(field, spec.note, max_w=note_width())
    return field


def render_fallback(spec: SingleSpec, show_plate_number: bool = True) -> Image.Image:
    """Typographic plate for a species we have no illustration for.

    Just the name, set in the caption's own voice, with 'First recorded
    <date>' beneath. Honest and quiet — the museum's way of saying 'no plate
    for this one'.
    """
    field = _new_field()
    when = spec.first_seen or (spec.when.strftime("%Y-%m-%d") if spec.when else None)
    lines: list[str] = []
    if when:
        try:
            d = datetime.strptime(when, "%Y-%m-%d")
            lines.append(f"First recorded {d.day} {d.strftime('%B')} {d.year}.")
        except ValueError:
            lines.append(f"First recorded {when}.")
    typography.caption(field, theme.HEIGHT * 0.34, spec.common_name, spec.scientific_name, lines)
    if show_plate_number and spec.plate_number:
        typography.plate_number_mark(field, spec.plate_number)
    if spec.note:
        typography.note_line(field, spec.note, max_w=note_width())
    return field
