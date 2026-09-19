"""The colour panel (EE02, Spectra 6): ink dither, wire format, panel config."""
import numpy as np
from PIL import Image

from featherframe import panels
from featherframe.config import Config
from featherframe.render import framebuffer, pipeline, spectra


def _flat(rgb, size=(64, 64)):
    return Image.new("RGB", size, rgb)


def test_paper_white_and_ink_black_are_one_solid_ink():
    # The big paper field must never stipple: white is exactly the white ink.
    assert (spectra.to_inks(_flat((255, 255, 255))) == spectra.WHITE).all()
    assert (spectra.to_inks(_flat((0, 0, 0))) == spectra.BLACK).all()


def test_neutral_gray_mixes_only_black_and_white():
    # The type is gray; it must not dither into a confetti of colours.
    inks = spectra.to_inks(_flat((120, 120, 120), (128, 128)))
    assert set(np.unique(inks)) == {spectra.BLACK, spectra.WHITE}


def test_every_neutral_gray_is_black_and_white_only():
    # The whole ramp, not one sample: an ink pair whose edge merely passes near
    # the gray axis (blue-yellow) once won some grays (caught baking the EE02
    # boot screens).
    ramp = Image.fromarray(np.tile(np.arange(256, dtype=np.uint8), (64, 1))).convert("RGB")
    assert set(np.unique(spectra.to_inks(ramp))) == {spectra.BLACK, spectra.WHITE}


def test_a_saturated_colour_leans_on_its_own_ink():
    for rgb, ink in (((0, 40, 200), spectra.BLUE), ((200, 20, 10), spectra.RED),
                     ((250, 220, 0), spectra.YELLOW), ((20, 120, 40), spectra.GREEN)):
        counts = np.bincount(spectra.to_inks(_flat(rgb, (128, 128))).ravel(), minlength=6)
        chromatic = counts[[spectra.BLUE, spectra.GREEN, spectra.RED, spectra.YELLOW]]
        assert chromatic.argmax() == [spectra.BLUE, spectra.GREEN, spectra.RED,
                                      spectra.YELLOW].index(ink), (rgb, counts)


def test_wire_nibbles_are_seeeds_colour_sprite_codes():
    # Contract with firmware/Seeed_GFX (T133A01_Defines.h COLOR_GET).
    inks = np.array([[spectra.BLACK, spectra.WHITE, spectra.YELLOW,
                      spectra.RED, spectra.BLUE, spectra.GREEN]], dtype=np.uint8)
    assert spectra.to_wire(inks).tolist() == [[0xF, 0x0, 0xB, 0x6, 0xD, 0x2]]


def test_dark_mode_swaps_black_and_white_only():
    inks = np.arange(6, dtype=np.uint8)[None, :]
    out = spectra.invert(inks)[0]
    assert out[spectra.BLACK] == spectra.WHITE and out[spectra.WHITE] == spectra.BLACK
    for ink in (spectra.BLUE, spectra.GREEN, spectra.RED, spectra.YELLOW):
        assert out[ink] == ink


def test_ee02_frame_is_native_portrait_inks():
    cfg = Config(panel="ee02").sanitize()
    assert cfg.panel_rotation == 0 and cfg.bit_depth == 4
    result = pipeline.render_image(Image.new("L", (1404, 1872), 255), cfg, "test", "blank")
    magic, version, bpp, w, h, flags = framebuffer.HEADER.unpack_from(result.frame, 0)
    assert (bpp, w, h, flags) == (4, 1200, 1600, framebuffer.FLAG_INKS)
    assert framebuffer.is_complete(result.frame)
    assert result.preview.mode == "RGB" and result.preview.size == (1200, 1600)
    # mat inset 4% leaves a gray ring; the centre is solid white ink (nibble 0x0)
    body = framebuffer.unpack(result.frame)
    assert (body[400:1200, 300:900] == 0x0).all()


def test_ee03_frame_is_unchanged():
    cfg = Config().sanitize()
    assert cfg.panel == "ee03" and cfg.panel_rotation == 90
    result = pipeline.render_image(Image.new("L", (1404, 1872), 255), cfg, "test", "blank")
    magic, version, bpp, w, h, flags = framebuffer.HEADER.unpack_from(result.frame, 0)
    assert (bpp, w, h, flags) == (4, 1872, 1404, 0)


