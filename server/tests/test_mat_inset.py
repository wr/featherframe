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


def test_both_kits_start_behind_a_four_percent_mat():
    """The kits' mats cover the art's edge unless it is inset ~4 % (both
    panels, on the wall). A screen with no mat of ours starts with none."""
    src = _solid_black()
    assert Config().mat_inset_pct == 0.0
    for panel in ("ee03", "ee02"):
        assert Config.defaults_for(panel).mat_inset_pct == 4.0
    assert Config.defaults_for("custom:800x480:gray16:90,270").mat_inset_pct == 0.0
    assert _apply_mat_inset(src, Config()) is src
    # A value that was stored is untouched by the default.
    assert Config.from_dict({"mat_inset_pct": 1.5}).mat_inset_pct == 1.5


def test_a_kit_the_owner_never_set_follows_its_panels_inset(tmp_path):
    """`set` holds only what the owner chose: a Save posts every field, and a
    mat at its panel's default is not kept, so the frame follows the default."""
    from featherframe import frames as frames_mod
    from featherframe.db import Database
    reg = frames_mod.FrameRegistry(Database(tmp_path / "s.db"))
    fid = "AA:BB:CC:00:00:01"
    reg.save({**frames_mod.new_row(fid, "kit", "2026-09-25T00:00:00"),
              "status": frames_mod.ON,
              "set": {"name": "Hall", "mat_inset_pct": 0.0, "mat_offset_x_px": 6}})
    other = "AA:BB:CC:00:00:02"
    reg.save({**frames_mod.new_row(other, "kit", "2026-09-25T00:00:00"),
              "set": {"mat_inset_pct": 2.5}})
    reg.drop_zero_mat_inset()
    own = frames_mod.settings_of(reg.get(fid))
    assert own == {"name": "Hall", "mat_offset_x_px": 6}
    assert frames_mod.frame_config(reg.get(fid), Config()).mat_inset_pct == 4.0
    assert frames_mod.settings_of(reg.get(other))["mat_inset_pct"] == 2.5
    # Once only: a 0 the owner sets afterwards is theirs.
    with reg.mutate() as rows:
        rows[fid]["set"]["mat_inset_pct"] = 0.0
    reg.drop_zero_mat_inset()
    assert frames_mod.settings_of(reg.get(fid))["mat_inset_pct"] == 0.0


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
