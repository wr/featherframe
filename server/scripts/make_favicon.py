#!/usr/bin/env python3
"""Render the config page's favicon set from the wordmark font.

The icon is the script "F" of the Featherframe wordmark (the plates' script,
the same face the firmware splash and the page header use — resolved by the
server's own typography module, so it needs data/fonts/script.ttf installed
locally), cream on a walnut rounded square — the page's one concentrated
accent, and the pair that still reads at 16 px in both a dark and a light tab
strip (a black glyph on paper did not). Rendered at 8x and downsampled so the
small sizes keep the script's hairlines.

Outputs (all under server/static/, committed — this script is a build tool):
  favicon.ico            16 / 32 / 48 px, for /favicon.ico and old browsers
  favicon-32.png         the crisp tab icon
  favicon-192.png        Android home screen / PWA-ish
  apple-touch-icon.png   180 px, iOS (which rounds the corners itself)

Run from server/:  ./.venv/bin/python scripts/make_favicon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from featherframe.render import typography  # noqa: E402

STATIC = SERVER / "static"

WALNUT = (107, 74, 44, 255)       # --accent
CREAM = (247, 239, 226, 255)      # --on-accent
RADIUS = 0.2                      # corner radius, fraction of the side
GLYPH = 0.72                      # glyph em size, fraction of the side (the script's caps fill the em)
SUPERSAMPLE = 8
FONT = typography.script_font_path()


def icon(size: int, rounded: bool = True) -> Image.Image:
    big = size * SUPERSAMPLE
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    if rounded:
        draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * RADIUS), fill=WALNUT)
    else:
        draw.rectangle([0, 0, big - 1, big - 1], fill=WALNUT)
    font = ImageFont.truetype(str(FONT), int(big * GLYPH))
    left, top, right, bottom = font.getbbox("F")
    # Centre the ink, not the advance: the script's F overhangs both sides.
    x = (big - (right - left)) / 2 - left
    y = (big - (bottom - top)) / 2 - top
    draw.text((x, y), "F", font=font, fill=CREAM)
    return im.resize((size, size), Image.LANCZOS)


def main() -> None:
    if not typography.has_script_font():
        sys.exit("data/fonts/script.ttf is not installed; the favicon must be the script F")
    STATIC.mkdir(exist_ok=True)
    icon(32).save(STATIC / "favicon-32.png")
    icon(192).save(STATIC / "favicon-192.png")
    # iOS masks its own corners and composites on black: keep it square and opaque.
    icon(180, rounded=False).convert("RGB").save(STATIC / "apple-touch-icon.png")
    sizes = [16, 32, 48]
    icon(48).save(STATIC / "favicon.ico", format="ICO", sizes=[(s, s) for s in sizes],
                  append_images=[icon(s) for s in sizes[:-1]])
    for name in ("favicon.ico", "favicon-32.png", "favicon-192.png", "apple-touch-icon.png"):
        print(f"wrote {STATIC / name} ({(STATIC / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
