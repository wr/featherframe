"""Two pictures (W-831, rebuilt on `pictures.py` in W-833): every frame shows
plates or the collage, and they are the same kind of thing. Wells's rules
(W-830): a hold affects plates only and the blocklist is global; there is one
collage, the same on every screen; and a picture no frame shows is never drawn.
The wall must not notice any of it."""
from __future__ import annotations

import io
from datetime import datetime, timedelta

import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe.render import pipeline
from tests._fixtures import create_birds_db, make_row
from tests._frames import FRAME_ID, add_kit, add_page, add_trmnl, frame_bytes

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
    svc.reload_config = lambda: None     # the test's config is the config
    add_kit(svc)                         # one kit on the wall, showing plates
    svc.source.db_path = _heard(tmp_path / "birds.db")
    app.state.service = svc
    svc.tick()
    assert svc._meta["mode"] == "single"
    return TestClient(app)


def _png(client, state) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(client.get(state["image"]).content)).convert("L"))


def _drawn(svc) -> list:
    """Which pictures this server is paying to draw, in order."""
    return [kind for kind in ("plates", "collage") if svc.pictures[kind].etag]


def _sheets(svc, kind) -> list:
    return sorted(f.name for f in svc.pictures[kind].dir.glob("*.png"))


def test_a_viewer_follows_the_frame_until_it_is_told_otherwise(client):
    svc = client.app.state.service
    assert svc.current_etag() in add_page(client, IPAD, "PAGE-IPAD").json()["image"]
    svc.tick()
    assert _drawn(svc) == ["plates"]             # nobody shows the collage: none is drawn


def test_an_ipad_on_the_collage_beside_a_frame_on_plates(client, tmp_path):
    svc = client.app.state.service
    add_page(client, IPAD, "PAGE-IPAD")
    client.post("/api/frames/PAGE-IPAD", json={"shows": "collage"})
    wall = (svc._etag, frame_bytes(svc), dict(svc._meta))
    svc.tick()
    assert _sheets(svc, "collage") == ["sheet.png"]
    state = client.get(IPAD).json()
    assert svc.pictures["collage"].etag in state["image"] and svc._etag not in state["image"]
    plate = client.get("/api/view.png?w=600&h=800&format=color").content
    assert not np.array_equal(_png(client, state),
                              np.asarray(Image.open(io.BytesIO(plate)).convert("L")))
    # The wall never noticed.
    assert (svc._etag, frame_bytes(svc), dict(svc._meta)) == wall
    # A new plate on the wall is not news to a screen on the collage: its
    # picture is the collage, which is redrawn on its own interval.
    drawn = svc.pictures["collage"].at
    svc.source.db_path = _heard(tmp_path / "later.db", SPECIES[:3] + [("Tufted Titmouse", "Baeolophus bicolor")])
    svc._clock = lambda: NOW + timedelta(minutes=1)
    client.get("/api/view/state?viewer=PAGE-GRAY&w=600&h=800")   # (keeps the colour ask out of it)
    svc._recolor.clear()
    svc.tick()
    assert svc.pictures["collage"].at == drawn
    assert svc.pictures["collage"].etag in client.get(IPAD).json()["image"]


def test_the_collage_is_redrawn_on_its_interval_not_every_tick(client):
    svc = client.app.state.service
    add_page(client, IPAD, "PAGE-IPAD")
    client.post("/api/frames/PAGE-IPAD", json={"shows": "collage"})
    svc.tick()
    drawn = svc.pictures["collage"].at
    svc._clock = lambda: NOW + timedelta(minutes=30)
    svc.tick()
    assert svc.pictures["collage"].at == drawn
    svc._clock = lambda: NOW + timedelta(hours=svc.config.collage_interval_hours, minutes=1)
    client.get(IPAD)
    svc.tick()
    assert svc.pictures["collage"].at != drawn


