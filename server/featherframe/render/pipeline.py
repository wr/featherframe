"""Render orchestration: the one call the scheduler, the web preview, and
`make preview` all go through. Composition -> dither -> framebuffer -> ETag.
"""
from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

from ..config import Config
from . import compose, finish, framebuffer, spectra, theme
from .compose import SingleSpec
from .provider import ArtProvider


def _apply_mat_inset(img: Image.Image, config: Config) -> Image.Image:
    """Scale the composition down by `mat_inset_pct` per edge and center it
    on white: the surround is what the physical mat covers, and a sliver of
    it on the glass is paper, not a ring (the gray registration ring is
    gone — the mat guide is how the inset is set now). Returns the image
    unchanged when the inset is 0."""
    pct = getattr(config, "mat_inset_pct", 0.0)
    if pct <= 0:
        return _mat_guide(img, config, (0, 0) + img.size)
    scale = 1.0 - 2.0 * (pct / 100.0)
    w, h = img.size
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    shrunk = img.resize((sw, sh), Image.LANCZOS)
    canvas = Image.new(img.mode, (w, h), 255 if img.mode == "L" else (255,) * 3)
    # The physical mat is rarely mounted dead-center; the offsets move the
    # composition to meet it, and the asymmetric ring shows the correction.
    dx = int(getattr(config, "mat_offset_x_px", 0))
    dy = int(getattr(config, "mat_offset_y_px", 0))
    x, y = (w - sw) // 2 + dx, (h - sh) // 2 + dy
    canvas.paste(shrunk, (x, y))
    return _mat_guide(canvas, config, (x, y, x + sw, y + sh))


MAT_GUIDE_PX = 2


def _mat_guide(img: Image.Image, config: Config, box: tuple) -> Image.Image:
    """The mat guide (`mat_guide`): a 2 px black line just inside the
    composition's edge, so the owner can set the inset and offset by where
    the mat's opening falls against it. Off, the image is returned as it is."""
    if not getattr(config, "mat_guide", False):
        return img
    img = img.copy()
    black = 0 if img.mode == "L" else (0, 0, 0)
    x0, y0, x1, y1 = box
    ImageDraw.Draw(img).rectangle((x0, y0, x1 - 1, y1 - 1), outline=black, width=MAT_GUIDE_PX)
    return img


@dataclass
class RenderResult:
    preview: Image.Image   # exactly what the panel will show ('L', or 'RGB' in inks)
    frame: bytes           # packed FFF framebuffer
    etag: str
    levels: int
    mode: str
    label: str             # species / description, for logging & status
    # The composed sheet this was finished from (theme size, before the panel
    # fit, the mat and the dither): what a viewer's render is drawn from.
    sheet: Optional[Image.Image] = None
    # The same sheet with the art's colour twin, when a gray frame's server has
    # a colour viewer to draw for (the service composes it; W-823).
    color_sheet: Optional[Image.Image] = None

    def save(self, directory: Path, name: str) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        png = directory / f"{name}.png"
        fff = directory / f"{name}.fff"
        self.preview.save(png)
        fff.write_bytes(self.frame)
        return png, fff


# The panel's own dither draws every frame (panels.py). This is the bench's
# override (preview.py --dither, and tests that want a cheap render): never
# persisted, never set by the service.
DITHER_OVERRIDE: "str | None" = None


def _dither(config: Config) -> str:
    return DITHER_OVERRIDE or config.panel_spec.dither


