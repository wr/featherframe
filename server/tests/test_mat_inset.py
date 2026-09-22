"""The mat inset shrinks the composition so the visible art clears the
frame's 8x6 mat opening; the surround is white paper, which the physical mat
covers. See pipeline._apply_mat_inset."""
from __future__ import annotations

from PIL import Image

from featherframe import PANEL_HEIGHT, PANEL_WIDTH
from featherframe.config import Config
from featherframe.render.pipeline import _apply_mat_inset


def _solid_black() -> Image.Image:
    return Image.new("L", (PANEL_WIDTH, PANEL_HEIGHT), 0)


def test_inset_keeps_canvas_size_and_leaves_the_surround_white():
    out = _apply_mat_inset(_solid_black(), Config(mat_inset_pct=4.0))
    assert out.size == (PANEL_WIDTH, PANEL_HEIGHT)
    # The corners fall under the mat: paper, not a registration ring.
    assert out.getpixel((0, 0)) == 255
    assert out.getpixel((PANEL_WIDTH - 1, PANEL_HEIGHT - 1)) == 255
    # The center is still the black composition.
    assert out.getpixel((PANEL_WIDTH // 2, PANEL_HEIGHT // 2)) == 0


def test_inset_scale_matches_pct():
    out = _apply_mat_inset(_solid_black(), Config(mat_inset_pct=4.0))
    # 4% per edge -> a white band roughly 4% of each dimension wide.
    band = round(PANEL_WIDTH * 0.04)
    assert out.getpixel((band // 2, PANEL_HEIGHT // 2)) == 255
    assert out.getpixel((band + 10, PANEL_HEIGHT // 2)) == 0


def test_zero_pct_is_a_noop():
    src = _solid_black()
    assert _apply_mat_inset(src, Config(mat_inset_pct=0.0)) is src


def test_no_mat_is_the_default_for_every_panel():
    """A frame with no mat is the common case: the allowance is opt-in."""
    src = _solid_black()
    assert Config().mat_inset_pct == 0.0
    for panel in ("ee03", "ee02"):
        assert Config.defaults_for(panel).mat_inset_pct == 0.0
    assert _apply_mat_inset(src, Config()) is src
    # A value that was stored is untouched by the change of default.
    assert Config.from_dict({"mat_inset_pct": 4.0}).mat_inset_pct == 4.0


def test_mat_offset_shifts_the_composition():
    out = _apply_mat_inset(_solid_black(),
                           Config(mat_inset_pct=4.0, mat_offset_x_px=40,
                                  mat_offset_y_px=-30))
    band = round(PANEL_WIDTH * 0.04)
    # Shifted right: the left ring widens by the offset, the right one thins.
    assert out.getpixel((band + 20, PANEL_HEIGHT // 2)) == 255
    assert out.getpixel((band + 60, PANEL_HEIGHT // 2)) == 0
    # Shifted up: content reaches into the former top ring.
    vband = round(PANEL_HEIGHT * 0.04)
    assert out.getpixel((PANEL_WIDTH // 2, vband - 10)) == 0


def test_mat_offset_is_clamped():
    cfg = Config(mat_offset_x_px=999, mat_offset_y_px=-999)
    assert cfg.mat_offset_x_px == 120
    assert cfg.mat_offset_y_px == -120
