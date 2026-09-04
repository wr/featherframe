"""The script caption (W-708): monoline script title, engraved Latin name with
a period, Audubon's legend lines in the small script, date · time and "No. NN"
tucked into the bottom corners in the same script."""
from __future__ import annotations

import shutil
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw

from featherframe import paths
from featherframe.render import compose, theme, typography
from featherframe.render.compose import SingleSpec
from featherframe.render.provider import ArtProvider, Artwork


# -- font chain ----------------------------------------------------------------
def test_script_font_is_the_bundled_pinyon_script():
    typography.script_font.cache_clear()
    assert typography.script_font(30).path == str(paths.fonts_dir() / "PinyonScript-Regular.ttf")
    assert typography.has_script_font()


def test_script_font_falls_back_to_garamond_italic_without_the_bundled_file(tmp_path, monkeypatch):
    monkeypatch.setattr(typography, "_SCRIPT", tmp_path / "missing.ttf")
    typography.script_font.cache_clear()
    assert "EBGaramond-Italic" in typography.script_font(30).path
    assert not typography.has_script_font()
    typography.script_font.cache_clear()


def test_script_font_falls_back_to_garamond_italic_when_the_file_will_not_load(tmp_path, monkeypatch):
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    monkeypatch.setattr(typography, "_SCRIPT", bad)
    typography.script_font.cache_clear()
    assert "EBGaramond-Italic" in typography.script_font(30).path
    typography.script_font.cache_clear()


def test_colon_kern_tightens_the_clock(monkeypatch):
    monkeypatch.setattr(theme, "COLON_KERN", -0.12)
    assert typography.script_width("8:14", 36) < typography.script_font(36).getlength("8:14")
    assert typography.script_width("814", 36) == typography.script_font(36).getlength("814")


def _ink(img, box=None):
    arr = np.asarray(img if box is None else img.crop(box))
    return int((arr < 128).sum())


def test_draw_script_centres_and_the_stroke_adds_weight():
    a = Image.new("L", (800, 200), 255)
    typography.draw_script(a, 400, 120, "Male, 1. Female, 2.", 30, theme.INK, stroke=0)
    b = Image.new("L", (800, 200), 255)
    typography.draw_script(b, 400, 120, "Male, 1. Female, 2.", 30, theme.INK, stroke=0.75)
    ys, xs = np.where(np.asarray(a) < 128)
    assert abs((xs.min() + xs.max()) / 2 - 400) < 6
    assert _ink(b) > _ink(a) * 1.3


# -- layout --------------------------------------------------------------------
class _Art(ArtProvider):
    def __init__(self, img, legend=(), composite=False):
        self._img, self._legend, self._composite = img, list(legend), composite

    def artwork(self, common_name, scientific_name):
        return Artwork(image=self._img, audubon_plate=None, composite=self._composite,
                       legend=list(self._legend))


def _spec(**kw):
    base = dict(common_name="American Robin", scientific_name="Turdus migratorius",
                when=datetime(2026, 9, 4, 11, 34), plate_number=43)
    base.update(kw)
    return SingleSpec(**base)


def _blank(legend=()):
    return _Art(Image.new("L", (600, 400), 255), legend)


def _dark(legend=()):
    # Square so it cover-fits (fills) the art box whatever the caption height:
    # the art's bottom edge is then exactly the box bottom.
    return _Art(Image.new("L", (900, 900), 30), legend)


LEGEND = ["Male, 1. Female, 2. Young, 3.", "Chestnut Oak. Quercus prinus."]


def test_caption_height_follows_the_legend_line_count():
    assert compose.caption_height(2) - compose.caption_height(1) == theme.LEGEND_PITCH
    assert (compose.caption_height(1) - compose.caption_height(0)
            == theme.LATIN_TO_LEGEND - theme.LATIN_BOTTOM_CLEAR)
    assert compose.caption_height(0) > theme.CAPTION_BOTTOM


