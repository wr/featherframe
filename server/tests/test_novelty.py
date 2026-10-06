"""W-692: novelty ordering and a date on the plate.

Among the detections one tick takes in, the most novel wins, then the newest,
so a first-ever bird is not lost to the cardinal that called after it; a plate
carries its date, so a three-day-old plate does not look like this morning's.
W-692's dwell (a new species held the glass for 90 minutes against repeats)
went with W-904: the next detection always takes the plate.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from PIL import Image, ImageDraw
from starlette.testclient import TestClient

from featherframe.config import Config
from featherframe.render import compose, pipeline, theme, typography
from featherframe.render.compose import SingleSpec
from featherframe.render.provider import ArtProvider, Artwork
from featherframe.pictures import COLLAGE, PLATES
from featherframe.service import FeatherframeService
from featherframe.sources.base import Detection

NOW = datetime(2026, 9, 2, 8, 20, 0)
TODAY = NOW.date().isoformat()
YESTERDAY = (NOW.date() - timedelta(days=1)).isoformat()

EAGLE = ("Bald Eagle", "Haliaeetus leucocephalus")
CARDINAL = ("Northern Cardinal", "Cardinalis cardinalis")
ROBIN = ("American Robin", "Turdus migratorius")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service._clock = lambda: NOW          # pin the wall clock to the fixtures' day
    pipeline.DITHER_OVERRIDE = "none"
    service._etag = "resident"
    service._set_cursor(0)
    service._cursor_verified = True
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


def _det(rowid, common, sci, conf, at: datetime):
    return Detection(rowid=rowid, date=at.strftime("%Y-%m-%d"), time=at.strftime("%H:%M:%S"),
                     common_name=common, scientific_name=sci, confidence=conf)


def _row(common, sci, count):
    return {"common": common, "scientific": sci, "count": count}


class _GateSource:
    """Same duck-typed double as test_corroborate: `rows` is every detection
    on record, `first_seen` maps a scientific name to its first-seen date
    (missing = unknown), `today` is the day's tally."""

    def __init__(self, rows, first_seen=None, today=None):
        self.rows = list(rows)
        self.first_seen = dict(first_seen or {})
        self.today = list(today or [])

    def available(self):
        return True

    def max_rowid(self):
        return max((d.rowid for d in self.rows), default=0)

    def new_since(self, cursor, min_confidence=0.0, limit=500):
        out = [d for d in self.rows if d.rowid > cursor and d.confidence >= min_confidence]
        return sorted(out, key=lambda d: d.rowid)[:limit]

    def latest_many(self, min_confidence=0.0, limit=25):
        out = [d for d in self.rows if d.confidence >= min_confidence]
        return sorted(out, key=lambda d: d.rowid, reverse=True)[:limit]

    def latest(self, min_confidence=0.0, scan=25):
        recent = self.latest_many(min_confidence, scan)
        return recent[0] if recent else None

    def top_species_today(self, on_date=None, min_confidence=0.0, limit=6):
        return list(self.today)[:limit]

    def all_time_species_count(self):
        return len({d.key for d in self.rows})

    def first_seen_date(self, sci):
        return self.first_seen.get(sci)



KNOWN = {CARDINAL[1]: YESTERDAY, ROBIN[1]: YESTERDAY}


def _capture_renders(svc, monkeypatch):
    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason, **kw: rendered.append(det.common_name))
    return rendered


