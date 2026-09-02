"""W-691: a first-ever species must earn the wall. One 0.71 hit of a rare bird
is routinely a car horn; unchecked it becomes the frame at once and, for a
plate-less species, buys a generated plate of a bird that was never there.
The gate lives in candidate selection: an uncorroborated new species is never
handed to the render (which is where the purchase would happen)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from PIL import Image
from starlette.testclient import TestClient

from featherframe.config import Config
from featherframe.render import theme
from featherframe.service import FeatherframeService
from featherframe.sources.base import Detection

NOW = datetime(2026, 9, 2, 8, 0, 0)
TODAY = NOW.date().isoformat()
YESTERDAY = (NOW.date() - timedelta(days=1)).isoformat()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
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


class _GateSource:
    """A duck-typed source with controllable history: `rows` is every
    detection on record (any order); `first_seen` maps a scientific name to
    its first-seen date (missing = unknown); `today` is top_species_today."""

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


def _capture_renders(svc, monkeypatch):
    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason: rendered.append((det.common_name, det.confidence)))
    return rendered


EAGLE = ("Bald Eagle", "Haliaeetus leucocephalus")


# -- single mode --------------------------------------------------------------
def test_single_low_confidence_new_species_waits(svc, monkeypatch):
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: TODAY})
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == []
    assert svc._cursor() == 1                       # the cursor still advances
    pending = svc.status()["pending"]
    assert pending["common"] == "Bald Eagle"
    assert pending["hits"] == 1
    assert pending["confidence"] == 0.71
    assert pending["waiting_text"] == "1 hit at 0.71 · waiting for a second"
    assert pending["first_at"] == (NOW - timedelta(minutes=1)).isoformat(timespec="seconds")


def test_single_confident_new_species_renders_alone(svc, monkeypatch):
    svc.source = _GateSource([_det(1, *EAGLE, 0.90, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: TODAY})
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == [("Bald Eagle", 0.90)]
    assert svc.status()["pending"] is None


@pytest.mark.parametrize("gap, expect", [
    (timedelta(minutes=15), True),    # two calls a quarter hour apart: a bird
    (timedelta(minutes=3), False),    # two triggers seconds-to-minutes apart: one noise
    (timedelta(hours=30), False),     # the first hit is outside the 24 h window
])
def test_single_second_hit_corroborates_only_with_a_real_gap(svc, monkeypatch, gap, expect):
    second_at = NOW - timedelta(minutes=1)
    svc.source = _GateSource([_det(1, *EAGLE, 0.72, second_at - gap),
                              _det(2, *EAGLE, 0.71, second_at)],
                             first_seen={EAGLE[1]: TODAY})
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert bool(rendered) is expect
    assert (svc.status()["pending"] is None) is expect


def test_single_known_species_renders_as_before(svc, monkeypatch):
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: YESTERDAY})
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == [("Bald Eagle", 0.71)]
    assert svc.status()["pending"] is None


def test_single_toggle_off_renders_as_before(svc, monkeypatch):
    svc.config.corroborate_new_species = False
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: TODAY})
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == [("Bald Eagle", 0.71)]


def test_unknown_history_counts_as_new(svc, monkeypatch):
    # A push feed can't say when a species was first heard: treat it as new.
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))])
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == []
    assert svc.status()["pending"]["common"] == "Bald Eagle"


def test_second_detection_later_passes_and_clears_pending(svc, monkeypatch):
    # Tick 1: one hit, held. Tick 2: a NEW row 15 min later, corroborated by
    # the first via latest_many, renders — and the page stops waiting.
    src = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                      first_seen={EAGLE[1]: TODAY})
    svc.source = src
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == [] and svc.status()["pending"] is not None

    later = NOW + timedelta(minutes=15)
    src.rows.append(_det(2, *EAGLE, 0.73, later - timedelta(seconds=30)))
    svc._single_tick(later)
    assert rendered == [("Bald Eagle", 0.73)]
    # (The clear itself lives in the real _render_single, stubbed out here —
    # see test_pending_clears_once_the_species_is_rendered.)


def test_pending_clears_once_the_species_is_rendered(svc):
    # The real _render_single (typographic fallback, no plates dir) clears it.
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: TODAY})
    svc._single_tick(NOW)
    assert svc.status()["pending"]["common"] == "Bald Eagle"
    assert svc.db.get("pending_species")["common"] == "Bald Eagle"   # survives a restart
    svc._render_single(_det(2, *EAGLE, 0.91, NOW), NOW, reason="test")
    assert svc.status()["pending"] is None
    assert svc.db.get("pending_species") is None


def test_pending_expires_with_the_window(svc, monkeypatch):
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: TODAY})
    _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert svc.status()["pending"] is not None
    assert svc.pending_view(NOW + timedelta(hours=25)) is None
    svc._single_tick(NOW + timedelta(hours=25))     # the tick forgets it for good
    assert svc._pending is None


def test_older_corroborated_bird_renders_while_the_new_one_waits(svc, monkeypatch):
    # Newest row is an uncorroborated new species; the row before it is a
    # known bird. The known bird goes up; the new one is recorded as pending.
    svc.source = _GateSource([_det(1, "Blue Jay", "Cyanocitta cristata", 0.8, NOW - timedelta(minutes=2)),
                              _det(2, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: TODAY, "Cyanocitta cristata": YESTERDAY})
    rendered = _capture_renders(svc, monkeypatch)
    svc._single_tick(NOW)
    assert rendered == [("Blue Jay", 0.8)]
    assert svc.status()["pending"]["common"] == "Bald Eagle"


# -- collage ------------------------------------------------------------------
def _collage_cells(svc, monkeypatch):
    seen = []

    def fake_render_collage(cells, provider, **kw):
        seen.append([c.common_name for c in cells])
        return Image.new("L", (theme.WIDTH, theme.HEIGHT), 255)
    from featherframe.service import collage_mod
    monkeypatch.setattr(collage_mod, "render_collage", fake_render_collage)
    return seen


def test_collage_drops_a_single_low_confidence_new_species(svc, monkeypatch):
    today = NOW.date()
    rows = [{"common": "Northern Cardinal", "scientific": "Cardinalis cardinalis", "count": 5},
            {"common": "Blue Jay", "scientific": "Cyanocitta cristata", "count": 3},
            {"common": "Bald Eagle", "scientific": EAGLE[1], "count": 1},
            {"common": "Golden Eagle", "scientific": "Aquila chrysaetos", "count": 1}]
    dets = [_det(1, "Northern Cardinal", "Cardinalis cardinalis", 0.9, NOW - timedelta(hours=2)),
            _det(2, "Blue Jay", "Cyanocitta cristata", 0.8, NOW - timedelta(hours=1)),
            _det(3, *EAGLE, 0.71, NOW - timedelta(minutes=30)),
            _det(4, "Golden Eagle", "Aquila chrysaetos", 0.93, NOW - timedelta(minutes=10))]
    svc.source = _GateSource(dets, today=rows,
                             first_seen={"Cardinalis cardinalis": YESTERDAY,
                                         "Cyanocitta cristata": YESTERDAY,
                                         EAGLE[1]: TODAY, "Aquila chrysaetos": TODAY})
    seen = _collage_cells(svc, monkeypatch)
    assert svc._build_collage(NOW, today) is True
    assert seen == [["Northern Cardinal", "Blue Jay", "Golden Eagle"]]


def test_collage_gate_off_keeps_every_row(svc, monkeypatch):
    svc.config.corroborate_new_species = False
    today = NOW.date()
    rows = [{"common": "Northern Cardinal", "scientific": "Cardinalis cardinalis", "count": 5},
            {"common": "Bald Eagle", "scientific": EAGLE[1], "count": 1}]
    svc.source = _GateSource([_det(3, *EAGLE, 0.71, NOW)], today=rows,
                             first_seen={EAGLE[1]: TODAY})
    seen = _collage_cells(svc, monkeypatch)
    assert svc._build_collage(NOW, today) is True
    assert seen == [["Northern Cardinal", "Bald Eagle"]]


def test_collage_fallback_single_is_gated_too(svc, monkeypatch):
    # One species today, new and unconfirmed: the < 2 rows fallback must not
    # sneak it onto the wall as a single plate.
    rows = [{"common": "Bald Eagle", "scientific": EAGLE[1], "count": 1}]
    svc.source = _GateSource([_det(3, *EAGLE, 0.71, NOW - timedelta(minutes=5))], today=rows,
                             first_seen={EAGLE[1]: TODAY})
    rendered = _capture_renders(svc, monkeypatch)
    assert svc._build_collage(NOW, NOW.date()) is False
    assert rendered == []
    assert svc.status()["pending"]["common"] == "Bald Eagle"


# -- config + page ------------------------------------------------------------
def test_sanitize_clamps_the_corroboration_fields():
    cfg = Config(corroborate_confidence=5, corroborate_window_hours=0,
                 corroborate_min_gap_minutes=99999, corroborate_new_species=1)
    assert cfg.corroborate_confidence == 1.0
    assert cfg.corroborate_window_hours == 1
    assert cfg.corroborate_min_gap_minutes == 720
    assert cfg.corroborate_new_species is True
    assert Config(corroborate_confidence="nan").corroborate_confidence == 0.85
    assert Config(corroborate_window_hours=400).corroborate_window_hours == 168
    d = Config().to_dict()
    assert (d["corroborate_new_species"], d["corroborate_confidence"],
            d["corroborate_window_hours"], d["corroborate_min_gap_minutes"]) == (True, 0.85, 24, 10)


def test_settings_form_round_trips_the_four_fields(client, svc):
    r = client.post("/settings", data={"corroborate_new_species": "on",
                                       "corroborate_confidence": "0.9",
                                       "corroborate_window_hours": "48",
                                       "corroborate_min_gap_minutes": "20"},
                    follow_redirects=False)
    assert r.status_code == 303 and "adjusted" not in r.headers["location"]
    assert svc.config.corroborate_new_species is True
    assert svc.config.corroborate_confidence == 0.9
    assert svc.config.corroborate_window_hours == 48
    assert svc.config.corroborate_min_gap_minutes == 20

    # Unticked checkbox -> off; out-of-range numbers are clamped and reported.
    r = client.post("/settings", data={"corroborate_confidence": "2",
                                       "corroborate_window_hours": "0",
                                       "corroborate_min_gap_minutes": "5000"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert svc.config.corroborate_new_species is False
    assert svc.config.corroborate_confidence == 1.0
    assert svc.config.corroborate_window_hours == 1
    assert svc.config.corroborate_min_gap_minutes == 720
    adjusted = r.headers["location"].split("adjusted=")[1].split(",")
    assert set(adjusted) == {"corroborate_confidence", "corroborate_window_hours",
                             "corroborate_min_gap_minutes"}


def test_page_shows_the_settings_group_and_the_pending_row(client, svc, monkeypatch):
    svc.source = _GateSource([_det(1, *EAGLE, 0.71, NOW - timedelta(minutes=1))],
                             first_seen={EAGLE[1]: TODAY})
    _capture_renders(svc, monkeypatch)
    html = client.get("/").text
    assert 'name="corroborate_new_species"' in html
    assert 'id="cns-fields"' in html and 'name="corroborate_min_gap_minutes"' in html
    assert 'id="fc-pending" hidden' in html            # nothing waiting yet

    svc._single_tick(NOW)
    html = client.get("/").text
    assert 'id="fc-pending-dt" >Pending' in html
    assert '<span class="who">Bald Eagle</span><span class="when">1 hit at 0.71 · waiting for a second' in html
    assert client.get("/api/status").json()["pending"]["common"] == "Bald Eagle"

    svc.config.corroborate_new_species = False
    assert 'id="cns-fields" hidden' in client.get("/").text
