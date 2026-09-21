"""W-819: the firmware's baked screens at any size. The bake lives with the
firmware (firmware/tools/screens/bake_screens.py) but draws with the server's
own typography, so it is tested here."""
import importlib.util
import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

BAKE = os.path.join(os.path.dirname(__file__), "..", "..", "firmware", "tools", "screens",
                    "bake_screens.py")


@pytest.fixture(scope="module")
def bake():
    spec = importlib.util.spec_from_file_location("bake_screens", BAKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # composes the screens once (a few seconds)
    return mod


def _unpackbits(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        n = data[i] - 256 if data[i] > 127 else data[i]
        i += 1
        if n >= 0:
            out += data[i:i + n + 1]; i += n + 1
        elif n != -128:
            out += bytes([data[i]]) * (1 - n); i += 1
    return bytes(out)


def _nibbles(body: bytes, w: int, h: int) -> np.ndarray:
    rows = np.frombuffer(body, dtype=np.uint8).reshape(h, w // 2)
    out = np.empty((h, w), dtype=np.uint8)
    out[:, 0::2], out[:, 1::2] = rows >> 4, rows & 0x0F
    return out


def test_the_ee02_is_the_same_target_it_always_was(bake):
    t = bake.EE02
    assert (t.native_w, t.native_h, t.fmt, t.rotation) == (1200, 1600, "spectra6", 0)
    assert t.sheet == (1200, 1600) and t.at == (0, 0)      # 3:4: only scaled


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_screen_is_the_native_canvas_and_letterboxed_on_paper(bake, rotation, tmp_path):
    t = bake.Target(480, 800, "gray16", rotation, str(tmp_path / "s.h"), "t", [])
    assert (t.native_w, t.native_h) == ((800, 480) if rotation in (90, 270) else (480, 800))
    name, im = next((n, i) for n, i in bake.FULL_SCREENS if i is not None)
    body = _unpackbits(t.screen(im))
    assert len(body) == t.bytes == 480 * 800 // 2
    upright = np.rot90(_nibbles(body, t.native_w, t.native_h), k=-(rotation // 90))
    assert upright.shape == (800, 480)
    # 480x800 is narrower than 3:4: the sheet is 480x640, paper above and below.
    assert t.sheet == (480, 640) and t.at == (0, 80)
    assert (upright[:80] == 15).all() and (upright[-80:] == 15).all()
    assert (upright[80:720] < 15).any()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_stamp_tile_lands_on_its_ink_whichever_way_the_canvas_turns(bake, rotation, tmp_path):
    t = bake.Target(480, 800, "gray16", rotation, str(tmp_path / "s.h"), "t", [])
    canvas = Image.new("L", (bake.W, bake.H), 255)
    bake._draw_toast(ImageDraw.Draw(canvas), "Up to date", "done")
    win = t.window([canvas], 1640, 1736)
    nx, ny, nw, nh = t.native_window(win)
    assert nx % 2 == 0 and nw % 2 == 0                     # a row is whole bytes
    assert 0 <= nx and nx + nw <= t.native_w and 0 <= ny and ny + nh <= t.native_h

    native = np.rot90(t.levels(canvas), k=rotation // 90)
    outside = native.copy()
    outside[ny:ny + nh, nx:nx + nw] = 15
    assert (outside == 15).all()                           # every inked pixel is in the window
    tile = _nibbles(_unpackbits(t.tile(canvas, win)), nw, nh)
    assert np.array_equal(tile, native[ny:ny + nh, nx:nx + nw]) and (tile < 15).any()


def test_a_spectra_target_is_black_and_white_ink_codes_only(bake, tmp_path):
    t = bake.Target(480, 800, "spectra6", 0, str(tmp_path / "s.h"), "t", [])
    _, im = next((n, i) for n, i in bake.FULL_SCREENS if i is not None)
    assert set(np.unique(t.levels(im))) <= {0x0, 0xF}      # black and white ink, nothing mixed


def test_the_header_names_what_it_was_baked_for(bake, tmp_path):
    t = bake.Target(480, 800, "gray16", 90, str(tmp_path / "s.h"), "t", ["// t"])
    bake.write_header_full(t)
    text = (tmp_path / "s.h").read_text()
    for line in ("#define FF_NATIVE_W       800", "#define FF_NATIVE_H       480",
                 "#define FF_SCREEN_BYTES   192000", "#define FF_SCREENS_ROTATION 90",
                 "FF_SCR_LOW_BATT", "ff_toast_tiles[FF_TOAST_COUNT]", "ff_corner_tiles[2]"):
        assert line in text


def test_formats_the_firmware_cannot_take_yet_are_refused(bake, tmp_path):
    with pytest.raises(SystemExit):
        bake.Target(480, 800, "mono", 0, str(tmp_path / "s.h"), "t", [])
    with pytest.raises(SystemExit):
        bake.Target(481, 800, "gray16", 0, str(tmp_path / "s.h"), "t", [])
