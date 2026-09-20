"""W-812: on some scans the printed "No. 20." / "PLATE XCVII." line sits below
the fixed margin trim, level with the top of the art (the Screech-Owl's pine
needles start beside it). The trim now also lifts small, isolated, line-shaped
lettering out of the top corners — and leaves anything that is part of the
picture alone."""
from __future__ import annotations

from PIL import Image, ImageDraw

from featherframe.render import plate

W, H = 1800, 2200
PAPER = 236


def _sheet(mode="L"):
    img = Image.new(mode, (W, H), PAPER if mode == "L" else (PAPER, PAPER - 4, PAPER - 14))
    return img, ImageDraw.Draw(img)


def _lettering(d, x, y, n=9, ink=40):
    """A row of small letter-sized marks: w 9, h 16, spaced like caps."""
    for i in range(n):
        d.rectangle((x + i * 13, y, x + i * 13 + 8, y + 16), fill=ink)


def _top_ink(img, x0, x1, rows=120):
    px = img.convert("L").load()
    return sum(1 for y in range(rows) for x in range(x0, x1, 2) if px[x, y] < 150)


def test_corner_lettering_below_the_trim_line_is_lifted():
    img, d = _sheet()
    d.ellipse((500, 400, 1300, 1700), fill=70)               # the subject
    _lettering(d, 1560, 172)                                   # "PLATE XCVII." right, below the 6.8 % trim
    _lettering(d, 60, 142, n=4)                                # "No. 20." left, straddling the trim line
    out = plate._trim_marginalia(img)
    assert _top_ink(out, int(W * 0.7), out.width) == 0
    assert _top_ink(out, 0, int(W * 0.25)) == 0
    assert out.size == plate._trim_marginalia(Image.new("L", (W, H), PAPER)).size   # geometry untouched


def test_art_that_reaches_the_corner_is_left_alone():
    img, d = _sheet()
    # Foliage sweeping into the top-right corner, right beside the lettering.
    for k in range(12):
        d.line((900 + k * 40, 900, 1380 + k * 12, 160 + k * 6), fill=60, width=5)
    _lettering(d, 1580, 172)
    out = plate._trim_marginalia(img)
    assert _top_ink(out, int(W * 0.5), 1500) > 50               # the needles are still there
    assert _top_ink(out, 1530, out.width) == 0                  # the lettering is not


def test_a_small_mark_that_is_not_a_line_of_type_survives():
    """A distant bird in the sky is small and isolated too, but it is not
    shaped like a line of lettering."""
    img, d = _sheet()
    d.ellipse((500, 400, 1300, 1700), fill=70)
    d.polygon([(1600, 200), (1630, 170), (1645, 205), (1660, 172), (1690, 204)], fill=50)  # a far gull, ~90x35
    out = plate._trim_marginalia(img)
    assert _top_ink(out, 1500, out.width) > 20


def test_the_colour_twin_is_cleaned_in_the_same_place():
    img, d = _sheet("RGB")
    d.ellipse((500, 400, 1300, 1700), fill=(90, 60, 40))
    for i in range(9):
        d.rectangle((1560 + i * 13, 172, 1568 + i * 13, 188), fill=(45, 40, 38))
    out = plate._trim_marginalia(img)
    assert out.mode == "RGB" and _top_ink(out, int(W * 0.7), out.width) == 0
    assert out.getpixel((out.width - 200, 40)) == (PAPER, PAPER - 4, PAPER - 14)      # filled with the sheet's own paper
