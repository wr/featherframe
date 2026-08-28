#!/usr/bin/env python3
"""Bake the boot + first-time-setup panel screens into firmware/src/ff_screens.h.

Renders 8 screens at the panel's native 1404x1872 and PackBits-compresses them
into a C header the firmware blits. The boot screens compose the designer's
pen-and-ink birdhouse art (./art) with type set by the SERVER's own typography
module — the wordmark is the plate title style (swash italic, theme.TITLE_SIZE)
and the version line is the plates' engraved capitals — so the boot face always
matches the plates. Pills use Inter (./fonts) for legibility at a glance.
Run after changing the art, copy, or layout:

    server/.venv/bin/python firmware/tools/screens/bake_screens.py [--preview]

--preview also writes contact_sheet.png next to this script for eyeballing.
"""
import os
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))

from featherframe.render import theme, typography  # noqa: E402  (needs sys.path)

# The panel's 16 gray levels render lighter than the source, so darken the midtones
# before quantizing (gamma > 1). This MUST be a fixed, content-independent curve —
# a mean-based contrast (ImageEnhance) maps the identical birdhouse to different
# values per screen, which would make the whole house diff and flash. Tune on-panel.
WHITE_PT = 246      # anything this light snaps to pure white (the v2 art's ~253 bg
                    # would otherwise quantize to a faint gray box behind the house)
GRAY_GAMMA = 1.4    # >1 darkens the midtones the panel renders too light
def _build_lut():
    lut = []
    for i in range(256):
        v = min(255.0, i * 255.0 / WHITE_PT)      # white-point stretch
        v = (v / 255.0) ** GRAY_GAMMA * 255.0      # darken midtones
        lut.append(min(255, round(v)))
    return lut
_GAMMA_LUT = _build_lut()

def apply_curve(im):
    return im.convert("L").point(_GAMMA_LUT)

# 8x8 ordered (Bayer) dither, POSITION-stable: a given grey at a given (x,y)
# always maps to the same bit no matter what's beside it — so a dithered box
# stays byte-identical wherever the screens share content. Used only where a
# box must be binary (=> DU, no flash) but the art is tonal, like the wren.
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

