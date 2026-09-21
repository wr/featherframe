"""The viewer render (W-823): the picture the frame is showing, drawn again as
a PNG for another screen — a TRMNL, an e-reader, a tablet — at that screen's
size and depth. It never touches the resident frame."""
from __future__ import annotations

import io
from datetime import datetime

import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe.config import Config
from featherframe.render import pipeline, theme
from featherframe.render.pipeline import View
from tests._frames import add_kit, device, frame_bytes


def _sheet(mode: str = "L") -> Image.Image:
    """A composed sheet stand-in: a smooth ramp, so a dither has work to do."""
    ramp = np.tile(np.linspace(0, 255, theme.WIDTH, dtype=np.uint8), (theme.HEIGHT, 1))
    return Image.fromarray(ramp, mode="L").convert(mode)


def test_a_render_keeps_the_sheet_it_was_finished_from():
    sheet = _sheet()
    result = pipeline.render_image(sheet, Config(mat_inset_pct=0.0), "single", "x")
    assert result.sheet is sheet


def test_gray16_at_the_ee03_size_is_the_ee03_preview():
    """TRMNL X is the EE03's glass: same sheet, same dither, same pixels."""
    sheet = _sheet()
    wall = pipeline.render_image(sheet, Config(mat_inset_pct=0.0), "single", "x").preview
    view = pipeline.render_view(sheet, View(1404, 1872, "gray16"))
    assert np.array_equal(np.asarray(view), np.asarray(wall))


def test_depths():
    sheet = _sheet()
    assert len(np.unique(np.asarray(pipeline.render_view(sheet, View(300, 400, "mono"))))) == 2
    assert len(np.unique(np.asarray(pipeline.render_view(sheet, View(300, 400, "gray2"))))) == 4
    smooth = pipeline.render_view(sheet, View(300, 400, "gray256"))
    assert smooth.mode == "L" and len(np.unique(np.asarray(smooth))) > 100


def test_another_shape_gets_the_whole_sheet_on_paper():
    view = pipeline.render_view(_sheet(), View(800, 480, "gray256"))
    assert view.size == (800, 480)
    px = np.asarray(view)
    assert (px[:, :100] == 255).all() and (px[:, -100:] == 255).all()   # paper either side


def test_rotation_turns_the_upright_picture_inside_the_asked_canvas():
    """A Kindle asks for 1448x1072 turned 90: the plate is drawn 1072x1448
    upright and delivered on its side."""
    upright = pipeline.render_view(_sheet(), View(1072, 1448, "gray256"))
    turned = pipeline.render_view(_sheet(), View(1448, 1072, "gray256", rotation=90))
    assert turned.size == (1448, 1072)
    assert np.array_equal(np.asarray(turned), np.rot90(np.asarray(upright), k=1))


def test_colour_is_the_colour_sheet_and_gray_when_there_is_none():
    assert pipeline.render_view(_sheet("RGB"), View(300, 400, "color")).mode == "RGB"
    assert pipeline.render_view(_sheet("L"), View(300, 400, "color")).mode == "L"
    # A gray viewer of a colour frame's sheet is still gray.
    assert pipeline.render_view(_sheet("RGB"), View(300, 400, "gray16")).mode == "L"


def test_view_parsing_refuses_what_it_cannot_draw():
    assert View.parse("1072", "1448", "gray256", "0") == View(1072, 1448, "gray256", 0)
    assert View.parse("1072", "1448", None, None) == View(1072, 1448, "gray256", 0)
    for bad in (("10", "1448", "gray16", "0"), ("9000", "100", "gray16", "0"),
                ("x", "1448", "gray16", "0"), ("1072", "1448", "plaid", "0"),
                ("1072", "1448", "gray16", "45")):
        assert View.parse(*bad) is None


# -- the service and the endpoint ---------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    app.state.service = svc
    return TestClient(app)


def _commit(svc, label: str = "x", sheet=None):
    """Make the plates picture be this sheet, as a render would."""
    svc._commit("plates", datetime(2026, 9, 20, 8, 0), sheet=sheet if sheet is not None else _sheet(),
                mode="single", species_key=None, label=label)
    return svc.pictures["plates"]


def test_no_frame_yet_is_a_404(client):
    assert client.get("/api/view.png?w=1072&h=1448").status_code == 404


def test_a_view_is_a_png_at_the_asked_size_with_its_own_etag(client):
    svc = client.app.state.service
    resident = _commit(svc)
    r = client.get("/api/view.png?w=1072&h=1448&format=gray256")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(r.content)).size == (1072, 1448)
    etag = r.headers["etag"]
    assert resident.etag in etag and "1072x1448" in etag
    again = client.get("/api/view.png?w=1072&h=1448&format=gray256",
                       headers={"If-None-Match": etag})
    assert again.status_code == 304
    # Another variant of the same frame is another ETag.
    assert client.get("/api/view.png?w=800&h=480&format=mono").headers["etag"] != etag


def test_a_view_never_touches_the_frame(client):
    svc = client.app.state.service
    add_kit(svc)
    svc._clock = lambda: datetime(2026, 9, 20, 8, 0)
    resident = _commit(svc)
    svc.tick()
    before = (svc._etag, frame_bytes(svc), device(svc))
    client.get("/api/view.png?w=1072&h=1448&format=gray256",
               headers={"X-Device-Id": "aa:bb", "X-Panel": "Kobo Clara"})
    assert (svc._etag, frame_bytes(svc), device(svc)) == before
    assert svc._etag == resident.etag


