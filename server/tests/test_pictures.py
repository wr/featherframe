"""The two pictures as pictures (W-833 step 2a).

A frame is a frame and a picture is a picture: `plates` and `collage` are the
same kind of thing, each drawn only while some frame shows it, and the wall's
framebuffer is the output of the one the primary kit shows — not state of its
own. These pin the rules that follow from that.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from featherframe import frames as frames_mod
from featherframe.db import Database
from featherframe.render import framebuffer, pipeline
from featherframe.render.compose import SingleSpec
from featherframe.sources.base import Detection
from tests._fixtures import create_birds_db, make_row

NOW = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
SPECIES = [("Northern Cardinal", "Cardinalis cardinalis"), ("Blue Jay", "Cyanocitta cristata"),
           ("American Goldfinch", "Spinus tristis")]


def _heard(path, species=SPECIES, at=NOW):
    rows = []
    for i, (c, s) in enumerate(species):
        rows += [make_row(at - timedelta(minutes=40 - i * 10 + j), c, s, 0.9) for j in range(3)]
    return str(create_birds_db(path, rows))


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
    yield service


def _kit(svc, frame_id: str, shows: str, primary: bool = False) -> dict:
    row = frames_mod.new_row(frame_id, "kit", NOW.isoformat(timespec="seconds"),
                             status=frames_mod.ON)
    row["primary"] = primary
    row["set"] = {"shows": shows}
    return svc.frames.save(row)


def _viewer(svc, viewer_id: str, shows: str, seen: datetime) -> None:
    svc.viewers.checkin(viewer_id, seen, "page", {"width": 600, "height": 800})
    svc.viewers.update(viewer_id, {"shows": shows})


# -- which pictures are worth drawing -----------------------------------------
def test_with_only_the_primary_kit_only_its_picture_is_wanted(svc):
    _kit(svc, "AA:00", "plates", primary=True)
    assert svc._kinds_shown(NOW) == {"plates"}
    svc.config.mode = "collage"
    assert svc._kinds_shown(NOW) == {"collage"}


def test_an_added_kit_on_the_other_picture_makes_it_wanted(svc):
    _kit(svc, "AA:00", "plates", primary=True)
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
    svc.viewers.update("PAGE-IPAD", {"shows": ""})
    svc.tick()
    assert svc.pictures["collage"].etag is None
    assert not svc.pictures["collage"].sheet_path.exists()


def test_another_frames_picture_never_touches_the_wall(svc):
    svc.tick()
    wall = (svc._etag, svc._frame_bytes, dict(svc._meta), svc._shown)
    _viewer(svc, "PAGE-IPAD", "collage", NOW)
    svc.tick()
    assert svc.pictures["collage"].etag
    assert (svc._etag, svc._frame_bytes, dict(svc._meta), svc._shown) == wall


# -- one collage, and the night -----------------------------------------------
def _quiet_all_day(svc) -> None:
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start = "11:00"
    svc.config.quiet_hours_end = "23:30"
    svc.config.quiet_hours_render_collage = True


def test_at_night_every_frame_on_plates_shows_the_collage(svc):
    svc.tick()
    assert svc._shown == "plates"
    plate = svc.pictures["plates"].etag
    _quiet_all_day(svc)
    svc.tick()
    # Drawn once, as the collage picture, and it is what the wall now shows.
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
def test_a_plate_on_the_wall_is_what_the_pipeline_packs(svc):
    det = Detection(rowid=-1, date=NOW.strftime("%Y-%m-%d"), time=NOW.strftime("%H:%M:%S"),
                    common_name="Northern Cardinal", scientific_name="Cardinalis cardinalis",
                    confidence=0.9)
    svc._render_single(det, NOW, reason="test")
    spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                      when=det.timestamp, first_seen=svc._first_seen(det.scientific_name),
                      note=None, note_kind=None,
                      first_ever=svc._novelty(det, NOW) == "first-ever")
    expected = pipeline.render_single(spec, svc.provider, svc.config)
    assert svc._frame_bytes == expected.frame
    assert svc._etag == expected.etag == framebuffer.etag_for(expected.frame)


def test_a_collage_on_the_wall_is_what_the_pipeline_packs(svc):
    svc.config.mode = "collage"
    assert svc._build_collage(NOW, NOW.date()) is True
    compose, _note = svc._collage_composer(NOW, NOW.date())
    img, label = compose(svc.config.panel_spec.color)
    expected = pipeline.render_image(img, svc.config, "collage", label)
    assert svc._frame_bytes == expected.frame and svc._etag == expected.etag


# -- an install that upgrades mid-flight --------------------------------------
def test_an_upgrade_adopts_the_frame_and_the_side_picture_it_finds(tmp_path, monkeypatch):
    """Nothing is re-rendered and the wall's ETag does not move: the resident
    frame becomes the plates picture, W-831's side picture the collage one."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe import paths
    from featherframe.config import Config
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"

    # What the old build left behind.
    from PIL import Image
    from featherframe.render import theme
    sheet = Image.new("L", (theme.WIDTH, theme.HEIGHT), 200)
    result = pipeline.render_image(sheet, Config(), "single", "Blue Jay")
    frames = paths.frames_dir()
    (frames / "current.fff").write_bytes(result.frame)
    result.preview.save(frames / "current.png")
    sheet.save(frames / "current_sheet.png")
    Image.new("L", (theme.WIDTH, theme.HEIGHT), 90).save(frames / "side_collage_sheet.png")
    db = Database()
    db.set("current_frame", {"etag": result.etag, "mode": "single", "label": "Blue Jay",
                             "species_key": "cyanocitta cristata",
                             "rendered_at": NOW.isoformat(timespec="seconds")})
    db.set("side_pictures", {"collage": {"etag": "0123456789abcdef",
                                         "at": NOW.isoformat(timespec="seconds"),
                                         "key": NOW.date().isoformat()}})

    svc = FeatherframeService(db)
    assert svc._shown == "plates"
    assert svc._etag == result.etag and svc._frame_bytes == result.frame
    assert svc._meta["label"] == "Blue Jay"
    assert svc.pictures["plates"].sheet_path.exists()
    assert svc.pictures["collage"].etag == "0123456789abcdef"
    assert svc.pictures["collage"].sheet_path.exists()
    # A second start reads the new store and changes nothing.
    again = FeatherframeService(Database())
    assert again._etag == result.etag and again.pictures["collage"].etag == "0123456789abcdef"
