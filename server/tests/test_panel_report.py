"""W-813: a frame describes its own panel (X-Panel-Width / -Height / -Format /
-Rotations), so the server draws for panels panels.py has never heard of."""
import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe import panels
from featherframe.config import Config
from featherframe.render import framebuffer, pipeline
from tests._frames import connect


# -- the report -----------------------------------------------------------------
def test_a_known_name_keeps_its_curated_panel_whatever_the_facts_say():
    facts = {"w": "800", "h": "480", "fmt": "mono", "rot": "0,180"}
    assert panels.from_report("ED103TC2 1404x1872 gray16", facts) is panels.EE03
    assert panels.from_report("T133A01 1200x1600 spectra6") is panels.EE02


def test_an_unknown_name_is_built_from_its_facts():
    p = panels.from_report("GDEY075 DIY", {"w": "800", "h": "480", "fmt": "gray16", "rot": "90,270"})
    assert (p.width, p.height) == (480, 800)          # portrait, as it hangs
    assert p.native == (800, 480)                     # as the firmware pushes it
    assert p.rotations == (90, 270) and p.fmt == "gray16" and not p.color
    assert not p.known and "GDEY075 DIY" in p.name and "800×480" in p.name
    # The key spells the facts out, and resolves back to the same panel.
    assert p.key == "custom:800x480:gray16:90,270"
    again = panels.get(p.key)
    assert (again.width, again.height, again.fmt, again.rotations) == (480, 800, "gray16", (90, 270))


def test_no_usable_size_is_no_panel():
    assert panels.from_report("mystery panel") is None
    assert panels.from_report("mystery", {"w": "0", "h": "480"}) is None
    assert panels.from_report("mystery", {"w": "99999", "h": "480"}) is None
    assert panels.from_report("mystery", {"w": "abc", "h": "480"}) is None


def test_rotations_share_an_axis_and_default_to_hanging_portrait():
    assert panels.custom(800, 480, "mono", "0,90,180").rotations == (0, 180)
    assert panels.custom(800, 480, "mono", "junk").rotations == (90, 270)    # landscape canvas
    assert panels.custom(480, 800, "mono", None).rotations == (0, 180)       # portrait canvas


def test_another_spectra_6_panel_is_not_taken_for_the_ee02():
    p = panels.from_report("E673 800x480 spectra6", {"w": "800", "h": "480", "fmt": "spectra6", "rot": "90,270"})
    assert p is not panels.EE02 and p.color and p.dither == "stucki"


def test_an_unknown_format_falls_back_to_gray_at_the_right_size():
    p = panels.from_report("ACeP", {"w": "600", "h": "448", "fmt": "acep7", "rot": "0,180"})
    assert p.fmt == "gray16" and p.unknown_format == "acep7" and not p.color
    assert (p.width, p.height) == (600, 448)
    assert panels.get(p.key).unknown_format == "acep7"


def test_config_keeps_a_custom_panel_and_its_rotation():
    cfg = Config(panel="custom:800x480:gray2:90,270", panel_rotation=0).sanitize()
    assert cfg.panel == "custom:800x480:gray2:90,270"
    assert cfg.panel_rotation == 90 and cfg.bit_depth == 2
    assert Config(panel="custom:800x480:mono:0,180").sanitize().bit_depth == 1
    assert Config(panel="custom:nonsense").sanitize().panel == "ee03"


# -- the wire ---------------------------------------------------------------------
def test_2bpp_round_trips_and_pads_with_white():
    idx = np.array([[0, 1, 2, 3, 3, 0], [3, 2, 1, 0, 0, 3]], dtype=np.uint8)
    frame = framebuffer.pack(idx, 2)
    _, _, bpp, w, h, flags = framebuffer.HEADER.unpack_from(frame, 0)
    assert (bpp, w, h, flags) == (2, 6, 2, 0)
    assert frame[16:18] == bytes([0b00011011, 0b11001111])     # left pixel = highest bits
    assert framebuffer.is_complete(frame)
    assert np.array_equal(framebuffer.unpack(frame), idx)


def _sheet():
    img = Image.new("L", (1404, 1872), 255)
    img.paste(0, (0, 0, 1404, 40))                    # ink along the sheet's top edge
    return img


