"""Render orchestration: the one call the scheduler, the web preview, and
`make preview` all go through. Composition -> dither -> framebuffer -> ETag.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from ..config import Config
from . import compose, finish, framebuffer, theme
from .compose import SingleSpec
from .provider import ArtProvider


def _apply_mat_inset(img: Image.Image, config: Config) -> Image.Image:
    """Scale the composition down by `mat_inset_pct` per edge and center it on the
    white field. Returns the image unchanged when the inset is 0."""
    pct = getattr(config, "mat_inset_pct", 0.0)
    if pct <= 0:
        return img
    scale = 1.0 - 2.0 * (pct / 100.0)
    w, h = img.size
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    shrunk = img.resize((sw, sh), Image.LANCZOS)
    canvas = Image.new(img.mode, (w, h), theme.FIELD)
    canvas.paste(shrunk, ((w - sw) // 2, (h - sh) // 2))
    return canvas


@dataclass
class RenderResult:
    preview: Image.Image   # 'L' image of exactly what the panel will show
    frame: bytes           # packed FFF framebuffer
    etag: str
    levels: int
    mode: str
    label: str             # species / description, for logging & status

    def save(self, directory: Path, name: str) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        png = directory / f"{name}.png"
        fff = directory / f"{name}.fff"
        self.preview.save(png)
        fff.write_bytes(self.frame)
        return png, fff


def _finish(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    levels = 16 if config.bit_depth == 4 else 2
    img = _apply_mat_inset(img, config)                             # clear the mat opening
    indices = finish.to_levels(img, levels, config.dither)          # portrait, upright
    preview = finish.levels_to_image(indices, levels)              # what the wall shows
    # The panel canvas is fixed landscape (1872x1404) and can't rotate itself, so
    # we rotate the framebuffer into native orientation here. np.rot90 is CCW.
    native = np.rot90(indices, k=(config.panel_rotation // 90) % 4)
    native = np.ascontiguousarray(native)
    frame = framebuffer.pack(native, config.bit_depth)
    return RenderResult(preview, frame, framebuffer.etag_for(frame), levels, mode, label)


def render_single(spec: SingleSpec, provider: ArtProvider, config: Config) -> RenderResult:
    img = compose.render_single(spec, provider, show_plate_number=config.show_plate_number)
    return _finish(img, config, "single", spec.common_name)


def render_image(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    """Finish an already-composed frame (used by collage)."""
    return _finish(img, config, mode, label)
