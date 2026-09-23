"""The compiled colour dither is the Python one, pixel for pixel."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from featherframe.render import spectra

pytest.importorskip("numba")


def _frame():
    rng = np.random.default_rng(7)
    img = Image.fromarray(rng.integers(0, 256, (120, 90, 3), dtype=np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 89, 20), fill=(255, 255, 255))         # clean paper
    d.rectangle((0, 100, 89, 119), fill=(128, 128, 128))      # neutral gray
    d.text((5, 5), "Plate CLIX", fill=(0, 0, 0))
    return img


def test_compiled_matches_python():
    mapped, inkset = spectra._gamut_mapped(_frame(), spectra.SATURATION)
    assert spectra._stucki_kernel() is not None
    assert np.array_equal(spectra._diffuse_stucki(mapped, inkset),
                          spectra._diffuse_stucki_py(mapped, inkset))


def test_falls_back_without_numba(monkeypatch):
    mapped, inkset = spectra._gamut_mapped(_frame(), spectra.SATURATION)
    monkeypatch.setattr(spectra, "_KERNEL", None)
    assert np.array_equal(spectra._diffuse_stucki(mapped, inkset),
                          spectra._diffuse_stucki_py(mapped, inkset))
