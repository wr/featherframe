"""The mat inset shrinks the composition so the visible art clears the
frame's 8x6 mat opening; the surround is painted MAT_BORDER — the ring the
physical mat should exactly cover. See pipeline._apply_mat_inset."""
from __future__ import annotations

from PIL import Image

from featherframe import PANEL_HEIGHT, PANEL_WIDTH
from featherframe.config import Config
from featherframe.render import theme
from featherframe.render.pipeline import _apply_mat_inset


def _solid_black() -> Image.Image:
    return Image.new("L", (PANEL_WIDTH, PANEL_HEIGHT), 0)


def test_inset_keeps_canvas_size_and_paints_mat_ring():
    out = _apply_mat_inset(_solid_black(), Config(mat_inset_pct=4.0))
    assert out.size == (PANEL_WIDTH, PANEL_HEIGHT)
    # The corners fall in the mat allowance, painted the registration gray.
    assert out.getpixel((0, 0)) == theme.MAT_BORDER
    assert out.getpixel((PANEL_WIDTH - 1, PANEL_HEIGHT - 1)) == theme.MAT_BORDER
    # The center is still the black composition.
    assert out.getpixel((PANEL_WIDTH // 2, PANEL_HEIGHT // 2)) == 0


def test_mat_ring_tone_sits_on_a_gray_level():
    # A tone off the 16-level grid would dither to stipple instead of a flat
    # ring (the same constraint that pins FIELD).
    assert theme.MAT_BORDER % 17 == 0


def test_inset_scale_matches_pct():
    out = _apply_mat_inset(_solid_black(), Config(mat_inset_pct=4.0))
    # 4% per edge -> a mat-gray band roughly 4% of each dimension wide.
    band = round(PANEL_WIDTH * 0.04)
    assert out.getpixel((band // 2, PANEL_HEIGHT // 2)) == theme.MAT_BORDER
    assert out.getpixel((band + 10, PANEL_HEIGHT // 2)) == 0


def test_zero_pct_is_a_noop():
    src = _solid_black()
    assert _apply_mat_inset(src, Config(mat_inset_pct=0.0)) is src


def test_mat_offset_shifts_the_composition():
    out = _apply_mat_inset(_solid_black(),
                           Config(mat_inset_pct=4.0, mat_offset_x_px=40,
                                  mat_offset_y_px=-30))
    band = round(PANEL_WIDTH * 0.04)
    # Shifted right: the left ring widens by the offset, the right one thins.
    assert out.getpixel((band + 20, PANEL_HEIGHT // 2)) == theme.MAT_BORDER
    assert out.getpixel((band + 60, PANEL_HEIGHT // 2)) == 0
    # Shifted up: content reaches into the former top ring.
    vband = round(PANEL_HEIGHT * 0.04)
    assert out.getpixel((PANEL_WIDTH // 2, vband - 10)) == 0


def test_mat_offset_is_clamped():
    cfg = Config(mat_offset_x_px=999, mat_offset_y_px=-999)
    assert cfg.mat_offset_x_px == 120
    assert cfg.mat_offset_y_px == -120
