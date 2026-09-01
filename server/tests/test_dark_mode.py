"""Dark mode (W-587 follow-up): invert everything the panel shows.

The flip happens once, in pipeline._finish, on the final level indices — so the
packed framebuffer and the PNG preview agree. The device learns the flag from
the X-FF-Invert header on every /api/frame response (a 304 included) so it can
invert its baked boot screens too.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe import PANEL_HEIGHT, PANEL_WIDTH
from featherframe.config import Config, load_config, save_config
from featherframe.db import Database
from featherframe.render import framebuffer, pipeline


# -- config ----------------------------------------------------------------
def test_dark_mode_defaults_off():
    assert Config().dark_mode == "off"


def test_dark_mode_migrates_legacy_bool():
    # Installs from before the enum stored a bool; it maps to on/off.
    assert Config(dark_mode=True).dark_mode == "on"
    assert Config(dark_mode=False).dark_mode == "off"


def test_dark_mode_survives_persistence_round_trip(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    save_config(db, Config(dark_mode="on"))
    assert load_config(db).dark_mode == "on"


# -- pipeline --------------------------------------------------------------
def _half_and_half() -> Image.Image:
    """Top half black, bottom half white: both extremes in one composition."""
    img = Image.new("L", (PANEL_WIDTH, PANEL_HEIGHT), 255)
    img.paste(0, (0, 0, PANEL_WIDTH, PANEL_HEIGHT // 2))
    return img


def _cfg(**kw) -> Config:
    # dither "none" + no inset + no rotation: the output is a pure quantise of
    # the input, so pixel assertions are exact.
    return Config(dither="none", mat_inset_pct=0.0, panel_rotation=0, **kw)


def test_pipeline_inverts_preview():
    light = pipeline.render_image(_half_and_half(), _cfg(), "single", "t")
    dark = pipeline.render_image(_half_and_half(), _cfg(dark_mode=True), "single", "t")
    x, top, bottom = PANEL_WIDTH // 2, PANEL_HEIGHT // 4, 3 * PANEL_HEIGHT // 4
    assert (light.preview.getpixel((x, top)), light.preview.getpixel((x, bottom))) == (0, 255)
    assert (dark.preview.getpixel((x, top)), dark.preview.getpixel((x, bottom))) == (255, 0)


def test_pipeline_inverts_packed_frame():
    light = pipeline.render_image(_half_and_half(), _cfg(), "single", "t")
    dark = pipeline.render_image(_half_and_half(), _cfg(dark_mode=True), "single", "t")
    assert light.frame[:16] == dark.frame[:16]  # same container, same geometry
    # Every level index flips end-to-end: 0 <-> 15.
    assert np.array_equal(framebuffer.unpack(dark.frame),
                          15 - framebuffer.unpack(light.frame))
    assert dark.etag != light.etag


def test_pipeline_inverts_1bit_too():
    light = pipeline.render_image(_half_and_half(), _cfg(gray_mode="1"), "single", "t")
    dark = pipeline.render_image(_half_and_half(), _cfg(gray_mode="1", dark_mode=True),
                                 "single", "t")
    assert np.array_equal(framebuffer.unpack(dark.frame),
                          1 - framebuffer.unpack(light.frame))


# -- device signal + settings form -----------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    """The real app wired to a real service, without the lifespan's scheduler
    thread — tests set the resident frame directly."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    app.state.service = svc
    return TestClient(app)


def _seed_frame(client) -> str:
    svc = client.app.state.service
    svc._frame_bytes = b"FFF1" + bytes(12)
    svc._etag = "abc123"
    return svc._etag


@pytest.mark.parametrize("dark,flag", [("off", "0"), ("on", "1")])
def test_frame_response_carries_invert_header(client, dark, flag):
    etag = _seed_frame(client)
    client.app.state.service.config.dark_mode = dark

    r = client.get("/api/frame")
    assert r.status_code == 200
    assert r.headers["x-ff-invert"] == flag

    r = client.get("/api/frame", headers={"If-None-Match": f'"{etag}"'})
    assert r.status_code == 304
    assert r.headers["x-ff-invert"] == flag


def test_view_variant_carries_invert_header(client):
    svc = client.app.state.service
    svc.config.dark_mode = "on"
    svc.config.dither = "none"  # keep the on-demand render cheap
    r = client.get("/api/frame", params={"view": "status"})
    assert r.status_code == 200
    assert r.headers["x-ff-invert"] == "1"


# Checkboxes that default on: omitting one from the form would turn it off and
# muddy the render_affecting comparison, so every POST carries them.
_BASE_FORM = {"single_show_latest": "on", "quiet_hours_mode": "custom",
              "show_plate_number": "on", "imagegen_enabled": "on",
              "collage_generated": "on"}


def test_settings_toggle_is_render_affecting(client, monkeypatch):
    svc = client.app.state.service
    calls = []
    monkeypatch.setattr(svc, "rerender_current", lambda: calls.append(1))

    # Flipping dark mode on re-renders the current frame.
    r = client.post("/settings", data={**_BASE_FORM, "dark_mode": "on"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert svc.config.dark_mode == "on"
    assert len(calls) == 1

    # Re-submitting the same settings does not.
    client.post("/settings", data={**_BASE_FORM, "dark_mode": "on"},
                follow_redirects=False)
    assert len(calls) == 1

    # Flipping it back off re-renders again.
    client.post("/settings", data={**_BASE_FORM, "dark_mode": "off"},
                follow_redirects=False)
    assert svc.config.dark_mode == "off"
    assert len(calls) == 2
