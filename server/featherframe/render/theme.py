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
FIELD = 255        # pure-white paper field, matching the v2 designer frame.
                   # 255 is exactly 16-level gray 15, so the big flat field
                   # dithers to a solid, stipple-free white; the normalised
                   # plate paper also tops out at 255, so the darken-composite
                   # blends seamlessly with no paste seam.
INK = 26           # primary type (near black, not pure — softer on e-ink)
INK_SOFT = 96      # tertiary type (faint)
INK_MEDIUM = 64    # dark gray: date/time line + the plate-number mark
RULE = 40          # hairline rule
FALLBACK_TINT = 236  # very light panel behind a fallback plate's name

# -- geometry (px) ---------------------------------------------------------
MARGIN_X = 100     # left/right breathing room (art fills wider, per v2)
MARGIN_TOP = 78
MARGIN_BOTTOM = 95

# The caption block reserves this much height at the bottom of a single frame;
# the bird art gets everything above it.
CAPTION_BLOCK_H = 278
CAPTION_GAP = 64   # gap between the art and the caption block

# Caption typography (point-ish sizes at native panel resolution)
NAME_SIZE = 78         # common name, faux small caps
NAME_TRACKING = 0.10   # extra letter-spacing as a fraction of size
SCI_SIZE = 46          # scientific name, italic
META_SIZE = 30         # date / time line
META_TRACKING = 0.22
PLATE_NO_SIZE = 32     # "№ 47" corner mark — same size as the date line
PLATE_NO_TRACKING = 0.18
PLATE_NO_NUMERO = "№"          # numero sign, then the ordinal in oldstyle figures
PLATE_NO_FEATURES = ("onum", "kern")

SMALLCAP_RATIO = 0.76  # small-cap glyph height as a fraction of full caps

# -- v2 caption typography (real OpenType via RAQM) ------------------------
# The common name is set as a flowing swash italic; the scientific name and
# date as real small caps with old-style figures. These need libraqm at render
# time; typography.py degrades to faux small caps if it's absent.
TITLE_SIZE = 96           # common name, swash italic (auto-fit to width)
TITLE_WEIGHT = 500
TITLE_FEATURES = ("swsh", "dlig", "hlig", "hist", "liga", "calt", "kern")

SUBTITLE_SIZE = 31         # scientific name, italic small caps
SUBTITLE_WEIGHT = 500
SUBTITLE_TRACKING = 0.14
SUBTITLE_FEATURES = ("smcp", "onum", "liga", "dlig", "hlig", "ordn", "kern")

DATE_SIZE = 32             # date / time line, italic small caps + oldstyle figs
DATE_WEIGHT = 500
DATE_TRACKING = 0.19
DATE_FEATURES = ("smcp", "onum", "ordn", "liga", "kern")
DATE_ORNAMENT = "❧"   # ❧ hedera leaf between date and time

# Hairline rule under the scientific name
RULE_WIDTH = 200
RULE_THICKNESS = 2

CONTENT_W = WIDTH - 2 * MARGIN_X