def test_a_new_frame_is_a_new_view_and_the_old_ones_are_dropped(client, tmp_path):
    svc = client.app.state.service
    _commit(svc, "one")
    first = client.get("/api/view.png?w=300&h=400").headers["etag"]
    sheet = Image.fromarray(np.full((theme.HEIGHT, theme.WIDTH), 40, dtype=np.uint8), mode="L")
    _commit(svc, "two", sheet)
    second = client.get("/api/view.png?w=300&h=400")
    assert second.headers["etag"] != first
    assert np.asarray(Image.open(io.BytesIO(second.content))).max() < 60
    views = list((tmp_path / "data" / "frames" / "views").glob("*.png"))
    assert len(views) == 1


def test_a_picture_with_no_sheet_yet_has_no_view(client, tmp_path):
    """A view is drawn from the picture\'s composed sheet and nothing else;
    without one there is nothing honest to send."""
    svc = client.app.state.service
    _commit(svc)
    svc.pictures["plates"].sheet_path.unlink()
    assert client.get("/api/view.png?w=300&h=400").status_code == 404


def test_bad_asks_are_400s(client):
    _commit(client.app.state.service)
    assert client.get("/api/view.png?w=10&h=10").status_code == 400
    assert client.get("/api/view.png?w=300&h=400&format=plaid").status_code == 400
    assert client.get("/api/view.png").status_code == 400


# -- colour for a gray frame's server ------------------------------------------
class _ColourArt:
    """A provider whose art has a colour twin: a red disc on paper."""
    name = "stub"

    def artwork(self, common_name, scientific_name):
        from PIL import ImageDraw
        from featherframe.render.provider import Artwork
        rgb = Image.new("RGB", (900, 900), "white")
        ImageDraw.Draw(rgb).ellipse((150, 150, 750, 750), fill=(200, 30, 30))
        gray = rgb.convert("L")
        return Artwork(gray, 159, color_loader=lambda: (gray, rgb))


def _is_coloured(png: bytes) -> bool:
    px = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    return bool((np.abs(px[..., 0] - px[..., 1]) > 60).any())


def _show_cardinal(svc):
    from featherframe.sources import Detection
    svc.provider = _ColourArt()
    det = Detection(rowid=1, date="2026-09-20", time="08:00:00",
                    common_name="Northern Cardinal",
                    scientific_name="Cardinalis cardinalis", confidence=0.9)
    svc._render_single(det, svc._clock(), reason="detection")


def test_nobody_asking_for_colour_costs_the_render_nothing(client, tmp_path):
    svc = client.app.state.service
    svc._clock = lambda: datetime(2026, 9, 20, 8, 0)
    _show_cardinal(svc)
    assert not svc.pictures["plates"].has_color()


def test_the_first_colour_ask_gets_colour_and_later_renders_keep_it(client, tmp_path):
    svc = client.app.state.service
    svc._clock = lambda: datetime(2026, 9, 20, 8, 0)
    add_kit(svc)
    _show_cardinal(svc)
    svc.tick()
    wall = frame_bytes(svc)
    r = client.get("/api/view.png?w=600&h=800&format=color")
    assert r.status_code == 200 and _is_coloured(r.content)
    # The wall's own pixels are what they were: colour is a second sheet.
    assert frame_bytes(svc) == wall
    # A later render composes the twin without being asked again.
    svc.pictures["plates"].color_sheet_path.unlink()
    _show_cardinal(svc)
    assert svc.pictures["plates"].has_color()
    # And a gray viewer of the same frame is still gray.
    assert not _is_coloured(client.get("/api/view.png?w=600&h=800&format=gray256").content)


def test_colour_stops_being_composed_when_no_colour_viewer_has_asked_for_a_month(client, tmp_path):
    svc = client.app.state.service
    svc._clock = lambda: datetime(2026, 9, 20, 8, 0)
    _show_cardinal(svc)
    client.get("/api/view.png?w=600&h=800&format=color")
    svc._clock = lambda: datetime(2026, 11, 1, 8, 0)
    svc._color_asked_at = None   # as after a restart: only the DB remembers
    _show_cardinal(svc)
    assert not svc.pictures["plates"].has_color()


def test_after_a_restart_the_first_colour_ask_renders_the_resident_subject_again(client):
    svc = client.app.state.service
    svc._clock = lambda: datetime(2026, 9, 20, 8, 0)
    _show_cardinal(svc)
    svc.pictures["plates"].recompose = None   # a restart forgets how it was composed
    r = client.get("/api/view.png?w=600&h=800&format=color")
    assert r.status_code == 200 and _is_coloured(r.content)
    assert svc._meta["label"] == "Northern Cardinal"


# -- the PNG on the wire --------------------------------------------------------
@pytest.mark.parametrize("fmt,bits", [("mono", 1), ("gray2", 2), ("gray16", 4)])
def test_dithered_views_are_png_at_their_true_depth(fmt, bits):
    """TRMNL's firmware paints 1/2/4-bit gray as it is and truncates 8-bit."""
    img = pipeline.render_view(_sheet(), View(301, 400, fmt))   # odd width: row padding
    png = pipeline.encode_png(img, fmt)
    assert png[24] == bits and png[25] == 0          # IHDR: bit depth, colour type gray
    back = Image.open(io.BytesIO(png)).convert("L")
    assert back.size == (301, 400)
    assert np.array_equal(np.asarray(back), np.asarray(img))


def test_smooth_views_are_ordinary_pngs():
    img = pipeline.render_view(_sheet(), View(300, 400, "gray256"))
    assert pipeline.encode_png(img, "gray256")[24] == 8
