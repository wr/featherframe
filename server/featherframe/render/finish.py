"""E-ink finishing: quantise the composed grayscale to the panel's gray levels.

Naive Floyd-Steinberg moirés on fine engraving lines at this resolution, so we
offer two aperiodic dithers:

  bluenoise  (default) ordered dithering against a void-and-cluster blue-noise
             mask. Fully vectorised in numpy -> fast and memory-light, the right
             choice on a Pi Zero. Blue noise is high-frequency, so it disperses
             cleanly without the low-frequency worms of error diffusion.
  stucki     Stucki error diffusion. Higher tonal fidelity on big flat washes,
             but a per-pixel Python loop -> much slower. Fine on a real PC.

Both quantise to `levels` gray steps (16 for the panel's grayscale mode, or 2
for the 1-bit fallback) and return per-pixel level *indices* (0..levels-1),
which is exactly what the framebuffer packer wants.
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from .. import paths

log = logging.getLogger("featherframe.finish")

# Stucki diffusion kernel, weights/42. (dx, dy, weight)
_STUCKI = [
    (1, 0, 8), (2, 0, 4),
    (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
    (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1),
]


def levels_to_image(indices: np.ndarray, levels: int) -> Image.Image:
    step = 255.0 / (levels - 1)
    return Image.fromarray(np.round(indices * step).astype(np.uint8), mode="L")


def to_levels(img: Image.Image, levels: int = 16, method: str = "bluenoise") -> np.ndarray:
    """Return uint8 array of level indices (0..levels-1)."""
    if img.mode != "L":
        img = img.convert("L")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    if method == "none":
        return _quantize_plain(arr, levels)
    if method == "stucki":
        return _dither_stucki(arr, levels)
    return _dither_bluenoise(arr, levels)


def _quantize_plain(arr01: np.ndarray, levels: int) -> np.ndarray:
    idx = np.round(arr01 * (levels - 1))
    return np.clip(idx, 0, levels - 1).astype(np.uint8)


def _dither_bluenoise(arr01: np.ndarray, levels: int) -> np.ndarray:
    mask = _bluenoise_mask()
    h, w = arr01.shape
    tiled = np.tile(mask, (h // mask.shape[0] + 1, w // mask.shape[1] + 1))[:h, :w]
    v = arr01 * (levels - 1)
    lower = np.floor(v)
    frac = v - lower
    idx = lower + (frac > tiled).astype(np.float32)
    return np.clip(idx, 0, levels - 1).astype(np.uint8)


def _dither_stucki(arr01: np.ndarray, levels: int) -> np.ndarray:
    h, w = arr01.shape
    buf = arr01 * (levels - 1)  # work in "level" units
    out = np.zeros((h, w), dtype=np.uint8)
    top = levels - 1
    for y in range(h):
        row = buf[y]
        for x in range(w):
            old = row[x]
            q = round(old)
            if q < 0:
                q = 0
            elif q > top:
                q = top
            out[y, x] = q
            err = old - q
            if err == 0:
                continue
            for dx, dy, wgt in _STUCKI:
                nx = x + dx
                if 0 <= nx < w and 0 <= y + dy < h:
                    buf[y + dy, nx] += err * wgt / 42.0
    return out


# -- blue-noise mask (void-and-cluster, cached) ---------------------------
_MASK_CACHE: np.ndarray | None = None


def _bluenoise_mask(size: int = 64) -> np.ndarray:
    """Threshold mask in [0,1), cached in memory and on disk (built once)."""
    global _MASK_CACHE
    if _MASK_CACHE is not None:
        return _MASK_CACHE
    cache = paths.data_dir() / f"bluenoise{size}.npy"
    if cache.exists():
        _MASK_CACHE = np.load(cache)
        return _MASK_CACHE
    log.info("Generating %dx%d blue-noise mask (one-time)…", size, size)
    _MASK_CACHE = _void_and_cluster(size)
    try:
        np.save(cache, _MASK_CACHE)
    except OSError:
        pass
    return _MASK_CACHE


def _gauss_energy_fft(sigma: float, size: int) -> np.ndarray:
    ax = np.arange(size)
    ax = np.minimum(ax, size - ax)  # toroidal distance
    gx = np.exp(-(ax ** 2) / (2 * sigma ** 2))
    kernel = np.outer(gx, gx)
    return np.fft.rfft2(kernel)


def _void_and_cluster(size: int, sigma: float = 1.9) -> np.ndarray:
    """Ulichney's method. Returns a size x size float mask in [0,1)."""
    n = size * size
    gf = _gauss_energy_fft(sigma, size)

    def energy(binary: np.ndarray) -> np.ndarray:
        return np.fft.irfft2(np.fft.rfft2(binary) * gf, s=(size, size))

    # Deterministic initial pattern (no RNG available in some sandboxes): a
    # scattered set via a low-discrepancy-ish index hash.
    rng = np.random.default_rng(1)
    binary = np.zeros((size, size), dtype=np.float32)
    ones = max(1, n // 10)
    flat = rng.permutation(n)[:ones]
    binary.flat[flat] = 1.0

    # Relax the initial pattern: repeatedly move tightest cluster -> largest void.
    for _ in range(ones):
        e = energy(binary)
        cluster = np.where(binary.ravel() == 1, e.ravel(), -np.inf).argmax()
        binary.flat[cluster] = 0.0
        e = energy(binary)
        void = np.where(binary.ravel() == 0, e.ravel(), np.inf).argmin()
        binary.flat[void] = 1.0
        if void == cluster:
            break

    rank = np.full(n, -1, dtype=np.int64)
    work = binary.copy()

    # Phase 1: remove tightest clusters, ranks (ones-1)..0
    for r in range(ones - 1, -1, -1):
        e = energy(work)
        cluster = np.where(work.ravel() == 1, e.ravel(), -np.inf).argmax()
        rank[cluster] = r
        work.flat[cluster] = 0.0

    # Phase 2/3: fill largest voids, ranks ones..n-1
    work = binary.copy()
    for r in range(ones, n):
        e = energy(work)
        void = np.where(work.ravel() == 0, e.ravel(), np.inf).argmin()
        rank[void] = r
        work.flat[void] = 1.0

    mask = (rank.astype(np.float32) + 0.5) / n
    return mask.reshape(size, size)
