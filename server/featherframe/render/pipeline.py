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
from . import compose, finish, framebuffer, spectra, theme
from .compose import SingleSpec
from .provider import ArtProvider


def _apply_mat_inset(img: Image.Image, config: Config) -> Image.Image:
    """Scale the composition down by `mat_inset_pct` per edge and center it.
    The surround is painted MAT_BORDER — the ring the physical mat should
    exactly cover, visible in the preview and, if the inset is dialed wrong,
    as a sliver on the glass. Returns the image unchanged when the inset is 0."""
    pct = getattr(config, "mat_inset_pct", 0.0)
    if pct <= 0:
        return img
    scale = 1.0 - 2.0 * (pct / 100.0)
    w, h = img.size
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    shrunk = img.resize((sw, sh), Image.LANCZOS)
    border = theme.MAT_BORDER if img.mode == "L" else (theme.MAT_BORDER,) * 3
    canvas = Image.new(img.mode, (w, h), border)
    # The physical mat is rarely mounted dead-center; the offsets move the
    # composition to meet it, and the asymmetric ring shows the correction.
    dx = int(getattr(config, "mat_offset_x_px", 0))
    dy = int(getattr(config, "mat_offset_y_px", 0))
    canvas.paste(shrunk, ((w - sw) // 2 + dx, (h - sh) // 2 + dy))
    return canvas


@dataclass
class RenderResult:
    preview: Image.Image   # exactly what the panel will show ('L', or 'RGB' in inks)
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


def _finish_inks(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    """The colour panel's finish: six-ink dither instead of gray levels. The
    canvas is natively portrait, so rotation is only ever 0 or 180."""
    img = _apply_mat_inset(img.convert("RGB"), config)
    inks = spectra.to_inks(img, config.effective_dither, config.color_saturation)
    if config.dark_now():
        inks = spectra.invert(inks)
    preview = spectra.inks_to_image(inks)
    native = np.ascontiguousarray(np.rot90(inks, k=(config.panel_rotation // 90) % 4))
    frame = framebuffer.pack(spectra.to_wire(native), 4, inks=True)
    return RenderResult(preview, frame, framebuffer.etag_for(frame), 6, mode, label)


def _finish(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    panel = config.panel_spec
    if img.size != (panel.width, panel.height):
        # Composed on the theme's sheet; both panels are 3:4, so this only scales.
        img = img.resize((panel.width, panel.height), Image.LANCZOS)
    if panel.color:
        return _finish_inks(img, config, mode, label)
    levels = 16 if config.bit_depth == 4 else 2
    img = _apply_mat_inset(img, config)                             # clear the mat opening
    indices = finish.to_levels(img, levels, config.effective_dither)          # portrait, upright
    if config.dark_now():
        # Flip every level end-to-end (black field, white ink) after dithering,
        # so the packed frame and the PNG preview invert identically.
        indices = (levels - 1 - indices).astype(np.uint8)
    preview = finish.levels_to_image(indices, levels)              # what the wall shows
    # The panel canvas is fixed landscape (1872x1404) and can't rotate itself, so
    # we rotate the framebuffer into native orientation here. np.rot90 is CCW.
    native = np.rot90(indices, k=(config.panel_rotation // 90) % 4)
    native = np.ascontiguousarray(native)
    frame = framebuffer.pack(native, config.bit_depth)
    return RenderResult(preview, frame, framebuffer.etag_for(frame), levels, mode, label)


def render_single(spec: SingleSpec, provider: ArtProvider, config: Config) -> RenderResult:
    img = compose.render_single(spec, provider, show_plate_number=config.show_plate_number,
                                color=config.panel_spec.color)
    return _finish(img, config, "single", spec.common_name)


def render_image(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    """Finish an already-composed frame (used by collage)."""
    return _finish(img, config, mode, label)
