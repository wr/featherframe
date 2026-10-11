"""What the server tells its front door it may switch to alone (W-1025)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from featherframe.render import pipeline
from featherframe.service import CONFIDENCE_FLOOR
from featherframe.sources.pushed import PushedSource
from tests._frames import EE02_PANEL, EE03_PANEL, add_kit
from tests.test_drawn import COLOUR, GRAY, NOW, SPECIES, _RefArt

VIEWER = "AA:BB:CC:00:00:01"


def GO(common, sci, note=None, at=NOW):
    return {"type": "detection", "metadata": {
        "species": common, "scientific_name": sci, "confidence": 0.9, "note_id": note,
        "days_since_first_seen": 30,
        "bg_detection_date": at.strftime("%Y-%m-%d"), "bg_detection_time": at.strftime("%H:%M:%S")}}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    s = FeatherframeService()
    s._clock = lambda: NOW
    s.config.quiet_hours_mode = "off"
    s.config.detection_backend = "birdnet_go"
    s.reload_config = lambda: None
    s.provider = _RefArt()
    s.source = PushedSource("birdnet_go", s.db)
    add_kit(s, GRAY, EE03_PANEL)
    add_kit(s, COLOUR, EE02_PANEL, shows="plates")    # the colour kit starts on the collage
    return s


def _hear(s, common, note=None):
    s.source.ingest(GO(common, SPECIES[common][0], note))
    # A tick's memo is keyed by its moment; this clock is frozen, so each tick
    # starts without the last one's tally, as a real one does.
    s._tick_memo = {}
    s.tick()


def test_a_species_drawn_for_every_frame_has_an_entry(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    t = svc.switch_table()
    card = t["species"]["cardinalis cardinalis"]
    assert card["common"] == "northern cardinal" and card["first_today"] is False
    assert set(card["frames"]) == {GRAY, COLOUR}
    # Exactly what the frames were served when the cardinal was shown.
    _hear(svc, "Northern Cardinal")
    assert {fid: svc._output_etag(fid) for fid in (GRAY, COLOUR)} == \
        {fid: f["etag"] for fid, f in card["frames"].items()}
    assert t["shown"] == "cyanocitta cristata" and t["shown_clean"] is True


def test_an_entry_says_what_each_frame_is_pointed_at(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    card = svc.switch_table()["species"]["cardinalis cardinalis"]
    assert card["etag"]
    for fid, f in card["frames"].items():
        assert f["file"].startswith("frames/drawn/out/") and f["file"].endswith(".fff")
        assert f["push"]["etag"] == f["etag"]
        assert set(f["push"]) == {"etag", "rotation", "power", "ota"}


def test_a_species_never_drawn_has_no_entry(svc):
    _hear(svc, "Northern Cardinal")
    assert "cyanocitta cristata" not in svc.switch_table()["species"]


def test_a_frame_missing_its_output_takes_the_entry_away(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    # A new frame, set up differently, so nothing is drawn for it.
    add_kit(svc, "CC:CC:CC:00:00:04", EE03_PANEL, panel_rotation=270)
    assert "cardinalis cardinalis" not in svc.switch_table()["species"]


def test_a_viewer_without_its_view_takes_the_entry_away(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    # The report a TRMNL makes, as tests/test_hosted.py's viewer tests build it.
    svc.apply_viewer_checkin(VIEWER, {"transport": "trmnl",
                                      "headers": {"Model": "og", "Width": "800", "Height": "480"}},
                             at=NOW.isoformat())
    svc.answer_frame(VIEWER, "add")
    assert "cardinalis cardinalis" not in svc.switch_table()["species"]


def test_a_viewer_with_its_view_drawn_is_in_the_entry(svc):
    _hear(svc, "Northern Cardinal")
    svc.apply_viewer_checkin(VIEWER, {"transport": "trmnl",
                                      "headers": {"Model": "og", "Width": "800", "Height": "480"}},
                             at=NOW.isoformat())
    svc.answer_frame(VIEWER, "add")
    svc.hosted_state()                  # draws the viewer's view of the cardinal ahead
    _hear(svc, "Blue Jay")
    svc.hosted_state()                  # and of the jay
    view = svc.switch_table()["species"]["cardinalis cardinalis"]["viewers"][VIEWER]
    assert view["file"] == f"frames/drawn/views/{view['name']}.png"


def test_a_frame_on_the_collage_is_in_no_entry(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    add_kit(svc, "CC:CC:CC:00:00:04", EE03_PANEL, shows="collage", panel_rotation=270)
    card = svc.switch_table()["species"]["cardinalis cardinalis"]
    assert set(card["frames"]) == {GRAY, COLOUR}


def test_first_today_is_what_novelty_says_of_a_fresh_detection(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    # Heard today, so a second detection would be a repeat.
    assert svc.switch_table()["species"]["cardinalis cardinalis"]["first_today"] is False
    # Tomorrow it has none: the next detection is its first of that day.
    svc._clock = lambda: NOW + timedelta(days=1)
    tomorrow = svc._clock()
    entry = svc.switch_table()["species"]["cardinalis cardinalis"]
    assert entry["first_today"] is True
    from featherframe.sources import Detection
    det = Detection(rowid=0, date=tomorrow.strftime("%Y-%m-%d"), time="08:00:00",
                    common_name="Northern Cardinal", scientific_name="Cardinalis cardinalis",
                    confidence=1.0)
    assert svc._novelty(det, tomorrow) == "first-today"


def test_first_today_is_what_a_tick_makes_of_the_next_detection(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    said = svc.switch_table()["species"]["cardinalis cardinalis"]["first_today"]
    _hear(svc, "Northern Cardinal")
    assert (said, svc.pictures["plates"].meta["novelty"]) == (False, "repeat")
    _hear(svc, "Blue Jay")
    # The day turns over: the cardinal has none today, and the next one is its first.
    tomorrow = NOW + timedelta(days=1)
    svc._clock = lambda: tomorrow
    said = svc.switch_table()["species"]["cardinalis cardinalis"]["first_today"]
    svc.source.ingest(GO("Northern Cardinal", SPECIES["Northern Cardinal"][0], at=tomorrow))
    svc.tick()
    assert (said, svc.pictures["plates"].meta["novelty"]) == (True, "first-today")


def test_nothing_in_quiet_hours(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    svc.in_quiet_hours = lambda now=None: True
    assert svc.switch_table() == {}


def test_nothing_from_a_source_that_is_not_a_push_feed(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    svc.source.kind = "birdweather"
    assert svc.switch_table() == {}


def test_nothing_while_no_frame_shows_detections(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    for fid in (GRAY, COLOUR):
        row = svc.frames.get(fid)
        row["set"] = {**row["set"], "shows": "collage"}
        svc.frames.save(row)
    assert svc.switch_table() == {}


def test_a_blocked_species_has_no_entry_and_is_listed(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    svc.config.species_blocklist = ["Northern Cardinal"]
    t = svc.switch_table()
    assert "cardinalis cardinalis" not in t["species"]
    assert "northern cardinal" in t["blocked"]


def test_the_floor_is_the_sources_own(svc):
    assert svc.switch_table()["floor"] == 0.0           # BirdNET-Go's threshold wins
    svc.source = PushedSource("apprise", svc.db)
    assert svc.switch_table()["floor"] == CONFIDENCE_FLOOR


def test_valid_until_is_the_sooner_of_midnight_and_quiet_hours(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    midnight = datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time())
    assert svc.switch_table()["valid_until"] == int(midnight.timestamp())
    svc.config.quiet_hours_mode = "custom"
    svc.config.quiet_hours_start, svc.config.quiet_hours_end = "20:00", "06:00"
    assert svc.switch_table()["valid_until"] == int(NOW.replace(hour=20, minute=0).timestamp())


def test_the_notes_taken_in_the_last_hour_ride_along(svc):
    _hear(svc, "Northern Cardinal", note="41")
    assert "41" in svc.switch_table()["notes"]


def test_a_note_older_than_the_hour_is_left_out(svc):
    _hear(svc, "Northern Cardinal", note="41")
    svc._clock = lambda: NOW + timedelta(hours=2)
    assert "41" not in svc.switch_table()["notes"]


def test_the_push_feed_names_each_species_heard_newest_first(svc):
    svc.source.ingest(GO("Northern Cardinal", SPECIES["Northern Cardinal"][0]))
    svc.source.ingest(GO("Blue Jay", SPECIES["Blue Jay"][0]))
    svc.source.ingest(GO("Northern Cardinal", SPECIES["Northern Cardinal"][0]))
    assert svc.source.heard() == [("Northern Cardinal", "Cardinalis cardinalis"),
                                  ("Blue Jay", "Cyanocitta cristata")]


def test_the_report_carries_it(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    assert "cardinalis cardinalis" in svc.hosted_state()["switches"]["species"]
