"""A kept drawing's name moves when its pixels do (W-999).

The drawn store names a colour twin by `compose.COLOR_VERSION`, a frame's
output or a viewer's image by `pipeline.FINISH_VERSION`, and finds a kept
gray single sheet by `compose.SHEET_VERSION` (W-1012). Change what one draws
without bumping it and every picture kept under the old number comes back
stale until the pruner lets it go. These hashes hold the pixels each
path draws. When one moves, bump the version the failure names, then record:

    FF_RECORD_DRAWN=1 ./.venv/bin/python -m pytest tests/test_drawn_versions.py

They hold for the libraries they were recorded with (`drawn._libs`); with
others the test is skipped, since a library change is a new name already.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from featherframe import drawn, plate_library
from featherframe.config import Config
from featherframe.render import compose, pipeline, plate
from featherframe.render.compose import SingleSpec
from featherframe.render.provider import Artwork

GOLDEN = Path(__file__).parent / "fixtures" / "drawn-versions.json"
SPEC = SingleSpec(common_name="Northern Cardinal", scientific_name="Cardinalis cardinalis",
                  when=datetime(2026, 10, 6, 8, 0), first_seen="2026-10-01")


def _h(img: Image.Image) -> str:
    return hashlib.sha256(img.mode.encode() + repr(img.size).encode()
                          + img.tobytes()).hexdigest()[:16]


def _raw() -> Image.Image:
    """A scan's colour crop, as the library keeps it: art on toned paper."""
    rgb = Image.new("RGB", (600, 800), (236, 226, 204))
    d = ImageDraw.Draw(rgb)
    d.ellipse((100, 150, 500, 600), fill=(190, 40, 30))
    d.line((80, 650, 520, 700), fill=(70, 60, 40), width=12)
    return rgb


def _art() -> Artwork:
    gray, rgb = plate_library.color_pair_from_raw(_raw())
    return Artwork(gray, 159, folio="havell", color_loader=lambda: (gray, rgb))


def _sheet(color: bool) -> Image.Image:
    """A fixed sheet, independent of compose (whose changes move the ETag)."""
    y, x = np.mgrid[0:1872, 0:1404]
    gray = ((x * 255 // 1403 + y * 255 // 1871) // 2).astype(np.uint8)
    if not color:
        return Image.fromarray(gray, "L")
    rgb = np.stack([gray, (x * 255 // 1403).astype(np.uint8), (y * 255 // 1871).astype(np.uint8)], -1)
    return Image.fromarray(rgb.astype(np.uint8), "RGB")


def _colour() -> dict:
    gray, rgb = plate_library.color_pair_from_raw(_raw())
    return {"library pair": [_h(gray), _h(rgb)],
            "paper": _h(plate.paper_normalize_color(_raw())),
            "twin": _h(compose.render_for(SPEC, _art(), color=True)),
            "bough twin": _h(compose.render_fallback(SPEC, color=True))}


def _gray() -> dict:
    return {"plate": _h(compose.render_for(SPEC, _art(), color=False)),
            "bough": _h(compose.render_fallback(SPEC))}


def _finish() -> dict:
    out = {}
    for panel in ("ee03", "ee02"):
        cfg = Config.defaults_for(panel)
        r = pipeline.render_image(_sheet(cfg.panel_spec.color), cfg, "single", "x")
        out[panel] = [hashlib.sha256(r.frame).hexdigest()[:16], _h(r.preview)]
    for fmt in ("gray16", "gray2", "mono", "gray256", "color"):
        png = pipeline.encode_png(pipeline.render_view(_sheet(fmt == "color"),
                                                       pipeline.View(300, 400, fmt)), fmt)
        with Image.open(io.BytesIO(png)) as im:
            im.load()
            out[f"view {fmt}"] = _h(im)
    return out


def test_what_is_drawn_moves_only_with_its_version(monkeypatch, tmp_path):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(pipeline, "DITHER_OVERRIDE", None)
    now = {"libs": drawn._libs(), "COLOR_VERSION": compose.COLOR_VERSION,
           "FINISH_VERSION": pipeline.FINISH_VERSION, "SHEET_VERSION": compose.SHEET_VERSION,
           "colour": _colour(), "finish": _finish(), "sheet": _gray()}
    if os.environ.get("FF_RECORD_DRAWN"):
        GOLDEN.write_text(json.dumps(now, indent=2) + "\n")
        return
    kept = json.loads(GOLDEN.read_text())
    if kept["libs"] != now["libs"]:
        pytest.skip(f"recorded with {kept['libs']}, here {now['libs']}")
    for part, version in (("colour", "COLOR_VERSION"), ("finish", "FINISH_VERSION"),
                          ("sheet", "SHEET_VERSION")):
        if kept[version] != now[version]:
            pytest.fail(f"{version} moved: record the hashes again (see this file's docstring)")
        moved = sorted(k for k in now[part] if kept[part].get(k) != now[part][k])
        assert not moved, (f"{', '.join(moved)} drew different pixels: bump {version} "
                           "and record again (see this file's docstring)")
