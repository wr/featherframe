"""One pill in one place (25 Sep 2026): a toast lands on the footer line where
the server draws its notes, whatever the frame's mat. The firmware moves a
baked toast by the mat (main.cpp placeToast); this is its formula, held to
the server's own mat (pipeline._apply_mat_inset)."""
from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image

from featherframe.config import Config
from featherframe.render import pipeline, system, theme


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
