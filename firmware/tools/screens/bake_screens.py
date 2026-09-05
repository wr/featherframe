#!/usr/bin/env python3
"""Bake the boot + first-time-setup panel screens into firmware/src/ff_screens.h.

Renders 5 screens at the panel's native 1404x1872 and PackBits-compresses them
into a C header the firmware blits. The boot screens compose the bough-and-wren
art (./art/plate_*.png — drawn through the plates' own image pipeline by
boot_art.py, in the Havell manner) with type set by the SERVER's own typography
module — the wordmark is the plate title style (the bundled script at the
plates' auto-fit title size) and the version line is the plates' engraved
capitals — so the boot face always matches the plates. Pills use Inter
(./fonts) for legibility at a glance.
Run after changing the art, copy, or layout:

    server/.venv/bin/python firmware/tools/screens/bake_screens.py [--preview]

--preview also writes contact_sheet.png next to this script for eyeballing.
"""
import json
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
VERSION = "v 1.0.1"

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

def slash(draw, cx, cy, s):
    # white casing under the black stroke so the slash reads over any glyph
    draw.line([(cx - s, cy + s), (cx + s, cy - s)], fill=255, width=14)
    draw.line([(cx - s, cy + s), (cx + s, cy - s)], fill=0, width=6)

def wifi_slash(draw, cx, cy, s):
    wifi_glyph(draw, cx, cy + s * 0.55, s, fill=0)
    slash(draw, cx, cy, s * 0.78)

def cloud_slash(draw, cx, cy, s):
    # A solid cloud (union of lobes over a flat base) reads at small sizes
    # where an outlined rack of boxes turns to mush.
    lobes = [(cx - 0.52 * s, cy + 0.10 * s, 0.38 * s),
             (cx + 0.02 * s, cy - 0.18 * s, 0.52 * s),
             (cx + 0.55 * s, cy + 0.12 * s, 0.36 * s)]
    for (x, y, r) in lobes:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=0)
    draw.rounded_rectangle([cx - 0.72 * s, cy + 0.05 * s, cx + 0.80 * s, cy + 0.48 * s],
                           radius=int(0.2 * s), fill=0)
    slash(draw, cx, cy, s * 0.95)

def new_canvas():
    c = Image.new("L", (W, H), 255)
    return c, ImageDraw.Draw(c)

