"""What has been drawn, kept once (W-999).

Everything drawn is kept under a name that says what it is, and pictures,
frames and viewers point at it instead of holding a copy. A species that
comes back is composed again (cheap, and deterministic, so it lands on the
same ETag) and everything after that is found, not made: its colour twin,
each frame's finished output, each viewer's image. Nothing already uploaded
is uploaded again.

    frames/drawn/sheets/<etag>.png            a picture's gray sheet: its ETag is
                                              the hash of these pixels
    frames/drawn/sheets/<etag>-c<N>.png       its colour twin (COLOR_VERSION)
    frames/drawn/out/<etag>-<okey>.fff|.png   one frame's finished output
    frames/drawn/views/<sheet>-<view>-<f>.png one viewer's image

Every name starts with its picture's ETag. Nothing but the pruner deletes
here: the owner's Refresh draws a picture's files again in place. On hosted the folder is lazy: a start
downloads none of it, and a file comes down only when something reads it
(`hosted.local`).
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Optional

from PIL import Image

from . import hosted, paths
from .render import compose, pipeline

ETAG_LEN = 16
# The record: {used: {etag: iso}, outs: {okey: {pic, etag, at, f}},
# views: {name: {at, f}}, fresh: {etag: token}}. `f` is the Refresh token an
# output or view was drawn under: one drawn before its picture's last Refresh
# is drawn again, in place.
KV = "drawn"
KEEP_ETAGS = 40                 # the most recently used pictures kept, besides those in use
OUTS_PER_ETAG = 4               # a mat being tuned must not fill the store
VIEWS_PER_ETAG = 8


def root() -> Path:
    return paths.frames_dir() / "drawn"


def _dir(name: str) -> Path:
    d = root() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def sheet_path(etag: str) -> Path:
    return _dir("sheets") / f"{etag}.png"


def twin_path(etag: str) -> Path:
    return _dir("sheets") / f"{etag}-c{compose.COLOR_VERSION}.png"


def _libs() -> str:
    import numpy
    try:
        import numba
        nb = numba.__version__
    except Exception:  # noqa: BLE001 — no numba: the Python diffusion
        nb = "-"
    return f"{Image.__version__},{numpy.__version__},{nb}"


def fkey() -> str:
    """What finishes a sheet for a screen: the code (FINISH_VERSION, a bench
    dither override) and the libraries that do it. In every output's and
    view's name, so neither outlives a change to how it is drawn."""
    h = hashlib.sha256(f"{pipeline.FINISH_VERSION}|{pipeline.DITHER_OVERRIDE}|{_libs()}".encode())
    return h.hexdigest()[:6]


def okey(src: str) -> str:
    """A frame output's key: what it was finished from (`_output_src`, which
    names the sheet file and every setting that changes a pixel) and fkey."""
    return hashlib.sha256(f"{fkey()}|{src}".encode()).hexdigest()[:12]


def out_paths(etag: str, key: str) -> tuple[Path, Path]:
    base = _dir("out") / f"{etag}-{key}"
    return base.with_suffix(".fff"), base.with_suffix(".png")


def view_name(source: Path, view_key: str) -> str:
    """A view's name: the sheet file it is drawn from (`<etag>` or the twin's
    `<etag>-c<N>`), the view, and fkey. Also the ETag a viewer is served."""
    return f"{Path(source).stem}-{view_key}-{fkey()}"


def view_path(name: str) -> Path:
    return _dir("views") / f"{name}.png"


def rel(path: Path) -> str:
    return Path(path).relative_to(paths.data_dir()).as_posix()


def exists(path: Path) -> bool:
    return hosted.exists(path)


def read_bytes(path: Path) -> Optional[bytes]:
    """The file's bytes, fetched first on hosted; None when it is not there."""
    if not hosted.exists(path):
        return None
    p = hosted.local(path)
    try:
        return p.read_bytes()
    except OSError:
        return None


def whole(path: Path) -> bool:
    """Kept and readable. A file here may be torn (a Pi that lost power
    mid-write); one still at the front door is whole (R2 holds whole objects)."""
    if not Path(path).exists():
        return hosted.exists(path)
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001 — anything unreadable is not kept
        return False


