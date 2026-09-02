"""W-696: when the detection source is misconfigured or unreachable, the
glass must eventually say so. The Source card already reads "Not reachable";
the plate gets a footnote after `source_alarm_minutes` of outage, and drops
it in one render when the source comes back.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config
from featherframe.service import FeatherframeService
from featherframe.sources.base import Detection


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    service.config.dither = "none"
    service.config.quiet_alarm_hours = 0
    service.config.quiet_hours_mode = "off"
    service.update_config(service.config)
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


class _Source:
    def __init__(self, latest=None, available=True):
        self._latest = latest
        self._available = available

    def available(self):
        return self._available

    def max_rowid(self):
        return self._latest.rowid if self._latest else 0

    def new_since(self, cursor, min_confidence=0.0, limit=500):
        return []

    def latest_many(self, min_confidence=0.0, limit=25):
        return [self._latest] if self._latest else []

    def latest(self, min_confidence=0.0, scan=25):
        return self._latest

    def species_ordinal(self, sci):
        return None

    def first_seen_date(self, sci):
        return None

    def all_time_species_count(self):
        return 1 if self._latest else 0

    def top_species_today(self, on_date=None, min_confidence=0.0, limit=6):
        return []


def _det_at(when: datetime, common="Blue Jay", sci="Cyanocitta cristata"):
    return Detection(rowid=7, date=when.strftime("%Y-%m-%d"), time=when.strftime("%H:%M:%S"),
                     common_name=common, scientific_name=sci, confidence=0.9)


def _resident(svc):
    """A bird on the glass, rendered while the source was fine."""
    svc.source = _Source(_det_at(datetime.now() - timedelta(minutes=30)))
    svc._render_single(svc.source.latest(), datetime.now(), reason="setup")
    assert svc._meta.get("quiet_note") is False
    return svc.current_etag()


# -- outage_state -------------------------------------------------------------
def test_outage_clock_starts_on_the_first_unreachable_tick_and_persists(svc):
    now = datetime(2026, 9, 2, 15, 0)
    svc.source = _Source(None, available=True)
    svc._track_source(now, available=True)
    assert svc.outage_state(now) is None
    assert svc.db.get("source_down_since") is None

    svc._track_source(now, available=False)
    assert svc.db.get("source_down_since") == "2026-09-02T15:00:00"
    # Under the threshold: no alarm yet, but the clock is running.
    assert svc.outage_state(now + timedelta(minutes=30)) is None
    o = svc.outage_state(now + timedelta(hours=3))
    assert o == {"since": "2026-09-02T15:00:00", "since_text": "3:00 pm",
                 "hours": 3, "hours_text": "3 h"}

    # A later unreachable tick does not restart the clock.
    svc._track_source(now + timedelta(hours=1), available=False)
    assert svc.db.get("source_down_since") == "2026-09-02T15:00:00"

    # Reachable again: cleared everywhere.
    svc._track_source(now + timedelta(hours=4), available=True)
    assert svc.outage_state(now + timedelta(hours=4)) is None
    assert svc.db.get("source_down_since") is None


def test_outage_clock_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    first = FeatherframeService()
    first._track_source(datetime(2026, 9, 2, 15, 0), available=False)
    second = FeatherframeService()
    assert second.outage_state(datetime(2026, 9, 2, 17, 0))["since_text"] == "3:00 pm"


def test_outage_state_off_at_zero_minutes(svc):
    svc.config.source_alarm_minutes = 0
    now = datetime(2026, 9, 2, 15, 0)
    svc._track_source(now, available=False)
    assert svc.outage_state(now + timedelta(days=2)) is None


def test_source_alarm_minutes_is_clamped_and_saved(client, svc):
    assert Config(source_alarm_minutes=99999).source_alarm_minutes == 10080
    assert Config(source_alarm_minutes=-3).source_alarm_minutes == 0
    assert Config(source_alarm_minutes="nan").source_alarm_minutes == 60
    r = client.post("/settings", data={"source_alarm_minutes": "15"}, follow_redirects=False)
    assert r.status_code == 303
    assert svc.config.source_alarm_minutes == 15


# -- tick: the footnote -------------------------------------------------------
def test_tick_notes_the_outage_once_past_the_threshold(svc, monkeypatch):
    before = _resident(svc)
    svc.source = _Source(svc.source.latest(), available=False)

    calls = []
    real = svc.rerender_current
    monkeypatch.setattr(svc, "rerender_current", lambda: (calls.append(1), real())[1])

    svc.tick()                                   # first miss: a blip, not an outage
    assert calls == []
    assert svc.current_etag() == before
    assert svc.status()["source_outage"] is None

    # Two hours in (the clock is set back rather than waited out).
    svc._source_down_since = datetime.now() - timedelta(hours=2)
    svc.tick()
    assert calls == [1]
    assert svc.current_etag() != before
    assert svc._meta.get("quiet_note") is True
    assert svc._meta.get("note_kind") == "outage"
    out = svc.status()["source_outage"]
    assert out is not None and out["hours"] == pytest.approx(2, abs=0.1)

    svc.tick()
    svc.tick()
    assert calls == [1]                          # same state: no more renders


def test_note_text_names_the_detector(svc):
    _resident(svc)
    svc.source = _Source(svc.source.latest(), available=False)
    svc._source_down_since = datetime.now() - timedelta(hours=2)
    svc.tick()
    assert svc._note_text().startswith("Detector unreachable since ")


def test_tick_drops_the_note_in_one_render_when_the_source_returns(svc, monkeypatch):
    _resident(svc)
    svc.source = _Source(svc.source.latest(), available=False)
    svc._source_down_since = datetime.now() - timedelta(hours=2)
    svc.tick()
    noted = svc.current_etag()
    assert svc._meta.get("note_kind") == "outage"

    svc.source = _Source(svc.source.latest(), available=True)
    renders = []
    real = svc._commit
    monkeypatch.setattr(svc, "_commit", lambda *a, **k: (renders.append(k.get("note")), real(*a, **k)))
    svc.tick()
    assert renders == [None]
    assert svc.current_etag() != noted
    assert svc._meta.get("quiet_note") is False
    assert svc.status()["source_outage"] is None
    svc.tick()
    assert renders == [None]


def test_outage_note_replaces_the_quiet_note(svc, monkeypatch):
    # A silent detector that then also goes unreachable: the more specific
    # diagnosis wins, and it takes one render to switch.
    svc.config.quiet_alarm_hours = 6
    svc.update_config(svc.config)
    svc.source = _Source(_det_at(datetime.now() - timedelta(hours=7)))
    svc._render_single(svc.source.latest(), datetime.now(), reason="setup")
    svc.tick()
    assert svc._meta.get("note_kind") == "quiet"
    quiet_etag = svc.current_etag()

    svc.source = _Source(svc.source.latest(), available=False)
    svc.tick()
    assert svc.current_etag() == quiet_etag      # blip: keep the quiet note
    svc._source_down_since = datetime.now() - timedelta(hours=2)
    svc.tick()
    assert svc._meta.get("note_kind") == "outage"
    assert svc._note_text().startswith("Detector unreachable")

    # Back, and still silent: the quiet note returns.
    svc.source = _Source(_det_at(datetime.now() - timedelta(hours=7)), available=True)
    svc.tick()
    assert svc._meta.get("note_kind") == "quiet"


def test_status_and_page_carry_the_outage(client, svc):
    _resident(svc)
    svc.source = _Source(svc.source.latest(), available=False)
    svc._source_down_since = datetime.now() - timedelta(hours=2)
    svc.tick()
    st = client.get("/api/status").json()
    assert st["source_outage"]["hours"] == pytest.approx(2, abs=0.1)
    html = client.get("/").text
    assert 'id="outage-banner"' in html
    assert "unreachable since" in html
    assert 'name="source_alarm_minutes"' in html