def _showing(svc, novelty="first-ever", minutes_ago=10, common=EAGLE[0], sci=EAGLE[1],
             mode="single", at: datetime = NOW):
    """Make the resident frame a plate of `common`, rendered `minutes_ago`."""
    svc._meta = {"etag": "abc", "mode": mode, "label": common,
                 "species_key": sci.lower(), "novelty": novelty,
                 "rendered_at": (at - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")}


# -- novelty class ------------------------------------------------------------
def test_novelty_classes(svc):
    svc.source = _GateSource([], first_seen={**KNOWN, EAGLE[1]: TODAY},
                             today=[_row(*CARDINAL, 5), _row(*ROBIN, 1), _row(*EAGLE, 1)])
    assert svc._novelty(_det(1, *EAGLE, 0.9, NOW), NOW) == "first-ever"
    assert svc._novelty(_det(2, *ROBIN, 0.9, NOW), NOW) == "first-today"
    assert svc._novelty(_det(3, *CARDINAL, 0.9, NOW), NOW) == "repeat"
    # Unknown history (a push feed) is first-ever, as in W-691.
    assert svc._novelty(_det(4, "Golden Eagle", "Aquila chrysaetos", 0.9, NOW), NOW) == "first-ever"


def test_novelty_falls_back_to_the_render_log_without_a_tally(svc):
    # No tally: a known species is first-today until a plate of it is logged today.
    svc.source = _GateSource([], first_seen=KNOWN)
    assert svc._novelty(_det(1, *ROBIN, 0.9, NOW), NOW) == "first-today"
    svc.db.log_render(NOW.isoformat(timespec="seconds"), "single", ROBIN[0], "e" * 16)
    later = NOW + timedelta(minutes=1)
    assert svc._novelty(_det(2, *ROBIN, 0.9, later), later) == "repeat"
    assert svc._novelty(_det(3, *CARDINAL, 0.9, later), later) == "first-today"


# -- ordering -----------------------------------------------------------------
def test_first_ever_bird_beats_a_newer_cardinal(svc, monkeypatch):
    svc.source = _GateSource([_det(1, *EAGLE, 0.9, NOW - timedelta(minutes=15)),
                              _det(2, *CARDINAL, 0.95, NOW - timedelta(minutes=10))],
                             first_seen={**KNOWN, EAGLE[1]: TODAY},
                             today=[_row(*CARDINAL, 5), _row(*EAGLE, 1)])
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == ["Bald Eagle"]
    assert svc._cursor() == 2


def test_first_today_beats_a_newer_repeat_and_newest_wins_within_a_class(svc, monkeypatch):
    svc.source = _GateSource([_det(1, *ROBIN, 0.8, NOW - timedelta(minutes=12)),
                              _det(2, *CARDINAL, 0.95, NOW - timedelta(minutes=10)),
                              _det(3, *CARDINAL, 0.95, NOW - timedelta(minutes=8))],
                             first_seen=KNOWN, today=[_row(*CARDINAL, 5), _row(*ROBIN, 1)])
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == ["American Robin"]

    # Two repeats only: the newest, as before.
    svc.source = _GateSource([_det(4, *ROBIN, 0.8, NOW - timedelta(minutes=2)),
                              _det(5, *CARDINAL, 0.95, NOW - timedelta(minutes=1))],
                             first_seen=KNOWN, today=[_row(*CARDINAL, 6), _row(*ROBIN, 2)])
    svc._single_tick(NOW + timedelta(seconds=20))
    assert rendered == ["American Robin", "Northern Cardinal"]


def test_ordering_still_respects_the_corroboration_gate(svc, monkeypatch):
    # The first-ever bird is a lone 0.71: it waits, the cardinal renders.
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=15)),
                              _det(2, *CARDINAL, 0.95, NOW - timedelta(minutes=10))],
                             first_seen={**KNOWN, EAGLE[1]: TODAY},
                             today=[_row(*CARDINAL, 5), _row(*EAGLE, 1)])
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == ["Northern Cardinal"]
    assert svc.status()["pending"]["common"] == "Bald Eagle"


# -- no hold (W-904) -----------------------------------------------------------
def _cardinal_repeat(svc, rowid=1):
    svc.source = _GateSource([_det(rowid, *CARDINAL, 0.95, NOW - timedelta(minutes=1))],
                             first_seen={**KNOWN, EAGLE[1]: TODAY},
                             today=[_row(*CARDINAL, 5), _row(*EAGLE, 1)])


def _note_renders(svc, monkeypatch):
    """Capture (label, footnote) per render without drawing anything."""
    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason, **kw: rendered.append((det.common_name,
                                                                        svc._note_text())))
    return rendered


