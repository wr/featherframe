"""W-690: render history thumbnails, the "last heard" time, and the gone-quiet
alarm. The month-two failure — the detector dies and nothing anywhere says so
— must show on the plate and the page, and only when the silence is real.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe import paths
from featherframe.config import Config
from featherframe.render import pipeline
from featherframe.render.compose import SingleSpec
from featherframe.render.provider import AudubonProvider
from featherframe.service import FeatherframeService, when_text
from featherframe.sources.base import Detection


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    service.config.dither = "none"
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


class _Source:
    """A reachable source whose only detection is `latest` (or none)."""

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


# -- render history ----------------------------------------------------------
def test_commit_writes_thumbnail_and_history_lists_it(client, svc):
    svc.force_test_detection("Northern Cardinal", "Cardinalis cardinalis")
    etag = svc.current_etag()
    thumb = paths.history_dir() / f"{etag}.png"
    assert thumb.exists()
    from PIL import Image
    with Image.open(thumb) as im:
        assert im.size == (-(-1404 // 8), -(-1872 // 8))   # reduce() rounds up

    items = client.get("/api/history").json()["items"]
    assert items[0]["etag"] == etag
    assert items[0]["title"] == "Northern Cardinal (test)"
    assert items[0]["thumb"] == f"/api/history/{etag}.png"
    assert items[0]["when_text"]
    r = client.get(items[0]["thumb"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "max-age=86400" in r.headers["cache-control"]


def test_history_thumbnails_are_capped_at_sixty(svc):
    from PIL import Image
    hist = paths.history_dir()
    for i in range(70):
        p = hist / f"{i:016x}.png"
        Image.new("L", (4, 4), 255).save(p)
        # distinct mtimes so "oldest" is well defined
        import os
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    svc.force_test_detection("Northern Cardinal", "Cardinalis cardinalis")
    left = sorted(hist.glob("*.png"))
    assert len(left) == 60
    assert (hist / f"{svc.current_etag()}.png") in left
    assert not (hist / f"{0:016x}.png").exists()      # the oldest went first
    assert (hist / f"{69:016x}.png").exists()


def test_history_png_404s_for_bad_or_missing_etag(client):
    assert client.get("/api/history/not-hex!.png").status_code == 404
    assert client.get("/api/history/../current.png").status_code == 404
    assert client.get("/api/history/0123456789abcdef.png").status_code == 404


def test_history_thumb_is_null_when_file_is_gone(client, svc):
    svc.force_test_detection("Northern Cardinal", "Cardinalis cardinalis")
    (paths.history_dir() / f"{svc.current_etag()}.png").unlink()
    items = client.get("/api/history").json()["items"]
    assert items[0]["thumb"] is None
    assert "Nothing rendered yet." in client.get("/").text


def test_page_renders_history_strip(client, svc):
    svc.force_test_detection("Northern Cardinal", "Cardinalis cardinalis")
    html = client.get("/").text
    assert 'class="hist-item"' in html
    assert f"/api/history/{svc.current_etag()}.png" in html
    assert "Nothing rendered yet." not in html or "hidden" in html


# -- last heard --------------------------------------------------------------
def test_when_text_drops_the_date_only_for_today():
    now = datetime(2026, 9, 2, 12, 0)
    assert when_text(datetime(2026, 9, 2, 23, 27), now) == "11:27 pm"
    assert when_text(datetime(2026, 9, 2, 0, 5), now) == "12:05 am"
    assert when_text(datetime(2026, 9, 1, 23, 27), now) == "1 Sep 11:27 pm"


def test_status_carries_quiet_and_last_heard_time(svc):
    svc.source = _Source(_det_at(datetime.now() - timedelta(minutes=5)))
    st = svc.status()
    assert "quiet" in st and st["quiet"] is None
    assert st["last_detection"]["when_text"].endswith(("am", "pm"))
    assert " " not in st["last_detection"]["when_text"].split(":")[0]   # today: no date


# -- quiet_state -------------------------------------------------------------
def test_quiet_state_off_at_zero_hours(svc):
    svc.config = Config(quiet_alarm_hours=0, quiet_hours_mode="off")
    svc.source = _Source(_det_at(datetime.now() - timedelta(hours=30)))
    assert svc.quiet_state(datetime.now()) is None


def test_quiet_state_alarms_after_seven_silent_hours(svc):
    now = datetime(2026, 9, 2, 15, 0)
    svc.config = Config(quiet_alarm_hours=6, quiet_hours_mode="off")
    svc.source = _Source(_det_at(now - timedelta(hours=7)))
    q = svc.quiet_state(now)
    assert q is not None
    assert q["hours"] == pytest.approx(7, abs=0.2)
    assert q["since"] == "2026-09-02T08:00:00"
    assert q["since_text"] == "8:00 am"
    assert q["hours_text"] == "7 h"


def test_quiet_state_ignores_quiet_hours(svc):
    # 01:00 -> 08:00 is seven hours, but 01:00 -> 06:00 is inside the night
    # window: only two active hours, under a six-hour threshold.
    now = datetime(2026, 9, 2, 8, 0)
    svc.config = Config(quiet_alarm_hours=6, quiet_hours_mode="custom",
                        quiet_hours_start="22:00", quiet_hours_end="06:00")
    svc.source = _Source(_det_at(now - timedelta(hours=7)))
    assert svc.quiet_state(now) is None
    # Same silence with the window off does alarm.
    svc.config.quiet_hours_mode = "off"
    assert svc.quiet_state(now) is not None


def test_quiet_state_is_unknown_when_source_is_down(svc):
    now = datetime(2026, 9, 2, 15, 0)
    svc.config = Config(quiet_alarm_hours=6, quiet_hours_mode="off")
    svc.source = _Source(_det_at(now - timedelta(hours=20)), available=False)
    assert svc.quiet_state(now) is None
    assert svc.quiet_state(now, available=False) is None


def test_quiet_state_counts_from_service_start_without_detections(svc):
    svc.config = Config(quiet_alarm_hours=6, quiet_hours_mode="off")
    svc.source = _Source(None)
    assert svc.quiet_state(svc._started_at + timedelta(hours=1)) is None
    q = svc.quiet_state(svc._started_at + timedelta(hours=7))
    assert q is not None and q["hours"] == pytest.approx(7, abs=0.2)


def test_quiet_alarm_hours_is_clamped(client, svc):
    assert Config(quiet_alarm_hours=999).quiet_alarm_hours == 168
    assert Config(quiet_alarm_hours=-3).quiet_alarm_hours == 0
    assert Config(quiet_alarm_hours="nan").quiet_alarm_hours == 6
    r = client.post("/settings", data={"quiet_alarm_hours": "12"}, follow_redirects=False)
    assert r.status_code == 303
    assert svc.config.quiet_alarm_hours == 12


# -- tick: one re-render per flip --------------------------------------------
def test_tick_rerenders_once_when_alarm_flips_on(svc, monkeypatch):
    svc.config.quiet_alarm_hours = 6
    svc.config.quiet_hours_mode = "off"
    svc.update_config(svc.config)
    svc.source = _Source(_det_at(datetime.now() - timedelta(hours=7)))
    # A resident bird from before the silence.
    svc._render_single(svc.source.latest(), datetime.now(), reason="setup")
    assert svc._meta.get("quiet_note") is False
    before = svc.current_etag()

    calls = []
    real = svc.rerender_current
    monkeypatch.setattr(svc, "rerender_current", lambda: (calls.append(1), real())[1])

    svc.tick()
    assert calls == [1]
    assert svc._meta.get("quiet_note") is True
    assert svc.current_etag() != before
    assert svc.status()["quiet"]["hours"] >= 6

    svc.tick()
    svc.tick()
    assert calls == [1]                        # same state: no more renders


def test_tick_drops_the_note_once_a_bird_is_heard(svc, monkeypatch):
    svc.config.quiet_alarm_hours = 6
    svc.config.quiet_hours_mode = "off"
    svc.update_config(svc.config)
    svc.source = _Source(_det_at(datetime.now() - timedelta(hours=7)))
    svc._render_single(svc.source.latest(), datetime.now(), reason="setup")
    svc.tick()
    assert svc._meta.get("quiet_note") is True

    # The detector comes back: the note goes, in one render.
    svc.source = _Source(_det_at(datetime.now(), common="Carolina Wren",
                                 sci="Thryothorus ludovicianus"))
    renders = []
    real = svc._commit
    monkeypatch.setattr(svc, "_commit", lambda *a, **k: (renders.append(k.get("note")), real(*a, **k)))
    svc.tick()
    assert renders == [None]
    assert svc._meta.get("quiet_note") is False
    assert svc.status()["quiet"] is None


def test_tick_keeps_the_note_through_an_outage(svc, monkeypatch):
    svc.config.quiet_alarm_hours = 6
    svc.config.quiet_hours_mode = "off"
    svc.update_config(svc.config)
    svc.source = _Source(_det_at(datetime.now() - timedelta(hours=7)))
    svc._render_single(svc.source.latest(), datetime.now(), reason="setup")
    svc.tick()
    etag = svc.current_etag()
    svc.source = _Source(_det_at(datetime.now() - timedelta(hours=7)), available=False)
    svc.tick()
    assert svc.current_etag() == etag          # unknown is not "all clear"
    assert svc.status()["quiet"] is None


# -- the plate note ----------------------------------------------------------
def test_note_renders_in_the_bottom_margin(svc):
    config = Config(dither="none", mat_inset_pct=0)
    provider = AudubonProvider()                # no plates -> typographic fallback
    base = SingleSpec(common_name="Painted Bunting", scientific_name="Passerina ciris",
                      when=datetime(2026, 9, 2, 8, 14), plate_number=3)
    plain = pipeline.render_single(base, provider, config)
    noted = pipeline.render_single(
        SingleSpec(**{**base.__dict__, "note": "Nothing heard since 11:27 pm"}),
        provider, config)
    assert noted.etag != plain.etag

    def ink(result):
        # the bottom margin, centred: the note's line
        region = result.preview.crop((450, 1800, 954, 1850))
        return min(region.getdata())
    assert ink(plain) == 255
    assert ink(noted) < 128


def test_collage_note_does_not_collide_with_the_key(svc):
    from PIL import Image
    from featherframe.render import collage as collage_mod, theme
    cells = [collage_mod.CollageCell("Blue Jay", "Cyanocitta cristata", 4),
             collage_mod.CollageCell("Carolina Wren", "Thryothorus ludovicianus", 2)]
    art = Image.new("L", (600, 400), 255)
    field = collage_mod.render_generated_collage(art, cells, note="Nothing heard since 8:00 am")
    # The key lifted clear of the note: no ink in the gap between them.
    key_bottom = theme.HEIGHT - theme.KEY_BOTTOM - theme.NOTE_CLEAR
    gap = field.crop((100, key_bottom + 4, theme.WIDTH - 100, key_bottom + 16))
    assert min(gap.getdata()) == 255
    note_band = field.crop((450, 1800, 954, 1850))
    assert min(note_band.getdata()) < 128
