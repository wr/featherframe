#!/usr/bin/env python3
"""Draw the boot-screen art through the plates' own image pipeline.

The boot sequence is four screens on one birdhouse: the house alone, a wren
flying in, the house alone again, the wren's head in the entrance hole. The
house must be PIXEL-IDENTICAL on all four — the firmware diffs consecutive
screens and repaints only the changed windows — so the art is three files:

    plate_house.png   the birdhouse (black ink on transparent, like the old art)
    plate_fly.png     the wren in flight, a cut-out that lands on bare paper
    plate_peek.png    the entrance hole with the wren's head in it, a disc-
                      masked patch that lands exactly over the empty hole
    plate_layout.json where the fly and peek patches sit, in plate_house.png
                      pixel space, plus the hole's disc

Two phases:

    generate   buys the images from the configured image model, the same
               ``OpenAIImageModel`` + ``/v1/images/edits`` with real Havell
               plates as style references that draws the frame's AI plates.
               Draw 1: the empty house (refs: the folio's wren plates).
               Draw 2: an edit of draw 1 adding a wren in flight.
               Draw 3: an edit of draw 1 with the wren's head in the hole.
               The image model has no notion of "unchanged", so draws 2 and 3
               come back as whole re-drawn sheets — only the bird is taken
               from each, which is why the house is drawn once and reused.
    cut        deterministic: normalises the paper like a real scan
               (``plate.extract_generated``), aligns each edit to the house by
               phase correlation, lifts the flying bird off the bare paper and
               the head out of the hole disc, and writes the four files above.

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
GEN_SIZE = "1024x1536"          # portrait: the box high, the post running off the foot

# The folio's own wrens, style references for every draw: plate 83 is the House
# Wren at a nest cavity (an old hat on a snag), plate 78 the Carolina Wren, plate
# 18 Bewick's. Scientific names as in the curated index.
REF_SPECIES = ["Troglodytes aedon", "Thryothorus ludovicianus", "Thryomanes bewickii"]
BIRD = "Carolina Wren (Thryothorus ludovicianus)"

# -- prompts -----------------------------------------------------------------
# Principles, never examples (see the genart prompt history): the model treats
# any named instance as a target to hit.
_HOUSE_OPEN = (
    "A hand-colored copperplate engraving with aquatint in the exact style of John James "
    "Audubon's 'The Birds of America' (Havell edition, 1827-1838), depicting a small "
    "wooden nest box — a birdhouse — on a squared wooden post, drawn as the period's "
    "engravers drew a nesting site: with a naturalist's accuracy and a carpenter's plain "
    "honesty. The background is bright, near-white wove paper left completely untouched — "
    "no sepia tint, no cream wash, no aging, no vignette, no border.\n\n"
)
_HOUSE_COLOR = (
    "Color as the colorists actually worked: the wood in low-chroma umber, ochre and gray "
    "built from engraved grain, the vine's greens muted sage-olive, never grass-green; "
    "whites are reserved bare paper with gray modeling, never opaque paint; nothing glows.\n\n"
)
_HOUSE_COMPOSE = (
    "The nest box is the sheet's whole subject and fills it. It is a simple gabled box of "
    "sawn boards with a pitched plank roof, one round entrance hole set high in the front "
    "face under the eaves, and a short perch peg below the hole; the box is fastened to a "
    "squared post that runs straight down and off the bottom edge of the sheet, cut flush. "
    "The box is seen a little from below and turned slightly so that its front face looks "
    "toward the left of the sheet, the front face the largest plane, one side wall and the "
    "roof's edge reading in honest perspective. The entrance hole is EMPTY: the box is "
    "unoccupied, there is no bird anywhere on the sheet, and the hole's interior is a plain "
    "deep dark. A single climbing vine of one real, identifiable species, its leaves "
    "individually veined, twines up the post and along the box's underside, drawn to "
    "botanical-plate standard and kept spare so the box carries the sheet. Bare paper "
    "surrounds the box above and on both sides; only the post touches the bottom edge. The "
    "wood is drawn as engraved grain and plank lines, weathered as a real box on a real "
    "post weathers, never decorated.\n\n"
)
_FOOTER = (
    "No text: no title, no names, no lettering, no numerals, no signature, no border, "
    "no frame line."
)
HOUSE_PROMPT = _HOUSE_OPEN + genart._P_PROCESS + _HOUSE_COLOR + _HOUSE_COMPOSE + _FOOTER

_EDIT_OPEN = (
    "The first image is a finished sheet from this folio: a wooden nest box on a post with "
    "an empty entrance hole. Reproduce that sheet exactly — every board, plank line, grain "
    "stroke, the vine, and their placement on the paper unchanged, the same size on the "
    "same bright untouched paper — "
)
FLY_PROMPT = (
    _EDIT_OPEN
    + "and add one figure only: a " + BIRD + " in full flight, arriving at the box from "
    "the open paper on the LEFT of the sheet, wings spread to their full extent, tail "
    "cocked, bill pointed toward the entrance hole, life-size relative to the box as a "
    "real wren is beside a real nest box. The bird stays entirely on bare paper at about "
    "the height of the entrance hole — clear of the box, the roof, the post and the vine, "
    "overlapping nothing, a clear gap of paper between it and the box. The entrance hole "
    "stays empty. Draw the bird in the very same engraved-and-washed manner as the rest "
    "of the sheet, exactly as the other images show the folio drew this species.\n\n"
    + genart._P_PROCESS + genart._P_ANATOMY + _FOOTER
)
PEEK_PROMPT = (
    _EDIT_OPEN
    + "with one change only: a " + BIRD + " now occupies the box. Its head and the top "
    "of its breast fill the round entrance hole, the bird looking out and to one side, "
    "the head kept entirely within the circle of the opening with the dark of the "
    "interior behind it, nothing of the bird outside the hole. No bird appears anywhere "
    "else on the sheet, and nothing else changes. Draw the head in the very same "
    "engraved-and-washed manner as the rest of the sheet, exactly as the other images "
    "show the folio drew this species.\n\n"
    + genart._P_PROCESS + genart._P_ANATOMY + _FOOTER
)


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
    steps = ["house", "fly", "peek"] if args.step == "all" else [args.step]
    if args.dry_run:
        print("refs:", *[p.name for p in refs], sep="\n  ")
        for s in steps:
            print(f"\n==== {s} ====\n" + {"house": HOUSE_PROMPT, "fly": FLY_PROMPT,
                                           "peek": PEEK_PROMPT}[s])
        return
    model = genart.OpenAIImageModel(api_key(args), model=args.model, quality=args.quality)
    house = Path(args.house) if args.house else out / "house.png"
    for step in steps:
        for n in range(args.candidates):
            suffix = "" if args.candidates == 1 else f"-{n + 1}"
            dest = out / f"{step}{suffix}.png"
            if step == "house":
                prompt, step_refs = HOUSE_PROMPT, refs
            else:
                if not house.exists():
                    sys.exit(f"{house} missing: draw the house first (or pass --house)")
                prompt = FLY_PROMPT if step == "fly" else PEEK_PROMPT
                step_refs = [house] + refs[:2]
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
    """The generated sheet the way the frame would show it: paper normalised
    like a real scan, no crop."""
    return plate.extract_generated(path)


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


def find_hole(house: np.ndarray) -> tuple[int, int, int, int]:
    """The entrance hole: the roundest near-black blob in the upper half of
    the sheet, as an ellipse (cx, cy, rx, ry) — the box is drawn in
    perspective, so the round hole is an upright ellipse on the sheet. The
    normalised paper puts the hole's interior at the black point and the
    shadowed wall a little above it, so the threshold sits just over black."""
    dark = _open(house < 15, 2)
    best = None
    for comp, (x0, y0, x1, y1) in _components(dark):
        w, h = x1 - x0, y1 - y0
        area = int(comp.sum())
        if y0 > house.shape[0] * 0.6 or w < 20 or h < 20:
            continue
        roundness = area / (np.pi * (w / 2) * (h / 2))   # ~1 for a filled ellipse
        if roundness < 0.75 or max(w, h) / min(w, h) > 2.2:
            continue
        if best is None or area > best[0]:
            best = (area, (x0 + x1) // 2, (y0 + y1) // 2, w // 2, h // 2)
    if best is None:
        sys.exit("could not find the entrance hole (no filled dark ellipse)")
    return best[1:]


def _refine(base: np.ndarray, other: np.ndarray, cx: int, cy: int, rx: int, ry: int,
            reach: int = 10) -> tuple[int, int]:
    """Small (dx, dy) that best matches `other` to `base` on an elliptical
    ring around the hole — the hole interior is excluded because that is
    exactly where the two are meant to differ."""
    R = max(rx, ry)
    y0, y1 = max(0, cy - 3 * R), min(base.shape[0], cy + 3 * R)
    x0, x1 = max(0, cx - 3 * R), min(base.shape[1], cx + 3 * R)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    e = ((xx - cx) / (rx + 3)) ** 2 + ((yy - cy) / (ry + 3)) ** 2
    ring = (e > 1.0) & (e < 6.0)
    ref = base[y0:y1, x0:x1].astype(np.float32)
    best = (None, 0, 0)
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            sy0, sx0 = y0 - dy, x0 - dx
            if sy0 < 0 or sx0 < 0 or sy0 + (y1 - y0) > other.shape[0] or sx0 + (x1 - x0) > other.shape[1]:
                continue
            cand = other[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)].astype(np.float32)
            err = float(np.abs(cand - ref)[ring].mean())
            if best[0] is None or err < best[0]:
                best = (err, dx, dy)
    return best[1], best[2]


def _rgba_ink(gray: np.ndarray) -> Image.Image:
    """Black ink on transparent: alpha = darkness, as the old boot art is."""
    a = (255 - gray).astype(np.uint8)
    rgba = np.zeros(gray.shape + (4,), dtype=np.uint8)
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA")


def cut(args) -> None:
    raw = Path(args.raw)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    house_g = _gray(raw / args.house_file)
    fly_g = _gray(raw / args.fly_file)
    peek_g = _gray(raw / args.peek_file)
    H0 = np.asarray(house_g)
    # The edits were told to reproduce the sheet in place at the same size;
    # what comes back is redrawn a little smaller and shifted, so register
    # each one (scale + translation) onto the house before cutting.
    fly_reg = _register(H0, np.asarray(fly_g))
    peek_reg = _register(H0, np.asarray(peek_g))
    cx, cy, rx, ry = find_hole(H0)
    PADX = args.padx
    H = np.full((H0.shape[0], H0.shape[1] + PADX), 255, dtype=np.uint8)
    H[:, PADX:] = H0
    F = _place(np.asarray(fly_g), *fly_reg, H0.shape, PADX)
    P = _place(np.asarray(peek_g), *peek_reg, H0.shape, PADX)
    cx += PADX
    # The global fit is the whole sheet's best compromise; the hole patch
    # needs the wood right around the hole to sit exactly, so refine the
    # peek's offset on the ring of grain just outside the ellipse.
    ddx, ddy = _refine(H, P, cx, cy, rx, ry)
    P = _place(P, 1.0, ddx, ddy, P.shape, 0)
    print(f"hole at ({cx - PADX},{cy}) rx={rx} ry={ry}; fly (scale,dx,dy)={fly_reg}, "
          f"peek={peek_reg} refined by ({ddx},{ddy})")
    if args.foot:
        foot = args.foot
        H, F, P = H[:foot], F[:foot], P[:foot]
    if args.scale != 1.0:
        # Scale the whole sheets now, so every cut below shares one
        # resampling and the peek patch lands on the hole pixel-for-pixel.
        H, F, P = (_resample(a, args.scale) for a in (H, F, P))
        cx, cy = round(cx * args.scale), round(cy * args.scale)
        rx, ry = round(rx * args.scale), round(ry * args.scale)

    # -- house: crop to its ink, keep the post cut flush at the foot ----------
    pad = args.pad
    x0, y0, x1, y1 = _ink_bbox(H)
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1 = min(H.shape[1], x1 + pad)
    y1 = H.shape[0] if y1 >= H.shape[0] - 2 else min(H.shape[0], y1 + pad)
    house_crop = H[y0:y1, x0:x1]
    _rgba_ink(house_crop).save(out / "plate_house.png")

    # -- fly: the ink that sits where the house sheet is bare paper -----------
    house_ink = _dilate(H < 235, args.clearance)
    fly_ink = _open((F < 235) & ~house_ink, 1)
    comps = _components(fly_ink)
    if not comps:
        sys.exit("no flying bird found on bare paper in the fly edit")
    bird, (bx0, by0, bx1, by1) = comps[0]
    # Detached feather tips and the like: any smaller component within reach.
    for comp, (cx0, cy0, cx1, cy1) in comps[1:]:
        if (cx0 < bx1 + 2 * pad and cx1 > bx0 - 2 * pad
                and cy0 < by1 + 2 * pad and cy1 > by0 - 2 * pad):
            bird |= comp
            bx0, by0 = min(bx0, cx0), min(by0, cy0)
            bx1, by1 = max(bx1, cx1), max(by1, cy1)
    bx0, by0 = max(0, bx0 - pad), max(0, by0 - pad)
    bx1, by1 = min(H.shape[1], bx1 + pad), min(H.shape[0], by1 + pad)
    fly_box = np.where(bird[by0:by1, bx0:bx1] | ~house_ink[by0:by1, bx0:bx1],
                       F[by0:by1, bx0:bx1], 255)
    _rgba_ink(fly_box).save(out / "plate_fly.png")
    # The model leaves the bird a generous gap; slide its box toward the
    # house as far as the sheet stays bare paper under the whole box (the
    # box must hold nothing but the bird, or its binary window would take
    # house grain with it), minus --fly-gap.
    shift = 0
    while (bx1 + shift + 1 <= H.shape[1]
           and (H[by0:by1, bx0 + shift + 1:bx1 + shift + 1] >= 250).all()):
        shift += 1
    shift = max(0, shift - args.fly_gap)
    bx0, bx1 = bx0 + shift, bx1 + shift
    if (H[by0:by1, bx0:bx1] < 250).any():
        print("warning: the fly box overlaps house ink — the bird will paint over it")

    # -- peek: the hole disc, lifted from the aligned edit ---------------------
    RX, RY = rx + args.disc_grow, ry + args.disc_grow
    px0, py0 = max(0, cx - RX - pad), max(0, cy - RY - pad)
    px1, py1 = cx + RX + pad, cy + RY + pad
    yy, xx = np.mgrid[py0:py1, px0:px1]
    # Signed distance to the ellipse edge, in px along the shorter radius.
    dist = (np.sqrt(((xx - cx) / RX) ** 2 + ((yy - cy) / RY) ** 2) - 1.0) * min(RX, RY)
    alpha = np.clip(1.0 - dist / max(1, args.feather), 0, 1)
    patch = np.zeros((py1 - py0, px1 - px0, 4), dtype=np.uint8)
    g = P[py0:py1, px0:px1]
    patch[..., 0] = patch[..., 1] = patch[..., 2] = g
    patch[..., 3] = (alpha * 255).astype(np.uint8)
    Image.fromarray(patch, "RGBA").save(out / "plate_peek.png")

    # The roof's widest row is the house body's visual centre: the bake
    # centres on that, not on the crop, whose vine sprawls to one side.
    top = house_crop[: house_crop.shape[0] * 2 // 5]
    widths = [(np.where(row < 235)[0]) for row in top]
    widest = max((w for w in widths if len(w)), key=lambda w: w.max() - w.min())
    body_cx = int((widest.min() + widest.max()) // 2)
    layout = {
        "sheet": [int(H.shape[1]), int(H.shape[0])],
        "body_cx": body_cx,
        "scale": args.scale,
        "foot": args.foot,
        "house_crop": [int(x0), int(y0), int(x1), int(y1)],
        "hole": {"cx": int(cx - x0), "cy": int(cy - y0), "rx": int(rx), "ry": int(ry)},
        "fly_at": [int(bx0 - x0), int(by0 - y0)],
        "peek_at": [int(px0 - x0), int(py0 - y0)],
    }
    (out / "plate_layout.json").write_text(json.dumps(layout, indent=2))
    print(json.dumps(layout))
    if args.preview:
        fx, fy = layout["fly_at"]
        ox, oy = max(0, -fx), max(0, -fy)
        cw = max(x1 - x0, fx + (bx1 - bx0)) + ox
        ch = max(y1 - y0, fy + (by1 - by0)) + oy
        sheet = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
        sheet.alpha_composite(Image.open(out / "plate_house.png"), (ox, oy))
        fly_sheet = sheet.copy()
        fly_sheet.alpha_composite(Image.open(out / "plate_fly.png"), (ox + fx, oy + fy))
        peek_sheet = sheet.copy()
        px, py = layout["peek_at"]
        peek_sheet.alpha_composite(Image.open(out / "plate_peek.png"), (ox + px, oy + py))
        strip = Image.new("RGB", (3 * cw, ch), "white")
        for i, sh in enumerate((sheet, fly_sheet, peek_sheet)):
            strip.paste(sh.convert("RGB"), (i * cw, 0))
        strip.save(out / "plate_preview.png")
        print(f"preview: {out / 'plate_preview.png'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="buy the three raw draws")
    g.add_argument("--out", default=str(ART / "raw"))
    g.add_argument("--step", choices=["all", "house", "fly", "peek"], default="all")
    g.add_argument("--house", help="raw house PNG to edit (default: <out>/house.png)")
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
    c.add_argument("--house-file", default="house.png")
    c.add_argument("--fly-file", default="fly.png")
    c.add_argument("--peek-file", default="peek.png")
    c.add_argument("--scale", type=float, default=1.0,
                   help="resample the sheets to this factor before cutting (the bake "
                        "composites the art 1:1)")
    c.add_argument("--foot", type=int, default=0,
                   help="sheet row (before scaling) where the post is cut flush")
    c.add_argument("--padx", type=int, default=512,
                   help="white columns added left of the sheet so a bird the edit put "
                        "beyond the sheet's origin still lands")
    c.add_argument("--pad", type=int, default=6, help="px of paper kept round each cut")
    c.add_argument("--fly-gap", type=int, default=24,
                   help="px of bare paper kept between the flying wren's box and the house")
    c.add_argument("--clearance", type=int, default=10,
                   help="px the house's ink is grown by before hunting the flying bird")
    c.add_argument("--disc-grow", type=int, default=3, help="px the hole disc mask grows")
    c.add_argument("--feather", type=int, default=2, help="px of soft edge on the disc")
    c.add_argument("--preview", action="store_true", help="also write plate_preview.png")
    c.set_defaults(fn=cut)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