def test_a_trmnl_on_plates_beside_a_frame_on_the_collage(client, tmp_path):
    svc = client.app.state.service
    svc.update_frame(FRAME_ID, {"shows": "collage"})
    svc.tick()
    assert svc._meta["mode"] == "collage"
    trmnl = {"ID": "AA:BB:CC:DD:EE:01", "Model": "x", "Width": "1872", "Height": "1404"}
    add_trmnl(client, trmnl)
    client.post(f"/api/frames/{trmnl['ID']}", json={"shows": "plates"})
    wall = svc._etag
    svc.tick()
    body = client.get("/api/display", headers=trmnl).json()
    assert body["filename"].startswith(svc.pictures["plates"].etag) and svc._etag == wall
    assert client.get(body["image_url"].split("http://testserver")[1]).status_code == 200
    # A hold pins the plate on every screen that shows plates (decision 1)...
    svc.user_hold = lambda now=None: {"until": "later"}
    svc.source.db_path = _heard(tmp_path / "later.db", [("Tufted Titmouse", "Baeolophus bicolor")] + SPECIES[:2],
                                at=NOW + timedelta(minutes=5))
    svc._clock = lambda: NOW + timedelta(minutes=6)
    svc.tick()
    assert client.get("/api/display", headers=trmnl).json()["filename"] == body["filename"]


def test_a_hold_pins_the_plate_while_the_collage_keeps_being_drawn(client):
    """Decision 1, the other way round: the wall is held on its plate, a tablet
    is on the collage, and the collage is still redrawn on its interval."""
    svc = client.app.state.service
    svc.config.collage_interval_hours = 1    # (a jump short of the gone-quiet alarm)
    add_page(client, IPAD, "PAGE-IPAD")
    client.post("/api/frames/PAGE-IPAD", json={"shows": "collage"})
    svc.tick()
    wall, first = svc._etag, svc.pictures["collage"].at
    svc.user_hold = lambda now=None: {"until": "later"}
    svc._clock = lambda: NOW + timedelta(hours=1, minutes=1)
    svc.tick()
    assert svc._etag == wall                               # the plate is pinned
    assert svc.pictures["collage"].at != first             # the collage is not


def test_there_is_one_collage_the_same_on_every_screen(client):
    """Decision 2: at night every frame on plates shows the collage picture —
    the same sheet, drawn once, that the frames on the collage show."""
    svc = client.app.state.service
    add_page(client, IPAD, "PAGE-IPAD")
    client.post("/api/frames/PAGE-IPAD", json={"shows": "collage", "dark_quiet": False})
    svc.tick()
    assert svc.pictures["collage"].etag in client.get(IPAD).json()["image"]
    # Nightfall: the frame on plates takes that same collage for the window.
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start, svc.config.quiet_hours_end = "11:00", "23:30"
    svc.tick()
    assert svc._meta["mode"] == "collage"
    assert svc._etag == svc.pictures["collage"].etag
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


def test_a_picture_nobody_shows_any_more_is_dropped(client):
    svc = client.app.state.service
    add_page(client, IPAD, "PAGE-IPAD")
    client.post("/api/frames/PAGE-IPAD", json={"shows": "collage"})
    svc.tick()
    assert _drawn(svc) == ["plates", "collage"]
    client.post("/api/frames/PAGE-IPAD", json={"shows": ""})
    svc.tick()
    assert _drawn(svc) == ["plates"] and _sheets(svc, "collage") == []


def test_the_card_offers_shows(client):
    """Every frame's row offers Shows, the tablet's exactly like the kit's."""
    add_page(client, IPAD, "PAGE-IPAD")
    row = client.get("/").text.split('data-frame="PAGE-IPAD"')[1].split(chr(10) + "    </li>")[0]
    assert 'data-f="shows"' in row and 'value="plates"' in row and 'value="collage"' in row


def test_the_species_limit_is_the_collages_however_it_is_drawn(client, tmp_path):
    """Wells: the limit only bit on the AI sheet; the grid always showed six."""
    from featherframe.render import collage as collage_mod
    assert [collage_mod._grid(n) for n in (1, 2, 6, 7, 12, 13, 20)] == \
        [(1, 1), (2, 1), (2, 3), (3, 3), (3, 4), (4, 4), (4, 5)]
    svc = client.app.state.service
    many = SPECIES + [("Tufted Titmouse", "Baeolophus bicolor"), ("Carolina Wren", "Thryothorus ludovicianus"),
                      ("Downy Woodpecker", "Dryobates pubescens"), ("American Robin", "Turdus migratorius")]
    svc.source.db_path = _heard(tmp_path / "many.db", many)
    label = lambda: svc._collage_composer(NOW, NOW.date())[0](False)[1]   # noqa: E731
    svc.config.collage_species_max = 0
    assert label() == "8-species collage"           # no limit: every species heard, not six
    svc.config.collage_species_max = 3
    assert label() == "3-species collage"