def open_image(path: Path) -> Optional[Image.Image]:
    """The image, loaded; None when it is missing or cannot be read (a torn
    write on a Pi that lost power), which is then removed so it is drawn
    again rather than read again."""
    if not hosted.exists(path):
        return None
    p = hosted.local(path)
    try:
        with Image.open(p) as im:
            im.load()
            return im.copy()
    except (OSError, ValueError):
        hosted.remove(p)
        return None


_TMP = threading.local()


def _tmp(target: Path) -> Path:
    # Unique per thread: two threads drawing the same ETag must never write
    # one temporary file.
    n = getattr(_TMP, "n", 0) + 1
    _TMP.n = n
    return target.with_name(f"{target.name}.{os.getpid()}-{threading.get_ident()}-{n}.tmp")


def write_image(target: Path, img: Image.Image, compress_level: Optional[int] = 1) -> None:
    """Keep `img` at `target`, whole or not at all. Sheets are written fast
    (level 1); a preview, which every picture pushes, at the default level."""
    tmp = _tmp(target)
    try:
        img.save(tmp, format="PNG", **({} if compress_level is None else
                                       {"compress_level": compress_level}))
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def write_bytes(target: Path, data: bytes) -> None:
    tmp = _tmp(target)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def etag_of(path) -> str:
    return Path(path).name[:ETAG_LEN]


def files() -> list[Path]:
    """Every file kept, here or still at the front door."""
    return [p for d in ("sheets", "out", "views") for p in hosted.glob(_dir(d), "*")
            if not p.name.endswith(".tmp")]


SWEEP_AGE_S = 600   # a file no record names, younger than this, may be mid-draw


def touch(state: dict, etag: str, at: str) -> None:
    state.setdefault("used", {})[etag] = at


def prune(state: dict, pinned: set, pinned_files: set, now: float) -> int:
    """Keep every file something points at (`pinned_files`, paths in the data
    dir) and everything of the `pinned` ETags but their surplus variants; then
    the KEEP_ETAGS most recently used pictures, each with at most
    OUTS_PER_ETAG outputs and VIEWS_PER_ETAG views not pointed at, newest
    first by the record's own time. A file no record names goes too, unless
    it is new here (another thread may be drawing it). `state` is the kv row,
    changed in place. Returns the number of files removed."""
    used = state.setdefault("used", {})
    outs = state.setdefault("outs", {})
    views = state.setdefault("views", {})
    recent = sorted((e for e in used if e not in pinned), key=lambda e: used[e], reverse=True)
    keep = set(pinned) | set(recent[:KEEP_ETAGS])

    def newest(items: dict, etag_of_item, cap: int) -> set:
        by: dict[str, list] = {}
        for k, at in items.items():
            by.setdefault(etag_of_item(k), []).append((at, k))
        return {k for e, xs in by.items() if e in keep for _, k in sorted(xs, reverse=True)[:cap]}

    keep_outs = newest({k: o.get("at", "") for k, o in outs.items()},
                       lambda k: outs[k].get("pic", ""), OUTS_PER_ETAG)
    keep_views = newest({n: (v or {}).get("at", "") for n, v in views.items()},
                        lambda n: n[:ETAG_LEN], VIEWS_PER_ETAG)
    removed, named = 0, set()
    for p in files():
        r = rel(p)
        e, folder = etag_of(p), p.parent.name
        key = p.stem.split("-", 1)[-1] if folder == "out" else p.stem
        known = (e in used if folder == "sheets" else
                 key in outs if folder == "out" else key in views)
        if r in pinned_files:
            named.add(key)
            continue
        if not known:
            try:
                young = Path(p).exists() and now - Path(p).stat().st_mtime < SWEEP_AGE_S
            except OSError:
                young = False
            if young:
                continue
            gone = True
        elif folder == "out":
            gone = key not in keep_outs
        elif folder == "views":
            gone = key not in keep_views
        else:
            gone = e not in keep
        if gone:
            hosted.remove(p)
            removed += 1
    for e in [e for e in used if e not in keep]:
        used.pop(e)
    for k in [k for k in outs if k not in keep_outs and k not in named]:
        outs.pop(k)
    for n in [n for n in views if n not in keep_views and n not in named]:
        views.pop(n)
    fresh = state.get("fresh") or {}
    for e in [e for e in fresh if e not in used]:
        fresh.pop(e)
    return removed
