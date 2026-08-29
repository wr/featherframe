"""Background regeneration (W-587 follow-up): the regenerate endpoint returns
immediately, the generated-plates listing carries the in-flight flag and the
last error per slug, and a finished job clears the flag — so a refreshed page
can pick the repaint state back up. The image model is faked — no network.
"""
from __future__ import annotations

import io
import threading
import time

import pytest
from PIL import Image, ImageDraw
from starlette.testclient import TestClient

from featherframe.render.genart import GeneratedArtProvider, ImageModel

SLUG = "passer-domesticus"


def _plate_png() -> bytes:
    """A plausible fake plate: light paper, one dark subject mass."""
    img = Image.new("L", (512, 768), 245)
    d = ImageDraw.Draw(img)
    d.ellipse([150, 250, 360, 520], fill=40)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class GateModel(ImageModel):
    """Blocks inside generate() until released, so tests can observe the
    in-flight state deterministically."""

    name = "gate"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, prompt, size, refs) -> bytes:
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=10), "test never released the model"
        if self.fail:
            raise RuntimeError("paint spilled")
        return _plate_png()


class SeedModel(ImageModel):
    name = "seed"

    def generate(self, prompt, size, refs) -> bytes:
        return _plate_png()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    # Seed one cached plate so there is something to regenerate.
    GeneratedArtProvider(SeedModel()).artwork("House Sparrow", "Passer domesticus")

    from featherframe.service import FeatherframeService
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    service.config.dither = "none"  # keep any re-render cheap
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app)


def _wait_done(svc, timeout: float = 15.0) -> list[dict]:
    """Poll the listing until nothing is in flight; return the final listing."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        listing = svc.generated_listing()
        if not any(m["regenerating"] for m in listing):
            return listing
        time.sleep(0.05)
    raise AssertionError("regeneration never finished")


def _entry(listing: list[dict], slug: str = SLUG) -> dict:
    return next(m for m in listing if m["slug"] == slug)


# -- the endpoint returns immediately ---------------------------------------
def test_regenerate_endpoint_returns_before_the_paint_dries(client, svc):
    model = GateModel()
    svc.genart._model = model

    r = client.post("/api/generated/regenerate", data={"slug": SLUG})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # The response came back while the model is still painting.
    assert model.started.wait(timeout=5)
    assert not model.release.is_set()

    # The listing (what the page polls, and what a refreshed page reads)
    # carries the in-flight flag.
    listing = client.get("/api/generated").json()
    assert _entry(listing["cached"])["regenerating"] is True
    assert SLUG in listing["regenerating"]

    model.release.set()
    _wait_done(svc)
    listing = client.get("/api/generated").json()
    entry = _entry(listing["cached"])
    assert entry["regenerating"] is False
    assert not entry.get("regen_error")
    assert listing["regenerating"] == []
    assert model.calls == 1


def test_regenerate_endpoint_refuses_unknown_slug(client, svc):
    svc.genart._model = GateModel()
    r = client.post("/api/generated/regenerate", data={"slug": "turdus-nemo"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_listing_keeps_existing_sidecar_fields(client):
    # Existing consumers read slug/common/created_at from "cached" — the new
    # fields ride along, they don't replace anything.
    listing = client.get("/api/generated").json()
    entry = _entry(listing["cached"])
    assert entry["common"] == "House Sparrow"
    assert entry["scientific"] == "Passer domesticus"
    assert entry["created_at"]


# -- failure and recovery ----------------------------------------------------
def test_failed_regeneration_reports_error_then_clears(svc):
    model = GateModel(fail=True)
    model.release.set()  # fail immediately
    svc.genart._model = model
    assert svc.start_regenerate(SLUG) is True
    entry = _entry(_wait_done(svc))
    assert entry["regenerating"] is False
    assert entry["regen_error"]
    # The old plate is kept on failure.
    assert svc.genart._png(SLUG).exists()

    # A later successful repaint clears the recorded error.
    ok_model = GateModel()
    ok_model.release.set()
    svc.genart._model = ok_model
    assert svc.start_regenerate(SLUG) is True
    entry = _entry(_wait_done(svc))
    assert entry["regenerating"] is False
    assert not entry["regen_error"]


# -- duplicate requests ------------------------------------------------------
def test_second_request_for_same_slug_does_not_double_buy(svc):
    model = GateModel()
    svc.genart._model = model
    assert svc.start_regenerate(SLUG) is True
    assert model.started.wait(timeout=5)
    # A second request while the first is painting joins it — one purchase.
    assert svc.start_regenerate(SLUG) is True
    model.release.set()
    _wait_done(svc)
    assert model.calls == 1


def test_start_regenerate_unknown_slug_is_refused(svc):
    svc.genart._model = GateModel()
    assert svc.start_regenerate("turdus-nemo") is False


# -- refresh-proof: the page itself renders the in-flight state --------------
def test_config_page_renders_inflight_state(client, svc):
    model = GateModel()
    svc.genart._model = model
    assert svc.start_regenerate(SLUG) is True
    assert model.started.wait(timeout=5)
    r = client.get("/")
    assert r.status_code == 200
    assert f'data-slug="{SLUG}"' in r.text
    # The in-flight class is server-rendered, so a refreshed page shows the
    # repaint before any JS runs. (Text alone won't do: the JS carries it.)
    assert "gen-item regenerating" in r.text
    model.release.set()
    _wait_done(svc)
    r = client.get("/")
    assert "gen-item regenerating" not in r.text


def test_config_page_shows_mat_caption(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "mat allowance" in r.text


# -- the current-frame re-render survives the move to a worker thread --------
def test_regenerate_rerenders_current_frame_from_the_worker(svc):
    model = GateModel()
    model.release.set()
    svc.genart._model = model
    # The resident frame currently shows this species.
    svc._meta["species_key"] = "passer domesticus"
    assert svc._etag is None
    assert svc.start_regenerate(SLUG) is True
    _wait_done(svc)
    assert svc._etag is not None
    assert svc._frame_bytes is not None and svc._frame_bytes[:4] == b"FFF1"
    assert svc._meta.get("mode") == "single"
