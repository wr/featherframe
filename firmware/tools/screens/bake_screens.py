#!/usr/bin/env python3
"""Bake the boot + first-time-setup panel screens into firmware/src/ff_screens.h.

Renders 8 screens at the panel's native 1404x1872 using EB Garamond (from the
server) and the pen-and-ink birdhouse art in ./art, dithers each to 1-bit like the
e-paper, then PackBits-compresses them into a C header the firmware blits with
drawBitmap. Run after changing the art, copy, or layout:

    server/.venv/bin/python firmware/tools/screens/bake_screens.py [--preview]

--preview also writes contact_sheet.png next to this script for eyeballing.
"""
import os
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 8x8 ordered (Bayer) dither matrix. Ordered dithering is POSITION-stable: a given
# grey at a given (x,y) always maps to the same bit, no matter what's beside it.
# That's what lets the firmware partial-refresh — the shared birdhouse is byte-for-
# byte identical across screens, so only the bird/pill/card boxes ever change.
_BAYER8 = np.array([
    [ 0, 48, 12, 60,  3, 51, 15, 63],
    [32, 16, 44, 28, 35, 19, 47, 31],
    [ 8, 56,  4, 52, 11, 59,  7, 55],
    [40, 24, 36, 20, 43, 27, 39, 23],
    [ 2, 50, 14, 62,  1, 49, 13, 61],
    [34, 18, 46, 30, 33, 17, 45, 29],
    [10, 58,  6, 54,  9, 57,  5, 53],
    [42, 26, 38, 22, 41, 25, 37, 21],
], dtype=np.float32)

