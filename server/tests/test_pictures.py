"""The two pictures as pictures (W-833).

A frame is a frame and a picture is a picture: `plates` and `collage` are the
same kind of thing, each drawn only while some frame shows it, and a frame's
framebuffer is the output of the one IT shows — not state of its own. These
pin the rules that follow from that.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from featherframe.db import Database
from featherframe.render import framebuffer, pipeline
from featherframe.render.compose import SingleSpec
from featherframe.sources.base import Detection
from tests._fixtures import create_birds_db, make_row
from tests._frames import add_kit, approve, frame_bytes, pin_today

NOW = datetime(2026, 9, 22, 12, 0, 0)
SPECIES = [("Northern Cardinal", "Cardinalis cardinalis"), ("Blue Jay", "Cyanocitta cristata"),
           ("American Goldfinch", "Spinus tristis")]


def _heard(path, species=SPECIES, at=NOW):
    rows = []
    for i, (c, s) in enumerate(species):
        rows += [make_row(at - timedelta(minutes=40 - i * 10 + j), c, s, 0.9) for j in range(3)]
    return str(create_birds_db(path, rows))


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    pin_today(monkeypatch, NOW)     # the collage's day is NOW's, not the runner's


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    service = FeatherframeService()
    service._clock = lambda: NOW
    service.config.quiet_hours_mode = "off"
    service.config.mode = "single"
    service.reload_config = lambda: None      # the test's config is the config
    service.source.db_path = _heard(tmp_path / "birds.db")
    add_kit(service, "AA:00")                 # one kit on the wall, showing plates
    yield service


def _kit(svc, frame_id: str, shows: str) -> dict:
    return add_kit(svc, frame_id, shows=shows)


def _viewer(svc, viewer_id: str, shows: str, seen: datetime) -> None:
    svc.checkin_viewer(viewer_id, seen, "page", {"width": 600, "height": 800})
    approve(svc, viewer_id)           # every frame is answered for (W-833)
    svc.update_frame(viewer_id, {"shows": shows})


# -- which pictures are worth drawing -----------------------------------------
def test_with_one_kit_only_its_picture_is_wanted(svc):
    assert svc._kinds_shown(NOW) == {"plates"}
    svc.update_frame("AA:00", {"shows": "collage"})
    assert svc._kinds_shown(NOW) == {"collage"}


def test_a_second_kit_on_the_other_picture_makes_it_wanted(svc):
    _kit(svc, "BB:02", "collage")
    assert svc._kinds_shown(NOW) == {"plates", "collage"}


def test_a_viewer_on_the_other_picture_makes_it_wanted(svc):
    _viewer(svc, "PAGE-IPAD", "collage", NOW)
    assert svc._kinds_shown(NOW) == {"plates", "collage"}


def test_a_viewer_that_has_not_asked_in_a_month_is_not_a_frame(svc):
    _viewer(svc, "PAGE-IPAD", "collage", NOW - timedelta(days=40))
    assert svc._kinds_shown(NOW) == {"plates"}


def test_a_picture_nobody_shows_is_never_drawn_and_is_dropped(svc):
    _viewer(svc, "PAGE-IPAD", "collage", NOW)
    svc.tick()
    assert svc.pictures["collage"].etag and svc.pictures["collage"].sheet_path.exists()
    svc.update_frame("PAGE-IPAD", {"shows": ""})
    svc.tick()
    assert svc.pictures["collage"].etag is None
    assert not svc.pictures["collage"].sheet_path.exists()


def test_another_frames_picture_never_touches_the_wall(svc):
    svc.tick()
    wall = (svc._etag, frame_bytes(svc, "AA:00"), dict(svc._meta), svc._shown)
    _viewer(svc, "PAGE-IPAD", "collage", NOW)
    svc.tick()
    assert svc.pictures["collage"].etag
    assert (svc._etag, frame_bytes(svc, "AA:00"), dict(svc._meta), svc._shown) == wall


# -- one collage, and the night -----------------------------------------------
def _quiet_all_day(svc) -> None:
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start = "11:00"
    svc.config.quiet_hours_end = "23:30"


def test_at_night_every_frame_on_plates_shows_the_collage(svc):
    svc.tick()
    assert svc._shown == "plates"
    plate = svc.pictures["plates"].etag
    _quiet_all_day(svc)
    svc.tick()
    # Drawn once, as the collage picture, and it is what the wall now shows.
    assert svc.pictures["collage"].etag is not None
    assert svc._shown == "collage" and svc._etag == svc.pictures["collage"].etag
    # A frame told to show plates shows that same sheet, not its own plate.
    assert svc.picture_for("plates", NOW).kind == "collage"
    assert svc.picture_for("collage", NOW) is svc.picture_for("plates", NOW)
    # And the plate is still there, untouched, for when the window ends.
    assert svc.pictures["plates"].etag == plate
    svc.config.quiet_hours_mode = "off"
    assert svc.picture_for("plates", NOW).etag == plate


def test_the_nightly_collage_is_drawn_once(svc):
    _quiet_all_day(svc)
    svc.tick()
    drawn = svc.pictures["collage"].at
    svc._clock = lambda: NOW + timedelta(hours=2)
    svc.tick()
    assert svc.pictures["collage"].at == drawn


def test_a_hold_pins_plates_and_nothing_else(svc):
    _viewer(svc, "PAGE-IPAD", "collage", NOW)
    svc.tick()
    plate, collage = svc.pictures["plates"].etag, svc.pictures["collage"].at
    svc.config.collage_interval_hours = 1
    svc.hold_current("day")
    svc._clock = lambda: NOW + timedelta(hours=1, minutes=1)
    svc.tick()
    assert svc.pictures["plates"].etag == plate          # pinned
    assert svc.pictures["collage"].at != collage         # still redrawn


# -- the wall's own pixels ----------------------------------------------------
def test_a_plate_on_a_frame_is_what_the_pipeline_packs(svc):
    """A frame's bytes are its picture's sheet, finished with its own config —
    exactly what render_single would have packed in one go."""
    det = Detection(rowid=-1, date=NOW.strftime("%Y-%m-%d"), time=NOW.strftime("%H:%M:%S"),
                    common_name="Northern Cardinal", scientific_name="Cardinalis cardinalis",
                    confidence=0.9)
    svc._render_single(det, NOW, reason="test")
    svc._tick_frames()
    spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                      when=det.timestamp, first_seen=svc._first_seen(det.scientific_name),
                      note=None, note_kind=None,
                      first_ever=svc._novelty(det, NOW) == "first-ever")
    cfg = svc.frame_config(svc.frames.get("AA:00"))
    expected = pipeline.render_single(spec, svc.provider, cfg)
    assert frame_bytes(svc, "AA:00") == expected.frame
    assert svc._out["AA:00"]["etag"] == framebuffer.etag_for(expected.frame)


def test_a_collage_on_a_frame_is_what_the_pipeline_packs(svc):
    svc.update_frame("AA:00", {"shows": "collage"})
    assert svc._build_collage(NOW, NOW.date()) is True
    svc._tick_frames()
    compose, _note = svc._collage_composer(NOW, NOW.date())
    cfg = svc.frame_config(svc.frames.get("AA:00"))
    img, label = compose(cfg.panel_spec.color)
    expected = pipeline.render_image(img, cfg, "collage", label)
    assert frame_bytes(svc, "AA:00") == expected.frame
    assert svc._out["AA:00"]["etag"] == expected.etag