def _finish_inks(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    """The colour panel's finish: six-ink dither instead of gray levels. The
    canvas is natively portrait, so rotation is only ever 0 or 180."""
    sheet = img
    img = _apply_mat_inset(img.convert("RGB"), config)
    inks = spectra.to_inks(img, _dither(config), spectra.SATURATION)
    preview = spectra.inks_to_image(inks)
    native = np.ascontiguousarray(np.rot90(inks, k=(config.panel_rotation // 90) % 4))
    frame = framebuffer.pack(spectra.to_wire(native), 4, inks=True)
    return RenderResult(preview, frame, framebuffer.etag_for(frame), 6, mode, label, sheet)


def _fit_to_panel(img: Image.Image, width: int, height: int) -> Image.Image:
    """The theme's sheet at the panel's size. Both known panels are 3:4, so
    that only scales; a reported panel of another shape (W-813) gets the whole
    sheet, contain-fitted and centred on the paper field — the layout and the
    type never fork."""
    if img.size == (width, height):
        return img
    scale = min(width / img.width, height / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    # A pixel of rounding is still "the same shape": fill the panel.
    if abs(w - width) <= 1 and abs(h - height) <= 1:
        return img.resize((width, height), Image.LANCZOS)
    paper = Image.new(img.mode, (width, height), "white")
    paper.paste(img.resize((w, h), Image.LANCZOS), ((width - w) // 2, (height - h) // 2))
    return paper


def _finish(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    panel = config.panel_spec
    sheet = img
    img = _fit_to_panel(img, panel.width, panel.height)
    if panel.color:
        result = _finish_inks(img, config, mode, label)
        result.sheet = sheet
        return result
    levels = 1 << config.bit_depth
    img = _apply_mat_inset(img, config)                             # clear the mat opening
    indices = finish.to_levels(img, levels, _dither(config))       # portrait, upright
    preview = finish.levels_to_image(indices, levels)              # what the wall shows
    # The panel canvas is fixed landscape (1872x1404) and can't rotate itself, so
    # we rotate the framebuffer into native orientation here. np.rot90 is CCW.
    native = np.rot90(indices, k=(config.panel_rotation // 90) % 4)
    native = np.ascontiguousarray(native)
    frame = framebuffer.pack(native, config.bit_depth)
    return RenderResult(preview, frame, framebuffer.etag_for(frame), levels, mode, label, sheet)


def render_single(spec: SingleSpec, provider: ArtProvider, config: Config) -> RenderResult:
    img = compose.render_single(spec, provider, color=config.panel_spec.color)
    return _finish(img, config, "single", spec.common_name)


def render_image(img: Image.Image, config: Config, mode: str, label: str) -> RenderResult:
    """Finish an already-composed frame (used by collage)."""
    return _finish(img, config, mode, label)


# -- viewers (W-822) -----------------------------------------------------------
# A viewer is any screen that is not the frame: a TRMNL, an e-reader, a tablet.
# It shows what the frame shows, drawn again from the same composed sheet at
# its own size and depth. No FFF, no mat unless it has one: a PNG.
VIEW_LEVELS = {"gray16": 16, "gray2": 4, "mono": 2}   # dithered; names as X-Panel-Format
VIEW_FORMATS = (*VIEW_LEVELS, "gray256", "color")     # these two are sent smooth
_VIEW_MIN_SIDE, _VIEW_MAX_SIDE = 64, 4096             # as panels.custom


@dataclass(frozen=True)
class View:
    """What a viewer asked for. `width`x`height` is the image as delivered;
    `rotation` is how the upright picture is turned inside it (a Kindle wants
    1448x1072 turned 90), counter-clockwise like `config.panel_rotation`."""
    width: int
    height: int
    fmt: str = "gray256"
    rotation: int = 0
    mat_inset_pct: float = 0.0

    @classmethod
    def parse(cls, width, height, fmt=None, rotation=None) -> "Optional[View]":
        """From a query string, or None for anything we cannot draw."""
        try:
            w, h = int(str(width).strip()), int(str(height).strip())
            rot = int(str(rotation).strip()) if rotation not in (None, "") else 0
        except (TypeError, ValueError):
            return None
        wire = str(fmt or "gray256").strip().lower()
        if not (_VIEW_MIN_SIDE <= w <= _VIEW_MAX_SIDE and _VIEW_MIN_SIDE <= h <= _VIEW_MAX_SIDE):
            return None
        if wire not in VIEW_FORMATS or rot not in (0, 90, 180, 270):
            return None
        return cls(w, h, wire, rot)

    @property
    def key(self) -> str:
        """Names the variant in an ETag and a cache file."""
        mat = f"-m{self.mat_inset_pct:g}" if self.mat_inset_pct else ""
        return f"{self.width}x{self.height}-{self.fmt}-{self.rotation}{mat}"


def render_view(sheet: Image.Image, view: View) -> Image.Image:
    """The sheet as one viewer shows it. Colour only when the sheet has it (a
    gray sheet asked for in colour comes back gray); every other format is
    gray whatever the sheet is."""
    upright = (view.height, view.width) if view.rotation in (90, 270) else (view.width, view.height)
    if view.fmt != "color" and sheet.mode != "L":
        sheet = sheet.convert("L")
    img = _fit_to_panel(sheet, *upright)
    img = _apply_mat_inset(img, view)   # reads .mat_inset_pct; a viewer's mat is never offset
    levels = VIEW_LEVELS.get(view.fmt)
    if levels:
        # Blue-noise whatever the frame's own dither: it is the vectorised one,
        # and a viewer's render must never cost a Pi Zero a Stucki loop.
        indices = finish.to_levels(img, levels, DITHER_OVERRIDE or "bluenoise")
        img = finish.levels_to_image(indices, levels)
    if view.rotation:
        img = img.rotate(view.rotation, expand=True)   # exact for quarter turns; CCW, as np.rot90
    return img


_PNG_BITS = {"mono": 1, "gray2": 2, "gray16": 4}


def encode_png(img: Image.Image, fmt: str) -> bytes:
    """A view as the PNG a viewer fetches. The dithered formats are written at
    their true depth (1, 2 or 4 bits of gray): TRMNL's firmware paints those
    as they are, but cuts an 8-bit PNG down by truncation, and a small device
    should not be handed four times the file. Pillow cannot write gray below
    8 bits, so that one is packed here; the smooth formats are Pillow's."""
    bits = _PNG_BITS.get(fmt)
    if bits is None or img.mode != "L":
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    levels = np.asarray(img, dtype=np.uint16)
    idx = ((levels * ((1 << bits) - 1) + 127) // 255).astype(np.uint8)   # 0..2^bits-1
    h, w = idx.shape
    per = 8 // bits
    pad = (-w) % per
    if pad:
        idx = np.pad(idx, ((0, 0), (0, pad)))
    packed = np.zeros((h, idx.shape[1] // per), dtype=np.uint8)
    for i in range(per):
        packed |= idx[:, i::per] << (8 - bits * (i + 1))
    rows = np.zeros((h, packed.shape[1] + 1), dtype=np.uint8)   # filter byte 0 per row
    rows[:, 1:] = packed

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bits, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
            + chunk(b"IEND", b""))
