"""E Ink Spectra 6 finishing: quantise a composed RGB frame to the panel's six
inks (the EE02's 13.3" colour panel).

The panel can show black, white, yellow, red, blue and green, and nothing in
between, so every other colour is a dithered mix. The inks are also far from
the sRGB primaries — panel "white" is a light gray-green, "red" a brick — so
dithering against ideal primaries makes clean paper speckle yellow and green.
Everything here works against the *measured* inks instead:

  1. Tone-map the frame into the panel's range in linear light: image white
     lands exactly on panel white and image black on panel black, so the big
     paper field is one solid ink (the colour twin of theme.FIELD == level 15).
  2. Express each colour as a convex mix of at most four inks (its position in
     the ink gamut; colours outside it project onto the gamut's surface).
  3. Pick one ink per pixel by walking the mix's cumulative weights against
     the same void-and-cluster blue-noise mask the gray dither uses. Ordered,
     so it is fully vectorised (Pi-friendly) and mean-preserving, with none of
     error diffusion's worms along engraving lines.

Step 2 is a 64^3 lookup table, built once in numpy and cached in the data dir
like the blue-noise mask.
"""
from __future__ import annotations

import hashlib
import itertools
import logging

import numpy as np
from PIL import Image

from .. import paths
from . import finish

log = logging.getLogger("featherframe.spectra")

# Ink order is the dither's walk order: dark to light, so the blue-noise
# threshold behaves like a tone threshold.
BLACK, BLUE, GREEN, RED, YELLOW, WHITE = range(6)
INK_NAMES = ("black", "blue", "green", "red", "yellow", "white")

# Measured sRGB of each ink on a Spectra 6 panel (aitjcize/esp32-photoframe's
# MEASURED_PALETTE). A per-panel calibration would replace this table.
MEASURED = np.array([
    (2, 2, 2),        # black
    (0, 47, 107),     # blue
    (33, 69, 40),     # green
    (117, 10, 0),     # red
    (201, 184, 0),    # yellow
    (179, 182, 171),  # white
], dtype=np.float32)

# What the wire wants: Seeed_GFX's colour sprite nibbles (TFT_BLACK = 0xF,
# TFT_WHITE = 0x0, ...; see T133A01_Defines.h COLOR_GET). The firmware pushes
# these verbatim. Any other nibble renders white.
WIRE_NIBBLE = np.array([0xF, 0xD, 0x2, 0x6, 0xB, 0x0], dtype=np.uint8)

# The preview shows the inks as the glass does, normalised so panel white is
# paper white (the eye adapts to the panel's own white the same way).
_LUT_BITS = 6
_LUT_N = 1 << _LUT_BITS


