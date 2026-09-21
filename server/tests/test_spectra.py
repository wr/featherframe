"""The colour panel (EE02, Spectra 6): ink dither, wire format, panel config."""
import numpy as np
from PIL import Image

from featherframe import panels
from featherframe import frames as frames_mod
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

from tests._frames import connect, device


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


def test_a_colour_frame_is_drawn_for_in_inks_at_its_own_size(client):
    """A frame's panel is simply what it reports (W-833): nothing to adopt,
    nothing to answer."""
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    r = connect(client, COLOUR)
    assert r.status_code == 200 and r.headers["x-ff-rotation"] == "0"
    _, _, bpp, w, h, flags = framebuffer.HEADER.unpack_from(r.content, 0)
    assert (w, h, flags) == (1200, 1600, framebuffer.FLAG_INKS)
    assert svc.frame_config(svc.frames.get(COLOUR["X-Device-Id"])).panel == "ee02"


GRAY = {"X-Panel": "ED103TC2 1404x1872 gray16", "X-Device-Id": "AAAAAAAAAA01"}
COLOUR = {"X-Panel": "T133A01 1200x1600 spectra6", "X-Device-Id": "BBBBBBBBBB02"}


def _answer(client, frame_id, action):
    r = client.post("/api/frames", data={"id": frame_id, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def _by_status(svc, status: str) -> list:
    return [f["id"] for f in svc.frames_list() if f["status"] == status]


def test_the_first_frame_is_let_in_and_a_second_one_waits(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    assert connect(client, GRAY).status_code == 200

    r = client.get("/api/frame", headers=COLOUR)
    assert r.status_code == 403 and r.headers["x-ff-frame"] == "pending"
    assert _by_status(svc, "on") == ["AAAAAAAAAA01"]
    assert _by_status(svc, "asking") == ["BBBBBBBBBB02"] and _by_status(svc, "ignored") == []
    # Each frame's report is its own.
    assert "ED103TC2" in device(svc, "AAAAAAAAAA01").panel
    assert client.get("/api/frame", headers=GRAY).status_code in (200, 304)


def test_two_kits_of_different_panels_are_each_drawn_for_their_own(client):
    """Adding the colour kit leaves the gray one exactly as it was: each
    frame's panel, rotation and mat are its own."""
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    assert connect(client, GRAY).status_code == 200
    gray_before = client.get("/api/frame", headers=GRAY)
    client.get("/api/frame", headers=COLOUR)
    _answer(client, "BBBBBBBBBB02", "add")
    svc.tick()

    r = client.get("/api/frame", headers=COLOUR)
    _, _, _, w, h, flags = framebuffer.HEADER.unpack_from(r.content, 0)
    assert r.status_code == 200 and (w, h, flags) == (1200, 1600, framebuffer.FLAG_INKS)
    assert r.headers["x-ff-rotation"] == "0"
    again = client.get("/api/frame", headers=GRAY)
    assert again.headers["etag"] == gray_before.headers["etag"]
    assert again.headers["x-ff-rotation"] == "90"


def test_there_is_no_replacing_a_frame_only_adding_and_removing(client):
    """W-833: there is no current frame, so "switch" is gone. Handing the
    server to a new kit is adding it and removing the old one."""
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    connect(client, GRAY)
    client.get("/api/frame", headers=COLOUR)

    assert client.post("/api/frames", data={"id": "BBBBBBBBBB02",
                                            "action": "switch"}).status_code == 400
    out = _answer(client, "BBBBBBBBBB02", "add")
    assert _by_status(svc, "asking") == []
    assert [f["id"] for f in out["frames"] if f["status"] == "on"] == ["AAAAAAAAAA01",
                                                                      "BBBBBBBBBB02"]
    _answer(client, "AAAAAAAAAA01", "forget")
    svc.tick()
    r = client.get("/api/frame", headers=COLOUR)
    _, _, _, w, h, flags = framebuffer.HEADER.unpack_from(r.content, 0)
    assert r.status_code == 200 and (w, h, flags) == (1200, 1600, framebuffer.FLAG_INKS)
    # Its panel's own defaults, unasked: rotation 0 and the collage (W-821).
    assert svc.frame_config(svc.frames.get("BBBBBBBBBB02")).panel_rotation == 0
    assert _by_status(svc, "on") == ["BBBBBBBBBB02"]
    # The kit that was removed is let in by itself only while nothing else is
    # on; with the colour kit serving, it has to be answered for again.
    assert client.get("/api/frame", headers=GRAY).status_code == 403
    assert _by_status(svc, "asking") == ["AAAAAAAAAA01"]


def test_an_ignored_frame_is_listed_and_can_be_added_or_forgotten(client):
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    connect(client, GRAY)
    client.get("/api/frame", headers=COLOUR)
    _answer(client, "BBBBBBBBBB02", "ignore")
    r = client.get("/api/frame", headers=COLOUR)
    assert r.status_code == 403 and r.headers["x-ff-frame"] == "ignored"
    assert _by_status(svc, "asking") == [] and _by_status(svc, "ignored") == ["BBBBBBBBBB02"]
    html = client.get("/").text
    assert "Ignored (1)" in html and "wants to connect" not in html

    _answer(client, "BBBBBBBBBB02", "forget")
    assert _by_status(svc, "ignored") == []
    assert client.get("/api/frame", headers=COLOUR).headers["x-ff-frame"] == "pending"
    assert "wants to connect" in client.get("/").text
    # The only frame cannot be ignored out from under itself: the first kit to
    # check in on a server with none is let in again the moment it asks.
    assert client.post("/api/frames", data={"id": "AAAAAAAAAA01",
                                            "action": "ignore"}).status_code == 400


def test_older_firmware_keeps_its_seat_when_it_starts_sending_an_id(client):
    # The wall frame predates X-Device-Id; after its update it must not be
    # asked about as if it were a stranger, nor repaint.
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    legacy = connect(client, {"X-Panel": GRAY["X-Panel"]})
    assert legacy.status_code == 200
    assert _by_status(svc, "on") == ["legacy"]
    named = client.get("/api/frame", headers=GRAY)
    assert named.status_code == 200 and named.content == legacy.content
    assert _by_status(svc, "on") == ["AAAAAAAAAA01"] and _by_status(svc, "asking") == []
    # ...while a frame with another panel is still a stranger to a legacy seat.
    assert client.get("/api/frame", headers=COLOUR).status_code == 403


def test_a_rows_advanced_offers_its_own_panels_defaults(client):
    """Reset is per frame now (W-833): the defaults come from THAT frame's
    panel, and no secret is ever sent to the page with them."""
    svc = client.app.state.service
    svc._render_welcome(svc._clock(), False)
    connect(client, GRAY)
    html = client.get("/").text
    row = html.split('data-frame="AAAAAAAAAA01"')[1].split(chr(10) + "    </li>")[0]
    assert 'data-fr-action="reset"' in row
    assert 'data-f="mat_inset_pct" data-default="4.0"' in row
    assert "imagegen" not in row      # nothing shared is offered per frame


def test_a_fresh_installs_first_colour_frame_starts_on_the_collage(client):
    """No FEATHERFRAME_PANEL: the frame's own report picks the picture it
    shows, as it picks the rotation (W-821)."""
    svc = client.app.state.service
    connect(client, COLOUR)
    row = svc.frames.get(COLOUR["X-Device-Id"])
    assert frames_mod.shows_of(row) == "collage"
    assert svc.frame_config(row).panel_rotation == 0


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


def test_the_panel_picks_the_dither_and_a_fresh_install_its_mode(monkeypatch):
    from featherframe.render import pipeline
    assert pipeline._dither(Config(panel="ee03")) == "bluenoise"
    assert pipeline._dither(Config(panel="ee02")) == "stucki"
    assert Config.defaults_for("ee02").mode == "collage"
    assert Config.defaults_for("ee03").mode == "single"
    monkeypatch.setenv("FEATHERFRAME_PANEL", "ee02")
    assert Config().mode == "collage"


def test_a_custom_colour_panel_defaults_to_the_collage_too():
    from featherframe import panels
    assert panels.get("custom:1200x1600:spectra6:0,180").mode == "collage"
    assert panels.get("custom:800x480:gray16:90,270").mode == "single"
