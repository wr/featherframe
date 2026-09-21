"""Two pictures (W-831): the frame shows plates or the collage, and a viewer
may show the other. Wells's rules (W-830): a hold affects plates only and the
blocklist is global; there is one collage, the same on every screen; and a
picture no screen shows is never drawn. The wall must not notice any of it."""
from __future__ import annotations

import io
from datetime import datetime, timedelta

import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe.render import pipeline
from tests._fixtures import create_birds_db, make_row

NOW = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
SPECIES = [("Northern Cardinal", "Cardinalis cardinalis"), ("Blue Jay", "Cyanocitta cristata"),
           ("American Goldfinch", "Spinus tristis"), ("House Sparrow", "Passer domesticus")]
IPAD = "/api/view/state?viewer=PAGE-IPAD&w=600&h=800&device=iPad"


def _heard(path, species=SPECIES, at=NOW):
    rows = []
    for i, (c, s) in enumerate(species):
        rows += [make_row(at - timedelta(minutes=50 - i * 10 + j), c, s, 0.9) for j in range(3)]
    return str(create_birds_db(path, rows))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc._clock = lambda: NOW
    svc.config.quiet_hours_mode = "off"
    svc.config.mode = "single"
    svc.reload_config = lambda: None     # the test's config is the config
    svc.source.db_path = _heard(tmp_path / "birds.db")
    app.state.service = svc
    svc.tick()
    assert svc._meta["mode"] == "single"
    return TestClient(app)


def _png(client, state) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(client.get(state["image"]).content)).convert("L"))


def _side_files(tmp_path):
    return sorted(f.name for f in (tmp_path / "data" / "frames").glob("side_*"))


def test_a_viewer_follows_the_frame_until_it_is_told_otherwise(client, tmp_path):
    svc = client.app.state.service
    assert svc.current_etag() in client.get(IPAD).json()["image"]
    svc.tick()
    assert _side_files(tmp_path) == []          # nobody shows the collage: none is drawn


def test_an_ipad_on_the_collage_beside_a_frame_on_plates(client, tmp_path):
    svc = client.app.state.service
    client.get(IPAD)
    client.post("/api/viewers/PAGE-IPAD", json={"shows": "collage"})
    wall = (svc._etag, svc._frame_bytes, dict(svc._meta))
    svc.tick()
    assert "side_collage_sheet.png" in _side_files(tmp_path)
    state = client.get(IPAD).json()
    assert svc._side["collage"]["etag"] in state["image"] and svc._etag not in state["image"]
    plate = client.get("/api/view.png?w=600&h=800&format=color").content
    assert not np.array_equal(_png(client, state),
                              np.asarray(Image.open(io.BytesIO(plate)).convert("L")))
    # The wall never noticed.
    assert (svc._etag, svc._frame_bytes, dict(svc._meta)) == wall
    # A new plate on the wall is not news to a screen on the collage: its
    # picture is named for the collage, which is redrawn on its own interval.
    drawn = svc._side["collage"]["at"]
    svc.source.db_path = _heard(tmp_path / "later.db", SPECIES[:3] + [("Tufted Titmouse", "Baeolophus bicolor")])
    svc._clock = lambda: NOW + timedelta(minutes=1)
    client.get("/api/view/state?viewer=PAGE-GRAY&w=600&h=800")   # (keeps the colour ask out of it)
    svc._side_stale.clear()
    svc.tick()
    assert svc._side["collage"]["at"] == drawn
    assert svc._side["collage"]["etag"] in client.get(IPAD).json()["image"]


def test_the_collage_is_redrawn_on_its_interval_not_every_tick(client):
    svc = client.app.state.service
    client.get(IPAD)
    client.post("/api/viewers/PAGE-IPAD", json={"shows": "collage"})
    svc.tick()
    drawn = svc._side["collage"]["at"]
    svc._clock = lambda: NOW + timedelta(minutes=30)
    svc.tick()
    assert svc._side["collage"]["at"] == drawn
    svc._clock = lambda: NOW + timedelta(hours=svc.config.collage_interval_hours, minutes=1)
    client.get(IPAD)
    svc.tick()
    assert svc._side["collage"]["at"] != drawn


def test_a_trmnl_on_plates_beside_a_frame_on_the_collage(client, tmp_path):
    svc = client.app.state.service
    svc.config.mode = "collage"
    svc.tick()
    assert svc._meta["mode"] == "collage"
    trmnl = {"ID": "AA:BB:CC:DD:EE:01", "Model": "x", "Width": "1872", "Height": "1404"}
    client.get("/api/display", headers=trmnl)
    client.post(f"/api/viewers/{trmnl['ID']}", json={"shows": "plates"})
    wall = svc._etag
    svc.tick()
    body = client.get("/api/display", headers=trmnl).json()
    assert body["filename"].startswith(svc._side["plates"]["etag"]) and svc._etag == wall
    assert client.get(body["image_url"].split("http://testserver")[1]).status_code == 200
    # A hold pins the plate on every screen that shows plates (decision 1)...
    svc.user_hold = lambda now=None: {"until": "later"}
    svc.source.db_path = _heard(tmp_path / "later.db", [("Tufted Titmouse", "Baeolophus bicolor")] + SPECIES[:2],
                                at=NOW + timedelta(minutes=5))
    svc._clock = lambda: NOW + timedelta(minutes=6)
    svc.tick()
    assert client.get("/api/display", headers=trmnl).json()["filename"] == body["filename"]


def test_there_is_one_collage_the_same_on_every_screen(client):
    """Decision 2: a frame on plates holding the nightly collage and a viewer
    on the collage show the identical sheet."""
    svc = client.app.state.service
    client.get(IPAD)
    client.post("/api/viewers/PAGE-IPAD", json={"shows": "collage", "dark_quiet": False})
    svc.tick()
    assert svc._side["collage"]["etag"] in client.get(IPAD).json()["image"]
    svc._build_collage(NOW, NOW.date(), generated_ok=True)      # nightfall: the frame takes the collage
    assert svc._meta["mode"] == "collage"
    assert svc._etag in client.get(IPAD).json()["image"]


def test_the_blocklist_is_global(client, tmp_path):
    """Decision 1: a blocked species is in no plate and no collage."""
    svc = client.app.state.service
    labels = lambda: [svc._collage_composer(NOW, NOW.date())[0](False)[1]]   # noqa: E731
    assert labels() == ["4-species collage"]
    svc.config.species_blocklist = ["House Sparrow"]
    assert labels() == ["3-species collage"]
    svc.source.db_path = _heard(tmp_path / "sparrow.db", [SPECIES[0], SPECIES[3]])   # sparrow heard last
    assert svc._first_showable(svc.source.latest_many(0.0), NOW).common_name == "Northern Cardinal"


def test_a_picture_nobody_shows_any_more_is_dropped(client, tmp_path):
    svc = client.app.state.service
    client.get(IPAD)
    client.post("/api/viewers/PAGE-IPAD", json={"shows": "collage"})
    svc.tick()
    assert _side_files(tmp_path)
    client.post("/api/viewers/PAGE-IPAD", json={"shows": ""})
    svc.tick()
    assert _side_files(tmp_path) == [] and svc._side == {}


def test_the_card_offers_shows(client):
    client.get(IPAD)
    html = client.get("/").text
    assert 'data-vw="shows"' in html and "What the frame shows" in html
