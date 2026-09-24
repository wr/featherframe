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
