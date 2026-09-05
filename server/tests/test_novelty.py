"""W-692: novelty ordering, dwell for new birds, and a date on the plate.

A first-ever bird used to lose the glass to the next cardinal within minutes,
and a plate carried only a clock time, so a three-day-old plate looked like
this morning's. Repeats still re-render (the owner has a partial-refresh
panel and wants the clock to move); this is about ORDER and STALENESS.
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
    service.config.dither = "none"
    service._frame_bytes = b"resident"
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

    def species_ordinal(self, sci):
        return None


KNOWN = {CARDINAL[1]: YESTERDAY, ROBIN[1]: YESTERDAY}


def _capture_renders(svc, monkeypatch):
    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason: rendered.append(det.common_name))
    return rendered


def _hold(svc, novelty="first-ever", minutes_ago=10, common=EAGLE[0], sci=EAGLE[1],
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


# -- dwell ----------------------------------------------------------------------
def _cardinal_repeat(svc, rowid=1):
    svc.source = _GateSource([_det(rowid, *CARDINAL, 0.95, NOW - timedelta(minutes=1))],
                             first_seen={**KNOWN, EAGLE[1]: TODAY},
                             today=[_row(*CARDINAL, 5), _row(*EAGLE, 1)])


def test_held_first_ever_bird_turns_away_a_repeat(svc, monkeypatch):
    _hold(svc, "first-ever", minutes_ago=10)
    _cardinal_repeat(svc)
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == []
    assert svc._cursor() == 1          # the cursor still advances: the repeat is simply not shown


def test_held_bird_yields_to_a_first_today_species(svc, monkeypatch):
    _hold(svc, "first-ever", minutes_ago=10)
    svc.source = _GateSource([_det(1, *ROBIN, 0.8, NOW - timedelta(minutes=1))],
                             first_seen={**KNOWN, EAGLE[1]: TODAY},
                             today=[_row(*ROBIN, 1), _row(*EAGLE, 1)])
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == ["American Robin"]


def test_held_bird_re_renders_on_its_own_repeat(svc, monkeypatch):
    _hold(svc, "first-ever", minutes_ago=10)
    svc.source = _GateSource([_det(1, *EAGLE, 0.9, NOW - timedelta(minutes=1))],
                             first_seen={**KNOWN, EAGLE[1]: TODAY},
                             today=[_row(*EAGLE, 2)])
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == ["Bald Eagle"]


def test_hold_expires_with_dwell(svc, monkeypatch):
    _hold(svc, "first-ever", minutes_ago=91)
    _cardinal_repeat(svc)
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == ["Northern Cardinal"]


def test_dwell_zero_turns_the_hold_off(svc, monkeypatch):
    svc.config.dwell_minutes = 0
    _hold(svc, "first-ever", minutes_ago=1)
    _cardinal_repeat(svc)
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == ["Northern Cardinal"]


def test_first_today_holds_too_but_a_repeat_or_collage_does_not(svc, monkeypatch):
    rendered = _capture_renders(svc, monkeypatch)
    _hold(svc, "first-today", minutes_ago=10, common=ROBIN[0], sci=ROBIN[1])
    _cardinal_repeat(svc, rowid=1)
    svc._single_tick(NOW)
    assert rendered == []

    _hold(svc, "repeat", minutes_ago=10, common=ROBIN[0], sci=ROBIN[1])
    _cardinal_repeat(svc, rowid=2)
    svc._single_tick(NOW + timedelta(seconds=20))
    assert rendered == ["Northern Cardinal"]

    _hold(svc, "first-ever", minutes_ago=10, common="day in review (3 species)",
          sci="", mode="collage")
    _cardinal_repeat(svc, rowid=3)
    svc._single_tick(NOW + timedelta(seconds=40))
    assert rendered == ["Northern Cardinal", "Northern Cardinal"]


def test_commit_carries_the_hold_across_the_held_birds_own_repeats(svc):
    # A first-today robin at 8:00 calling again at 8:05 is classed a repeat,
    # but keeps the hold it earned (and its label); the clock does not restart.
    src = _GateSource([], first_seen=KNOWN, today=[_row(*ROBIN, 1)])
    svc.source = src
    svc._render_single(_det(1, *ROBIN, 0.8, NOW), NOW, reason="test")
    assert svc._meta["novelty"] == "first-today"
    assert svc._meta["held_since"] == NOW.isoformat(timespec="seconds")

    src.today = [_row(*ROBIN, 2)]
    later = NOW + timedelta(minutes=5)
    svc._render_single(_det(2, *ROBIN, 0.8, later), later, reason="test")
    assert svc._meta["novelty"] == "first-today"
    assert svc._meta["held_since"] == NOW.isoformat(timespec="seconds")
    assert svc._meta["rendered_at"] == later.isoformat(timespec="seconds")

    # Another species takes the frame: a repeat carries no hold at all.
    # (A fresh `now`: the tally is memoised per tick.)
    src.today = [_row(*ROBIN, 2), _row(*CARDINAL, 4)]
    later = later + timedelta(minutes=1)
    svc._render_single(_det(3, *CARDINAL, 0.9, later), later, reason="test")
    assert svc._meta["novelty"] == "repeat"
    assert svc._meta["held_since"] is None
    assert svc._holding(svc._meta, later) is None


# -- status() -----------------------------------------------------------------
def test_status_reports_novelty_and_the_hold(svc):
    svc.source = _GateSource([], first_seen=KNOWN)
    real_now = NOW
    _hold(svc, "first-ever", minutes_ago=10, at=real_now)
    cur = svc.status()["current"]
    assert cur["novelty"] == "first-ever"
    assert cur["holding"]["minutes_left"] == 80
    assert cur["holding"]["reason"] == "new species"
    assert cur["holding"]["until"] == (real_now - timedelta(minutes=10) + timedelta(minutes=90)
                                       ).isoformat(timespec="seconds")

    # The pure computation, pinned: held_since wins over rendered_at.
    meta = dict(svc._meta, held_since=(NOW - timedelta(minutes=30)).isoformat(timespec="seconds"))
    assert svc._holding(meta, NOW)["minutes_left"] == 60
    assert svc._holding(meta, NOW + timedelta(minutes=60)) is None

    _hold(svc, "repeat", minutes_ago=1, at=real_now)
    assert svc.status()["current"]["holding"] is None
    _hold(svc, "first-ever", minutes_ago=1, mode="collage", at=real_now)
    assert svc.status()["current"]["holding"] is None
    _hold(svc, None, minutes_ago=1, at=real_now)
    assert svc.status()["current"]["holding"] is None


# -- bypasses -----------------------------------------------------------------
def test_manual_refresh_and_test_detection_bypass_the_hold(svc, monkeypatch):
    # refresh_now re-reads the saved config and the wall clock: keep it out
    # of quiet hours whenever this runs.
    svc.config.quiet_hours_mode = "off"
    svc.update_config(svc.config)
    _hold(svc, "first-ever", minutes_ago=10, at=NOW)
    _cardinal_repeat(svc)
    rendered = _capture_renders(svc, monkeypatch)
    svc.refresh_now()
    assert rendered == ["Northern Cardinal"]

    monkeypatch.undo()
    _hold(svc, "first-ever", minutes_ago=10, at=NOW)
    svc.force_test_detection(*CARDINAL)
    assert svc._meta["label"] == "Northern Cardinal (test)"
    assert svc._meta["novelty"] is None          # a test bird never holds the frame
    assert svc.status()["current"]["holding"] is None


# -- the plate ------------------------------------------------------------------
class _BlankArt(ArtProvider):
    """A provider with art (so render_single takes the real-plate path), but
    blank art — every pixel of ink on the plate is then typography."""

    def artwork(self, common_name, scientific_name):
        return Artwork(image=Image.new("L", (600, 400), 255), audubon_plate=None)


def _ink(img: Image.Image, box) -> int:
    return sum(1 for px in img.crop(box).getdata() if px < 128)


def _spec(**kw):
    base = dict(common_name="Bald Eagle", scientific_name="Haliaeetus leucocephalus",
                when=datetime(2026, 9, 2, 8, 14), plate_number=7)
    base.update(kw)
    return SingleSpec(**base)


def test_corner_mark_carries_the_date():
    cfg = Config(dither="none")
    a = pipeline.render_single(_spec(), _BlankArt(), cfg)
    b = pipeline.render_single(_spec(when=datetime(2026, 8, 30, 8, 14)), _BlankArt(), cfg)
    assert a.etag != b.etag                       # same time of day, different date: different plate
    # The composition itself (before the mat inset moves everything): the
    # mark region (bottom-left corner) has ink on both, and the two marks
    # differ inside it.
    ca = compose.render_single(_spec(), _BlankArt())
    cb = compose.render_single(_spec(when=datetime(2026, 8, 30, 8, 14)), _BlankArt())
    mark = (theme.CORNER_INSET, theme.MARKS_BASELINE - 40, theme.CORNER_INSET + 400, theme.MARKS_BASELINE + 6)
    assert _ink(ca, mark) > 200 and _ink(cb, mark) > 200
    assert ca.crop(mark).tobytes() != cb.crop(mark).tobytes()


def test_corner_mark_text_is_day_month_and_time():
    assert typography._corner_parts(datetime(2026, 9, 1, 8, 14)) == ("1 Sep", "8:14 am")
    assert typography._corner_parts(datetime(2026, 12, 25, 23, 5)) == ("25 Dec", "11:05 pm")


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

    cfg = Config(dither="none")
    assert (pipeline.render_single(_spec(), _BlankArt(), cfg).etag
            != pipeline.render_single(_spec(first_ever=True), _BlankArt(), cfg).etag)


def test_render_single_sets_first_ever_from_the_novelty_class(svc, monkeypatch):
    seen = []
    real = compose.render_single

    def spy(spec, provider, show_plate_number=True):
        seen.append(spec)
        return real(spec, provider, show_plate_number)
    monkeypatch.setattr(compose, "render_single", spy)
    svc.source = _GateSource([], first_seen={**KNOWN, EAGLE[1]: TODAY}, today=[_row(*ROBIN, 1)])
    svc._render_single(_det(1, *EAGLE, 0.9, NOW), NOW, reason="test")
    svc._render_single(_det(2, *ROBIN, 0.9, NOW), NOW, reason="test")
    assert [s.first_ever for s in seen] == [True, False]


# -- config + page --------------------------------------------------------------
def test_sanitize_clamps_dwell():
    assert Config(dwell_minutes=-5).dwell_minutes == 0
    assert Config(dwell_minutes=9999).dwell_minutes == 720
    assert Config(dwell_minutes="nan").dwell_minutes == 90
    assert Config().to_dict()["dwell_minutes"] == 90


def test_settings_form_round_trips_dwell(client, svc):
    r = client.post("/settings", data={"dwell_minutes": "45"}, follow_redirects=False)
    assert r.status_code == 303 and "adjusted" not in r.headers["location"]
    assert svc.config.dwell_minutes == 45
    r = client.post("/settings", data={"dwell_minutes": "5000"}, follow_redirects=False)
    assert svc.config.dwell_minutes == 720
    assert "dwell_minutes" in r.headers["location"].split("adjusted=")[1].split(",")
    r = client.post("/settings", data={"dwell_minutes": "0"}, follow_redirects=False)
    assert svc.config.dwell_minutes == 0


def test_page_shows_the_field_and_the_holding_text(client, svc):
    svc.source = _GateSource([], first_seen=KNOWN)
    html = client.get("/").text
    assert 'name="dwell_minutes"' in html
    assert 'id="fc-holding"></span>' in html          # nothing held

    _hold(svc, "first-ever", minutes_ago=50, at=NOW)
    html = client.get("/").text
    assert '<span id="fc-showing">Bald Eagle</span>' in html
    assert '<span class="when" id="fc-holding">holding 40 min</span>' in html
    assert client.get("/api/status").json()["current"]["holding"]["minutes_left"] == 40
