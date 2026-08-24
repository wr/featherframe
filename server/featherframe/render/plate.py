"""Audubon plate image handling: load, find the bird, normalise the paper.

The plates are big (5000-9500 px) aged-paper scans with the subject as the bold
central ink mass and thin engraved marginalia in the corners / a printed caption
at the very bottom. The content crop isolates the subject band and drops that
marginalia; composites (several birds on one plate) skip the crop and show the
whole plate, because a "densest region" crop could land on the wrong bird — and
priority #2 is never a wrong bird.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger("featherframe.plate")

# The plates are trusted public-domain files; lift Pillow's DoS guard so the
# big ones (the robin is ~109 MP) open without a warning.
Image.MAX_IMAGE_PIXELS = None

# We never display more than ~1200 px of bird, so cap working resolution to
# stay memory-frugal on a Pi Zero.
WORK_MAX_SIDE = 1500
ANALYSIS_W = 380


def load_gray(path: str | Path, max_side: int = WORK_MAX_SIDE) -> Image.Image:
    im = Image.open(path)
    im.draft("L", (max_side, max_side))  # let the JPEG decoder downscale cheaply
    im = im.convert("L")
    if max(im.size) > max_side:
        scale = max_side / max(im.size)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return im


def _ink_map(gray: Image.Image) -> tuple[np.ndarray, float]:
    """Return (inkiness at analysis resolution, scale-back factor to `gray`)."""
    scale = ANALYSIS_W / gray.width
    small = gray.resize((ANALYSIS_W, max(1, round(gray.height * scale))), Image.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    paper = np.percentile(arr, 94)  # the bright paper level
    ink = np.clip(paper - arr, 0, None)  # only meaningfully-darker-than-paper counts
    return ink, gray.width / ANALYSIS_W


def content_box(gray: Image.Image, pad: float = 0.04) -> tuple[int, int, int, int]:
    """Bounding box (in `gray` pixel coords) of the main subject mass.

    Vertical extent = the single contiguous band of inked rows carrying the most
    ink (this drops the separated caption band and the top marginalia).
    Horizontal extent = significant columns within that band.
    """
    ink, back = _ink_map(gray)
    row_mass = ink.sum(axis=1)
    if row_mass.max() <= 0:
        return _fallback_box(gray)

    # significant rows -> contiguous runs -> heaviest run
    thr = 0.06 * row_mass.max()
    sig = row_mass > thr
    runs = _runs(sig)
    if not runs:
        return _fallback_box(gray)
    t0, t1 = max(runs, key=lambda r: row_mass[r[0]:r[1]].sum())

    band = ink[t0:t1, :]
    col_mass = band.sum(axis=0)
    cthr = 0.05 * col_mass.max() if col_mass.max() > 0 else 0
    cols = np.where(col_mass > cthr)[0]
    if cols.size == 0:
        return _fallback_box(gray)
    c0, c1 = int(cols.min()), int(cols.max()) + 1

    # scale back to `gray` coords and pad
    l, r = c0 * back, c1 * back
    tt, bb = t0 * back, t1 * back
    pw, ph = (r - l) * pad, (bb - tt) * pad
    l, r = l - pw, r + pw
    tt, bb = tt - ph, bb + ph
    box = (int(max(0, l)), int(max(0, tt)),
           int(min(gray.width, r)), int(min(gray.height, bb)))
    # sanity: reject degenerate / tiny crops
    area = (box[2] - box[0]) * (box[3] - box[1])
    if area < 0.18 * gray.width * gray.height:
        return _fallback_box(gray)
    return box


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _fallback_box(gray: Image.Image) -> tuple[int, int, int, int]:
    w, h = gray.size
    return (int(w * 0.05), int(h * 0.05), int(w * 0.95), int(h * 0.90))


def _norm_box(gray: Image.Image, box: list[float]) -> tuple[int, int, int, int]:
    x, y, bw, bh = box
    w, h = gray.size
    return (int(x * w), int(y * h), int((x + bw) * w), int((y + bh) * h))


def paper_normalize(gray: Image.Image) -> Image.Image:
    """Levels stretch + gentle S-curve: lift aged cream to clean paper and deepen
    the ink so the plate reads with punch on e-ink (the flat 16-level panel
    otherwise makes a raw scan look washed out). Turns a foxed scan into a crisp
    plate while a shallow S keeps engraving detail in the midtones."""
    arr = np.asarray(gray, dtype=np.float32)
    lo = np.percentile(arr, 3.5)               # tighter than before -> more separation
    hi = np.percentile(arr, 90.0)              # clip more paper to pure white
    if hi - lo < 1e-3:
        return gray
    norm = np.clip((arr - lo) / (hi - lo), 0, 1)
    # Shallow S-curve (blend toward smoothstep): darkens the ink, brightens the
    # paper, leaves the midtone engraving lines mostly alone.
    s = norm * norm * (3.0 - 2.0 * norm)
    norm = norm * 0.62 + s * 0.38
    out = 6 + norm * (255 - 6)                  # deep black point, paper -> pure white
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="L")


def _trim_marginalia(gray: Image.Image) -> Image.Image:
    """Physically remove the outer printed margin bands of the plate: the
    'N° 32 / PLATE CLIX' line across the top and the engraved species caption
    across the bottom. The bird is always well inside these, so this guarantees
    no plate lettering leaks into the composition."""
    # Measured across the plates: the top "N° / PLATE" line sits at ~5-6.5% and
    # the printed caption in the bottom ~6-9%. The bird is always below/above.
    w, h = gray.size
    return gray.crop((int(w * 0.025), int(h * 0.068), int(w * 0.975), int(h * 0.912)))


def extract(path: str | Path, composite: bool = False,
            crop_box: Optional[list] = None) -> Image.Image:
    """Load a plate and return the normalised bird artwork ('L')."""
    gray = _trim_marginalia(load_gray(path))
    if crop_box:
        # crop_box is normalised within the marginalia-trimmed plate
        box = _norm_box(gray, crop_box)
    elif composite:
        box = (0, 0, gray.width, gray.height)  # whole (trimmed) plate: all birds
    else:
        box = content_box(gray)
    crop = gray.crop(box)
    return paper_normalize(crop)