def screen_setup():
    # First-run instructions. Shares the splash birdhouse (smaller) and hands
    # over to the normal boot flow once a network is saved — there is no
    # separate onboarding checklist. The card fits its widest line.
    # Balanced inside the mat's visible window (~75..1797): even margins above
    # the house and below the card, instead of both hugging the mat edges.
    c = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    # The plate art, reduced to the band above the card (it is nearly
    # square, so a height fit leaves paper at the sides).
    sh = 800
    sw = int(BASE.width * sh / BASE.height)
    c.alpha_composite(BASE.resize((sw, sh), Image.LANCZOS), ((W - sw) // 2, 110))
    im = c.convert("L")
    d = ImageDraw.Draw(im)
    steps = [
        "From your computer or smartphone,\njoin the wi-fi hotspot:",
        "Choose a wi-fi network for Featherframe\nto join.",
        "Fill in the IP address of your BirdNET\ndevice, if not auto-detected.",
    ]
    fnt = font(46, weight=600)   # semibold: reversed type on e-ink loses weight
    numf = font(34, weight=600)
    maxw = max(d.textlength(ln, font=fnt) for s in steps for ln in s.split("\n"))
    cardw = int(140 + maxw + 64)
    x0 = (W - cardw) // 2
    y0, y1 = 966, 1682
    d.rounded_rectangle([x0, y0, x0 + cardw, y1], radius=24, fill=0)
    y = y0 + 68
    R = 26
    for i, s in enumerate(steps, 1):
        # number dot centered on the whole step's text block
        bb = d.multiline_textbbox((x0 + 140, y - 4), s, font=fnt, spacing=14)
        cyc = (bb[1] + bb[3]) / 2
        d.ellipse([x0 + 50, cyc - R, x0 + 50 + 2 * R, cyc + R], fill=255)
        d.text((x0 + 50 + R, cyc), str(i), font=numf, fill=0, anchor="mm")
        d.multiline_text((x0 + 140, y - 4), s, font=fnt, fill=255, spacing=14)
        if i == 1:
            chipf = sans(33); ct = "Featherframe-Setup"
            ctw = d.textlength(ct, font=chipf); chy = y + 124; chh = 76; iconw = 60
            chw = iconw + ctw + 58
            d.rounded_rectangle([x0 + 140, chy, x0 + 140 + chw, chy + chh], radius=chh / 2, fill=255)
            wifi_glyph(d, x0 + 140 + 40, chy + chh / 2 + 7, 18)
            cb = chipf.getbbox("H")
            d.text((x0 + 140 + iconw + 12, chy + chh / 2 + (cb[3] - cb[1]) / 2), ct,
                   font=chipf, fill=0, anchor="ls")
            y = chy + chh + 64
        else:
            y += 154
    return im

# The four boot/loading screens compose the Audubon-manner bough from
# boot_art.py at native size — the cut step already resampled the sheets to
# screen scale, and the perch patch only lands on its twig pixel-for-pixel if
# nothing here rescales. plate_layout.json carries where the flying and the
# perched wren sit, in plate_base.png pixel space.
BASE  = Image.open(os.path.join(ART, "plate_base.png")).convert("RGBA")
FLY   = Image.open(os.path.join(ART, "plate_fly.png")).convert("RGBA")
PERCH = Image.open(os.path.join(ART, "plate_perch.png")).convert("RGBA")
with open(os.path.join(ART, "plate_layout.json")) as _f:
    LAYOUT = json.load(_f)

# Layout (portrait 1404x1872): the plate's own. The art is full-bleed from
# the top of the panel to the caption gap above the wordmark, exactly the
# art box a single-species plate gets (compose.render_single with no legend
# lines and the title where the wordmark sits); the limb is cut flush at the
# sheet's edges like a plate's stems. Art + wordmark are identical on every
# screen, so only the bird boxes and the pill ever repaint.
BASE_XY  = (0, 0)
FLY_XY   = (BASE_XY[0] + LAYOUT["fly_at"][0],   BASE_XY[1] + LAYOUT["fly_at"][1])
PERCH_XY = (BASE_XY[0] + LAYOUT["perch_at"][0], BASE_XY[1] + LAYOUT["perch_at"][1])

# The wordmark is the plate title, verbatim: the script at the plates' auto-fit
# title size (theme.SCRIPT_TITLE_SIZE), drawn by the server's own draw_script so
# any future plate-title change re-bakes into the boot face. The baseline keeps
# the descenders (the script f's) well clear of the pill.
WORDMARK_BASELINE = 1534
# Splash footer: a hedera between the wordmark and the version line (the same
# ornament the plates' date line uses), then the version in the plates'
# engraved capitals (Adorn Engraved, theme.SUBTITLE_SIZE).
HEDERA_BASELINE  = 1632
VERSION_BASELINE = 1718

def draw_wordmark(im):
    if not typography.has_script_font():
        sys.exit("the bundled script font is missing; the wordmark must be the plates' script")
    size = typography.fit_script_title("Featherframe", theme.CONTENT_W)
    typography.draw_script(im, W / 2, WORDMARK_BASELINE, "Featherframe", size, 0,
                           stroke=theme.TITLE_STROKE)

def draw_version(im):
    d = ImageDraw.Draw(im)
    d.text((W / 2, HEDERA_BASELINE), theme.DATE_ORNAMENT,
           font=font(36, italic=True, weight=500), fill=0, anchor="ms")
    typography.draw_engraved(d, W / 2, VERSION_BASELINE, VERSION,
                             theme.SUBTITLE_SIZE, 0)

PILL_TEXT = {
    "wifi":     "Connecting to Wi-Fi",
    "birdnet":  "Connecting to server",
    "download": "Downloading image",
}

# Pill geometry: y=1648, height 82, centered, rounded (rx = h/2). The loading
# mark sits in a slot at the left, the text after it — a plain sans (Inter
# Medium) for at-a-glance readability against all the Garamond around it.
# Drawn pure black/white so the window refreshes flash-less with DU.
PILL_Y, PILL_H, PILL_PAD, PILL_GAP = 1648, 82, 30, 22
PILL_TEXT_SIZE = 34

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
    c.alpha_composite(BASE, BASE_XY)
    art = None
    if name == "wifi":
        art, xy = FLY, FLY_XY                      # the wren arrives on the wing
    elif name in ("birdnet", "download"):
        art, xy = PERCH, PERCH_XY                  # and settles on the bough
    if art is not None:
        c.alpha_composite(art, xy)
    im = c.convert("L")
    if art is not None:
        # The bird boxes go binary so their windows refresh with DU (no
        # flash) both coming and going. The engraved wren is tonal (wash over
        # line), so a hard threshold would blob it; ordered dither instead —
        # the gamma curve first, so the stipple density matches the gray it
        # replaces. The perch box takes its bit of twig with it, and that
        # twig re-dithers identically on every screen the box appears on.
        x0, y0 = xy
        x1, y1 = x0 + art.width, y0 + art.height
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
    ("BOOT_BIRDNET",  _compose("birdnet")),     # wren perched
    ("BOOT_DOWNLOAD", _compose("download")),    # wren perched, pill changes
    ("SETUP",         screen_setup()),
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

# -- error states ------------------------------------------------------------
# When a connect/fetch attempt dead-ends, the firmware swaps the pill band in
# place (windowed DU): true errors get an OUTLINED pill with a slashed icon —
# unmistakably not the loading state — while "waiting for the first bird"
# (HTTP 503: server up, no detection yet) keeps the normal solid pill with the
# mark parked. A "Trying again …" line sits in its own band beneath, swapped
# separately so the three backoff stages don't multiply the pill tiles. Over a
# painted plate the firmware shows only a small slashed glyph in the margin
# corner (see FF_CORNER_*). All tiles are pure black/white on white => DU.
ERR_TEXTS = [("Can't reach Wi-Fi", "wifi"), ("Can't reach server", "server")]
WAIT_TEXT = "Waiting for the first bird"
RETRY_TEXTS = ["Trying again in 1 minute", "Trying again in 5 minutes",
               "Trying again in 15 minutes", "Trying again shortly"]
ERR_ICON_SLOT = 56
RETRY_SIZE = 28
# The physical mat covers ~4% per edge (config.mat_inset_pct default): the
# plate pipeline scales its whole render to clear it, but boot screens push
# full-bleed — so art may bleed under the mat, type must stay inside
# (usable bottom ≈ 1797, right ≈ 1348).
RETRY_BASELINE = 1776
CORNER_CX, CORNER_CY, CORNER_S = 1290, 1742, 26

def _err_icon(kind):
    return wifi_slash if kind == "wifi" else cloud_slash

def _draw_error_pill(d, text, kind):
    fnt = sans(PILL_TEXT_SIZE)
    tw = d.textlength(text, font=fnt)
    pillw = int(26 + ERR_ICON_SLOT + 18 + tw + 30)
    px = int(W / 2 - pillw / 2)
    cy = PILL_Y + PILL_H / 2
    d.rounded_rectangle([px, PILL_Y, px + pillw, PILL_Y + PILL_H],
                        radius=PILL_H / 2, fill=255, outline=0, width=5)
    _err_icon(kind)(d, px + 26 + ERR_ICON_SLOT / 2, cy, 22)
    capbox = fnt.getbbox("H")
    d.text((px + 26 + ERR_ICON_SLOT + 18, cy + (capbox[3] - capbox[1]) / 2),
           text, font=fnt, fill=0, anchor="ls")

def _draw_wait_pill(d):
    fnt = sans(PILL_TEXT_SIZE)
    tw = d.textlength(WAIT_TEXT, font=fnt)
    pillw = int(PILL_PAD + LOADER_SLOT_W + PILL_GAP + tw + PILL_PAD + 4)
    px = int(W / 2 - pillw / 2)
    cy = PILL_Y + PILL_H / 2
    d.rounded_rectangle([px, PILL_Y, px + pillw, PILL_Y + PILL_H], radius=PILL_H / 2, fill=0)
    draw_loader_mark(d, px + PILL_PAD + LOADER_SLOT_W / 2, cy, frame=-1)   # parked
    capbox = fnt.getbbox("H")
    d.text((px + PILL_PAD + LOADER_SLOT_W + PILL_GAP, cy + (capbox[3] - capbox[1]) / 2),
           WAIT_TEXT, font=fnt, fill=255, anchor="ls")

def _canvas(fn):
    im = Image.new("L", (W, H), 255)
    fn(ImageDraw.Draw(im))
    return im

def _ink_cols(im, ry0, ry1):
    a = np.asarray(im)[ry0:ry1, :]
    cols = np.where((a < 250).any(axis=0))[0]
    return (int(cols.min()), int(cols.max())) if len(cols) else (W // 2, W // 2)

def _aligned_region(canvases, ry0, ry1, pad=8):
    x0 = min(_ink_cols(c, ry0, ry1)[0] for c in canvases) - pad
    x1 = max(_ink_cols(c, ry0, ry1)[1] for c in canvases) + pad
    x0 &= ~7; x1 = (x1 + 8) & ~7
    return x0, ry0, x1 - x0, ry1 - ry0

def _region_tiles(region, canvases):
    x0, y0, rw, rh = region
    tiles = [_pack_nibbles(_to_native_nibbles(c.crop((x0, y0, x0 + rw, y0 + rh))))
             for c in canvases]
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[y0:y0 + rh, x0:x0 + rw] = 1
    nat = np.rot90(mask, k=(PANEL_ROTATION // 90) % 4)
    ny0 = int(np.argmax(np.any(nat, axis=1)))
    nx0 = int(np.argmax(np.any(nat, axis=0)))
    nw, nh = rh, rw                                # portrait w/h swap under rot90
    return (NATIVE_W - nx0 - nw, ny0, nw, nh), tiles

def error_assets():
    # The pill band must also ERASE whichever normal pill it replaces, so its
    # union includes the three boot screens' own pill rows.
    boot = [im for name, im in SCREENS
            if name in ("BOOT_WIFI", "BOOT_BIRDNET", "BOOT_DOWNLOAD")]
    pills = [_canvas(lambda d, t=t, k=k: _draw_error_pill(d, t, k)) for t, k in ERR_TEXTS]
    pills.append(_canvas(_draw_wait_pill))
    band = _aligned_region(boot + pills, 1640, 1736)
    err_geo, err_tiles = _region_tiles(band, pills)

    retries = [_canvas(lambda d, t=t: d.text((W / 2, RETRY_BASELINE), t,
                                             font=sans(RETRY_SIZE), fill=0, anchor="ms"))
               for t in RETRY_TEXTS]
    rband = _aligned_region(retries, 1736, 1792)
    retry_geo, retry_tiles = _region_tiles(rband, retries + [Image.new("L", (W, H), 255)])

    corners = [_canvas(lambda d: wifi_slash(d, CORNER_CX, CORNER_CY, CORNER_S)),
               _canvas(lambda d: cloud_slash(d, CORNER_CX, CORNER_CY, CORNER_S))]
    cband = _aligned_region(corners, 1688, 1792)
    corner_geo, corner_tiles = _region_tiles(cband, corners + [Image.new("L", (W, H), 255)])
    return ((err_geo, err_tiles), (retry_geo, retry_tiles), (corner_geo, corner_tiles))

# -- button toasts ------------------------------------------------------------
# The button-press pills (check now / collage / status) used to be drawn by the
# firmware in a stock GFX font — rough, and refreshed through the 1-bit path.
# They are now baked exactly like the boot pills (Inter, same geometry) at the
# toast position over the plate's bottom margin, pushed as windowed DU tiles:
# in-progress toasts carry the loading mark (the firmware sweeps it), success
# carries a check, failures use the outlined+slashed error language.
TOAST_Y = 1648            # same rest position as the boot pills
TOASTS = [
    ("CHECKING",       "Checking",              "progress"),
    ("COLLAGE",        "Making the collage",    "progress"),
    ("STATUS",         "Making the status page", "progress"),
    ("UP_TO_DATE",     "Up to date",     "done"),
    ("NO_COLLAGE",     "No collage yet", "plain"),
    ("CHECK_FAILED",   "Check failed",   "fail"),
    ("COLLAGE_FAILED", "Collage failed", "fail"),
    ("STATUS_FAILED",  "Status failed",  "fail"),
    ("PORTAL",         "Join Featherframe-Setup", "wifi"),
]

def _draw_toast(d, text, style):
    fnt = sans(PILL_TEXT_SIZE)
    tw = d.textlength(text, font=fnt)
    cy = TOAST_Y + PILL_H / 2
    capbox = fnt.getbbox("H")
    baseline = cy + (capbox[3] - capbox[1]) / 2
    if style == "fail":
        pillw = int(26 + ERR_ICON_SLOT + 18 + tw + 30)
        px = int(W / 2 - pillw / 2)
        d.rounded_rectangle([px, TOAST_Y, px + pillw, TOAST_Y + PILL_H],
                            radius=PILL_H / 2, fill=255, outline=0, width=5)
        cloud_slash(d, px + 26 + ERR_ICON_SLOT / 2, cy, 22)
        d.text((px + 26 + ERR_ICON_SLOT + 18, baseline), text, font=fnt, fill=0, anchor="ls")
        return None
    if style == "wifi":
        # The setup-portal announcement over a plate: the setup card's hotspot
        # chip, inverted.
        slot = 48
        pillw = int(PILL_PAD + slot + PILL_GAP + tw + PILL_PAD + 4)
        px = int(W / 2 - pillw / 2)
        d.rounded_rectangle([px, TOAST_Y, px + pillw, TOAST_Y + PILL_H],
                            radius=PILL_H / 2, fill=0)
        wifi_glyph(d, px + PILL_PAD + slot / 2, cy + 8, 19, fill=255)
        d.text((px + PILL_PAD + slot + PILL_GAP, baseline), text, font=fnt,
               fill=255, anchor="ls")
        return None
    if style == "plain":
        pillw = int(PILL_PAD + tw + PILL_PAD + 8)
        px = int(W / 2 - pillw / 2)
        d.rounded_rectangle([px, TOAST_Y, px + pillw, TOAST_Y + PILL_H],
                            radius=PILL_H / 2, fill=0)
        d.text((px + PILL_PAD + 4, baseline), text, font=fnt, fill=255, anchor="ls")
        return None
    pillw = int(PILL_PAD + LOADER_SLOT_W + PILL_GAP + tw + PILL_PAD + 4)
    px = int(W / 2 - pillw / 2)
    d.rounded_rectangle([px, TOAST_Y, px + pillw, TOAST_Y + PILL_H],
                        radius=PILL_H / 2, fill=0)
    if style == "done":
        check(d, px + PILL_PAD + LOADER_SLOT_W / 2, cy, 26)
    else:
        draw_loader_mark(d, px + PILL_PAD + LOADER_SLOT_W / 2, cy)
    d.text((px + PILL_PAD + LOADER_SLOT_W + PILL_GAP, baseline), text,
           font=fnt, fill=255, anchor="ls")
    return (px + PILL_PAD + LOADER_SLOT_W / 2, cy) if style == "progress" else None

def toast_assets():
    canvases, centers = [], []
    for _, text, style in TOASTS:
        im = Image.new("L", (W, H), 255)
        centers.append(_draw_toast(ImageDraw.Draw(im), text, style))
        canvases.append(im)
    band = _aligned_region(canvases, 1640, 1736)
    geo, tiles = _region_tiles(band, canvases + [Image.new("L", (W, H), 255)])
    loaders = [loader_tiles(c, *ctr) if ctr else None
               for c, ctr in zip(canvases, centers)]
    return geo, tiles, loaders

def write_header():
    screens = {name: im for name, im in SCREENS}
    (err_geo, err_tiles), (retry_geo, retry_tiles), (corner_geo, corner_tiles) = error_assets()
    toast_geo, toast_tiles, toast_loaders = toast_assets()
    max_tile = max(len(t) for t in
                   err_tiles + retry_tiles + corner_tiles + toast_tiles)
    loaders = {name: loader_tiles(screens[name], cx, cy)
               for name, (cx, cy) in
               (("BOOT_WIFI", LOADER_AT["wifi"]),
                ("BOOT_BIRDNET", LOADER_AT["birdnet"]),
                ("BOOT_DOWNLOAD", LOADER_AT["download"]))}
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
         "// Error-state tiles (see the error-states section of the bake).",
         "// ff_err_tiles: 0 = can't reach Wi-Fi (outlined + slashed wifi),",
         "// 1 = can't reach server (outlined + slashed server), 2 = waiting",
         "// for the first bird (solid pill, parked mark). The window also",
         "// erases whichever normal pill it replaces.",
         f"#define FF_ERR_X          {err_geo[0]}",
         f"#define FF_ERR_Y          {err_geo[1]}",
         f"#define FF_ERR_W          {err_geo[2]}",
         f"#define FF_ERR_H          {err_geo[3]}",
         "// ff_retry_tiles: 0/1/2 = trying again in 1/5/15 minutes, 3 =",
         "// 'shortly' (always-awake), 4 = blank (erases the line).",
         f"#define FF_RETRY_X        {retry_geo[0]}",
         f"#define FF_RETRY_Y        {retry_geo[1]}",
         f"#define FF_RETRY_W        {retry_geo[2]}",
         f"#define FF_RETRY_H        {retry_geo[3]}",
         "// ff_corner_tiles: 0 = slashed wifi, 1 = slashed server, 2 = blank",
         "// (erase). Shown over a painted plate in the bottom-right margin.",
         f"#define FF_CORNER_X       {corner_geo[0]}",
         f"#define FF_CORNER_Y       {corner_geo[1]}",
         f"#define FF_CORNER_W       {corner_geo[2]}",
         f"#define FF_CORNER_H       {corner_geo[3]}", "",
         "// Button toasts, baked in the boot-pill style at the toast position",
         "// over the plate's bottom margin. FF_TOAST_BLANK erases the band.",
         f"#define FF_TOAST_X        {toast_geo[0]}",
         f"#define FF_TOAST_Y        {toast_geo[1]}",
         f"#define FF_TOAST_W        {toast_geo[2]}",
         f"#define FF_TOAST_H        {toast_geo[3]}", "",
         f"#define FF_MAX_TILE_BYTES {max_tile}   // largest uncompressed tile",
         "", "enum FfToast {"]
    for i, (tname, _, _) in enumerate(TOASTS):
        L.append(f"  FF_TOAST_{tname} = {i},")
    L += [f"  FF_TOAST_BLANK = {len(TOASTS)},",
          f"  FF_TOAST_COUNT = {len(TOASTS) + 1},", "};", ""]
    L += ["enum FfScreen {"]
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
    for k, t in enumerate(err_tiles):
        emit_array(f"ff_err_{k}", t)
    for k, t in enumerate(retry_tiles):
        emit_array(f"ff_retry_{k}", t)
    for k, t in enumerate(corner_tiles):
        emit_array(f"ff_corner_{k}", t)
    for k, t in enumerate(toast_tiles):
        emit_array(f"ff_toast_{k}", t)
    for i, ld in enumerate(toast_loaders):
        if ld:
            for k, t in enumerate(ld[1]):
                emit_array(f"ff_tldr_{i}_{k}", t)
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
          "static const uint8_t* const ff_err_tiles[3] = { ff_err_0, ff_err_1, ff_err_2 };",
          "static const uint8_t* const ff_retry_tiles[5] = { ff_retry_0, ff_retry_1, ff_retry_2, ff_retry_3, ff_retry_4 };",
          "static const uint8_t* const ff_corner_tiles[3] = { ff_corner_0, ff_corner_1, ff_corner_2 };",
          "static const uint8_t* const ff_toast_tiles[FF_TOAST_COUNT] = {",
          "  " + ", ".join(f"ff_toast_{k}" for k in range(len(toast_tiles))) + " };",
          "// Loading-mark window per toast (in-progress toasts only).",
          "static const FfLoader ff_toast_loader[" + str(len(TOASTS)) + "] = {"] + [
          (f"  {{ {ld[0][0]}, {ld[0][1]}, {{ " +
           ", ".join(f"ff_tldr_{i}_{k}" for k in range(LOADER_FRAMES)) + " } },")
          if ld else "  { -1, -1, { 0 } },"
          for i, ld in enumerate(toast_loaders)] + ["};", "",
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
