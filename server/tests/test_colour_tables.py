"""W-997: the colour maths as tables gives the pixels the arithmetic did."""
from __future__ import annotations

import numpy as np
from PIL import Image

from featherframe.render import plate, spectra

RNG = np.random.default_rng(7)


def _image(h=300, w=200):
    # Noise over a paper-coloured field with dark strokes: percentiles that
    # land between values, as a scan's do.
    px = RNG.normal(225, 30, (h, w, 3))
    px[RNG.random((h, w)) < 0.2] = RNG.normal(40, 25, 3)
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), mode="RGB")


def test_the_crop_curve_as_a_table_is_the_arithmetic():
    img = _image()
    arr = np.asarray(img, dtype=np.float32)
    lo = np.percentile(arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32), 3.5)
    hi = np.percentile(arr.reshape(-1, 3), 90.0, axis=0)
    assert np.array_equal(np.asarray(plate.paper_normalize_color(img)), plate._curve(arr, lo, hi))
    gray = img.convert("L")
    g = np.asarray(gray, dtype=np.float32)
    assert np.array_equal(np.asarray(plate.paper_normalize(gray)),
                          plate._curve(g, np.percentile(g, 3.5), np.percentile(g, 90.0)))


def _mapped_per_pixel(img, saturation):
    """The mapping as it was computed before W-997, pixel by pixel."""
    lut = spectra._lut()
    pal = spectra._palette_linear()
    pal_n = (pal - pal[spectra.BLACK]) / (pal[spectra.WHITE] - pal[spectra.BLACK])
    rgb = np.asarray(img, dtype=np.float32)
    if saturation != 1.0:
        luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        rgb = np.clip(luma[..., None] + (rgb - luma[..., None]) * saturation, 0, 255)
    q = np.clip(np.round(spectra._to_linear(rgb) * (spectra._LUT_N - 1)).astype(np.int32), 0,
                spectra._LUT_N - 1)
    cum = lut[q[..., 0], q[..., 1], q[..., 2]].astype(np.float32) / 255.0
    wts = np.diff(cum, axis=2, prepend=0.0)
    bits = (1 << np.arange(len(pal))).astype(np.uint8)
    return (wts @ pal_n).astype(np.float32), ((wts > 0) * bits).sum(axis=2).astype(np.uint8)


def test_the_ink_cells_map_a_frame_as_the_table_does():
    img = _image(300, 260)
    for saturation in (1.0, spectra.SATURATION):
        mapped, inkset = spectra._gamut_mapped(img, saturation)
        ref_mapped, ref_inkset = _mapped_per_pixel(img, saturation)
        assert np.array_equal(mapped, ref_mapped) and np.array_equal(inkset, ref_inkset)
