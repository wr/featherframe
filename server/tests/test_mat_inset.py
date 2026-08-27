"""The mat inset shrinks the composition onto white paper so the visible art
clears the frame's 8x6 mat opening. See pipeline._apply_mat_inset."""
from __future__ import annotations

from PIL import Image

from featherframe import PANEL_HEIGHT, PANEL_WIDTH
from featherframe.config import Config
from featherframe.render import theme
from featherframe.render.pipeline import _apply_mat_inset


def _solid_black() -> Image.Image:
    return Image.new("L", (PANEL_WIDTH, PANEL_HEIGHT), 0)


def test_inset_keeps_canvas_size_and_adds_white_border():
    out = _apply_mat_inset(_solid_black(), Config(mat_inset_pct=4.0))
    assert out.size == (PANEL_WIDTH, PANEL_HEIGHT)
    # The corners fall in the reserved border, so they are the white field.
    assert out.getpixel((0, 0)) == theme.FIELD
    assert out.getpixel((PANEL_WIDTH - 1, PANEL_HEIGHT - 1)) == theme.FIELD
    # The center is still the black composition.
    assert out.getpixel((PANEL_WIDTH // 2, PANEL_HEIGHT // 2)) == 0


def test_inset_scale_matches_pct():
    out = _apply_mat_inset(_solid_black(), Config(mat_inset_pct=4.0))
    # 4% per edge -> a white band roughly 4% of each dimension wide.
    band = round(PANEL_WIDTH * 0.04)
    assert out.getpixel((band // 2, PANEL_HEIGHT // 2)) == theme.FIELD
    assert out.getpixel((band + 10, PANEL_HEIGHT // 2)) == 0


def test_zero_pct_is_a_noop():
    src = _solid_black()
    assert _apply_mat_inset(src, Config(mat_inset_pct=0.0)) is src
