"""Framebuffer packing round-trips and ETag behaviour — the wire contract with
the firmware."""
from __future__ import annotations

import numpy as np
import pytest

from featherframe.render import framebuffer as fb


@pytest.mark.parametrize("w,h", [(1404, 1872), (10, 4), (7, 3)])  # incl. odd width
def test_4bpp_roundtrip(w, h):
    idx = (np.arange(w * h).reshape(h, w) % 16).astype(np.uint8)
    frame = fb.pack(idx, 4)
    back = fb.unpack(frame)
    assert np.array_equal(idx, back)


@pytest.mark.parametrize("w,h", [(1404, 1872), (13, 5)])  # width not /8
def test_1bpp_roundtrip(w, h):
    bits = (np.arange(w * h).reshape(h, w) % 2).astype(np.uint8)
    frame = fb.pack(bits, 1)
    back = fb.unpack(frame)
    assert np.array_equal(bits > 0, back > 0)


def test_header_fields():
    idx = np.zeros((1872, 1404), dtype=np.uint8)
    frame = fb.pack(idx, 4)
    magic, ver, bpp, w, h, flags = fb.HEADER.unpack_from(frame, 0)
    assert magic == b"FFF1" and ver == 1 and bpp == 4 and w == 1404 and h == 1872
    assert len(frame) == fb.HEADER_SIZE + 1404 * 1872 // 2


def test_etag_is_deterministic_and_content_sensitive():
    a = fb.pack(np.zeros((8, 8), np.uint8), 4)
    b = fb.pack(np.zeros((8, 8), np.uint8), 4)
    c = fb.pack(np.ones((8, 8), np.uint8) * 15, 4)
    assert fb.etag_for(a) == fb.etag_for(b)   # same content -> same etag
    assert fb.etag_for(a) != fb.etag_for(c)   # different content -> different etag


def test_row_stride():
    assert fb.row_stride(1404, 4) == 702
    assert fb.row_stride(1404, 1) == 176   # ceil(1404/8), byte-padded rows
    assert fb.row_stride(7, 4) == 4
