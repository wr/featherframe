"""What the server tells its front door it may switch to alone (W-1025)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import pytest

from featherframe import drawn
from featherframe import service as service_mod
from featherframe.render import pipeline
from featherframe.service import CONFIDENCE_FLOOR, _drawn_from
from featherframe.sources import Detection
from featherframe.sources.pushed import PushedSource
from tests._frames import EE02_PANEL, EE03_PANEL, add_kit
from tests.test_drawn import COLOUR, GRAY, NOW, SPECIES, _RefArt

VIEWER = "AA:BB:CC:00:00:01"
CARDINAL = "cardinalis cardinalis"


def GO(common, sci, note=None, at=NOW, days=30):
    return {"type": "detection", "metadata": {
        "species": common, "scientific_name": sci, "confidence": 0.9, "note_id": note,
        "days_since_first_seen": days,
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


def _hear(s, common, note=None, days=30):
    s.source.ingest(GO(common, SPECIES[common][0], note, days=days))
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


def test_nothing_while_detections_wait_that_no_tick_has_decided_over(svc):
    """A page's start takes the queue and stops with no tick (W-1025). The next
    marker is replayed over those detections too, so a table the front door
    chooses against would be a choice over less than the server will."""
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    assert CARDINAL in svc.switch_table()["species"]
    svc.source.ingest(GO("Blue Jay", SPECIES["Blue Jay"][0]))
    assert svc.switch_table() == {}
    assert svc.hosted_state()["switches"] == {}
    svc._tick_memo = {}
    svc.tick()                                          # decided over: a table again
    assert CARDINAL in svc.switch_table()["species"]


def test_nothing_before_a_tick_has_set_the_cursor(svc):
    assert svc._cursor() is None
    assert svc.switch_table() == {}
    svc.tick()
    assert svc._cursor() is not None
    assert "floor" in svc.switch_table()


def test_a_detection_below_the_floor_is_not_waiting(svc):
    """The tick never looks at it, so it never waits to be decided over."""
    svc.source = PushedSource("apprise", svc.db)
    svc.tick()
    svc.source.ingest({"title": "x", "message": json.dumps({
        "comname": "Blue Jay", "sciname": "Cyanocitta cristata", "confidence": CONFIDENCE_FLOOR / 2,
        "date": NOW.strftime("%Y-%m-%d"), "time": NOW.strftime("%H:%M:%S")})})
    assert svc.source.new_since(svc._cursor(), 0.0)         # the source holds it
    assert "floor" in svc.switch_table()


def test_a_blocked_species_has_no_entry_and_is_listed(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    svc.config.species_blocklist = ["Northern Cardinal"]
    t = svc.switch_table()
    assert "cardinalis cardinalis" not in t["species"]
    assert "northern cardinal" in t["blocked"]


def test_the_floor_is_the_sources_own(svc):
    _hear(svc, "Northern Cardinal")                     # a tick has set the cursor
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


# -- what a real switch would have to compose or buy (W-1025) ------------------


def _file_under(svc, common, ref, etag):
    """Keep `etag` under the key a detection of `common` would look up with
    this art ref: a sheet kept where a real tick would find it."""
    svc._tick_memo = {}
    spec = svc._fresh_spec(common, SPECIES[common][0], NOW, "first-today", None, None,
                           svc._fallback_note())
    key = drawn.sheet_key(_drawn_from(spec), ref)
    svc._drawn_note(lambda st: drawn.remember_sheet(st, key, etag, ()))
    assert svc._kept_sheet(key) == (etag, ())


def test_a_species_new_today_has_no_entry_whatever_is_kept(svc):
    _hear(svc, "Blue Jay")
    _hear(svc, "Northern Cardinal", days=0)             # first heard today
    assert svc.pictures["plates"].meta["novelty"] == "first-ever"
    # File its sheet where a repeat would look, so only its being new keeps it out.
    _file_under(svc, "Northern Cardinal", svc.provider.ref("Northern Cardinal", ""),
                svc.pictures["plates"].etag)
    _hear(svc, "Blue Jay")
    assert CARDINAL not in svc.switch_table()["species"]
    # The control: were it not new, the kept sheet would be an entry.
    svc._is_new_species = lambda *a: False
    assert CARDINAL in svc.switch_table()["species"]


def test_a_sheet_whose_colour_twin_is_missing_has_no_entry(svc):
    svc.provider.colour_fails = True                    # the colour kit is drawn from the gray sheet
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    etag = svc._output_etag(COLOUR)
    assert etag and not drawn.whole(drawn.twin_path(svc.picture_for("plates").etag))
    # Its output is kept, so only the twin a real switch would compose holds it back.
    svc.provider.colour_fails = False
    assert CARDINAL not in svc.switch_table()["species"]


def test_a_colour_viewer_that_asked_lately_holds_back_a_sheet_with_no_twin(svc):
    svc.frames.forget(COLOUR)                           # only a gray kit shows plates
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    assert CARDINAL in svc.switch_table()["species"]
    # A colour viewer asked, then went away: the next commit composes a twin
    # for it (`_color_wanted`), which a switch cannot do without this server.
    svc.db.set("color_viewer_at", NOW.isoformat(timespec="seconds"))
    svc._color_asked_at = None
    assert not drawn.whole(drawn.twin_path(svc.pictures["plates"].etag))
    assert CARDINAL not in svc.switch_table()["species"]


@pytest.mark.parametrize("ref", [None, ""])
def test_art_that_would_be_bought_or_composed_has_no_entry(svc, ref):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    etag = svc.switch_table()["species"][CARDINAL]["etag"]
    # None: the providers cannot say without drawing; "": no art, and an
    # illustration could arrive. Filed where a real tick would find it.
    svc.provider.ref = lambda common, scientific: ref
    svc._art_could_arrive = lambda: True
    _file_under(svc, "Northern Cardinal", ref, etag)
    assert CARDINAL not in svc.switch_table()["species"]
    # The control: with art that is there, the same kept sheet is an entry.
    svc.provider.ref = lambda common, scientific: ref or "stub:filed"
    _file_under(svc, "Northern Cardinal", ref or "stub:filed", etag)
    assert CARDINAL in svc.switch_table()["species"]


def test_the_shown_sheet_is_not_clean_when_it_carries_a_footnote(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    assert svc.switch_table()["shown_clean"] is True
    # The cardinal again, drawn under a gone-quiet note: another sheet than the
    # one a fresh detection of it draws.
    svc._quiet = {"since": (NOW - timedelta(hours=7)).isoformat(timespec="seconds")}
    svc._tick_memo = {}
    svc._render_single(Detection(1, "2026-10-06", "08:00:00", "Northern Cardinal",
                                 SPECIES["Northern Cardinal"][0], 0.9), NOW, reason="detection")
    svc._tick_frames()
    svc._quiet = None
    t = svc.switch_table()
    assert t["shown"] == CARDINAL and CARDINAL in t["species"]
    assert t["species"][CARDINAL]["etag"] != svc.pictures["plates"].etag
    assert t["shown_clean"] is False


def test_the_shown_sheet_is_not_clean_while_it_waits_for_colour(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    svc._recolor.add("plates")
    assert svc.switch_table()["shown_clean"] is False


def test_the_shown_sheet_is_not_clean_without_art_that_could_arrive(svc):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    svc.pictures["plates"].meta["art"] = False
    assert svc.switch_table()["shown_clean"] is True     # nothing could arrive
    svc._art_could_arrive = lambda: True
    assert svc.switch_table()["shown_clean"] is False


# -- a report never fails or hangs on the table (W-1025) -------------------------
def test_a_table_that_fails_to_build_is_none_and_a_warning(svc, caplog):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    memo = svc._tick_memo

    def boom(now, src):
        raise RuntimeError("no table today")
    svc._switch_table = boom
    with caplog.at_level(logging.WARNING, logger="featherframe.service"):
        assert svc.switch_table() == {}
        assert svc.hosted_state()["switches"] == {}
    assert any("switch table" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    # Neither the lock nor the memo is left as the failure found them.
    assert svc._tick_lock.acquire(blocking=False)
    svc._tick_lock.release()
    assert svc._tick_memo is memo


def test_a_tick_that_outlasts_the_wait_leaves_the_table_empty(svc, monkeypatch, caplog):
    _hear(svc, "Northern Cardinal")
    _hear(svc, "Blue Jay")
    monkeypatch.setattr(service_mod, "SWITCH_LOCK_WAIT_S", 0.05)
    svc._tick_lock.acquire()                            # a tick that has not finished
    try:
        with caplog.at_level(logging.WARNING, logger="featherframe.service"):
            assert svc.switch_table() == {}
        assert any("a tick still runs" in r.getMessage() for r in caplog.records)
    finally:
        svc._tick_lock.release()
    assert "cardinalis cardinalis" in svc.switch_table()["species"]
