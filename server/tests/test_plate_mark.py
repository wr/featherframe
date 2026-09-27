"""W-821: the corner mark is the Havell plate number, in roman as engraved."""
from __future__ import annotations

from datetime import datetime

import pytest
from PIL import Image

from featherframe.render import compose, theme, typography
from featherframe.render.compose import SingleSpec
from featherframe.render.provider import ArtProvider, Artwork


@pytest.mark.parametrize("n,numeral", [
    (1, "I"), (4, "IV"), (9, "IX"), (40, "XL"), (90, "XC"), (159, "CLIX"),
    (388, "CCCLXXXVIII"), (400, "CCCC"), (435, "CCCCXXXV"),
])
def test_roman_as_havell_engraved_it(n, numeral):
    assert typography.roman(n) == numeral


class _Art(ArtProvider):
    def __init__(self, **kw):
        self.kw = kw

    def artwork(self, common, scientific):
        return Artwork(image=Image.new("L", (600, 400), 255), composite=False, **self.kw)


def _right_corner_ink(img) -> int:
    import numpy as np
    y0, y1 = theme.MARKS_BASELINE - 40, theme.MARKS_BASELINE + 8
    return int((np.asarray(img)[y0:y1, theme.WIDTH // 2:] < 128).sum())


SPEC = SingleSpec(common_name="Northern Cardinal", scientific_name="Cardinalis cardinalis",
                  when=datetime(2026, 9, 20, 8, 14))


def test_a_havell_plate_carries_its_number():
    numbered = compose.render_single(SPEC, _Art(plate=159))
    other = compose.render_single(SPEC, _Art(plate=388))
    assert _right_corner_ink(numbered) > 0
    assert _right_corner_ink(other) > _right_corner_ink(numbered)     # a longer numeral


def test_a_generated_plate_carries_the_star_alone_and_a_bough_nothing():
    star = _right_corner_ink(compose.render_single(SPEC, _Art(plate=None, generated=True)))
    assert 0 < star < _right_corner_ink(compose.render_single(SPEC, _Art(plate=1)))
    assert _right_corner_ink(compose.render_single(SPEC, _Art(plate=None))) == 0


def test_the_footnote_clears_the_widest_numeral():
    widest = max(range(1, theme.MAX_PLATE + 1), key=typography.plate_mark_width)
    assert typography.plate_mark_max_width() == typography.plate_mark_width(widest)
    assert compose.note_width() <= theme.WIDTH - 2 * (
        theme.CORNER_INSET + typography.plate_mark_max_width() + theme.NOTE_MARK_GAP)
    assert compose.note_width() > 500            # still room for "No detections since…"



# A folio numbered per volume (W-874) is cited as its own lists cite it:
# volume, then number.
@pytest.mark.parametrize("plate,volume_no,numeral", [
    (18, 2, "II. 18"), (18, "II", "II. 18"), (18, "2", "II. 18"),
    (76, "Supp.", "Supp. 76"), (1, 7, "VII. 1"),
])
def test_a_plate_numbered_per_volume_is_cited_by_volume(plate, volume_no, numeral):
    assert typography._plate_mark_parts(plate, volume_no) == (f"{theme.PLATE_PREFIX} ", numeral)


def test_a_volume_mark_is_drawn_and_fits_the_footnote_as_it_was():
    assert _right_corner_ink(compose.render_single(SPEC, _Art(plate=18, volume_no=2))) > 0
    widest = max(typography.plate_mark_width(n, v) for v in theme.VOLUMES
                 for n in range(1, theme.MAX_VOLUME_PLATE + 1))
    assert widest <= typography.plate_mark_max_width()
    # Narrower than Havell's widest, so every Havell footnote, and so every
    # Havell render, is what it was.
    running = max(typography.plate_mark_width(n) for n in range(1, theme.MAX_PLATE + 1))
    assert typography.plate_mark_max_width() == running
