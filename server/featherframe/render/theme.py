"""The house style: one place for layout geometry and tonal values.

Everything is grayscale (0 = black, 255 = white) because the panel is
monochrome. Values are chosen to read as a museum plate: a warm-neutral field,
near-black type, and a lot of quiet space. Tuned so they still separate cleanly
after dithering to 16 levels.
"""
from __future__ import annotations

from .. import PANEL_HEIGHT, PANEL_WIDTH

WIDTH = PANEL_WIDTH    # 1404
HEIGHT = PANEL_HEIGHT  # 1872

# -- tone (0..255) ---------------------------------------------------------
FIELD = 238        # off-white paper field — sits exactly on 16-level gray 14,
                   # so it dithers to a solid, seamless tone (no field stipple)
                   # and stays just below the normalised plate paper, which lets
                   # the darken-composite use it as one uniform background.
INK = 26           # primary type (near black, not pure — softer on e-ink)
INK_SOFT = 96      # secondary type (date/time, plate credit)
RULE = 40          # hairline rule
FALLBACK_TINT = 236  # very light panel behind a fallback plate's name

# -- geometry (px) ---------------------------------------------------------
MARGIN_X = 118     # left/right breathing room
MARGIN_TOP = 120
MARGIN_BOTTOM = 132

# The caption block reserves this much height at the bottom of a single frame;
# the bird art gets everything above it.
CAPTION_BLOCK_H = 360
CAPTION_GAP = 64   # gap between the art and the caption block

# Caption typography (point-ish sizes at native panel resolution)
NAME_SIZE = 78         # common name, faux small caps
NAME_TRACKING = 0.10   # extra letter-spacing as a fraction of size
SCI_SIZE = 46          # scientific name, italic
META_SIZE = 30         # date / time line
META_TRACKING = 0.22
PLATE_NO_SIZE = 32     # "No. 47" corner mark
PLATE_NO_TRACKING = 0.18

SMALLCAP_RATIO = 0.76  # small-cap glyph height as a fraction of full caps

# Hairline rule under the scientific name
RULE_WIDTH = 300
RULE_THICKNESS = 2

CONTENT_W = WIDTH - 2 * MARGIN_X
