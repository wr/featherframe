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
INK = 17           # main text (title) — #111
INK_SOFT = 96      # tertiary type (faint)
INK_MEDIUM = 51    # secondary text (sci name, date, number) — #333
RULE = 68          # divider line — #444
FALLBACK_TINT = 236  # very light panel behind a fallback plate's name
MAT_BORDER = 204   # the mat allowance ring around the inset composition — a
                   # quiet gray the physical mat should exactly cover, so a
                   # sliver of it on the glass means "adjust mat_inset_pct".
                   # Multiple of 17 so it sits ON a 16-level gray (no stipple),
                   # same rationale as FIELD.

# -- geometry (px) ---------------------------------------------------------
MARGIN_X = 100     # left/right breathing room (art fills wider, per v2)
MARGIN_TOP = 78
MARGIN_BOTTOM = 95

# The caption block reserves this much height at the bottom of a single frame
# (its top edge is the title's cap top); the bird art gets everything above it.
# v4: name + scientific name only — the rule and date line are gone (the
# detection date is a corner mark now), so the block is shallower and the pair
# breathes inside it.
CAPTION_BLOCK_H = 264
CAPTION_GAP = 84   # gap between the art and the caption block

# Caption typography (point-ish sizes at native panel resolution)
NAME_SIZE = 78         # common name, faux small caps
NAME_TRACKING = 0.10   # extra letter-spacing as a fraction of size
SCI_SIZE = 46          # scientific name, italic
META_SIZE = 30         # date / time line
META_TRACKING = 0.22
PLATE_NO_SIZE = 42     # "№ 47" corner mark — same size as the date line
PLATE_NO_TRACKING = 0.18
PLATE_NO_NUMERO = "№"          # numero sign, then the ordinal in oldstyle figures
PLATE_NO_FEATURES = ("onum", "kern")
PLATE_NO_WEIGHT = 600          # semibold corner mark

SMALLCAP_RATIO = 0.76  # small-cap glyph height as a fraction of full caps

# -- v3 caption typography (real OpenType via RAQM) ------------------------
# The common name is a flowing swash italic, gently tightened; the scientific
# name is engraved capitals (Adorn Engraved); the date is mixed-case italic
# with old-style figures. Shaping needs libraqm at render time; typography.py
# degrades to faux small caps if it's absent.
TITLE_SIZE = 112          # common name, swash italic (auto-fit to width)
TITLE_WEIGHT = 500
TITLE_TRACKING = -0.02    # tightened letter-spacing, fraction of size
# Swash + standard ligatures + contextual alternates. No discretionary or
# historical ligatures: the st/ct arcs read too precious at display size.
TITLE_FEATURES = ("swsh", "liga", "calt", "kern")
TITLE_NO_SWASH = ("J",)   # capitals that keep their plain form under "swsh"
TITLE_CAP = 0.74          # block top -> title baseline, fraction of size
                          # (cap height plus the swash capitals' overshoot)

SUBTITLE_SIZE = 35         # scientific name, engraved capitals
SUBTITLE_TRACKING = 0.155
ENGRAVED_CAP = 0.775       # Adorn Engraved cap height as a fraction of size

DATE_SIZE = 36             # date / time line, italic + oldstyle figures
DATE_WEIGHT = 500
DATE_TRACKING = 0.09
DATE_FEATURES = ("onum", "liga", "kern")           # "17 May 2026"
TIME_FEATURES = ("smcp", "onum", "liga", "kern")   # "8:14 am" -> small-cap AM
CORNER_SEP = "·"           # between date and time in the corner mark ("1 Sep · 8:14 am")
DATE_ORNAMENT = "❧"   # ❧ hedera leaf between date and time
DATE_ORNAMENT_GAP = 1.15   # gap on each side of the hedera, fraction of size

# Baseline rhythm inside the caption block, from the title baseline down.
# Offsets scale with the title's fitted size so long names stay balanced.
CAPTION_SCI_DROP = 0.80    # title baseline -> sci baseline, fraction of title size

# "First recorded today" under the scientific name of a first-ever species on
# a REAL plate (and "First recorded 17 May 2026" on the no-plate fallback):
# the corner date's italic voice, larger and in the full ink, flanked by a
# pair of hederae. A first-ever bird is an event, and the line must read
# from across the room — the small italic caps it replaced did not (W-695).
# The caption block grows by FIRST_LINE_EXTRA so the line sits in the block's
# own rhythm and never meets the gone-quiet footnote below it.
FIRST_LINE_SIZE = 42
FIRST_LINE_WEIGHT = 500
FIRST_LINE_FEATURES = ("onum", "liga", "kern")
FIRST_LINE_ORNAMENTS = ("☙", "❧")   # left and right hedera; ("", "") for none
FIRST_LINE_ORNAMENT_GAP = 0.45      # ornament -> text gap, fraction of size
FIRST_LINE_DROP = 1.65         # sci baseline -> first-recorded baseline, fraction of size
FIRST_LINE_EXTRA = 80          # caption block height added for the line

# Hairline rule under the scientific name
RULE_WIDTH = 200
RULE_THICKNESS = 2

# -- gone-quiet footnote ----------------------------------------------------
# One small italic line centred in the bottom margin ("Nothing heard since
# 11:27 pm"), below the caption block. Anchored to the panel's bottom edge so
# it never chases the caption's baselines, which move with the title's size.
NOTE_SIZE = 32
NOTE_WEIGHT = 500
NOTE_FEATURES = ("onum", "liga", "kern")
NOTE_BASELINE = HEIGHT - 40    # note baseline above the panel bottom
NOTE_CLEAR = 46                # how far a collage key lifts to make room for it

# -- collage sheet ----------------------------------------------------------
# One italic header line ("Sightings ~ August 27"), art, and an engraved-caps
# key along the bottom. No subtitle, no rule: the sheet stays quiet.
COLLAGE_TITLE_SIZE = 118       # header, swash italic (auto-fit to width)
COLLAGE_TITLE_BASELINE = 152   # header baseline from the top of the panel
COLLAGE_ART_TOP = 212          # top of the art box, below the header

KEY_SIZE = 34                  # bottom key, engraved capitals
KEY_SIZES = (34, 30, 27, 24)   # shrink steps when the widest line won't fit
KEY_TRACKING = 0.035
KEY_LINE_H = 1.3               # baseline pitch, fraction of key size
KEY_ENTRY_GAP = 1.9            # gap between entries on a line, fraction of size
KEY_BOTTOM = 45                # last key baseline above the panel bottom
KEY_ART_GAP = 53               # gap between the art box and the key's ink top

CONTENT_W = WIDTH - 2 * MARGIN_X