def _to_linear(srgb255: np.ndarray) -> np.ndarray:
    c = srgb255.astype(np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _to_srgb255(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    c = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.round(c * 255.0).astype(np.uint8)


def _palette_linear() -> np.ndarray:
    return _to_linear(MEASURED)


# -- convex decomposition ---------------------------------------------------
def _decompose(points: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Weights [N,6] (>=0, sum 1) mixing `pal` to the closest reproducible
    colour for each of `points` [N,3]. Tries every simplex of the inks, fewest
    inks first, and takes more only when they reproduce the colour better —
    so a neutral gray (the type) is black + white and never a confetti of
    colours that happens to average gray. Among equals it keeps the tightest
    simplex (inks closest to the colour), which dithers quietest."""
    n = len(points)
    best_err = np.full(n, np.inf, dtype=np.float32)
    best_spread = np.full(n, np.inf, dtype=np.float32)
    best_w = np.zeros((n, len(pal)), dtype=np.float32)
    best_k = np.zeros(n, dtype=np.int8)
    dist = np.linalg.norm(points[:, None, :] - pal[None, :, :], axis=2)   # [N,6]

    for k in (1, 2, 3, 4):
        for verts in itertools.combinations(range(len(pal)), k):
            v = pal[list(verts)]                                  # [k,3]
            if k == 1:
                w = np.ones((n, 1), dtype=np.float32)
            else:
                # Barycentric coordinates of the projection onto the simplex's
                # affine hull: least squares on the edge vectors from v[0].
                edges = (v[1:] - v[0]).T                          # [3,k-1]
                if np.linalg.matrix_rank(edges) < k - 1:
                    continue
                sol = np.linalg.lstsq(edges, (points - v[0]).T, rcond=None)[0].T
                w = np.concatenate([1.0 - sol.sum(axis=1, keepdims=True), sol], axis=1)
            ok = (w >= -1e-5).all(axis=1)
            if not ok.any():
                continue
            w = np.clip(w, 0.0, None)
            w /= w.sum(axis=1, keepdims=True)
            err = np.linalg.norm(w @ v - points, axis=1)
            spread = (w * dist[:, list(verts)]).sum(axis=1)
            same_k = best_k == k
            better = ok & ((err < best_err - 2e-3)
                           | (same_k & (np.abs(err - best_err) <= 2e-3) & (spread < best_spread)))
            if better.any():
                best_err[better] = err[better]
                best_spread[better] = spread[better]
                best_k[better] = k
                full = np.zeros((int(better.sum()), len(pal)), dtype=np.float32)
                full[:, list(verts)] = w[better]
                best_w[better] = full
    return best_w


_LUT_CACHE: np.ndarray | None = None


def _lut() -> np.ndarray:
    """Cumulative ink weights, uint8 [64,64,64,6], indexed by the tone-mapped
    colour in panel-range linear RGB (0 = panel black .. 1 = panel white per
    channel). Cached in memory and on disk, keyed by the palette."""
    global _LUT_CACHE
    if _LUT_CACHE is not None:
        return _LUT_CACHE
    key = hashlib.sha1(MEASURED.tobytes() + b"v2").hexdigest()[:8]
    cache = paths.data_dir() / f"spectra6-lut-{key}.npy"
    if cache.exists():
        try:
            _LUT_CACHE = np.load(cache)
            return _LUT_CACHE
        except (OSError, ValueError):
            pass
    log.info("Building the Spectra 6 ink table (one-time)…")
    pal = _palette_linear()
    lo, hi = pal[BLACK], pal[WHITE]
    # Grid points sit ON the ends of each axis, so paper white and ink black
    # are exactly one ink (no stray specks across the field).
    axis = np.arange(_LUT_N, dtype=np.float32) / (_LUT_N - 1)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    w = _decompose(lo + grid * (hi - lo), pal)
    cum = np.clip(np.round(np.cumsum(w, axis=1) * 255.0), 0, 255).astype(np.uint8)
    cum[:, -1] = 255
    _LUT_CACHE = cum.reshape(_LUT_N, _LUT_N, _LUT_N, len(pal))
    try:
        np.save(cache, _LUT_CACHE)
    except OSError:
        pass
    return _LUT_CACHE


# -- the finish ---------------------------------------------------------------
def to_inks(img: Image.Image, method: str = "bluenoise", saturation: float = 1.0) -> np.ndarray:
    """Return uint8 [H,W] of ink indices (BLACK..WHITE) for an RGB frame."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    lut = _lut()
    h, w = img.height, img.width
    mask = finish._bluenoise_mask()
    # One mask period tall, the frame wide; floor keeps it under 255 so the
    # last (always 255) cumulative weight is never passed.
    mask_row = np.floor(np.tile(mask, (1, w // mask.shape[1] + 1))[:, :w] * 255.0).astype(np.uint8)
    out = np.empty((h, w), dtype=np.uint8)
    band = 256                                   # rows per pass: keeps the Pi's RAM flat
    for y0 in range(0, h, band):
        rgb = np.asarray(img.crop((0, y0, w, min(h, y0 + band))), dtype=np.float32)
        if saturation != 1.0:
            luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
            rgb = np.clip(luma[..., None] + (rgb - luma[..., None]) * saturation, 0, 255)
        # sRGB -> linear; the LUT's axes already span panel black..white, so
        # image white IS panel white.
        lin = _to_linear(rgb)
        q = np.clip(np.round(lin * (_LUT_N - 1)).astype(np.int32), 0, _LUT_N - 1)
        cum = lut[q[..., 0], q[..., 1], q[..., 2]]                  # [b,W,6] uint8
        bh = cum.shape[0]
        if method == "none":
            thr = np.full((bh, w), 127, dtype=np.uint8)
        else:
            thr = mask_row[np.arange(y0, y0 + bh) % mask.shape[0]]
        out[y0:y0 + bh] = (cum <= thr[..., None]).sum(axis=2).clip(0, 5).astype(np.uint8)
    return out


def inks_to_image(inks: np.ndarray) -> Image.Image:
    """The glass as the eye sees it: measured inks, white-adapted."""
    pal = _palette_linear()
    shown = _to_srgb255(np.clip((pal - pal[BLACK]) / (pal[WHITE] - pal[BLACK]), 0, 1))
    return Image.fromarray(shown[inks], mode="RGB")


def invert(inks: np.ndarray) -> np.ndarray:
    """Dark mode: black and white trade places; the colours stay."""
    swap = np.arange(6, dtype=np.uint8)
    swap[BLACK], swap[WHITE] = WHITE, BLACK
    return swap[inks]


def to_wire(inks: np.ndarray) -> np.ndarray:
    return WIRE_NIBBLE[inks]
