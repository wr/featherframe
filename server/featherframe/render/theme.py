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
MARGIN_X = 100     # left/right inset of the caption's marks and footnote
MARGIN_BOTTOM = 95

# The art is full-bleed (W-707): it runs to the panel's top and side edges —
# i.e. to the mat opening, since the mat inset scales the whole composition —
# and only the caption block below it is reserved. The block's height follows
# its line count (compose.caption_height).
CAPTION_GAP = 96       # air between the art and the title: the script capitals
                       # swash well above their nominal cap line (4 Sep 2026)

# -- script caption (W-708) --------------------------------------------------
# Wells's mockup: a monoline script title over the engraved Latin name (with
# its period, as Audubon printed it), then the plate's own legend lines —
# "Male, 1. Female, 2." / "Black berry. Rubus villosus." — in the small
# script, and the date · time / "No. NN" tucked into the bottom corners in
# that same script. Sizes were tuned on the glass 4 Sep 2026 (W-713/W-714):
# Kapakana at 160 px set the x-height, Pinyon Script matches it at 124 px and
# the small sizes scale with it; the title-to-Latin gap is the original 72.
SCRIPT_TITLE_SIZE = 124        # common name, auto-fit down to SCRIPT_TITLE_MIN
SCRIPT_TITLE_MIN = 60
SCRIPT_TITLE_ASCENT = 0.62     # block top -> title baseline, fraction of size
TITLE_TO_LATIN = 72            # title baseline -> Latin baseline
LATIN_TO_LEGEND = 68           # Latin baseline -> first legend baseline
LATIN_BOTTOM_CLEAR = 24        # Latin baseline -> caption bottom when there is no legend
LEGEND_SIZE = 32
LEGEND_PITCH = 47              # baseline pitch of the legend lines
TITLE_STROKE = 0.0             # synthetic bold, px per edge: the script has one weight
LEGEND_STROKE = 0.5            # and its small lines read thin on e-ink (0.75 read bolder
                               # than the title's hairlines; 0.5 matches them)
CAPTION_BOTTOM = 52            # last caption baseline above the panel bottom
LATIN_PERIOD = "."             # Audubon's trailing period on the Latin name
# Provenance for an AI-drawn plate (W-733): a four-point star before the
# number in the corner, so a synthetic sheet never hangs as a Havell scan
# and the caption keeps the plate's own lines only.
GENERATED_MARK_GAP = 12        # star -> "No." in the corner

# A species never heard before today (W-744): a rule around the whole sheet
# at the mat edge, the folio's way of marking a plate of note, with a small
# "NEW" pill — the system voice, Inter semibold on black — set into its top
# edge. ("line" keeps the old script legend line.)
FIRST_EVER_MARK = "rule"
FIRST_EVER_RULE_INSET = 14     # from the composition's edges (the mat opening)
FIRST_EVER_RULE_WIDTH = 6
FIRST_EVER_LABEL = "NEW"
FIRST_EVER_LABEL_SIZE = 22     # Inter semibold capitals
FIRST_EVER_LABEL_TRACKING = 0.08   # letter-spacing, fraction of the size
FIRST_EVER_LABEL_PAD = 18      # pill side padding
FIRST_EVER_PILL_H = 44
FIRST_EVER_PILL_GAP = 14       # paper between the pill and the rule on either side

CORNER_SIZE = 28               # date · time (left) and "No. NN" (right)
CORNER_INSET = 36              # from the side edges
MARKS_BASELINE = HEIGHT - 30
PLATE_NO_PREFIX = "No."        # the script has no numero glyph
CORNER_SEP = "·"               # between date and time ("1 Sep · 8:14 am")
COLON_KERN = 0.0               # pulls the run after a colon in, fraction of size
                               # (Kapakana needed -0.12; Pinyon is kerned, so 0)

NAME_TRACKING = 0.10   # faux small caps letter-spacing (no-RAQM fallback)
SMALLCAP_RATIO = 0.76  # small-cap glyph height as a fraction of full caps

# -- engraved capitals ------------------------------------------------------
# The scientific name, key entries, and status labels are engraved capitals
# (IM Fell Double Pica SC, OFL). If the file is ever absent typography.py
# degrades to Garamond faux small caps.
SUBTITLE_SIZE = 35         # scientific name, engraved capitals
SUBTITLE_TRACKING = 0.155
ENGRAVED_CAP = 0.775       # cap height of the capitals as a fraction of the nominal size
ENGRAVED_SCALE = 0.775 / 0.728   # nominal size -> IM Fell's point size (its caps are 0.728 em)

DATE_ORNAMENT = "❧"   # ❧ hedera leaf under the splash / status wordmark (Garamond's)

# Hairline rule under the scientific name
RULE_WIDTH = 200
RULE_THICKNESS = 2

# -- gone-quiet footnote ----------------------------------------------------
# One small script line ("Nothing heard since 11:27 pm") centred on the corner
# marks' baseline, between them; shrinks rather than clips.
NOTE_SIZE = CORNER_SIZE
NOTE_MIN_SIZE = 19
NOTE_MARK_GAP = 40             # clearance between the footnote and either mark
NOTE_CLEAR = 46                # how far a collage key lifts to make room for it

# -- status plate -----------------------------------------------------------
STATUS_VALUE_SIZE = 44         # the values, in the script (shrink to fit)
STATUS_FOOT_SIZE = 34          # "8:14 pm · 4 September" under the rows


# -- collage sheet ----------------------------------------------------------
# One script header line ("A Day in the Garden ~ August 27", auto-fit like a
# plate title), art, and an engraved-caps key along the bottom. No subtitle,
# no rule: the sheet stays quiet.
COLLAGE_TITLE_BASELINE = 152   # header baseline from the top of the panel
COLLAGE_ART_TOP = 212          # top of the art box, below the header
COLLAGE_NAME_SIZE = 36         # grid cell: common name in the script (shrink to fit)
COLLAGE_COUNT_SIZE = 28        # grid cell: "×14" under the name

KEY_SIZE = 34                  # bottom key, engraved capitals
KEY_SIZES = (34, 30, 27, 24)   # shrink steps when the widest line won't fit
KEY_TRACKING = 0.035
KEY_LINE_H = 1.3               # baseline pitch, fraction of key size
KEY_ENTRY_GAP = 1.9            # gap between entries on a line, fraction of size
KEY_BOTTOM = 45                # last key baseline above the panel bottom
KEY_ART_GAP = 53               # gap between the art box and the key's ink top
KEY_MAX_H = 480                # the key's ink height cap; the art keeps the rest

# -- generated sheet (the day in review) ------------------------------------
# No header: the art starts at the top margin and takes everything the key
# leaves it. The key is set small and packs into columns before it grows
# tall, and the image is generated at the art box's own aspect.
SHEET_MARGIN_X = 44
SHEET_MARGIN_TOP = 56
SHEET_KEY_SIZES = (24, 22, 20, 18)
SHEET_KEY_MAX_H = 300
SHEET_KEY_MAX_ROWS = 8         # widen into another column before going deeper
SHEET_DATE_TRACKING = 0.4      # the date line: key-sized caps, spaced wide open
SHEET_DATE_GAP = 1.9           # date baseline to first key baseline, fraction of key size
SHEET_KEY_ART_GAP = 40
SHEET_GEN_W = 1024             # generated width; height follows the box aspect

CONTENT_W = WIDTH - 2 * MARGIN_X