def to_1bit(im):
    """Grey L image -> 1-bit via ordered dither (deterministic per pixel)."""
    a = np.asarray(im.convert("L"), dtype=np.float32)
    h, w = a.shape
    thresh = (_BAYER8 + 0.5) / 64.0 * 255.0
    tile = np.tile(thresh, ((h + 7) // 8, (w + 7) // 8))[:h, :w]
    bw = (a > tile).astype(np.uint8) * 255      # 255 = white
    return Image.fromarray(bw, mode="L").convert("1")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ART = os.path.join(HERE, "art")
FONTS = os.path.join(REPO, "server", "featherframe", "fonts")
OUT_H = os.path.join(REPO, "firmware", "src", "ff_screens.h")

W, H = 1404, 1872
STRIDE = (W + 7) // 8
NBYTES = STRIDE * H
ROMAN = os.path.join(FONTS, "EBGaramond[wght].ttf")
ITALIC = os.path.join(FONTS, "EBGaramond-Italic[wght].ttf")

# Firmware version baked into the splash footer. Bump and re-run on release.
VERSION = "VERSION 1.0.1"
BUILD = "BUILD A1B4321"

def font(size, italic=False, weight=None):
    f = ImageFont.truetype(ITALIC if italic else ROMAN, size)
    if weight:
        try: f.set_variation_by_axes([weight])
        except Exception: pass
    return f

ARTS = {k: Image.open(os.path.join(ART, f"{k}.png")).convert("L")
        for k in ("house", "fly", "wren", "bird")}

def new_canvas():
    c = Image.new("L", (W, H), 255)
    return c, ImageDraw.Draw(c)

def paste_art(canvas, key, cx, top, target_h):
    art = ARTS[key]
    w, h = art.size
    tw = int(w * target_h / h)
    a = art.resize((tw, target_h), Image.LANCZOS)
    canvas.paste(a, (int(cx - tw / 2), int(top)), Image.eval(a, lambda p: 255 - p).convert("L"))

def paste_at(canvas, key, right, top, target_h):
    # Paste an art by its RIGHT edge + top (used for the fly-in bird, which sits
    # to the left of the house). The bird is a cut-out so the house stays identical
    # to the splash — the firmware then repaints only the little bird box.
    art = ARTS[key]
    w, h = art.size
    tw = int(w * target_h / h)
    a = art.resize((tw, target_h), Image.LANCZOS)
    canvas.paste(a, (int(right - tw), int(top)), Image.eval(a, lambda p: 255 - p).convert("L"))

def text_v(draw, x, yc, s, fnt, fill=0):
    asc, desc = fnt.getmetrics()
    draw.text((x, yc - (asc + desc) / 2), s, font=fnt, fill=fill)

def tracked(draw, cx, y, text, fnt, track, fill=0):
    widths = [draw.textlength(c, font=fnt) for c in text]
    total = sum(widths) + track * (len(text) - 1)
    x = cx - total / 2
    for c, wch in zip(text, widths):
        text_v(draw, x, y, c, fnt, fill)
        x += wch + track

def diamond(draw, cx, cy, r, fill=0):
    draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=fill)

def wifi_glyph(draw, cx, cy, s, fill=0):
    for rad in (s, s * 0.62, s * 0.30):
        draw.arc([cx - rad, cy - rad, cx + rad, cy + rad], 225, 315,
                 fill=fill, width=max(3, int(s * 0.12)))
    draw.ellipse([cx - s * 0.07, cy - s * 0.07, cx + s * 0.07, cy + s * 0.07], fill=fill)

def spinner(draw, cx, cy, r, fill=255):
    import math
    for k in range(4):
        a0 = math.radians(90 * k)
        pts = [(cx, cy)]
        for t in range(0, 60, 6):
            ang = a0 + math.radians(t)
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        draw.polygon(pts, fill=fill)

def check(draw, cx, cy, s, fill=255, width=6):
    draw.line([(cx - s * 0.5, cy), (cx - s * 0.1, cy + s * 0.45), (cx + s * 0.6, cy - s * 0.55)],
              fill=fill, width=width, joint="curve")

WORDMARK = "FEATHERFRAME"

def draw_wordmark(draw, y):
    tracked(draw, W / 2, y, WORDMARK, font(96, weight=520), track=26, fill=0)

# Shared placement for the splash + boot states, so only the bottom strip (footer
# vs pill) changes between them — that lets the firmware partial-refresh just that
# band and leave the birdhouse untouched (no flash).
BOOT_ART_TOP, BOOT_ART_H, BOOT_WORDMARK_Y = 220, 770, 1185

def screen_splash():
    c, d = new_canvas()
    paste_art(c, "house", W / 2, BOOT_ART_TOP, BOOT_ART_H)
    draw_wordmark(d, BOOT_WORDMARK_Y)
    fitn = font(40, italic=True)
    lw = d.textlength(VERSION, font=fitn); rw = d.textlength(BUILD, font=fitn)
    gap = 120; total = lw + gap + rw; x0 = W / 2 - total / 2
    text_v(d, x0, 1740, VERSION, fitn); diamond(d, x0 + lw + gap / 2, 1740, 9)
    text_v(d, x0 + lw + gap, 1740, BUILD, fitn)
    return c

def screen_boot(text, bird=False, home=False):
    c, d = new_canvas()
    # "home" swaps to the wren-in-the-hole drawing (the bird has arrived); otherwise
    # the same empty house as the splash, optionally with the wren flying in.
    paste_art(c, "wren" if home else "house", W / 2, BOOT_ART_TOP, BOOT_ART_H)
    if bird:
        paste_at(c, "bird", 470, 300, 330)                  # flies in from the left
    draw_wordmark(d, BOOT_WORDMARK_Y)
    fnt = font(50, italic=True)
    tw = d.textlength(text, font=fnt)
    padx, icon = 60, 70
    pillw = tw + padx * 2 + icon; pillh = 116
    px = (W - pillw) / 2; py = 1560
    d.rounded_rectangle([px, py, px + pillw, py + pillh], radius=pillh / 2, fill=0)
    spinner(d, px + padx + icon * 0.4, py + pillh / 2, 26)
    text_v(d, px + padx + icon + 6, py + pillh / 2, text, fnt, fill=255)
    return c

def screen_setup():
    c, d = new_canvas()
    paste_art(c, "house", W / 2, 150, 660)
    x0, y0, x1, y1 = 150, 1140, W - 150, 1740
    d.rounded_rectangle([x0, y0, x1, y1], radius=24, fill=0)
    steps = [
        "From your computer or smartphone,\njoin the wi-fi hotspot:",
        "Choose a wi-fi network for Featherframe\nto join.",
        "Fill in the IP address of your BirdNET\ndevice, if not auto-detected.",
    ]
    fnt = font(46); numf = font(34, weight=600)
    y = y0 + 70
    for i, s in enumerate(steps, 1):
        d.ellipse([x0 + 50, y - 4, x0 + 50 + 52, y + 48], fill=255)
        d.text((x0 + 50 + 26, y + 22), str(i), font=numf, fill=0, anchor="mm")
        d.multiline_text((x0 + 140, y - 4), s, font=fnt, fill=255, spacing=12)
        if i == 1:
            chipf = font(46, weight=600); ct = "Featherframe-Setup"
            ctw = d.textlength(ct, font=chipf); chy = y + 96; iconw = 64
            chw = iconw + ctw + 60
            d.rounded_rectangle([x0 + 140, chy, x0 + 140 + chw, chy + 76], radius=14, fill=255)
            wifi_glyph(d, x0 + 140 + 40, chy + 42, 22)
            d.text((x0 + 140 + iconw + 18, chy + 38), ct, font=chipf, fill=0, anchor="lm")
            y = chy + 128
        else:
            y += 150
    return c

def screen_check(states, bird=False, home=False):
    c, d = new_canvas()
    paste_art(c, "wren" if home else "house", W / 2, 150, 660)
    if bird:
        paste_at(c, "bird", 500, 250, 280)
    rows = ["Connecting to wi-fi...", "Connecting to BirdNET...", "Downloading image..."]
    x0, y0, x1 = 260, 1300, W - 260
    rowh = 130; y1 = y0 + rowh * len(rows) + 60
    d.rounded_rectangle([x0, y0, x1, y1], radius=24, fill=0)
    fnt = font(48); y = y0 + 90
    for r, st in zip(rows, states):
        ix = x0 + 90
        if st == "done": check(d, ix, y, 34)
        elif st == "now": spinner(d, ix, y, 26)
        text_v(d, x0 + 170, y, r, fnt, fill=255 if st != "pending" else 150)
        y += rowh
    return c

# (enum name, image) — order defines FF_SCR_* indices
SCREENS = [
    ("SPLASH",        screen_splash()),
    ("BOOT_WIFI",     screen_boot("Connecting to Wi-Fi...", bird=True)),
    ("BOOT_BIRDNET",  screen_boot("Connecting to BirdNET...", home=True)),
    ("BOOT_DOWNLOAD", screen_boot("Downloading image...", home=True)),
    ("SETUP",         screen_setup()),
    ("CHK1",          screen_check(["now", "pending", "pending"], bird=True)),
    ("CHK2",          screen_check(["done", "now", "pending"], home=True)),
    ("CHK3",          screen_check(["done", "done", "now"], home=True)),
]

def packbits(data: bytes) -> bytes:
    out = bytearray(); i = 0; n = len(data)
    while i < n:
        run = 1
        while i + run < n and data[i + run] == data[i] and run < 128:
            run += 1
        if run >= 2:
            out.append(257 - run); out.append(data[i]); i += run
        else:
            j = i; lit = bytearray()
            while j < n and len(lit) < 128:
                r = 1
                while j + r < n and data[j + r] == data[j] and r < 3:
                    r += 1
                if r >= 2:
                    break
                lit.append(data[j]); j += 1
            out.append(len(lit) - 1); out.extend(lit); i = j
    return bytes(out)

# Native panel orientation (the panel canvas is fixed landscape and can't rotate
# itself, so we rotate into native like the server pipeline does: np.rot90 CCW by
# panel_rotation/90). 4bpp: 2 px/byte, high nibble = left, 0=black 15=white.
NATIVE_W, NATIVE_H = H, W                 # 1872 x 1404
GRAY_BYTES = NATIVE_W * NATIVE_H // 2     # 1,314,144
PANEL_ROTATION = 90

def packed(im: Image.Image) -> bytes:
    a = np.asarray(im.convert("L"), dtype=np.uint8)          # portrait 1404x1872
    idx = (a.astype(np.uint16) * 15 // 255).astype(np.uint8)  # 16 levels, 0..15
    native = np.rot90(idx, k=(PANEL_ROTATION // 90) % 4)      # -> [1404, 1872]
    hi = native[:, 0::2].astype(np.uint8) << 4
    lo = native[:, 1::2].astype(np.uint8)
    body = (hi | lo).tobytes()
    assert len(body) == GRAY_BYTES, len(body)
    return packbits(body)

def write_header():
    L = ["// GENERATED by firmware/tools/screens/bake_screens.py — do not edit by hand.",
         "// Boot + first-time-setup panel screens, baked as 16-level gray in the",
         "// panel's native 1872x1404 orientation (4bpp), pushed via the same gray",
         "// path as the bird plates — the panel's 1-bit path can't render the full",
         "// width, so these use gray. PackBits-compressed; ff_unpack expands one.",
         "#pragma once", "#include <stdint.h>", "",
         f"#define FF_NATIVE_W       {NATIVE_W}", f"#define FF_NATIVE_H       {NATIVE_H}",
         f"#define FF_SCREEN_BYTES   {GRAY_BYTES}   // decoded 4bpp body, per screen", "",
         "enum FfScreen {"]
    for i, (name, _) in enumerate(SCREENS):
        L.append(f"  FF_SCR_{name} = {i},")
    L += [f"  FF_SCR_COUNT = {len(SCREENS)},", "};", ""]
    refs = []
    for name, im in SCREENS:
        pb = packed(im); arr = f"ff_scr_{name.lower()}"; refs.append((arr, len(pb)))
        L.append(f"// {name}: {len(pb)} bytes packed")
        L.append(f"static const uint8_t {arr}[] = {{")
        row = "  "
        for b in pb:
            row += f"{b},"
            if len(row) >= 116:
                L.append(row); row = "  "
        if row.strip():
            L.append(row)
        L += ["};", ""]
    L += ["struct FfScreenAsset { const uint8_t* data; uint32_t len; };",
          "static const FfScreenAsset ff_screens[FF_SCR_COUNT] = {"]
    for arr, ln in refs:
        L.append(f"  {{ {arr}, {ln} }},")
    L += ["};", "",
          "// PackBits decode into a caller buffer of FF_SCREEN_BYTES. Returns bytes written.",
          "static inline uint32_t ff_unpack(const uint8_t* src, uint32_t len, uint8_t* dst) {",
          "  uint32_t si = 0, di = 0;",
          "  while (si < len && di < FF_SCREEN_BYTES) {",
          "    int8_t n = (int8_t)src[si++];",
          "    if (n >= 0) {                       // 1+n literal bytes",
          "      for (int k = 0; k <= n && di < FF_SCREEN_BYTES && si < len; k++) dst[di++] = src[si++];",
          "    } else if (n != -128) {             // 1-n repeats of one byte",
          "      uint8_t v = src[si++];",
          "      for (int k = 0; k < 1 - n && di < FF_SCREEN_BYTES; k++) dst[di++] = v;",
          "    }",
          "  }",
          "  return di;",
          "}", ""]
    with open(OUT_H, "w") as f:
        f.write("\n".join(L))
    total = sum(ln for _, ln in refs)
    print(f"wrote {OUT_H}: {len(SCREENS)} gray screens, {total/1024:.0f}K packed")

def write_preview():
    cols, rows, pad, tw = 4, 2, 30, 520
    th = int(tw * H / W)
    sheet = Image.new("L", (cols * tw + (cols + 1) * pad, rows * (th + 70) + pad), 235)
    sd = ImageDraw.Draw(sheet)
    for idx, (name, im) in enumerate(SCREENS):
        one = to_1bit(im).convert("L").resize((tw, th), Image.LANCZOS)
        r, cc = divmod(idx, cols)
        x = pad + cc * (tw + pad); y = pad + r * (th + 70)
        sheet.paste(one, (x, y)); sd.rectangle([x, y, x + tw, y + th], outline=0)
        sd.text((x + 6, y + th + 8), f"{idx} {name}", font=font(30, weight=600), fill=0)
    out = os.path.join(HERE, "contact_sheet.png"); sheet.save(out)
    print("wrote", out)

if __name__ == "__main__":
    write_header()
    if "--preview" in sys.argv:
        write_preview()
