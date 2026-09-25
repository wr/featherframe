"""Single-detection composition: one bird, museum-plate styling.

Field + full-bleed bird art (seamlessly darken-composited so the plate's paper
melts into our field) + the script caption (title, Latin name, the plate's own
legend lines) + the date and 'Plate CLIX' marks in the bottom corners. When the
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

from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageChops, ImageDraw

from .. import paths
from . import plate, theme, typography
from .provider import ArtProvider, Artwork

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
    first_seen: Optional[str] = None      # 'YYYY-MM-DD', for the fallback plate
    # A species never heard before today. The service sets it from the
    # novelty class (not derived from first_seen here, because a source that
    # can't give a first-seen date still knows the class); the real plate
    # then carries "first recorded today" under the scientific name.
    first_ever: bool = False
    # One footnote in the bottom margin ("Nothing heard since 11:27 pm"), in
    # the system voice. Set only by an alarm; None draws nothing. `note_kind`
    # picks the pill: "outage" is a fault (outlined, slashed), else information.
    note: Optional[str] = None
    note_kind: Optional[str] = None
    # The footnote for the empty bough only, when image generation would
    # have drawn this species but the owner must fix it first (an empty
    # account, a refused key). An illustration never carries it, and an
    # alarm's `note` comes first.
    fallback_note: Optional[str] = None


def _new_field() -> Image.Image:
    return Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)


def _fit(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    scale = min(box_w / img.width, box_h / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    return img.resize((w, h), Image.LANCZOS)


def _composite(field: Image.Image, fitted: Image.Image, x: int, y: int) -> None:
    """Darken-composite `fitted` onto `field` so near-white paper blends into
    the field and only the ink shows (no paste seam)."""
    layer = Image.new(field.mode, field.size, 255 if field.mode == "L" else (255, 255, 255))
    layer.paste(fitted, (x, y))
    field.paste(ImageChops.darker(field, layer), (0, 0))


def new_color_layer() -> Image.Image:
    """The colour panel's art layer: white, the size of the field. Colour art
    is placed here instead of on the gray field, and `merge_color` lays the
    field's type over it, so the type and layout code stays gray."""
    return Image.new("RGB", (theme.WIDTH, theme.HEIGHT), (255, 255, 255))


def merge_color(field: Image.Image, layer: Image.Image) -> Image.Image:
    return ImageChops.darker(field.convert("RGB"), layer)


def _place_art(field: Image.Image, art: Image.Image, box: tuple[int, int, int, int],
               v_align: float = 0.5) -> None:
    """Contain-fit `art` into `box` (whole plate visible) and composite it.
    `field` is the gray field, or the colour layer when `art` is colour."""
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
    """Room for the footnote between the widest possible date and plate marks."""
    reserve = max(typography.date_mark_max_width(), typography.plate_mark_max_width())
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
                  color: bool = False) -> Image.Image:
    art = provider.artwork(spec.common_name, spec.scientific_name)
    if art is None:
        return render_fallback(spec, color=color)
    return _render_art(spec, art, color)


def _render_art(spec: SingleSpec, art: Artwork, color: bool = False) -> Image.Image:
    """The plate layout proper: art in the box above the caption, the
    caption, the corner marks, the footnote. Shared by a real or generated
    plate and by the fallback's empty bough, so the type never moves.

    With `color` (a colour panel) the result is 'RGB': every layout decision
    is still made on the gray art, and its colour twin is what gets placed."""
    field = _new_field()
    layer = new_color_layer() if color else None
    pair = art.color_pair() if color else None

    lines = list(art.legend)
    # A species never heard before today: a rule around the sheet (W-744),
    # or the old script line when the theme asks for it.
    first_line = spec.first_ever and theme.FIRST_EVER_MARK == "line"
    if first_line:
        lines.append(FIRST_EVER_LINE)
    caption_top = theme.HEIGHT - caption_height(len(art.legend), first_line)
    if spec.note and lines:
        # The footnote pill sits on the footer baseline, which is also the
        # last legend line's: lift the caption clear of it, as a collage
        # lifts its key. Without a legend the Latin name already clears it.
        caption_top -= theme.NOTE_CLEAR
    art_box = (0, 0, theme.WIDTH, caption_top - theme.CAPTION_GAP)
    img, twin = pair if pair else (art.image, None)
    # A composite is always shown whole (never a wrong bird); anything else
    # whose picture runs to its own edges fills the opening, unless that
    # would crop too much of it.
    cover = False
    if not art.composite and plate.edge_ink_fraction(img) > COVER_EDGE_INK:
        tbox = plate.trim_box(img)
        if _cover_loss(img.crop(tbox), art_box) <= COVER_MAX_LOSS:
            img, cover = img.crop(tbox), True
            twin = twin.crop(tbox) if twin is not None else None
    # The colour twin lands on the colour layer; gray art (any art on a gray
    # panel) on the field.
    target, placed = (layer, twin) if twin is not None else (field, img)
    if cover:
        _place_cover(target, placed, art_box, v_bias=0.5 if img.width > img.height else 0.0)
    else:
        _place_art(target, placed, art_box)

    typography.caption(field, caption_top, spec.common_name, spec.scientific_name, lines)
    if spec.when:
        typography.date_mark(field, spec.when)
    # The right corner says where the sheet came from: Havell's own plate
    # number on a scan, a ✦ on a synthetic sheet, which never passes as one
    # (W-733). The bough of a species with no plate at all carries neither.
    if art.plate:
        typography.plate_mark(field, art.plate, art.volume_no)
    elif art.generated:
        typography.generated_mark(field, theme.WIDTH - theme.CORNER_INSET)
    if spec.first_ever and not first_line:
        typography.first_ever_rule(field)
    if spec.note:
        typography.note_line(field, spec.note, max_w=note_width(), kind=spec.note_kind)
    return merge_color(field, layer) if color else field


@lru_cache(maxsize=1)
def bough() -> Image.Image:
    """The empty hawthorn bough from the boot screens' setting (W-743): a
    plate's own art box with no bird on it."""
    return Image.open(paths.art_dir() / "bough.png").convert("L")


@lru_cache(maxsize=1)
def _bough_pair() -> tuple:
    """The bough for a colour panel: (gray, colour), the same cut of the same
    draw (boot_art.py writes both)."""
    return bough(), Image.open(paths.art_dir() / "bough_color.png").convert("RGB")


def render_fallback(spec: SingleSpec, color: bool = False) -> Image.Image:
    """The plate for a species we have no illustration for: the empty bough
    where the bird would be, the name in the caption's own voice, 'First
    recorded <date>' as its legend line. The same layout as a real plate,
    so the type sits where it always does — the museum's way of saying
    'no plate for this one' without moving the furniture.
    """
    when = spec.first_seen or (spec.when.strftime("%Y-%m-%d") if spec.when else None)
    lines: list[str] = []
    if when:
        try:
            d = datetime.strptime(when, "%Y-%m-%d")
            lines.append(f"First recorded {d.day} {d.strftime('%B')} {d.year}.")
        except ValueError:
            lines.append(f"First recorded {when}.")
    # composite=True: shown whole, never cover-cropped, though the limb runs
    # off the sheet's edge exactly as a plate's stems do.
    art = Artwork(image=bough(), composite=True, legend=lines,
                  color_loader=_bough_pair)
    if spec.fallback_note and not spec.note:
        spec = replace(spec, note=spec.fallback_note, note_kind="imagegen")
    return _render_art(spec, art, color)
