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
SOURCE_DOWN_HINT = "Check the detection source in Settings"
SOURCE_UP_HINT = "The first detection will appear here"

# What a screen that has not been added yet shows (W-833). The same sentence
# the kit's own baked screen carries, so the answer is the same wherever the
# owner reads it.
WAITING_LINE = "ADD THIS FRAME ON THE FEATHERFRAME WEBAPP"
_WAITING_SIZE = 40
_WAITING_ID_SIZE = 28
PAIRING_LINE = "PAIR THIS FRAME AT CLOUD.FEATHERFRAME.APP"
# The pairing screen is the kit's boot screen (bake_screens.py): the bough,
# the wordmark on its baseline, and the code where the splash sets its
# version and the boot screens rest their pills.
_BOOT_WORDMARK_BASELINE = 1534
_CODE_BASELINE = 1690
_CODE_SIZE = 84
_PAIRING_LINE_BASELINE = 1764
_PAIRING_LINE_SIZE = 24
_EXPIRES_BASELINE = 1806
# With a setup URL (W-888, W-889) the pairing screen is a row under the
# wordmark: the QR on the left, the code and its lines beside it, set left.
# Whole-pixel QR modules on the EE03 (10 px), quiet zone included; ~46 mm
# across there, ~55 mm on the EE02.
SETUP_LINE = "SCAN WITH YOUR PHONE TO SET UP"
SETUP_OR_LINES = ("OR ENTER THE CODE AT", "CLOUD.FEATHERFRAME.APP")
_QR_MODULE_PX = 10
_ROW_WORDMARK_BASELINE = 1290
_ROW_WORDMARK_SIZE = 104
# The bough sits this much higher on this screen, for the row's room; its
# highest twig stays inside the 4 % mat.
_ROW_BOUGH_LIFT = 45
_ROW_TOP = 1395
_ROW_GAP = 40
_ROW_LINE_SIZE = 28
_ROW_CODE_SIZE = 112


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


def setup_qr(url: str) -> Image.Image:
    """`url` as a QR code, black on the field, one module `_QR_MODULE_PX`
    square, its 4-module quiet zone included. Upper case keeps it in the
    alphanumeric mode: a smaller symbol, so larger modules."""
    import segno
    rows = [list(r) for r in segno.make(url, error="m", boost_error=False).matrix_iter(border=4)]
    n = len(rows)
    img = Image.new("L", (n, n), theme.FIELD)
    img.putdata([0 if dark else theme.FIELD for row in rows for dark in row])
    return img.resize((n * _QR_MODULE_PX, n * _QR_MODULE_PX), Image.NEAREST)


def _setup_lines(code: str, expires: str) -> list:
    lines = [(code, _ROW_CODE_SIZE, theme.INK), (SETUP_LINE, _ROW_LINE_SIZE, theme.INK_MEDIUM)]
    lines += [(t, _ROW_LINE_SIZE, theme.INK_MEDIUM) for t in SETUP_OR_LINES]
    if expires:
        lines.append((f"CODE EXPIRES {expires}".upper(), _ROW_LINE_SIZE, theme.INK_MEDIUM))
    return lines


def setup_qr_origin(code: str, expires: str, url: str) -> tuple:
    """Where the QR's top-left lands: the QR and its lines, as one block,
    centred on the sheet."""
    qr_w = setup_qr(url).width
    text_w = max(typography.engraved_width(t, z) for t, z, _ in _setup_lines(code, expires))
    return round((theme.WIDTH - (qr_w + _ROW_GAP + text_w)) / 2), _ROW_TOP


def _setup_row(type_: Image.Image, code: str, expires: str, url: str) -> None:
    """The wordmark, then the QR with the code and its lines beside it."""
    typography.draw_script(type_, theme.WIDTH / 2, _ROW_WORDMARK_BASELINE, "Featherframe",
                           _ROW_WORDMARK_SIZE, theme.INK, stroke=theme.TITLE_STROKE)
    qr = setup_qr(url)
    draw = ImageDraw.Draw(type_)
    lines = _setup_lines(code, expires)
    qr_left, _ = setup_qr_origin(code, expires, url)
    type_.paste(qr, (qr_left, _ROW_TOP))
    left = qr_left + qr.width + _ROW_GAP
    # The block's middle on the QR's middle; the code a larger step from its lines.
    steps = [0] + [_ROW_CODE_SIZE * 0.9] + [_ROW_LINE_SIZE * 1.85] * (len(lines) - 2)
    height = sum(steps)
    base = _ROW_TOP + qr.height / 2 - height / 2 + _ROW_CODE_SIZE * 0.35
    y = base
    for (text, size, fill), step in zip(lines, steps):
        y += step
        w = typography.engraved_width(text, size)
        typography.draw_engraved(draw, left + w / 2, y, text, size, fill)


def render_pairing(code: str, color: bool = False, expires: str = "",
                   url: str = "") -> Image.Image:
    """What a hosted frame no one has claimed shows (W-845): the kit's own
    boot screen, the empty bough over the wordmark, with its pairing `code`
    under the wordmark and the one line that says where to type it. `color`:
    the bough in colour under the same type, for a colour panel. `expires`:
    when the code stops working ("25 September, 10:32 am"). The glass keeps
    its picture unpowered, so a frame found in a drawer still shows a code;
    the date says whether it can still be typed. `url`: the setup page for
    this code (W-888), drawn as a QR code the owner scans with a phone."""
    type_ = Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)
    if url:
        _setup_row(type_, code, expires, url)
    else:
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
    art.paste(Image.open(paths.art_dir() / name).convert(mode), (0, -_ROW_BOUGH_LIFT if url else 0))
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