def test_a_repeat_takes_the_plate_from_a_new_species(svc, monkeypatch):
    """Until W-904 a first-ever bird held the glass for 90 minutes and the plate
    named each repeat it turned away along its foot ("Just now: …"). Now the
    cardinal heard a minute ago is the plate, and it carries no line."""
    _showing(svc, "first-ever", minutes_ago=10)
    _cardinal_repeat(svc)
    rendered = _note_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == [("Northern Cardinal", None)]
    assert svc._cursor() == 1


def test_status_reports_novelty_and_no_hold(client, svc):
    svc.source = _GateSource([], first_seen=KNOWN)
    assert 'name="dwell_minutes"' not in client.get("/").text   # a constant since W-821, gone since W-904
    _showing(svc, "first-ever", minutes_ago=10)
    cur = client.get("/api/status").json()["current"]
    assert cur["novelty"] == "first-ever" and "holding" not in cur


def test_a_plate_is_as_new_as_its_own_detection(svc):
    """A first-today robin calling again five minutes later is a repeat: the
    plate says what this detection is, not what the last one was."""
    src = _GateSource([], first_seen=KNOWN, today=[_row(*ROBIN, 1)])
    svc.source = src
    svc._render_single(_det(1, *ROBIN, 0.8, NOW), NOW, reason="test")
    assert svc._meta["novelty"] == "first-today" and "held_since" not in svc._meta
    src.today = [_row(*ROBIN, 2)]
    later = NOW + timedelta(minutes=5)
    svc._render_single(_det(2, *ROBIN, 0.8, later), later, reason="test")
    assert svc._meta["novelty"] == "repeat"


# -- the plate ------------------------------------------------------------------
class _BlankArt(ArtProvider):
    """A provider with art (so render_single takes the real-plate path), but
    blank art — every pixel of ink on the plate is then typography."""

    def artwork(self, common_name, scientific_name):
        return Artwork(image=Image.new("L", (600, 400), 255), plate=None)


def _ink(img: Image.Image, box) -> int:
    return sum(1 for px in img.crop(box).getdata() if px < 128)


def _spec(**kw):
    base = dict(common_name="Bald Eagle", scientific_name="Haliaeetus leucocephalus",
                when=datetime(2026, 9, 2, 8, 14))
    base.update(kw)
    return SingleSpec(**base)


def test_a_repeat_draws_the_same_sheet():
    """The corner names the artist, never the time (W-984): a later detection
    of the species shown is the same picture, so no frame repaints for it."""
    cfg = Config()
    a = pipeline.render_single(_spec(), _BlankArt(), cfg)
    b = pipeline.render_single(_spec(when=datetime(2026, 8, 30, 9, 31)), _BlankArt(), cfg)
    assert a.etag == b.etag


class _Art(ArtProvider):
    def __init__(self, **kw):
        self.kw = kw

    def artwork(self, common_name, scientific_name):
        return Artwork(image=Image.new("L", (600, 400), 255), **self.kw)


_LEFT = (theme.CORNER_INSET, theme.MARKS_BASELINE - 40, theme.CORNER_INSET + 420, theme.MARKS_BASELINE + 8)


def test_the_corner_names_the_artist_or_says_ai_generated():
    audubon = compose.render_single(_spec(), _Art(plate=159, artist="John James Audubon"))
    gould = compose.render_single(_spec(), _Art(plate=18, volume_no=2, artist="John Gould"))
    ai = compose.render_single(_spec(), _Art(generated=True))
    for img in (audubon, gould, ai):
        assert _ink(img, _LEFT) > 200
    assert len({img.crop(_LEFT).tobytes() for img in (audubon, gould, ai)}) == 3
    # Art with no one to name, and the fallback bough, carry no left mark.
    assert _ink(compose.render_single(_spec(), _BlankArt()), _LEFT) == 0
    assert _ink(compose.render_fallback(_spec()), _LEFT) == 0