@pytest.mark.parametrize("key,native,bpp", [
    ("custom:800x480:gray16:90,270", (800, 480), 4),
    ("custom:800x480:gray2:90,270", (800, 480), 2),
    ("custom:480x800:mono:0,180", (480, 800), 1),
])
def test_a_reported_panel_gets_a_frame_of_exactly_its_native_size(key, native, bpp):
    cfg = Config(panel=key, mat_inset_pct=0.0).sanitize()
    result = pipeline.render_image(_sheet(), cfg, "single", "t")
    _, _, got_bpp, w, h, _ = framebuffer.HEADER.unpack_from(result.frame, 0)
    assert (w, h) == native and got_bpp == bpp
    assert framebuffer.is_complete(result.frame)
    assert result.preview.size == (480, 800)          # upright, as it hangs


def test_a_panel_that_is_not_3_to_4_gets_the_whole_sheet_on_paper():
    cfg = Config(panel="custom:800x480:gray16:90,270", mat_inset_pct=0.0).sanitize()
    pipeline.DITHER_OVERRIDE = "none"
    px = np.asarray(pipeline.render_image(_sheet(), cfg, "single", "t").preview)
    # 480x800 is narrower than 3:4: the sheet is 480x640, centred, paper above and below.
    assert px[:78].min() == 255 and px[-78:].min() == 255
    assert px[82:88].max() == 0                        # the sheet's inked top edge, uncropped
    assert px[400, 0] == 255 and px.shape == (800, 480)


# -- the check-in -------------------------------------------------------------------
DIY = {"X-Panel": "GDEY075 DIY", "X-Panel-Width": "800", "X-Panel-Height": "480",
       "X-Panel-Format": "gray16", "X-Panel-Rotations": "90,270", "X-Device-Id": "CCCCCCCCCC03"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    app.state.service = svc
    svc._render_welcome(svc._clock(), False)
    return TestClient(app)


def test_first_checkin_from_an_unknown_panel_is_drawn_for_at_its_size(client):
    svc = client.app.state.service
    r = connect(client, DIY)
    assert r.status_code == 200
    cfg = svc.frame_config(svc.frames.get(DIY["X-Device-Id"]))
    assert cfg.panel == "custom:800x480:gray16:90,270" and cfg.panel_rotation == 90
    _, _, bpp, w, h, flags = framebuffer.HEADER.unpack_from(r.content, 0)
    assert (bpp, w, h, flags) == (4, 800, 480, 0)
    assert "GDEY075 DIY" in svc.frames_view()["active"]["panel_name"]
    page = client.get("/").text
    assert "GDEY075 DIY" in page


def test_an_unknown_panel_with_no_facts_says_so_on_the_page(client):
    svc = client.app.state.service
    connect(client, {"X-Panel": "mystery panel", "X-Device-Id": "DDDDDDDDDD04"})
    assert svc.frame_config(svc.frames.get("DDDDDDDDDD04")).panel == "ee03"
    assert svc.panel_notices()["unrecognised"]["label"] == "mystery panel"
    assert "did not say what panel it has" in client.get("/").text


def test_an_unknown_format_says_so_on_the_page(client):
    svc = client.app.state.service
    r = connect(client, {**DIY, "X-Panel-Format": "acep7"})
    _, _, bpp, w, h, flags = framebuffer.HEADER.unpack_from(r.content, 0)
    assert (bpp, w, h, flags) == (4, 800, 480, 0)      # gray, never a wrong size
    assert svc.panel_notices()["unknown_format"] == {"format": "acep7"}
    assert "not one this server can draw" in client.get("/").text




def test_the_panel_is_not_a_setting(client):
    """W-821/W-833: a frame's panel is what it reports; a posted `panel` is
    ignored and the household config has no say."""
    svc = client.app.state.service
    connect(client, DIY)
    same = {"Origin": "http://testserver"}
    client.post("/settings", data={"panel": "ee03"}, headers=same, follow_redirects=False)
    assert svc.page_config().panel == "custom:800x480:gray16:90,270"
    assert 'name="panel"' not in client.get("/").text