def test_rotation_follows_the_panel():
    assert Config(panel="ee02", panel_rotation=270).sanitize().panel_rotation == 180
    assert Config(panel="ee03", panel_rotation=180).sanitize().panel_rotation == 270
    assert Config(panel="nonsense").sanitize().panel == "ee03"


def test_panel_from_device_report():
    assert panels.from_report("ED103TC2 1404x1872 gray16") is panels.EE03
    assert panels.from_report("T133A01 1200x1600 spectra6") is panels.EE02
    assert panels.from_report(None) is None and panels.from_report("mystery") is None


# -- the device names its panel ----------------------------------------------
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    app.state.service = svc
    return TestClient(app)


def test_first_checkin_from_a_colour_frame_turns_the_server_colour(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)          # a resident gray frame
    assert svc.config.panel == "ee03"

    r = client.get("/api/frame", headers={"X-Panel": "T133A01 1200x1600 spectra6"})
    assert r.status_code == 200
    assert svc.config.panel == "ee02" and svc.config.panel_rotation == 0
    _, _, bpp, w, h, flags = framebuffer.HEADER.unpack_from(r.content, 0)
    assert (w, h, flags) == (1200, 1600, framebuffer.FLAG_INKS)

    # The gray frame's own report changes nothing on a gray server, and an
    # unknown report is ignored.
    r = client.get("/api/frame", headers={"X-Panel": "mystery panel"})
    assert svc.config.panel == "ee02"


def test_a_live_gray_frame_keeps_its_server(client):
    # The wall frame polls every few seconds; a colour frame that finds this
    # server over mDNS must be turned away, not flip the render under it.
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    assert client.get("/api/frame", headers={"X-Panel": "ED103TC2 1404x1872 gray16"}).status_code == 200

    r = client.get("/api/frame", headers={"X-Panel": "T133A01 1200x1600 spectra6"})
    assert r.status_code == 409
    assert svc.config.panel == "ee03"
    assert "ED103TC2" in svc.device.panel            # the stray frame was not recorded


def test_firmware_is_only_served_to_its_own_board(client, tmp_path):
    from featherframe import paths
    (paths.data_dir()).mkdir(parents=True, exist_ok=True)
    (paths.data_dir() / "firmware.bin").write_bytes(b"\xe9" + b"\0" * 64 + b"XIAO-ESP32S3 EE03" + b"\0" * 64)
    ok = client.get("/api/firmware", headers={"X-Board": "XIAO-ESP32S3 EE03", "X-Firmware-MD5": "0"})
    assert ok.status_code == 200
    assert client.get("/api/firmware", headers={"X-Board": "XIAO-ESP32S3 EE02",
                                                "X-Firmware-MD5": "0"}).status_code == 404
    # Older firmware sends no X-Board and is served as before.
    assert client.get("/api/firmware", headers={"X-Firmware-MD5": "0"}).status_code == 200


def test_automatic_dither_follows_the_panel():
    assert Config().sanitize().dither == "auto"
    assert Config(panel="ee03").sanitize().effective_dither == "bluenoise"
    assert Config(panel="ee02").sanitize().effective_dither == "stucki"
    assert Config(panel="ee02", dither="bluenoise").sanitize().effective_dither == "bluenoise"
    assert Config(dither="nonsense").sanitize().dither == "auto"


def test_stucki_inks_keep_paper_and_type_clean():
    # Clean paper and solid black skip the diffusion entirely; grays stay black + white.
    assert (spectra.to_inks(_flat((255, 255, 255)), "stucki") == spectra.WHITE).all()
    assert (spectra.to_inks(_flat((0, 0, 0)), "stucki") == spectra.BLACK).all()
    gray = spectra.to_inks(_flat((120, 120, 120), (96, 96)), "stucki")
    assert set(np.unique(gray)) == {spectra.BLACK, spectra.WHITE}
    # and the mean is kept, in linear light: sRGB 120 is 19 % of the way to white
    assert abs((gray == spectra.WHITE).mean() - 0.19) < 0.03


def test_stucki_leans_on_the_colours_own_ink():
    counts = np.bincount(spectra.to_inks(_flat((0, 40, 200), (96, 96)), "stucki").ravel(), minlength=6)
    assert counts[spectra.BLUE] == counts.max()
