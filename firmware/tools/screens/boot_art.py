#!/usr/bin/env python3
"""Draw the boot-screen art through the plates' own image pipeline.

The boot sequence is four screens on one bare bough — the folio's own
armature, the perch on which every Audubon bird sits: the bough alone
(splash / setup), a wren arriving on the wing (Wi-Fi), the wren perched
(server, download). The bough must be PIXEL-IDENTICAL on every screen — the
firmware diffs consecutive screens and repaints only the changed windows —
so the art is three files:

    plate_base.png    the bough (black ink on transparent)
    plate_fly.png     the wren in flight, a cut-out that lands on bare paper
    plate_perch.png   the wren perched, a feathered rectangle that carries
                      its bit of twig with it
    plate_layout.json where the two bird patches sit, in plate_base.png
                      pixel space

Two phases:

    generate   buys the images from the configured image model, the same
               ``OpenAIImageModel`` + ``/v1/images/edits`` with real Havell
               plates as style references that draws the frame's AI plates.
               Draw 1: the empty bough (refs: the folio's wren plates).
               Draw 2: an edit of draw 1 adding a wren in flight.
               Draw 3: an edit of draw 1 with the wren perched.
               The image model has no notion of "unchanged", so draws 2 and 3
               come back as whole re-drawn sheets — only the bird is taken
               from each, which is why the bough is drawn once and reused.
    cut        deterministic: normalises the paper like a real scan
               (``plate.extract_generated``), registers each edit onto the
               bough (scale + shift by phase correlation), lifts the flying
               bird off the bare paper and the perched bird with its twig,
               and writes the four files above at screen scale.

    server/.venv/bin/python firmware/tools/screens/boot_art.py generate --out art/raw
    server/.venv/bin/python firmware/tools/screens/boot_art.py cut --raw art/raw

The key comes from OPENAI_API_KEY or, with --key-from-db, the server's own
config (FEATHERFRAME_DATA_DIR / FEATHERFRAME_DB as for the service) — the
same place the frame keeps it. Reference plates come from
FEATHERFRAME_PLATES_DIR's index, so both phases run wherever the plates are.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
# Run from the repo (…/firmware/tools/screens) or as a lone file on the box
# with FEATHERFRAME_SERVER_DIR pointing at the installed package.
SERVER_DIR = Path(os.environ.get("FEATHERFRAME_SERVER_DIR", HERE.parent.parent.parent / "server"))
sys.path.insert(0, str(SERVER_DIR))

from featherframe import paths  # noqa: E402
from featherframe.render import genart, plate  # noqa: E402

ART = HERE / "art"
GEN_SIZE = "1024x1024"          # the boot plate's art box is all but square (1404 x 1361)

# The folio's own wrens, style references for every draw: plate 83 is the House
# Wren at a nest cavity (an old hat on a snag), plate 78 the Carolina Wren, plate
# 18 Bewick's. Scientific names as in the curated index.
REF_SPECIES = ["Troglodytes aedon", "Thryothorus ludovicianus", "Thryomanes bewickii"]
BIRD = "Carolina Wren (Thryothorus ludovicianus)"

# -- prompts -----------------------------------------------------------------
# Principles, never examples (see the genart prompt history): the model treats
# any named instance as a target to hit.
_BASE_OPEN = (
    "A hand-colored copperplate engraving with aquatint in the exact style of John James "
    "Audubon's 'The Birds of America' (Havell edition, 1827-1838): a finished plate's "
    "setting with its bird not yet placed — the armature and the plant, composed as "
    "Audubon composed a sheet for one small songbird, drawn with the engraver's exactness. "
    "The background is bright, near-white wove paper left completely untouched — no sepia "
    "tint, no cream wash, no aging, no vignette, no border.\n\n"
)
_BASE_COLOR = (
    "Color and tone as the colorists actually worked, and LIGHT: the wood in thin "
    "transparent washes of pale umber and gray with the paper glowing through, its bark "
    "carried by fine engraved line rather than by dark wash; foliage greens muted "
    "sage-olive, never grass-green; any berry or blossom a single confident pigment "
    "accent; nothing heavy, nothing black, nothing glows; whites are reserved bare paper.\n\n"
)
_BASE_COMPOSE = (
    "Compose as Audubon composed. A limb of real weight enters the sheet from the LOWER "
    "LEFT, cut off flush at the sheet's edge, and sweeps upward across the sheet in one "
    "living curve, forking once: a lesser twig reaches back toward the upper left, and the "
    "main twig climbs to end in open paper in the upper right third of the sheet, its last "
    "span bare — a perch waiting for its bird, with clear paper all round it. One real, "
    "identifiable plant of a wren's own world dresses the LOWER part of the limb as the "
    "sheet's counter-mass — a few leaves and a spray of berries or blossom, drawn to "
    "botanical-plate standard with individually veined leaves, chosen fresh from that "
    "world rather than from a painter's stock — and thins to nothing before the perch. "
    "Half to two-thirds of the sheet stays bare paper, asymmetrically. There is NO BIRD "
    "anywhere on the sheet, no nest, no insect. No ground, no sky, no horizon, no shadow.\n\n"
)
_FOOTER = (
    "No text: no title, no names, no lettering, no numerals, no signature, no border, "
    "no frame line."
)
BASE_PROMPT = _BASE_OPEN + genart._P_PROCESS + _BASE_COLOR + _BASE_COMPOSE + _FOOTER

_EDIT_OPEN = (
    "The first image is a finished sheet from this folio: a limb with its plant on bright "
    "untouched paper, its bare main twig ending in open paper in the upper right. "
    "Reproduce that sheet exactly — every twig, leaf and engraved stroke, and their "
    "placement on the paper unchanged, the same size on the same paper — "
)
FLY_PROMPT = (
    _EDIT_OPEN
    + "and add one figure only: a " + BIRD + " in full flight, arriving at the bare "
    "twig's tip from the open paper above and to the right of it, flying down toward it, "
    "caught at the top of the upstroke — both wings raised high above the back and spread "
    "to their full extent, the tail fanned and cocked, the feet tucked, the bill pointed "
    "at the twig — life-size relative to the bough. The bird stays entirely on bare paper "
    "— clear of the bough and of every twig, overlapping nothing, a clear gap of paper "
    "between it and the twig's tip. Draw the bird in the very same engraved-and-washed "
    "manner as the bough, exactly as the other images show the folio drew this "
    "species.\n\n"
    + genart._P_PROCESS + genart._P_ANATOMY + _FOOTER
)
PERCH_PROMPT = (
    _EDIT_OPEN
    + "and add one figure only: a " + BIRD + " perched on the bare main twig near its tip, "
    "its feet gripping the twig, in the folio's characteristic wren attitude — body "
    "angled upward, tail cocked high, head turned and alert, the bold pale stripe over "
    "the eye plainly visible — life-size relative to the bough, in clean profile, drawn "
    "crisply. Nothing else on the sheet changes and no other bird appears. Draw the bird "
    "in the very same engraved-and-washed manner as the bough, exactly as the other "
    "images show the folio drew this species.\n\n"
    + genart._P_PROCESS + genart._P_ANATOMY + _FOOTER
)
STEPS = ("base", "fly", "perch")
PROMPTS = {"base": BASE_PROMPT, "fly": FLY_PROMPT, "perch": PERCH_PROMPT}


# -- generate ----------------------------------------------------------------
def reference_plates() -> list[Path]:
    idx = json.loads(paths.plate_index_path().read_text())
    images_dir = Path(idx.get("images_dir", paths.plate_images_dir()))
    by_sci = {e.get("scientific"): e for e in idx.get("species", []) if isinstance(e, dict)}
    refs = []
    for sci in REF_SPECIES:
        e = by_sci.get(sci)
        if e and e.get("image") and (images_dir / e["image"]).exists():
            refs.append(images_dir / e["image"])
    if not refs:
        sys.exit(f"no wren reference plates under {images_dir}; set FEATHERFRAME_PLATES_DIR")
    return refs


def api_key(args) -> str:
    if args.key_from_db:
        from featherframe.config import load_config
        from featherframe.db import Database
        key = load_config(Database(paths.db_path())).imagegen_api_key
    else:
        key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        sys.exit("no API key: set OPENAI_API_KEY or pass --key-from-db")
    return key


def _sidecar(path: Path, **fields) -> None:
    path.with_suffix(".json").write_text(json.dumps(fields, indent=2))


def generate(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    refs = reference_plates()
    steps = list(STEPS) if args.step == "all" else [args.step]
    if args.dry_run:
        print("refs:", *[p.name for p in refs], sep="\n  ")
        for s in steps:
            print(f"\n==== {s} ====\n" + PROMPTS[s])
        return
    model = genart.OpenAIImageModel(api_key(args), model=args.model, quality=args.quality)
    base = Path(args.base) if args.base else out / "base.png"
    for step in steps:
        for n in range(args.candidates):
            suffix = "" if args.candidates == 1 else f"-{n + 1}"
            dest = out / f"{step}{suffix}.png"
            if step == "base":
                step_refs = refs
            else:
                if not base.exists():
                    sys.exit(f"{base} missing: draw the bough first (or pass --base)")
                step_refs = [base] + refs[:2]
            prompt = PROMPTS[step]
            started = time.time()
            print(f"drawing {dest.name} ({model.name}, {model.quality}) …", flush=True)
            png = model.generate(prompt, GEN_SIZE, step_refs)
            Image.open(io.BytesIO(png)).verify()
            dest.write_bytes(png)
            _sidecar(dest, step=step, model=model.name, quality=model.quality,
                     size=GEN_SIZE, prompt=prompt, reference_images=[p.name for p in step_refs],
                     created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     elapsed_s=round(time.time() - started, 1))
            print(f"  {dest} in {time.time() - started:.0f}s", flush=True)


# -- cut ---------------------------------------------------------------------
def _gray(path: Path) -> Image.Image:
    """The generated sheet as gray with clean paper. The plates' own
    normaliser sets its black point at the 3.5th percentile, which on a
    sheet that is nine-tenths bare paper lands in the middle of the ink and
    turns a washed bough into a silhouette — so: paper (the 90th
    percentile) to white, the darkest ink (0.1th) to black, linear."""
    arr = np.asarray(plate.load_gray(path), dtype=np.float32)
    lo, hi = np.percentile(arr, 0.1), np.percentile(arr, 90.0)
    if hi - lo < 1e-3:
        return Image.fromarray(arr.astype(np.uint8))
    return Image.fromarray(np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8))


def _align(base: np.ndarray, other: np.ndarray, window=None) -> tuple[int, int]:
    """Translation (dx, dy) that moves `other` onto `base`, by phase
    correlation over an optional (x0, y0, x1, y1) window."""
    if window:
        x0, y0, x1, y1 = window
        base, other = base[y0:y1, x0:x1], other[y0:y1, x0:x1]
    a = 255.0 - base.astype(np.float32)
    b = 255.0 - other.astype(np.float32)
    a -= a.mean(); b -= b.mean()
    fa, fb = np.fft.fft2(a), np.fft.fft2(b)
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-6
    corr = np.real(np.fft.ifft2(cross))
    dy, dx = np.unravel_index(np.argmax(corr), corr.shape)
    h, w = corr.shape
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return int(dx), int(dy), float(corr.max())


def _resample(arr: np.ndarray, scale: float) -> np.ndarray:
    im = Image.fromarray(arr)
    return np.asarray(im.resize((max(1, round(im.width * scale)),
                                 max(1, round(im.height * scale))), Image.LANCZOS))


def _register(base: np.ndarray, other: np.ndarray, window=None,
              scales=np.arange(0.84, 1.17, 0.01)) -> tuple[float, int, int]:
    """(scale, dx, dy) that lays `other` over `base`: the edits come back
    redrawn at a slightly different size and place (the model shifts the box
    to make room for a bird), so search a small range of scales and take the
    sharpest phase-correlation peak."""
    best = None
    h, w = base.shape
    for sc in scales:
        o = _resample(other, float(sc))
        # Same-size canvases for the FFT: crop or pad `o` to base's shape.
        canvas = np.full_like(base, 255)
        oh, ow = min(h, o.shape[0]), min(w, o.shape[1])
        canvas[:oh, :ow] = o[:oh, :ow]
        dx, dy, peak = _align(base, canvas, window)
        if best is None or peak > best[3]:
            best = (float(sc), dx, dy, peak)
    return best[:3]


def _place(other: np.ndarray, scale: float, dx: int, dy: int,
           shape: tuple[int, int], padx: int) -> np.ndarray:
    """`other` registered onto a canvas of `shape` widened by `padx` white
    columns on the left, so content the edit put left of the sheet's own
    origin (a bird flying in) survives with negative coordinates."""
    o = _resample(other, scale)
    h, w = shape
    out = np.full((h, w + padx), 255, dtype=np.uint8)
    x0, y0 = padx + dx, dy
    sx0, sy0 = max(0, -x0), max(0, -y0)
    tx0, ty0 = max(0, x0), max(0, y0)
    cw = min(o.shape[1] - sx0, out.shape[1] - tx0)
    ch = min(o.shape[0] - sy0, out.shape[0] - ty0)
    if cw > 0 and ch > 0:
        out[ty0:ty0 + ch, tx0:tx0 + cw] = o[sy0:sy0 + ch, sx0:sx0 + cw]
    return out


def _dilate(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask
    im = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * px + 1))
    return np.asarray(im) > 0


def _erode(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask
    im = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(2 * px + 1))
    return np.asarray(im) > 0


def _open(mask: np.ndarray, px: int) -> np.ndarray:
    return _dilate(_erode(mask, px), px)


def _components(mask: np.ndarray) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
    """4-connected components of a bool mask, largest first: (mask, bbox).
    Plain numpy (no scipy on the Pi or the box): label by repeated
    scanline flood fill over the remaining pixels."""
    remaining = mask.copy()
    h, w = mask.shape
    comps = []
    ys, xs = np.nonzero(remaining)
    for y0, x0 in zip(ys, xs):
        if not remaining[y0, x0]:
            continue
        comp = np.zeros_like(mask)
        stack = [(int(y0), int(x0))]
        remaining[y0, x0] = False
        while stack:
            y, x = stack.pop()
            # run left/right along the row, then seed the rows above/below
            xl = x
            while xl > 0 and remaining[y, xl - 1]:
                xl -= 1; remaining[y, xl] = False
            xr = x
            while xr < w - 1 and remaining[y, xr + 1]:
                xr += 1; remaining[y, xr] = False
            comp[y, xl:xr + 1] = True
            for ny in (y - 1, y + 1):
                if 0 <= ny < h:
                    row = remaining[ny, xl:xr + 1]
                    for i in np.nonzero(row)[0]:
                        nx = xl + int(i)
                        if remaining[ny, nx]:
                            remaining[ny, nx] = False
                            stack.append((ny, nx))
        cys, cxs = np.nonzero(comp)
        comps.append((comp, (int(cxs.min()), int(cys.min()), int(cxs.max()) + 1, int(cys.max()) + 1)))
    comps.sort(key=lambda c: -int(c[0].sum()))
    return comps


def _ink_bbox(arr: np.ndarray, thr: int = 235) -> tuple[int, int, int, int]:
    ys, xs = np.where(arr < thr)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _rgba_ink(gray: np.ndarray) -> Image.Image:
    """Black ink on transparent: alpha = darkness, as the old boot art is."""
    a = (255 - gray).astype(np.uint8)
    rgba = np.zeros(gray.shape + (4,), dtype=np.uint8)
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA")


def _added_bird(E: np.ndarray, H: np.ndarray, thr: int, clearance: int, pad: int,
                touching_ok: bool, twig: int = 5, detail: int = 10
                ) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """The bird an edit added, as (mask, bbox): the edit's ink off the base
    sheet's own ink (grown by `clearance`), largest blob first. The model
    also redraws twigs a little — a lengthened twig is ink off the base that
    TOUCHES it, so for a bird that must fly on bare paper those blobs are
    dropped (`touching_ok=False`); a perched bird stands on its twig and
    keeps the largest blob regardless."""
    base = _dilate(H < thr, clearance)
    off = (E < thr) & ~base
    # A lengthened twig reaches the bird and would join it into one blob;
    # opening at twig thickness separates bodies from twigs, and the bird's
    # own fine parts (bill, toes, feather tips) come back from `off` within
    # `detail` px of its body.
    body = _open(off, twig)
    comps = _components(body)
    if not comps:
        sys.exit("no added bird found in the edit")
    # The bird is the biggest body by far; twig remnants are small. (The
    # bird may overlap a base twig the model erased — the caller moves its
    # box onto bare paper afterwards — so touching is no ground to drop it.)
    bird, _ = comps[0]
    bird = bird | (off & _dilate(bird, detail))
    ys, xs = np.nonzero(bird)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    # Detached feather tips and the like: small satellites within reach.
    for comp, (cx0, cy0, cx1, cy1) in comps[1:]:
        if (comp.sum() < bird.sum() * 0.05
                and cx0 < x1 + 2 * pad and cx1 > x0 - 2 * pad
                and cy0 < y1 + 2 * pad and cy1 > y0 - 2 * pad):
            bird |= comp
            x0, y0 = min(x0, cx0), min(y0, cy0)
            x1, y1 = max(x1, cx1), max(y1, cy1)
    box = (max(0, x0 - pad), max(0, y0 - pad), min(E.shape[1], x1 + pad), min(E.shape[0], y1 + pad))
    return bird, box


def _refine_local(base: np.ndarray, other: np.ndarray, box, reach: int = 40,
                  margin: int = 20) -> tuple[int, int]:
    """Small (dx, dy) that best lays `other`'s twig onto `base`'s around
    `box` (the added bird): mean |diff| over the base's own ink in a window
    round the box. The bird itself sits on base paper, so it is outside the
    mask and cannot bias the fit."""
    x0, y0, x1, y1 = box
    wx0, wy0 = max(reach, x0 - margin), max(reach, y0 - margin)
    wx1, wy1 = min(base.shape[1] - reach, x1 + margin), min(base.shape[0] - reach, y1 + margin)
    ref = base[wy0:wy1, wx0:wx1].astype(np.float32)
    mask = ref < 235
    if not mask.any():
        return 0, 0
    best = None
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            cand = other[wy0 - dy:wy1 - dy, wx0 - dx:wx1 - dx].astype(np.float32)
            err = float(np.abs(cand - ref)[mask].mean())
            if best is None or err < best[0]:
                best = (err, dx, dy)
    return best[1], best[2]


def cut(args) -> None:
    raw = Path(args.raw)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    H = np.asarray(_gray(raw / args.base_file))
    fly_g = np.asarray(_gray(raw / args.fly_file))
    perch_g = np.asarray(_gray(raw / args.perch_file))
    # The edits were told to reproduce the sheet in place at the same size.
    # A thin bough gives phase correlation too little to lock a SCALE on
    # (the search wandered 90 px off), so the sheets are registered by
    # translation only — the model keeps the sheet's scale for a sparse
    # subject — and the perched bird's twig is then refined locally.
    scales = np.arange(0.84, 1.17, 0.01) if args.scale_search else (1.0,)
    fly_reg = _register(H, fly_g, scales=scales)
    perch_reg = _register(H, perch_g, scales=scales)
    F = _place(fly_g, *fly_reg, H.shape, 0)
    P = _place(perch_g, *perch_reg, H.shape, 0)
    _, rough = _added_bird(P, H, 235, args.clearance, args.pad, touching_ok=True)
    ddx, ddy = _refine_local(H, P, rough)
    P = _place(P, 1.0, ddx, ddy, P.shape, 0)
    print(f"fly (scale,dx,dy)={fly_reg}, perch={perch_reg} refined by ({ddx},{ddy})")
    if args.scale != 1.0:
        # Scale the whole sheets now, so every cut below shares one
        # resampling and the perch patch lands on the twig pixel-for-pixel.
        H, F, P = (_resample(a, args.scale) for a in (H, F, P))
    if args.top_pad:
        # The model composes to the sheet's very top and the mat hides the
        # first ~4 %: drop the whole composition by this much paper.
        def _pad(a):
            return np.concatenate([np.full((args.top_pad, a.shape[1]), 255, np.uint8), a])
        H, F, P = _pad(H), _pad(F), _pad(P)
    _lift_lut = np.clip(255.0 * (np.arange(256) / 255.0) ** args.lift, 0, 255).astype(np.uint8)

    def lifted(a: np.ndarray) -> np.ndarray:
        # Output-only gamma: analysis stays on the normalised sheets, so
        # the thresholds below don't drift with the lift.
        return _lift_lut[a]

    pad = args.pad
    # -- base: the whole sheet — the bake lays it full-bleed like a plate ----
    x0, y0, x1, y1 = 0, 0, H.shape[1], min(H.shape[0], args.art_bottom or H.shape[0])
    _rgba_ink(lifted(H[y0:y1, x0:x1])).save(out / "plate_base.png")

    # -- fly: the ink that sits where the bough sheet is bare paper -----------
    bird, (bx0, by0, bx1, by1) = _added_bird(F, H, 235, args.clearance, pad, touching_ok=False)
    soft = _dilate(bird, 2)                       # keep the wash edge round the ink
    fly_box = np.where(soft[by0:by1, bx0:bx1], F[by0:by1, bx0:bx1], 255)
    _rgba_ink(lifted(fly_box)).save(out / "plate_fly.png")
    # The edit's bird must land on bare paper in the BASE sheet — the model
    # shortens or moves twigs to make room, and in the base frame the bird
    # may sit over a twig that isn't there in the edit. Find the nearest
    # place (prefer moving away from the bough, then vertically) where the
    # whole box is paper and still inside the panel's mat, then slide it
    # back toward the bough to --fly-gap.
    bw, bh = bx1 - bx0, by1 - by0
    inset = int(H.shape[1] * args.mat_inset)
    toward = -1 if (bx0 + bx1) / 2 > (x0 + x1) / 2 else 1

    def clear(dx, dy):
        a, b, c, d = bx0 + dx, bx1 + dx, by0 + dy, by1 + dy
        return (inset <= a and b <= H.shape[1] - inset and inset <= c and d <= y1
                and (H[c:d, a:b] >= 235).all())
    best = None
    for dy in range(-args.fly_reach, args.fly_reach + 1):
        for dx in range(0, args.fly_reach + 1):
            ddx = -toward * dx                     # away from the bough only
            if clear(ddx, dy):
                cost = abs(dx) + 1.5 * abs(dy)
                if best is None or cost < best[0]:
                    best = (cost, ddx, dy)
    if best is None:
        print("warning: no clear paper for the fly box — the bird will paint over twig")
        ddx, dy = 0, 0
    else:
        _, ddx, dy = best
        while clear(ddx + toward, dy):
            ddx += toward
        ddx -= toward * args.fly_gap
    bx0, bx1, by0, by1 = bx0 + ddx, bx1 + ddx, by0 + dy, by1 + dy
    print(f"fly box moved by ({ddx},{dy}) to clear paper")

    # -- perch: the bird alone, the base's own twig showing through ----------
    # The model redraws the bough on its own line, so the edit's twig can't
    # be carried over; after the local fit the edit's twig lies within a few
    # px of the base's, and the bird is cut as ink OFF the base's (grown)
    # twig — its toes end where the base twig begins, which reads as a bird
    # standing on it. The base twig fills the rest of the window.
    bird, (qx0, qy0, qx1, qy1) = _added_bird(P, H, args.perch_thr, args.perch_clearance, pad,
                                             touching_ok=True)
    bird = _erode(_dilate(bird, 4), 4)            # close gaps in pale plumage
    # Stand the bird on the BASE twig by its feet: the lowest ink of the
    # bird is its toes; find the base twig's top edge nearest below them
    # and shift the bird (and its box) so the toes rest on it.
    ys, xs = np.nonzero(bird)
    foot_y = int(ys.max())
    foot_x = int(np.median(xs[ys >= foot_y - 4]))
    base_ink = H < 235
    best = None
    for dx in range(-args.perch_reach, args.perch_reach + 1):
        col = foot_x + dx
        if not (0 <= col < H.shape[1]):
            continue
        rows = np.nonzero(base_ink[max(0, foot_y - 40):foot_y + 120, col])[0]
        if len(rows):
            top = int(rows[0]) + max(0, foot_y - 40)
            cost = abs(dx) + 0.5 * abs(top - foot_y)
            if best is None or cost < best[0]:
                best = (cost, dx, top - foot_y - 1)
    if best is None:
        print("warning: no base twig under the perched bird's feet — leaving it where drawn")
        sdx, sdy = 0, 0
    else:
        _, sdx, sdy = best
    print(f"perched bird stood on the twig by ({sdx},{sdy})")
    P = _place(P, 1.0, sdx, sdy, P.shape, 0)
    bird = _place(bird.astype(np.uint8), 1.0, sdx, sdy, bird.shape, 0).astype(bool) if False else \
        np.roll(np.roll(bird, sdy, axis=0), sdx, axis=1)
    qx0, qx1, qy0, qy1 = qx0 + sdx, qx1 + sdx, qy0 + sdy, qy1 + sdy
    soft = _dilate(bird, 2)
    g = lifted(P[qy0:qy1, qx0:qx1])
    a = np.asarray(Image.fromarray(soft[qy0:qy1, qx0:qx1].astype(np.uint8) * 255)
                   .filter(ImageFilter.GaussianBlur(1)), dtype=np.uint8)
    patch = np.zeros(g.shape + (4,), dtype=np.uint8)
    patch[..., 0] = patch[..., 1] = patch[..., 2] = g
    patch[..., 3] = np.where(bird[qy0:qy1, qx0:qx1], 255, a)
    Image.fromarray(patch, "RGBA").save(out / "plate_perch.png")

    layout = {
        "sheet": [int(H.shape[1]), int(H.shape[0])],
        "scale": args.scale,
        "lift": args.lift,
        "base_crop": [int(x0), int(y0), int(x1), int(y1)],
        "fly_at": [int(bx0 - x0), int(by0 - y0)],
        "perch_at": [int(qx0 - x0), int(qy0 - y0)],
    }
    (out / "plate_layout.json").write_text(json.dumps(layout, indent=2))
    print(json.dumps(layout))
    if args.preview:
        base = Image.open(out / "plate_base.png")
        # Room for patches that fall outside the base crop.
        ox = max(0, -layout["fly_at"][0], -layout["perch_at"][0])
        oy = max(0, -layout["fly_at"][1], -layout["perch_at"][1])
        cw = ox + max(base.width, layout["fly_at"][0] + Image.open(out / "plate_fly.png").width,
                      layout["perch_at"][0] + Image.open(out / "plate_perch.png").width)
        ch = oy + max(base.height, layout["fly_at"][1] + Image.open(out / "plate_fly.png").height,
                      layout["perch_at"][1] + Image.open(out / "plate_perch.png").height)
        sheet = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
        sheet.alpha_composite(base, (ox, oy))
        panels = [sheet.copy()]
        for name, at in (("plate_fly.png", layout["fly_at"]), ("plate_perch.png", layout["perch_at"])):
            s = sheet.copy()
            s.alpha_composite(Image.open(out / name), (ox + at[0], oy + at[1]))
            panels.append(s)
        strip = Image.new("RGB", (3 * cw, ch), "white")
        for i, s in enumerate(panels):
            strip.paste(s.convert("RGB"), (i * cw, 0))
        strip.save(out / "plate_preview.png")
        print(f"preview: {out / 'plate_preview.png'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="buy the three raw draws")
    g.add_argument("--out", default=str(ART / "raw"))
    g.add_argument("--step", choices=["all", *STEPS], default="all")
    g.add_argument("--base", help="raw bough PNG to edit (default: <out>/base.png)")
    g.add_argument("--candidates", type=int, default=1, help="draws per step")
    g.add_argument("--model", default="gpt-image-2")
    g.add_argument("--quality", default="high")
    g.add_argument("--key-from-db", action="store_true",
                   help="read imagegen_api_key from the server's own config db")
    g.add_argument("--dry-run", action="store_true", help="print prompts and refs only")
    g.set_defaults(fn=generate)
    c = sub.add_parser("cut", help="cut the boot art out of the raw draws")
    c.add_argument("--raw", default=str(ART / "raw"))
    c.add_argument("--out", default=str(ART))
    c.add_argument("--base-file", default="base.png")
    c.add_argument("--fly-file", default="fly.png")
    c.add_argument("--perch-file", default="perch.png")
    c.add_argument("--top-pad", type=int, default=60,
                   help="rows of paper added above the sheet so its top clears the mat")
    c.add_argument("--art-bottom", type=int, default=1361,
                   help="rows of the scaled sheet the plate's art box shows (the wordmark's "
                        "caption gap begins there)")
    c.add_argument("--scale", type=float, default=1404 / 1024,
                   help="resample the sheets to this factor before cutting (default: the "
                        "sheet's width to the panel's; the bake composites the art 1:1)")
    c.add_argument("--scale-search", action="store_true",
                   help="also search the edits' scale when registering (off: translation only)")
    c.add_argument("--lift", type=float, default=1.0,
                   help="output-only gamma (<1 lightens the wood on the glass)")
    c.add_argument("--pad", type=int, default=6, help="px of paper kept round each cut")
    c.add_argument("--fly-gap", type=int, default=16,
                   help="px of bare paper kept between the flying wren's box and the bough")
    c.add_argument("--fly-reach", type=int, default=260,
                   help="px the flying wren may be moved to find bare paper for its box")
    c.add_argument("--mat-inset", type=float, default=0.04,
                   help="fraction of the sheet's width under the mat at each side")
    c.add_argument("--clearance", type=int, default=10,
                   help="px the bough's ink is grown by before hunting the flying bird")
    c.add_argument("--perch-thr", type=int, default=225,
                   help="gray below which the edit's pixels can count as the perched bird")
    c.add_argument("--perch-reach", type=int, default=80,
                   help="px the perched wren may slide sideways to find the base twig under its feet")
    c.add_argument("--perch-clearance", type=int, default=4,
                   help="px the base twig is grown by before cutting the perched bird off it")
    c.add_argument("--preview", action="store_true", help="also write plate_preview.png")
    c.set_defaults(fn=cut)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