def test_art_gives_way_to_a_longer_legend():
    short = compose.render_single(_spec(), _dark())
    long = compose.render_single(_spec(), _dark(LEGEND))
    def art_bottom(img):
        # The art is the dark run from the top of the mid-plate column; the
        # caption's own ink sits below a white gap.
        col = np.asarray(img)[:, theme.WIDTH // 2] < 128
        y = int(np.argmax(col))
        while y + 1 < len(col) and col[y + 1]:
            y += 1
        return y
    assert art_bottom(short) - art_bottom(long) == compose.caption_height(2) - compose.caption_height(0)


def test_legend_lines_are_drawn_under_the_latin_name():
    with_legend = compose.render_single(_spec(), _blank(LEGEND))
    without = compose.render_single(_spec(), _blank())
    top = theme.HEIGHT - compose.caption_height(2)
    latin = top + round(theme.SCRIPT_TITLE_SIZE * theme.SCRIPT_TITLE_ASCENT) + theme.TITLE_TO_LATIN
    band = (300, latin + 20, theme.WIDTH - 300, theme.HEIGHT - theme.CAPTION_BOTTOM + 10)
    assert _ink(with_legend, band) > 500
    # On the no-legend plate nothing sits between the Latin name and the
    # corner marks (which are outside this x-range).
    clear = theme.CORNER_INSET + typography.date_mark_max_width() + 20
    assert _ink(without, (clear, theme.HEIGHT - 72, theme.WIDTH - clear, theme.HEIGHT - 44)) == 0


def test_marks_sit_in_the_bottom_corners():
    out = compose.render_single(_spec(), _blank(LEGEND))
    y0, y1 = theme.MARKS_BASELINE - 30, theme.MARKS_BASELINE + 8
    assert _ink(out, (theme.CORNER_INSET, y0, theme.CORNER_INSET + 320, y1)) > 100
    assert _ink(out, (theme.WIDTH - theme.CORNER_INSET - 140, y0, theme.WIDTH - theme.CORNER_INSET, y1)) > 60
    assert _ink(out, (0, y0, theme.CORNER_INSET - 2, y1)) == 0
    assert _ink(out, (theme.WIDTH - theme.CORNER_INSET + 2, y0, theme.WIDTH, y1)) == 0


def test_note_sits_between_the_corner_marks_without_touching_them():
    note = "Nothing heard since 11:27 pm on a long, long, long, long quiet evening"
    out = compose.render_single(_spec(note=note), _blank(LEGEND))
    scratch = Image.new("L", out.size, 255)
    typography.date_mark(scratch, _spec().when)
    typography.plate_number_mark(scratch, 43)
    marks = np.asarray(scratch) < 128
    only_note = Image.new("L", out.size, 255)
    typography.note_line(only_note, note, max_w=compose.note_width())
    note_px = np.asarray(only_note) < 128
    assert note_px.any() and not (marks & note_px).any()
    ys, xs = np.where(note_px)
    assert abs((xs.min() + xs.max()) / 2 - theme.WIDTH / 2) < 8


def test_first_ever_adds_a_line_after_the_legend():
    plain = compose.render_single(_spec(), _blank(LEGEND))
    first = compose.render_single(_spec(first_ever=True), _blank(LEGEND))
    assert plain.tobytes() != first.tobytes()
    assert compose.caption_height(2, first_ever=True) - compose.caption_height(2) == theme.LEGEND_PITCH


def test_fallback_plate_keeps_the_corner_number():
    out = compose.render_fallback(_spec(first_seen="2026-05-17"))
    y0, y1 = theme.MARKS_BASELINE - 30, theme.MARKS_BASELINE + 8
    assert _ink(out, (theme.WIDTH - theme.CORNER_INSET - 140, y0, theme.WIDTH - theme.CORNER_INSET, y1)) > 60
    assert _ink(out, (0, 0, theme.WIDTH, 200)) == 0
