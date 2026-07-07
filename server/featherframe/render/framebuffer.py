"""Pack a dithered frame into the panel's native wire format.

The ESP32 is deliberately dumb: it does no image processing, just DMAs bytes to
the panel. So the server emits a tiny self-describing container ("FFF1") the
firmware can unpack with a couple of shifts.

Wire format (little-endian):
    offset  size  field
    0       4     magic  b'FFF1'
    4       1     version (1)
    5       1     bpp     (4 = 16-level grayscale, 1 = 1-bit)
    6       2     width   (pixels, 1404)
    8       2     height  (pixels, 1872)
    10      1     flags   (0)
    11      5     reserved (zero)
    16      ...   pixel data, row-major, top-to-bottom

Pixel packing:
    4bpp  two pixels per byte, high nibble = left pixel. Values 0..15,
          0 = black, 15 = white. Row stride = ceil(width/2) bytes.
    1bpp  eight pixels per byte, MSB = left pixel. Bit set = white.
          Row stride = ceil(width/8) bytes (rows are byte-padded).

The image is always portrait 1404x1872 as the frame hangs; the firmware owns
panel rotation, so the server never has to think about the panel's landscape
native orientation.
"""
from __future__ import annotations

import hashlib
import struct

import numpy as np

MAGIC = b"FFF1"
VERSION = 1
HEADER = struct.Struct("<4sBBHHB5x")  # 16 bytes
HEADER_SIZE = 16


def pack(indices: np.ndarray, bit_depth: int) -> bytes:
    """indices: uint8 [H,W] of level indices (0..15 for 4bpp, 0/1 for 1bpp)."""
    h, w = indices.shape
    idx = indices.astype(np.uint8, copy=False)

    if bit_depth == 4:
        if w % 2:  # pad to even with a white pixel
            idx = np.pad(idx, ((0, 0), (0, 1)), constant_values=15)
        hi = (idx[:, 0::2] << 4).astype(np.uint8)
        lo = idx[:, 1::2].astype(np.uint8)
        body = (hi | lo).tobytes()
    elif bit_depth == 1:
        bits = (idx > 0).astype(np.uint8)  # 1 = white
        body = np.packbits(bits, axis=1).tobytes()  # MSB-first, row byte-padded
    else:
        raise ValueError(f"unsupported bit_depth {bit_depth}")

    header = HEADER.pack(MAGIC, VERSION, bit_depth, w, h, 0)
    return header + body


def etag_for(frame: bytes) -> str:
    """Stable content hash used as the HTTP ETag (unquoted)."""
    return hashlib.sha1(frame).hexdigest()[:16]


def row_stride(width: int, bit_depth: int) -> int:
    if bit_depth == 4:
        return (width + 1) // 2
    return (width + 7) // 8


# -- unpack (for tests / preview round-trip) ------------------------------
def unpack(frame: bytes) -> np.ndarray:
    magic, version, bpp, w, h, flags = HEADER.unpack_from(frame, 0)
    if magic != MAGIC:
        raise ValueError("not an FFF frame")
    body = frame[HEADER_SIZE:]
    stride = row_stride(w, bpp)
    if bpp == 4:
        rows = np.frombuffer(body, dtype=np.uint8).reshape(h, stride)
        hi = rows >> 4
        lo = rows & 0x0F
        out = np.empty((h, stride * 2), dtype=np.uint8)
        out[:, 0::2] = hi
        out[:, 1::2] = lo
        return out[:, :w]
    else:
        rows = np.frombuffer(body, dtype=np.uint8).reshape(h, stride)
        bits = np.unpackbits(rows, axis=1)[:, :w]
        return bits.astype(np.uint8)