def test_a_long_artist_name_is_set_smaller_not_into_the_footnote():
    scratch = Image.new("L", (theme.WIDTH, theme.HEIGHT), 255)
    w = typography.artist_mark(scratch, "John Gould and Elizabeth Gould")
    assert w <= typography.artist_mark_max_width() + 12          # + the swash's overhang


def test_first_ever_plate_says_so_under_the_latin_name():
    known = compose.render_single(_spec(), _BlankArt())
    first = compose.render_single(_spec(first_ever=True), _BlankArt())
    assert known.tobytes() != first.tobytes()

    # The extra line takes one legend pitch under the Latin name; the known
    # plate has nothing there (between the corner marks).
    mid_l, mid_r = theme.CORNER_INSET + 400, theme.WIDTH - theme.CORNER_INSET - 400
    top = theme.HEIGHT - compose.caption_height(0, first_ever=True)
    latin = top + round(theme.SCRIPT_TITLE_SIZE * theme.SCRIPT_TITLE_ASCENT) + theme.TITLE_TO_LATIN
    line = latin + theme.LATIN_TO_LEGEND
    band = (mid_l, line - theme.LEGEND_SIZE, mid_r, line + 8)
    assert _ink(first, band) > 100

    # With the gone-quiet footnote too, the note sits on the marks' baseline
    # below the new line.
    noted = compose.render_single(_spec(first_ever=True, note="Nothing heard since 11:27 pm"),
                                  _BlankArt())
    assert _ink(noted, (mid_l, theme.MARKS_BASELINE - 22, mid_r, theme.MARKS_BASELINE + 6)) > 100

    cfg = Config()
    assert (pipeline.render_single(_spec(), _BlankArt(), cfg).etag
            != pipeline.render_single(_spec(first_ever=True), _BlankArt(), cfg).etag)


def test_render_single_sets_first_ever_from_the_novelty_class(svc, monkeypatch):
    seen = []
    real = compose.render_for

    def spy(spec, art, color=False):
        seen.append(spec)
        return real(spec, art, color)
    monkeypatch.setattr(compose, "render_for", spy)
    svc.source = _GateSource([], first_seen={**KNOWN, EAGLE[1]: TODAY}, today=[_row(*ROBIN, 1)])
    svc._render_single(_det(1, *EAGLE, 0.9, NOW), NOW, reason="test")
    svc._render_single(_det(2, *ROBIN, 0.9, NOW), NOW, reason="test")
    assert [s.first_ever for s in seen] == [True, False]


# -- "new" means never heard here before, and it wears off ---------------------
def test_a_source_with_no_history_is_answered_by_what_this_server_has_heard(svc):
    """A push feed can't say when a species was first heard. The server can:
    a species it took in yesterday is not first-ever today; one it has never
    taken in still is."""
    svc.source = _GateSource([], today=[_row(*ROBIN, 3)])
    assert svc._novelty(_det(1, *ROBIN, 0.9, NOW), NOW) == "first-ever"
    svc._note_heard([_det(1, *ROBIN, 0.9, NOW - timedelta(days=1))])
    later = NOW + timedelta(minutes=1)
    assert svc._novelty(_det(2, *ROBIN, 0.9, later), later) == "repeat"
    assert svc._novelty(_det(3, *EAGLE, 0.9, later), later) == "first-ever"
    # Kept in the DB: a restart remembers.
    fresh = FeatherframeService()
    assert fresh._heard()[ROBIN[1].lower()] == YESTERDAY


def test_a_source_that_knows_better_is_asked(svc):
    """BirdWeather has no first dates but knows whether a species was heard
    before today (heard_before); its answer wins over "can't say"."""
    class _Knows(_GateSource):
        def heard_before(self, sci, on_date):
            return {ROBIN[1]: True, EAGLE[1]: False}.get(sci)
    svc.source = _Knows([], today=[_row(*ROBIN, 1), _row(*EAGLE, 1)])
    assert svc._novelty(_det(1, *ROBIN, 0.9, NOW), NOW) == "first-today"
    assert svc._novelty(_det(2, *EAGLE, 0.9, NOW), NOW) == "first-ever"


