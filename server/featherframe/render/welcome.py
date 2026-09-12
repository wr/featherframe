"""The welcome plate (W-734): what hangs before the first bird.

A fresh install used to answer the frame with a 503 and the dashboard with
a broken preview, and the glass kept the baked "Waiting for the first bird"
band, which looks the same after three minutes and three days. This is a
real frame in the plates' own voice — the script wordmark, a script
headline, an engraved "listening since" line — so the device paints it,
304s on it, and the since-date says how long the wait has been. It is
replaced by the first detection and re-rendered only when what it says
would change (the source comes up or goes down, dark mode flips).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from . import theme, typography

LABEL = "Waiting for the first bird"
HEADLINE = "Waiting for the first bird."
SOURCE_DOWN = "DETECTION SOURCE NOT REACHABLE."
SOURCE_DOWN_HINT = "Connect it from the dashboard."
SOURCE_UP_HINT = "The first bird it hears will hang here."


def since_words(since: datetime) -> str:
    hour = since.hour % 12 or 12
    stamp = f"{hour}:{since.minute:02d} {'am' if since.hour < 12 else 'pm'}"
    return f"Listening since {since.day} {since.strftime('%B')}, {stamp}."


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

    # The headline in the plate title's script, a size down from a title.
    size = min(typography.fit_script_title(HEADLINE, theme.CONTENT_W),
               round(theme.SCRIPT_TITLE_SIZE * 0.72))
    y = theme.HEIGHT * 0.50
    typography.draw_script(field, cx, y, HEADLINE, size, theme.INK, stroke=theme.TITLE_STROKE)

    # Engraved capitals for the facts, the Latin-name voice.
    y += 110
    typography.draw_engraved(draw, cx, y, since_words(since).upper(), theme.SUBTITLE_SIZE,
                             theme.INK_MEDIUM, theme.SUBTITLE_TRACKING)
    y += 96
    if source_ok:
        typography.draw_script(field, cx, y, SOURCE_UP_HINT, theme.LEGEND_SIZE + 6,
                               theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE)
    else:
        typography.draw_engraved(draw, cx, y, SOURCE_DOWN, theme.SUBTITLE_SIZE,
                                 theme.INK, theme.SUBTITLE_TRACKING)
        y += 72
        typography.draw_script(field, cx, y, SOURCE_DOWN_HINT, theme.LEGEND_SIZE + 6,
                               theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE)

    # "as of" footer in the corner marks' voice, like the status plate.
    hour = now.hour % 12 or 12
    stamp = f"{hour}:{now.minute:02d} {'am' if now.hour < 12 else 'pm'}"
    footer = f"{stamp} {theme.CORNER_SEP} {now.day} {now.strftime('%B')}"
    typography.draw_script(field, cx, theme.HEIGHT - theme.CAPTION_BOTTOM, footer,
                           theme.STATUS_FOOT_SIZE, theme.INK_MEDIUM, stroke=theme.LEGEND_STROKE)
    return field
