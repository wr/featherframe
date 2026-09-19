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


GRAY = {"X-Panel": "ED103TC2 1404x1872 gray16", "X-Device-Id": "AAAAAAAAAA01"}
COLOUR = {"X-Panel": "T133A01 1200x1600 spectra6", "X-Device-Id": "BBBBBBBBBB02"}


def _answer(client, frame_id, action):
    r = client.post("/api/frames", data={"id": frame_id, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def test_first_frame_takes_the_seat_and_a_second_one_waits(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    assert client.get("/api/frame", headers=GRAY).status_code == 200

    r = client.get("/api/frame", headers=COLOUR)
    assert r.status_code == 403 and r.headers["x-ff-frame"] == "pending"
    assert svc.config.panel == "ee03"                 # nothing moved under the wall frame
    assert "ED103TC2" in svc.device.panel             # and the card is still its card
    view = svc.frames_view()
    assert view["active"]["id"] == "AAAAAAAAAA01"
    assert [f["id"] for f in view["pending"]] == ["BBBBBBBBBB02"] and view["ignored"] == []
    assert client.get("/api/frame", headers=GRAY).status_code in (200, 304)


def test_switching_frames_follows_the_new_panel_and_offers_its_defaults(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    client.get("/api/frame", headers=GRAY)
    svc.update_config(Config.from_dict({**svc.config.to_dict(), "dither": "bluenoise",
                                        "mat_inset_pct": 3.5, "mat_offset_x_px": -10}))
    client.get("/api/frame", headers=COLOUR)

    out = _answer(client, "BBBBBBBBBB02", "switch")
    assert out["frames"]["active"]["id"] == "BBBBBBBBBB02" and out["frames"]["pending"] == []
    assert svc.config.panel == "ee02" and svc.config.panel_rotation == 0
    swap = svc.panel_notices()["swap"]
    assert (swap["from"], swap["to"]) == ("ee03", "ee02")
    assert {"dither", "mat_inset_pct", "mat_offset_x_px"} <= set(swap["off_default"])
    assert svc.config.dither == "bluenoise"           # nothing reset behind the owner's back

    r = client.get("/api/frame", headers=COLOUR)
    _, _, _, w, h, flags = framebuffer.HEADER.unpack_from(r.content, 0)
    assert r.status_code == 200 and (w, h, flags) == (1200, 1600, framebuffer.FLAG_INKS)

    # The frame that was switched away from goes through the same question.
    assert client.get("/api/frame", headers=GRAY).status_code == 403
    assert [f["id"] for f in svc.frames_view()["pending"]] == ["AAAAAAAAAA01"]

    ok = client.post("/api/panel-notice", data={"action": "defaults"})
    assert ok.status_code == 200 and ok.json()["panel_notices"]["swap"] is None
    assert svc.config.effective_dither == "stucki" and svc.config.mat_offset_x_px == 0


def test_an_ignored_frame_is_listed_and_can_be_switched_to_or_forgotten(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    client.get("/api/frame", headers=GRAY)
    client.get("/api/frame", headers=COLOUR)
    _answer(client, "BBBBBBBBBB02", "ignore")
    r = client.get("/api/frame", headers=COLOUR)
    assert r.status_code == 403 and r.headers["x-ff-frame"] == "ignored"
    view = svc.frames_view()
    assert view["pending"] == [] and [f["id"] for f in view["ignored"]] == ["BBBBBBBBBB02"]
    html = client.get("/").text
    assert "Ignored frames (1)" in html and "Another frame wants to connect." not in html

    _answer(client, "BBBBBBBBBB02", "forget")
    assert svc.frames_view()["ignored"] == []
    assert client.get("/api/frame", headers=COLOUR).headers["x-ff-frame"] == "pending"
    assert "Another frame wants to connect." in client.get("/").text
    # The active frame cannot be ignored out from under itself.
    assert client.post("/api/frames", data={"id": "AAAAAAAAAA01", "action": "ignore"}).status_code == 400


def test_older_firmware_keeps_its_seat_when_it_starts_sending_an_id(client):
    # The wall frame predates X-Device-Id; after its update it must not be
    # asked about as if it were a stranger.
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    assert client.get("/api/frame", headers={"X-Panel": GRAY["X-Panel"]}).status_code == 200
    assert svc.frames_view()["active"]["id"] == "legacy"
    assert client.get("/api/frame", headers=GRAY).status_code in (200, 304)
    view = svc.frames_view()
    assert view["active"]["id"] == "AAAAAAAAAA01" and view["pending"] == []
    # ...while a frame with another panel is still a stranger to a legacy seat.
    assert client.get("/api/frame", headers=COLOUR).status_code == 403


def test_keeping_my_settings_only_clears_the_notice(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    client.get("/api/frame", headers=GRAY)
    svc.update_config(Config.from_dict({**svc.config.to_dict(), "mat_inset_pct": 3.5}))
    client.get("/api/frame", headers=COLOUR)
    _answer(client, "BBBBBBBBBB02", "switch")
    assert client.post("/api/panel-notice", data={"action": "keep"}).status_code == 200
    assert svc.panel_notices()["swap"] is None and svc.config.mat_inset_pct == 3.5


def test_first_frame_on_a_fresh_install_raises_no_notice(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    assert client.get("/api/frame", headers=COLOUR).status_code == 200
    assert svc.config.panel == "ee02" and svc.panel_notices()["swap"] is None


def test_page_offers_defaults_and_reset(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    client.get("/api/frame", headers=GRAY)
    svc.update_config(Config.from_dict({**svc.config.to_dict(), "mat_inset_pct": 3.5}))
    client.get("/api/frame", headers=COLOUR)
    assert "Another frame wants to connect." in client.get("/").text
    _answer(client, "BBBBBBBBBB02", "switch")
    html = client.get("/").text
    assert "New panel connected." in html and "Use this panel's defaults" in html
    assert 'id="adv-reset"' in html and '"dither": "auto"' in html
    assert "api_key" not in html.split('id="display-defaults">')[1].split("</script>")[0]


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


def test_a_parked_frame_is_told_where_its_panels_instance_is(client):
    # Two instances on one LAN: the wall's server parks the colour frame (403)
    # and points it at the instance advertising its panel.
    class _Adv:
        def find_peer(self, panel):
            return "http://192.0.2.9:8082" if panel == "ee02" else None

        def set_panel(self, panel):
            pass
    client.app.state.advertiser = _Adv()
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    client.get("/api/frame", headers=GRAY)
    r = client.get("/api/frame", headers=COLOUR)
    assert r.status_code == 403 and r.headers["x-ff-server"] == "http://192.0.2.9:8082"
    # A second frame with the SAME panel has nowhere better to go: no hint.
    other = {**GRAY, "X-Device-Id": "CCCCCCCCCC03"}
    r = client.get("/api/frame", headers=other)
    assert r.status_code == 403 and "x-ff-server" not in r.headers
    del client.app.state.advertiser