def test_a_birds_newness_wears_off(svc):
    """The same first-ever bird heard again the next day is a repeat: it does
    not carry "new" for as long as it keeps calling."""
    src = _GateSource([], first_seen={EAGLE[1]: TODAY}, today=[_row(*EAGLE, 1)])
    svc.source = src
    svc._render_single(_det(1, *EAGLE, 0.9, NOW), NOW, reason="test")
    assert svc._meta["novelty"] == "first-ever"
    # The next day it is known.
    tomorrow = NOW + timedelta(days=1)
    src.first_seen = {EAGLE[1]: TODAY}
    src.today = [_row(*EAGLE, 4)]
    svc._render_single(_det(2, *EAGLE, 0.9, tomorrow), tomorrow, reason="test")
    assert svc._meta["novelty"] == "repeat"


# -- a repeat changes nothing (W-984) -------------------------------------------
class _ArtFor(ArtProvider):
    """Art for every species, with an artist, so the sheet is a real plate."""

    def artwork(self, common_name, scientific_name):
        return Artwork(image=Image.new("L", (600, 400), 255), plate=131,
                       artist="John James Audubon")


def test_a_repeat_detection_is_not_drawn_again(svc):
    svc.provider = _ArtFor()
    svc.source = _GateSource([], first_seen=dict(KNOWN), today=[_row(*ROBIN, 2)])
    svc._render_single(_det(1, *ROBIN, 0.9, NOW), NOW, reason="detection")
    etag, renders = svc.pictures[PLATES].etag, len(svc.db.render_history(50))
    later = NOW + timedelta(minutes=53)
    svc._render_single(_det(2, *ROBIN, 0.9, later), later, reason="detection")
    assert svc.pictures[PLATES].etag == etag
    assert len(svc.db.render_history(50)) == renders           # no history row for it
    # The owner's Refresh still draws it; another species is drawn.
    svc._render_single(_det(2, *ROBIN, 0.9, later), later, reason="refresh")
    assert len(svc.db.render_history(50)) == renders + 1
    svc._render_single(_det(3, *EAGLE, 0.9, later), later, reason="detection")
    assert svc.pictures[PLATES].meta["label"] == EAGLE[0]


def test_a_repeat_of_a_sheet_without_art_is_drawn_while_art_could_come(svc):
    """The bough shown for want of an illustration is drawn again on a repeat
    while one would be bought: this time it may arrive."""
    svc.source = _GateSource([], first_seen=dict(KNOWN), today=[_row(*ROBIN, 2)])
    svc._render_single(_det(1, *ROBIN, 0.9, NOW), NOW, reason="detection")
    renders = len(svc.db.render_history(50))
    svc._art_could_arrive = lambda: True
    svc._render_single(_det(2, *ROBIN, 0.9, NOW), NOW, reason="detection")
    assert len(svc.db.render_history(50)) == renders + 1
    svc._art_could_arrive = lambda: False
    svc._render_single(_det(3, *ROBIN, 0.9, NOW), NOW, reason="detection")
    assert len(svc.db.render_history(50)) == renders + 1


def test_the_front_door_is_told_which_detections_change_nothing(svc):
    from tests._frames import add_kit
    svc.provider = _ArtFor()
    svc.config.species_blocklist = ["House Sparrow"]
    svc.source = _GateSource([], first_seen=dict(KNOWN), today=[_row(*ROBIN, 1)])
    add_kit(svc, shows=PLATES)
    assert svc.news_unchanged() == ["house sparrow"]               # nothing shown yet
    svc._render_single(_det(1, *ROBIN, 0.9, NOW), NOW, reason="detection")
    assert svc.news_unchanged() == sorted(["house sparrow", ROBIN[0].lower(), ROBIN[1].lower()])
    assert svc.hosted_state()["unchanged"] == svc.news_unchanged()
    add_kit(svc, shows=COLLAGE)                                    # the same kit, now on the collage
    assert svc.news_unchanged() == "*"
