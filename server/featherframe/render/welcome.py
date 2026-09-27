"""The welcome plate (W-734): what hangs before the first bird.

A fresh install used to answer the frame with a 503 and the dashboard with
a broken preview, and the glass kept the baked "Waiting for the first image"
band, which looks the same after three minutes and three days. This is a
real frame: the script wordmark, then the message in the system voice
(W-741: the setup card's black box and the toast pills, not the plate's
script) so what it says can be read from across the room and acted on. It
is replaced by the first detection and re-rendered only when what it says
would change (the source comes up or goes down).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PIL import Image, ImageChops, ImageDraw

from .. import paths
from . import system, theme, typography

LABEL = "No detections yet"
HEADLINE = "No detections yet"
SOURCE_DOWN = "Detection source unreachable"
SOURCE_DOWN_HINT = "Configure the source in the dashboard"
SOURCE_UP_HINT = "The first detection will appear here"

# What a screen that has not been added yet shows (W-833). The same sentence
# the kit's own baked screen carries, so the answer is the same wherever the
# owner reads it.
WAITING_LINE = "ADD THIS FRAME ON THE FEATHERFRAME WEBAPP"
_WAITING_SIZE = 40
_WAITING_ID_SIZE = 28
PAIRING_LINE = "PAIR THIS FRAME AT APP.FEATHERFRAME.APP"
# The pairing screen is the kit's boot screen (bake_screens.py): the bough,
# the wordmark on its baseline, and the code where the splash sets its
# version and the boot screens rest their pills.
_BOOT_WORDMARK_BASELINE = 1534
_CODE_BASELINE = 1690
_CODE_SIZE = 84
_PAIRING_LINE_BASELINE = 1764
_PAIRING_LINE_SIZE = 24
_EXPIRES_BASELINE = 1806


def since_words(since: datetime) -> str:
    hour = since.hour % 12 or 12
    stamp = f"{hour}:{since.minute:02d} {'am' if since.hour < 12 else 'pm'}"
    return f"Listening since {since.day} {since.strftime('%B')}, {stamp}"


def render_waiting(short_id: str = "", line: str = WAITING_LINE) -> Image.Image:
    """What a screen shows while it waits to be added (W-833): the wordmark,
    and under it the one thing the owner has to do. A TRMNL and an e-reader
    fetch this as their image; the kiosk page says the same in HTML."""
    field = Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)
    draw = ImageDraw.Draw(field)
    cx = theme.WIDTH / 2
    baseline = theme.HEIGHT * 0.44
    typography.draw_script(field, cx, baseline, "Featherframe",
                           typography.fit_script_title("Featherframe", theme.CONTENT_W),
                           theme.INK, stroke=theme.TITLE_STROKE)
    hedera_font = typography.FONTS.get(36, italic=True, weight=500)
    draw.text((cx, baseline + 92), theme.DATE_ORNAMENT, font=hedera_font,
              fill=theme.INK_MEDIUM, anchor="ms")
    size = _WAITING_SIZE
    while size > 22 and typography.engraved_width(line, size) > theme.CONTENT_W:
        size -= 1
    typography.draw_engraved(draw, cx, baseline + 220, line, size, theme.INK)
    if short_id:
        typography.draw_engraved(draw, cx, baseline + 220 + _WAITING_SIZE * 2,
                                 str(short_id), _WAITING_ID_SIZE, theme.INK_SOFT)
    return field


def render_pairing(code: str, color: bool = False, expires: str = "") -> Image.Image:
    """What a hosted frame no one has claimed shows (W-845): the kit's own
    boot screen, the empty bough over the wordmark, with its pairing `code`
    under the wordmark and the one line that says where to type it. `color`:
    the bough in colour under the same type, for a colour panel. `expires`:
    when the code stops working ("25 September, 10:32 am"). The glass keeps
    its picture unpowered, so a frame found in a drawer still shows a code;
    the date says whether it can still be typed."""
    type_ = Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)
    cx = theme.WIDTH / 2
    typography.draw_script(type_, cx, _BOOT_WORDMARK_BASELINE, "Featherframe",
                           typography.fit_script_title("Featherframe", theme.CONTENT_W),
                           theme.INK, stroke=theme.TITLE_STROKE)
    draw = ImageDraw.Draw(type_)
    typography.draw_engraved(draw, cx, _CODE_BASELINE, code, _CODE_SIZE, theme.INK)
    typography.draw_engraved(draw, cx, _PAIRING_LINE_BASELINE, PAIRING_LINE,
                             _PAIRING_LINE_SIZE, theme.INK_MEDIUM)
    if expires:
        typography.draw_engraved(draw, cx, _EXPIRES_BASELINE, f"CODE EXPIRES {expires}".upper(),
                                 _PAIRING_LINE_SIZE, theme.INK_MEDIUM)
    mode, name = ("RGB", "bough_color.png") if color else ("L", "bough.png")
    art = Image.new(mode, type_.size, "white")
    art.paste(Image.open(paths.art_dir() / name).convert(mode), (0, 0))
    return ImageChops.darker(type_.convert(mode), art)


def render_welcome(since: datetime, source_ok: bool,
                   now: Optional[datetime] = None) -> Image.Image:
    now = now or datetime.now()
    field = Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)
    draw = ImageDraw.Draw(field)
    cx = theme.WIDTH / 2

    # The wordmark over its hedera, exactly as the status plate sets it.
    title_baseline = theme.HEIGHT * 0.20
    typography.draw_script(field, cx, title_baseline, "Featherframe",
                           typography.fit_script_title("Featherframe", theme.CONTENT_W),
                           theme.INK, stroke=theme.TITLE_STROKE)
    hedera_font = typography.FONTS.get(36, italic=True, weight=500)
    draw.text((cx, title_baseline + 92), theme.DATE_ORNAMENT, font=hedera_font,
              fill=theme.INK_MEDIUM, anchor="ms")

    # The message: the setup card's box, centred on the panel, and the fault
    # (or the plain hint) at the bottom where the firmware rests its pills.
    lines = [(HEADLINE, 54, 600), (since_words(since), 38, 500)]
    _, card_h = system.card_size(lines)
    system.card(draw, cx, (theme.HEIGHT - card_h) / 2, lines)
    if source_ok:
        system.line(draw, cx, system.NOTE_CY + system.NOTE_TEXT * 0.36, SOURCE_UP_HINT, size=system.NOTE_TEXT)
    else:
        system.pill(draw, cx, system.NOTE_CY, SOURCE_DOWN,
                    icon="cloud", max_w=theme.CONTENT_W)
        system.line(draw, cx, system.RETRY_BASELINE, SOURCE_DOWN_HINT, size=system.RETRY_TEXT)
    return field
