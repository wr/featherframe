"""Full-bleed artwork (W-707): the crop keeps faint contiguous ink and is
symmetric about the plate centre; inked-edge plates cover-fit to the mat
opening, paper-bordered plates contain-fit centred; the date and № marks
sit on one footer line instead of the top corners."""
from __future__ import annotations

from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw

from featherframe.render import compose, plate, theme, typography
from featherframe.render.compose import SingleSpec
from featherframe.render.provider import ArtProvider, Artwork


# -- crop ---------------------------------------------------------------------
def _sheet(w=800, h=1000):
    return Image.new("L", (w, h), 250)


def test_content_box_extends_through_faint_contiguous_ink():
    # A heavy blob with a faint "straw" tail hanging straight below it.
    im = _sheet()
    d = ImageDraw.Draw(im)
    d.rectangle([200, 250, 600, 650], fill=20)
    for y in range(650, 900, 10):                     # faint, sparse tail
        d.line([(380, y), (420, y + 6)], fill=228, width=3)
    l, t, r, b = plate.content_box(im, pad=0.0)
    assert b >= 890, f"faint tail cut at {b}"


def test_content_box_excludes_marginalia_across_a_paper_gap():
    im = _sheet()
    d = ImageDraw.Draw(im)
    d.rectangle([200, 300, 600, 700], fill=20)
    d.rectangle([100, 40, 300, 60], fill=60)          # "No. 32 / PLATE" line, far above
    l, t, r, b = plate.content_box(im, pad=0.0)
    assert t >= 250, f"marginalia pulled the crop up to {t}"


def test_content_box_is_symmetric_about_the_plate_centre():
    # Content sits left of centre and below it: the crop mirrors it.
    im = _sheet()
    d = ImageDraw.Draw(im)
    d.rectangle([50, 400, 400, 850], fill=20)
    l, t, r, b = plate.content_box(im, pad=0.0)
    cx, cy = im.width / 2, im.height / 2
    assert abs((cx - l) - (r - cx)) <= 2
    assert abs((cy - t) - (b - cy)) <= 2
    assert l <= 50 and b >= 850


def test_trim_paper_strips_the_blank_border():
    im = Image.new("L", (400, 300), 255)
    ImageDraw.Draw(im).rectangle([30, 20, 369, 279], fill=40)
    out = plate.trim_paper(im)
    assert out.size == (340, 260)


# -- fit ----------------------------------------------------------------------
class _Art(ArtProvider):
    def __init__(self, img, composite=False):
        self._img, self._composite = img, composite

    def artwork(self, common_name, scientific_name):
        return Artwork(image=self._img, audubon_plate=None, composite=self._composite)


def _spec(**kw):
    base = dict(common_name="Snowy Owl", scientific_name="Bubo scandiacus",
                when=datetime(2026, 9, 4, 8, 14), plate_number=41)
    base.update(kw)
    return SingleSpec(**base)


def _ink_bbox(img, box=None):
    arr = np.asarray(img if box is None else img.crop(box))
    ys, xs = np.where(arr < 128)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _art_box():
    return (0, 0, theme.WIDTH, theme.HEIGHT - theme.CAPTION_BLOCK_H - theme.CAPTION_GAP)


def test_inked_edge_art_bleeds_to_both_sides_and_the_top():
    # A dark plate a little taller than the art box's aspect (the Snowy Owl
    # is 0.72 to the box's 0.90): cover-fit must reach the left, right and
    # top edges of the composition.
    dark = Image.new("L", (720, 1000), 30)
    out = compose.render_single(_spec(), _Art(dark))
    l, t, r, b = _ink_bbox(out, _art_box())
    assert l == 0 and r == theme.WIDTH and t == 0


def test_paper_bordered_art_is_contained_and_centred():
    art = Image.new("L", (600, 1000), 255)
    ImageDraw.Draw(art).rectangle([100, 100, 499, 899], fill=30)   # bird on paper
    out = compose.render_single(_spec(), _Art(art))
    bl, bt, br, bb = _art_box()
    l, t, r, b = _ink_bbox(out, _art_box())
    assert abs(l - (br - r)) <= 2, "not centred horizontally"
    assert abs((t - bt) - (bb - b)) <= 2, "not centred vertically"
    assert l > 0 and r < br, "paper-bordered art must not be cropped to the edge"


def test_landscape_inked_art_is_contained_not_cover_cropped():
    # A dark landscape plate (a wader on a shoreline) would lose half its
    # width to a cover-fit in the portrait box: it must be shown whole.
    dark = Image.new("L", (1000, 600), 30)
    out = compose.render_single(_spec(), _Art(dark))
    bl, bt, br, bb = _art_box()
    l, t, r, b = _ink_bbox(out, _art_box())
    assert (r - l) == (br - bl)                       # width-limited ...
    assert (b - t) < (bb - bt) and t > bt             # ... whole plate visible, centred


def test_composite_art_is_never_cover_cropped():
    dark = Image.new("L", (600, 1000), 30)
    out = compose.render_single(_spec(), _Art(dark, composite=True))
    bl, bt, br, bb = _art_box()
    l, t, r, b = _ink_bbox(out, _art_box())
    # Contain: the whole plate is visible, so it is height-limited here and
    # cannot span the full width.
    assert (b - t) == (bb - bt) and (r - l) < (br - bl)


# -- marks --------------------------------------------------------------------
def _blank():
    return _Art(Image.new("L", (600, 400), 255))


def test_marks_sit_on_the_footer_line_not_the_top_corners():
    out = compose.render_single(_spec(), _blank())
    top_left = (0, 0, 700, 140)
    assert _ink_bbox(out, top_left) is None
    footer_l = (0, theme.MARKS_BASELINE - 50, theme.WIDTH // 2, theme.MARKS_BASELINE + 4)
    footer_r = (theme.WIDTH // 2, theme.MARKS_BASELINE - 50, theme.WIDTH, theme.MARKS_BASELINE + 4)
    assert _ink_bbox(out, footer_l) is not None
    assert _ink_bbox(out, footer_r) is not None


def test_footer_marks_and_note_never_overlap():
    note = "Nothing heard since 11:27 pm on a long, long, long quiet evening"
    out = compose.render_single(_spec(note=note), _blank())
    scratch = Image.new("L", out.size, 255)
    typography.date_mark(ImageDraw.Draw(scratch), _spec().when)
    typography.plate_number_mark(ImageDraw.Draw(scratch), 41)
    marks = np.asarray(scratch) < 128
    only_note = Image.new("L", out.size, 255)
    typography.note_line(ImageDraw.Draw(only_note), note, max_w=compose.note_width())
    note_px = np.asarray(only_note) < 128
    assert note_px.any()
    assert not (marks & note_px).any()
    assert np.asarray(out)[theme.MARKS_BASELINE - 40:theme.MARKS_BASELINE].min() < 128


def test_fallback_plate_number_moves_to_the_footer_too():
    out = compose.render_fallback(_spec(first_seen="2026-05-17"))
    assert _ink_bbox(out, (theme.WIDTH - 500, 0, theme.WIDTH, 140)) is None
    footer_r = (theme.WIDTH // 2, theme.MARKS_BASELINE - 50, theme.WIDTH, theme.MARKS_BASELINE + 4)
    assert _ink_bbox(out, footer_r) is not None
