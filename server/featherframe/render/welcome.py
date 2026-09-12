"""The welcome plate (W-734): what hangs before the first bird.

A fresh install used to answer the frame with a 503 and the dashboard with
a broken preview, and the glass kept the baked "Waiting for the first bird"
band, which looks the same after three minutes and three days. This is a
real frame: the script wordmark, then the message in the system voice
(W-741: the setup card's black box and the toast pills, not the plate's
script) so what it says can be read from across the room and acted on. It
is replaced by the first detection and re-rendered only when what it says
would change (the source comes up or goes down, dark mode flips).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from . import system, theme, typography

LABEL = "No detections yet"
HEADLINE = "No detections yet"
SOURCE_DOWN = "Detection source unreachable"
SOURCE_DOWN_HINT = "Configure the source in the dashboard"
SOURCE_UP_HINT = "The first detection will appear here"


def since_words(since: datetime) -> str:
    hour = since.hour % 12 or 12
    stamp = f"{hour}:{since.minute:02d} {'am' if since.hour < 12 else 'pm'}"
    return f"Listening since {since.day} {since.strftime('%B')}, {stamp}"


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

    # The message: the setup card's box, then the pill for a fault.
    bottom = system.card(draw, cx, theme.HEIGHT * 0.42,
                         [(HEADLINE, 54, 600), (since_words(since), 38, 500)])
    y = bottom + 74
    if source_ok:
        system.line(draw, cx, y + 10, SOURCE_UP_HINT, size=30)
    else:
        system.pill(draw, cx, y + system.PILL_H / 2, SOURCE_DOWN, style="outline", icon="cloud",
                    max_w=theme.CONTENT_W)
        system.line(draw, cx, y + system.PILL_H + 58, SOURCE_DOWN_HINT, size=28)

    # "as of" footer in the corner marks' voice, like the status plate.
    hour = now.hour % 12 or 12
    stamp = f"{hour}:{now.minute:02d} {'am' if now.hour < 12 else 'pm'}"
    footer = f"{stamp} {theme.CORNER_SEP} {now.day} {now.strftime('%B')}"
    typography.draw_script(field, cx, theme.HEIGHT - theme.CAPTION_BOTTOM, footer,
                           theme.STATUS_FOOT_SIZE, theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE)
    return field
