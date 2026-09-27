"""One pill in one place (25 Sep 2026): a toast lands on the footer line where
the server draws its notes, whatever the frame's mat. The firmware moves a
baked toast by the mat (main.cpp placeToast); this is its formula, held to
the server's own mat (pipeline._apply_mat_inset)."""
from __future__ import annotations

import importlib.util
import math
import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

from featherframe import panels
from featherframe.config import Config
from featherframe.render import compose, pipeline, system, theme

BAKE = os.path.join(os.path.dirname(__file__), "..", "..", "firmware", "tools", "screens",
                    "bake_screens.py")


def mat_at(size: float, inset: float, off: float, at: float) -> float:
    """main.cpp matAt, line for line."""
    s = 1.0 - inset / 50.0
    return math.floor((size - round(size * s)) / 2.0) + off + at * s


@pytest.mark.parametrize("inset, dx, dy", [(0, 0, 0), (4, 0, 0), (5.5, 12, -30), (9, -40, 18)])
def test_the_footer_line_moves_with_the_mat_as_the_firmware_says(inset, dx, dy):
    w, h = theme.WIDTH, theme.HEIGHT
    cy = int(system.NOTE_CY)
    # The toast's centre, and the plate number's right edge (the offline mark's).
    for cx in (w // 2, w - theme.CORNER_INSET - 6):
        sheet = Image.new("L", (w, h), 255)
        sheet.paste(0, (cx - 6, cy - 6, cx + 6, cy + 6))   # a mark on the footer line
        cfg = Config(mat_inset_pct=inset, mat_offset_x_px=dx, mat_offset_y_px=dy)
        out = np.asarray(pipeline._apply_mat_inset(sheet, cfg))
        ys, xs = np.where(out < 128)
        assert abs((xs.min() + xs.max()) / 2 - mat_at(w, inset, dx, cx)) <= 1.5
        assert abs((ys.min() + ys.max()) / 2 - mat_at(h, inset, dy, cy)) <= 1.5


@pytest.fixture(scope="module")
def bake():
    spec = importlib.util.spec_from_file_location("bake_screens", BAKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_footnote_is_the_frames_own_pill(bake):
    """The footnote a plate carries ("Just now: Northern Cardinal" while it is
    held) is the pill the frame bakes its toasts as, 34 px type since W-897:
    under the kits' mat it lands on the glass the size and place a toast does,
    so one covers the other exactly."""
    assert {panels.get(k).mat_inset_pct for k in ("ee03", "ee02")} == {bake.REF_INSET}
    sheet = Image.new("L", (theme.WIDTH, theme.HEIGHT), 255)
    system.note_pill(ImageDraw.Draw(sheet), "Just now: Northern Cardinal", None, compose.note_width())
    out = np.asarray(pipeline._apply_mat_inset(sheet, Config(mat_inset_pct=bake.REF_INSET)))
    ys, _ = np.where(out < 128)
    top, bottom = ys.min(), ys.max() + 1
    assert abs((bottom - top) - bake.PILL_H) <= 1
    assert abs(bottom - (bake.PILL_Y + bake.PILL_H)) <= 1
    assert abs(system.PILL_TEXT * bake.REF_SCALE - bake.PILL_TEXT_SIZE) <= 0.5