def to_1bit(im, phase=(0, 0)):
    """Grey L image -> 0/255 via ordered dither. `phase` = the crop's top-left
    in canvas coords, so a cropped region dithers exactly as it would in situ."""
    a = np.asarray(im.convert("L"), dtype=np.float32)
    h, w = a.shape
    thresh = (_BAYER8 + 0.5) / 64.0 * 255.0
    ty, tx = phase[1] % 8, phase[0] % 8
    tile = np.tile(thresh, ((h + 15) // 8, (w + 15) // 8))[ty:ty + h, tx:tx + w]
    bw = (a > tile).astype(np.uint8) * 255      # 255 = white
    return Image.fromarray(bw, mode="L")

ART = os.path.join(HERE, "art")
FONTS = os.path.join(REPO, "server", "featherframe", "fonts")
OUT_H = os.path.join(REPO, "firmware", "src", "ff_screens.h")

W, H = 1404, 1872
ROMAN = os.path.join(FONTS, "EBGaramond[wght].ttf")
ITALIC = os.path.join(FONTS, "EBGaramond-Italic[wght].ttf")
SANS = os.path.join(HERE, "fonts", "Inter-Medium.otf")

# Firmware version baked into the splash footer. Bump and re-run on release.
VERSION = "VERSION 1.0.1"
BUILD = "BUILD A1B4321"

def font(size, italic=False, weight=None):
    f = ImageFont.truetype(ITALIC if italic else ROMAN, size)
    if weight:
        try: f.set_variation_by_axes([weight])
        except Exception: pass
    return f

def sans(size):
    return ImageFont.truetype(SANS, size)

def text_v(draw, x, yc, s, fnt, fill=0):
    asc, desc = fnt.getmetrics()
    draw.text((x, yc - (asc + desc) / 2), s, font=fnt, fill=fill)

def diamond(draw, cx, cy, r, fill=0):
    draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=fill)

def wifi_glyph(draw, cx, cy, s, fill=0):
    for rad in (s, s * 0.62, s * 0.30):
        draw.arc([cx - rad, cy - rad, cx + rad, cy + rad], 225, 315,
                 fill=fill, width=max(3, int(s * 0.12)))
    draw.ellipse([cx - s * 0.07, cy - s * 0.07, cx + s * 0.07, cy + s * 0.07], fill=fill)

def check(draw, cx, cy, s, fill=255, width=6):
    draw.line([(cx - s * 0.5, cy), (cx - s * 0.1, cy + s * 0.45), (cx + s * 0.6, cy - s * 0.55)],
              fill=fill, width=width, joint="curve")

# -- the loading mark --------------------------------------------------------
# Three small diamonds (the splash footer's separator motif): the active one is
# solid, the others hollow rings. The firmware sweeps the solid diamond left to
# right as a fast DU partial (pure black/white, so no flash) — motion, not a
# blink, is what reads as "loading, not stuck" on a panel this slow. Frame k of
# FF_LOADER_FRAMES lights diamond k; the baked screens carry frame 0.
LOADER_R = 9          # half-height of one diamond
LOADER_PITCH = 30     # diamond center-to-center
LOADER_SLOT_W = LOADER_PITCH * 2 + LOADER_R * 2
LOADER_FRAMES = 3

def draw_loader_mark(draw, cx, cy, frame=0):
    for k in range(3):
        x = cx + (k - 1) * LOADER_PITCH
        diamond(draw, x, cy, LOADER_R, fill=255)
        if k != frame:
            diamond(draw, x, cy, LOADER_R - 3.5, fill=0)

ARTS = {k: Image.open(os.path.join(ART, f"{k}.png")).convert("L")
        for k in ("house", "fly", "wren", "bird", "wren_hole")}

def new_canvas():
    c = Image.new("L", (W, H), 255)
    return c, ImageDraw.Draw(c)

def paste_art(canvas, key, cx, top, target_h):
    art = ARTS[key]
    w, h = art.size
    tw = int(w * target_h / h)
    a = art.resize((tw, target_h), Image.LANCZOS)
    canvas.paste(a, (int(cx - tw / 2), int(top)), Image.eval(a, lambda p: 255 - p).convert("L"))

def paste_wren_hole(canvas, cx, top, target_h):
    # Overlay the wren-in-the-hole onto the (already-pasted) house, scaled/positioned
    # to match. house.png is the reference frame for WREN_HOLE_AT.
    house = ARTS["house"]
    scale = target_h / house.height
    house_left = cx - (house.width * scale) / 2
    a = ARTS["wren_hole"]
    tw = int(a.width * scale); th = int(a.height * scale)
    a = a.resize((tw, th), Image.LANCZOS)
    x = int(house_left + WREN_HOLE_AT[0] * scale)
    y = int(top + WREN_HOLE_AT[1] * scale)
    canvas.paste(a, (x, y), Image.eval(a, lambda p: 255 - p).convert("L"))

def paste_at(canvas, key, right, top, target_h):
    art = ARTS[key]
    w, h = art.size
    tw = int(w * target_h / h)
    a = art.resize((tw, target_h), Image.LANCZOS)
    canvas.paste(a, (int(right - tw), int(top)), Image.eval(a, lambda p: 255 - p).convert("L"))

# wren_hole is a cut-out of the wren peeking from the entrance, aligned to house.png's
# hole (both drawings share the hole position), so "arrived" screens keep the exact
# same house and only the little hole box changes.
WREN_HOLE_AT = (145, 235)          # top-left in house.png pixel space (670x990)

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

def screen_check(name, states):
    c, d = new_canvas()
    paste_art(c, "house", W / 2, 150, 660)
    rows = ["Connecting to wi-fi...", "Connecting to BirdNET...", "Downloading image..."]
    x0, y0, x1 = 260, 1300, W - 260
    rowh = 130; y1 = y0 + rowh * len(rows) + 60
    d.rounded_rectangle([x0, y0, x1, y1], radius=24, fill=0)
    fnt = font(48); y = y0 + 90
    for r, st in zip(rows, states):
        ix = x0 + 90
        if st == "done":
            check(d, ix, y, 34)
        elif st == "now":
            draw_loader_mark(d, ix, y)
            LOADER_AT[name] = (ix, y)
        text_v(d, x0 + 170, y, r, fnt, fill=255 if st != "pending" else 150)
        y += rowh
    return c

# The four boot/loading screens compose the designer's clean pen-and-ink line art
# (black hatching on transparent) at native size — do NOT rescale birdhouse.png,
# it is 1402 wide by design.
HOUSE = Image.open(os.path.join(ART, "birdhouse.png")).convert("RGBA")
FLY   = Image.open(os.path.join(ART, "birdfly.png")).convert("RGBA")
PEEK  = Image.open(os.path.join(ART, "birdpeek.png")).convert("RGBA")

# Layout (portrait 1404x1872). birdhouse.png (1402x1122) sits full width near the
# top; its entrance hole is at (600,390) in the PNG, so the wren-in-hole lands at
# HOUSE_XY + that. House + wordmark are identical on every screen, so only the
# bird/wren/pill boxes ever repaint. House/bird/wren placements are the designer's,
# read off the reference frames in Desktop/stils (Frame 12-15.svg).
HOUSE_XY = (1, 200)
FLY_XY   = (1, 403)
PEEK_XY  = (501, 546)

# The wordmark is the plate title, verbatim: EB Garamond swash italic at
# theme.TITLE_SIZE with the v3 weight/tracking, drawn by the server's own
# draw_title so any future plate-title change re-bakes into the boot face.
# The baseline keeps the descenders (the swash f's) well clear of the pill.
WORDMARK_BASELINE = 1534
# Splash footer: a hedera between the wordmark and the version line (the same
# ornament the plates' date line uses), then the version in the plates'
# engraved capitals (Adorn Engraved, theme.SUBTITLE_SIZE).
HEDERA_BASELINE  = 1632
VERSION_BASELINE = 1718
VERSION_GAP = 110          # gap between VERSION and BUILD, diamond in the middle

def draw_wordmark(im):
    d = ImageDraw.Draw(im)
    f = font(theme.TITLE_SIZE, italic=True, weight=theme.TITLE_WEIGHT)
    typography.draw_title(d, W / 2, WORDMARK_BASELINE, "Featherframe", f, 0,
                          theme.TITLE_SIZE * theme.TITLE_TRACKING)

def draw_version(im):
    d = ImageDraw.Draw(im)
    d.text((W / 2, HEDERA_BASELINE), theme.DATE_ORNAMENT,
           font=font(36, italic=True, weight=500), fill=0, anchor="ms")
    size = theme.SUBTITLE_SIZE
    lw = typography.engraved_width(VERSION, size)
    rw = typography.engraved_width(BUILD, size)
    x0 = W / 2 - (lw + VERSION_GAP + rw) / 2
    typography.draw_engraved(d, x0 + lw / 2, VERSION_BASELINE, VERSION, size, 0)
    typography.draw_engraved(d, x0 + lw + VERSION_GAP + rw / 2, VERSION_BASELINE,
                             BUILD, size, 0)
    cap = size * theme.ENGRAVED_CAP
    diamond(d, x0 + lw + VERSION_GAP / 2, VERSION_BASELINE - cap / 2, 7)

PILL_TEXT = {
    "wifi":     "Connecting to Wi-Fi…",
    "birdnet":  "Connecting to BirdNET…",
    "download": "Downloading image…",
}

# Pill geometry: y=1648, height 82, centered, rounded (rx = h/2). The loading
# mark sits in a slot at the left, the text after it — a plain sans (Inter
# Medium) for at-a-glance readability against all the Garamond around it.
# Drawn pure black/white so the window refreshes flash-less with DU.
PILL_Y, PILL_H, PILL_PAD, PILL_GAP = 1648, 82, 30, 22
PILL_TEXT_SIZE = 38

def draw_pill(im, text):
    # Returns the loading mark's portrait center so the bake can emit its
    # native panel coords for the firmware to animate (see FfLoader).
    d = ImageDraw.Draw(im)
    fnt = sans(PILL_TEXT_SIZE)
    tw = d.textlength(text, font=fnt)
    pillw = int(PILL_PAD + LOADER_SLOT_W + PILL_GAP + tw + PILL_PAD + 4)
    px = int(W / 2 - pillw / 2)
    cy = PILL_Y + PILL_H / 2
    d.rounded_rectangle([px, PILL_Y, px + pillw, PILL_Y + PILL_H], radius=PILL_H / 2, fill=0)
    lcx = px + PILL_PAD + LOADER_SLOT_W / 2
    draw_loader_mark(d, lcx, cy)
    # Optically center the text on its cap height (metric centering sits low —
    # Inter's tall ascent/descent box isn't where the ink is).
    capbox = fnt.getbbox("H")
    baseline = cy + (capbox[3] - capbox[1]) / 2
    d.text((px + PILL_PAD + LOADER_SLOT_W + PILL_GAP, baseline), text,
           font=fnt, fill=255, anchor="ls")
    return (lcx, cy)

LOADER_AT = {}      # screen name -> loading mark's portrait center (cx, cy)

def _compose(name):
    c = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    c.alpha_composite(HOUSE, HOUSE_XY)
    if name == "wifi":
        c.alpha_composite(FLY, FLY_XY)                 # bird flies in
    elif name == "download":
        c.alpha_composite(PEEK, PEEK_XY)               # wren in the hole
    im = c.convert("L")
    if name == "wifi":
        # Threshold the fly-in bird's box to pure black/white (it is line art on
        # empty sky — nothing else lives in the box). A binary window refreshes
        # with DU both coming and going, so the bird appears and leaves without
        # the GC16 white-black-white flash.
        x0, y0 = FLY_XY; x1, y1 = x0 + FLY.width, y0 + FLY.height
        box = im.crop((x0, y0, x1, y1)).point(lambda p: 0 if p < 176 else 255)
        im.paste(box, (x0, y0))
    elif name == "download":
        # The wren is tonal (its light feathers threshold into a black blob),
        # so its box goes binary by ordered dither instead — the gamma curve
        # first, so the stipple density matches the gray it replaces.
        x0, y0 = PEEK_XY; x1, y1 = x0 + PEEK.width, y0 + PEEK.height
        box = to_1bit(apply_curve(im.crop((x0, y0, x1, y1))), phase=(x0, y0))
        im.paste(box, (x0, y0))
    draw_wordmark(im)
    if name == "splash":
        draw_version(im)
    else:
        LOADER_AT[name] = draw_pill(im, PILL_TEXT[name])
    # Keep the art in 16-level gray (packed() applies the panel gamma + quantize):
    # smooth grayscale reads far better than a hard threshold. The bird box above
    # and the pills are the exception — binary on purpose, so their windows take
    # the non-flashing DU waveform. House + wordmark are identical across screens,
    # so only the bird/wren/pill boxes ever repaint.
    return im

SCREENS = [
    ("SPLASH",        _compose("splash")),
    ("BOOT_WIFI",     _compose("wifi")),        # wren flies in
    ("BOOT_BIRDNET",  _compose("birdnet")),     # empty house
    ("BOOT_DOWNLOAD", _compose("download")),    # wren in the hole
    ("SETUP",         screen_setup()),
    ("CHK1",          screen_check("CHK1", ["now", "pending", "pending"])),
    ("CHK2",          screen_check("CHK2", ["done", "now", "pending"])),
    ("CHK3",          screen_check("CHK3", ["done", "done", "now"])),
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

def _to_native_nibbles(im: Image.Image) -> np.ndarray:
    a = np.asarray(apply_curve(im), dtype=np.uint8)
    idx = (a.astype(np.uint16) * 15 // 255).astype(np.uint8)  # 16 levels, 0..15
    return np.rot90(idx, k=(PANEL_ROTATION // 90) % 4)

def _pack_nibbles(native: np.ndarray) -> bytes:
    hi = native[:, 0::2].astype(np.uint8) << 4
    lo = native[:, 1::2].astype(np.uint8)
    return (hi | lo).tobytes()

def packed(im: Image.Image) -> bytes:
    body = _pack_nibbles(_to_native_nibbles(im))
    assert len(body) == GRAY_BYTES, len(body)
    return packbits(body)

# Loader animation tiles. For each screen with a loading mark, cut a fixed-size
# window around the mark out of the baked screen and re-draw it once per frame
# (the sweep position changes, everything else in the tile — the black pill —
# stays). The firmware pushes one tile per step as a windowed DU update. Tile
# coords go through the SAME rot90 as packed() and the SAME X-mirror the
# firmware's showScreen() uses (mx = FF_NATIVE_W - nx - nw); portrait-8-aligned
# corners stay 8-aligned through both (all dims are multiples of 8).
TILE_W, TILE_H = 112, 40      # portrait px, multiples of 8

def loader_tiles(im: Image.Image, cx, cy):
    px0 = (int(cx) - TILE_W // 2) & ~7
    py0 = (int(cy) - TILE_H // 2) & ~7
    tiles = []
    for k in range(LOADER_FRAMES):
        t = im.crop((px0, py0, px0 + TILE_W, py0 + TILE_H)).copy()
        d = ImageDraw.Draw(t)
        mx, my = cx - px0, cy - py0
        d.rectangle([mx - LOADER_SLOT_W / 2 - 2, my - LOADER_R - 2,
                     mx + LOADER_SLOT_W / 2 + 2, my + LOADER_R + 2], fill=0)
        draw_loader_mark(d, mx, my, frame=k)
        tiles.append(_pack_nibbles(_to_native_nibbles(t)))
    # Portrait -> native -> mirrored native, as in showScreen().
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[py0:py0 + TILE_H, px0:px0 + TILE_W] = 1
    nat = np.rot90(mask, k=(PANEL_ROTATION // 90) % 4)
    rows, cols = np.any(nat, axis=1), np.any(nat, axis=0)
    ny0 = int(np.argmax(rows)); nx0 = int(np.argmax(cols))
    nw, nh = TILE_H, TILE_W                      # portrait w/h swap under rot90
    mx0 = NATIVE_W - nx0 - nw                    # firmware mirrors X
    return (mx0, ny0), tiles

def write_header():
    screens = {name: im for name, im in SCREENS}
    loaders = {name: loader_tiles(screens[name], cx, cy)
               for name, (cx, cy) in
               (("BOOT_WIFI", LOADER_AT["wifi"]),
                ("BOOT_BIRDNET", LOADER_AT["birdnet"]),
                ("BOOT_DOWNLOAD", LOADER_AT["download"]),
                ("CHK1", LOADER_AT["CHK1"]),
                ("CHK2", LOADER_AT["CHK2"]),
                ("CHK3", LOADER_AT["CHK3"]))}
    L = ["// GENERATED by firmware/tools/screens/bake_screens.py — do not edit by hand.",
         "// Boot + first-time-setup panel screens, baked as 16-level gray in the",
         "// panel's native 1872x1404 orientation (4bpp), pushed via the same gray",
         "// path as the bird plates — the panel's 1-bit path can't render the full",
         "// width, so these use gray. PackBits-compressed; ff_unpack expands one.",
         "#pragma once", "#include <stdint.h>", "",
         f"#define FF_NATIVE_W       {NATIVE_W}", f"#define FF_NATIVE_H       {NATIVE_H}",
         f"#define FF_SCREEN_BYTES   {GRAY_BYTES}   // decoded 4bpp body, per screen", "",
         "// Loading-mark animation: per-screen window tiles in native mirrored",
         "// panel coords (see loader_tiles). The firmware sweeps the frames as",
         "// fast DU partials while it connects/downloads — the tiles are pure",
         "// black/white so the sweep never flashes. Frame 0 == the baked screen.",
         f"#define FF_LOADER_FRAMES  {LOADER_FRAMES}",
         f"#define FF_LOADER_NW      {TILE_H}   // native px (portrait h)",
         f"#define FF_LOADER_NH      {TILE_W}   // native px (portrait w)",
         "#define FF_LOADER_BYTES   (FF_LOADER_NW / 2 * FF_LOADER_NH)", "",
         "enum FfScreen {"]
    for i, (name, _) in enumerate(SCREENS):
        L.append(f"  FF_SCR_{name} = {i},")
    L += [f"  FF_SCR_COUNT = {len(SCREENS)},", "};", ""]

    def emit_array(arr_name, data, comment=None):
        if comment:
            L.append(f"// {comment}")
        L.append(f"static const uint8_t {arr_name}[] = {{")
        row = "  "
        for b in data:
            row += f"{b},"
            if len(row) >= 116:
                L.append(row); row = "  "
        if row.strip():
            L.append(row)
        L.extend(["};", ""])

    refs = []
    for name, im in SCREENS:
        pb = packed(im); arr = f"ff_scr_{name.lower()}"; refs.append((arr, len(pb)))
        emit_array(arr, pb, f"{name}: {len(pb)} bytes packed")
    for name, (_, tiles) in loaders.items():
        for k, t in enumerate(tiles):
            emit_array(f"ff_ldr_{name.lower()}_{k}", t)
    L += ["struct FfScreenAsset { const uint8_t* data; uint32_t len; };",
          "static const FfScreenAsset ff_screens[FF_SCR_COUNT] = {"]
    for arr, ln in refs:
        L.append(f"  {{ {arr}, {ln} }},")
    L += ["};", "",
          "// Loading-mark window per screen (x,y = native mirrored top-left;",
          "// x < 0 = this screen has no animated mark).",
          "struct FfLoader { int16_t x, y; const uint8_t* frames[FF_LOADER_FRAMES]; };",
          "static const FfLoader ff_loader[FF_SCR_COUNT] = {"]
    for name, _ in SCREENS:
        if name in loaders:
            (x, y), _tiles = loaders[name]
            fr = ", ".join(f"ff_ldr_{name.lower()}_{k}" for k in range(LOADER_FRAMES))
            L.append(f"  {{ {x}, {y}, {{ {fr} }} }},")
        else:
            L.append("  { -1, -1, { 0 } },")
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
    print(f"wrote {OUT_H}: {len(SCREENS)} gray screens + "
          f"{len(loaders)}x{LOADER_FRAMES} loader tiles, {total/1024:.0f}K packed")

def write_preview():
    cols, rows, pad, tw = 4, 2, 30, 520
    th = int(tw * H / W)
    sheet = Image.new("L", (cols * tw + (cols + 1) * pad, rows * (th + 70) + pad), 235)
    sd = ImageDraw.Draw(sheet)
    for idx, (name, im) in enumerate(SCREENS):
        g = apply_curve(im)
        g = g.point(lambda p: (p * 15 // 255) * 255 // 15)   # simulate 16 gray levels
        one = g.resize((tw, th), Image.LANCZOS)
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
