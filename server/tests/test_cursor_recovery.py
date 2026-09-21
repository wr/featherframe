"""A stored ingest cursor left *ahead* of every real rowid must not freeze the
frame forever. This happened live: a cursor of ~10.8 billion (a leftover from an
old detection-id scheme) sat above a real max_rowid of ~450k, so `new_since`
returned nothing on every tick and the frame stuck on one bird for hours.

`_single_tick` now treats `cursor > max_rowid` as stale, restarts at the tail,
and shows the latest detection once. These pin that recovery — and that the
normal path is untouched.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from featherframe.service import FeatherframeService
from featherframe.sources.base import Detection


class _StubSource:
    """Minimal DetectionSource: no new rows since the cursor, but a real tail."""

    def __init__(self, max_rowid: int, latest: list[Detection]):
        self._max = max_rowid
        self._latest = latest

    def available(self) -> bool:
        return True

    def max_rowid(self) -> int:
        return self._max

    def new_since(self, cursor, min_confidence=0.0, limit=200):
        return []

    def latest_many(self, min_confidence=0.0, limit=25):
        return list(self._latest)

    def latest(self, min_confidence=0.0, scan=25):
        return self._latest[0] if self._latest else None


    def first_seen_date(self, scientific_name):
        return None


def _det(rowid, common, sci, conf=0.9):
    return Detection(rowid=rowid, date="2026-09-01", time="17:50:44",
                     common_name=common, scientific_name=sci, confidence=conf)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    return FeatherframeService()


def test_stale_cursor_resets_and_renders_latest(svc, monkeypatch):
    latest = _det(449928, "Northern Flicker", "Colaptes auratus")
    svc.source = _StubSource(max_rowid=449928, latest=[latest])
    # A resident frame is on the glass (the "stuck" state) and the stored cursor
    # is absurdly ahead of every real rowid.
    svc._frame_bytes = b"stuck"
    svc._meta = {"species_key": "cyanocitta cristata", "label": "Blue Jay"}
    svc._set_cursor(10_861_837_115)

    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason, **kw: rendered.append((det.common_name, reason)))

    svc._single_tick(datetime(2026, 9, 1, 17, 53, 0))

    assert rendered == [("Northern Flicker", "cursor-reset")]   # unstuck, once
    assert svc._cursor() == 449928                              # cursor back at the tail


def test_healthy_cursor_with_no_new_detections_does_nothing(svc, monkeypatch):
    svc.source = _StubSource(max_rowid=449928, latest=[_det(449928, "X", "x x")])
    svc._frame_bytes = b"resident"
    svc._set_cursor(449927)   # a sane cursor, just behind the tail

    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason, **kw: rendered.append(reason))

    svc._single_tick(datetime(2026, 9, 1, 17, 53, 0))

    assert rendered == []                 # no new_since rows -> hold the frame
    assert svc._cursor() == 449927        # cursor untouched


def test_source_blip_zero_rowid_is_not_treated_as_stale(svc, monkeypatch):
    # max_rowid soft-fails to 0; a real cursor must NOT be mistaken for stale
    # (which would reset it and churn a render every tick).
    svc.source = _StubSource(max_rowid=0, latest=[_det(1, "X", "x x")])
    svc._frame_bytes = b"resident"
    svc._set_cursor(449927)

    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason, **kw: rendered.append(reason))

    svc._single_tick(datetime(2026, 9, 1, 17, 53, 0))

    assert rendered == []
    assert svc._cursor() == 449927


def _switch_source(svc, monkeypatch, source):
    """Change the saved source the way the settings page does."""
    from featherframe.config import load_config, save_config
    import featherframe.service as service_mod

    monkeypatch.setattr(service_mod, "make_source", lambda cfg, db: source)
    cfg = load_config(svc.db)
    cfg.birdnet_go_url = "http://elsewhere:8080"
    save_config(svc.db, cfg)
    svc.reload_config()


def test_source_switch_starts_from_a_clean_slate(svc, monkeypatch):
    # Live, 18 Sep 2026: the backend went BirdNET-Go -> BirdWeather -> BirdNET-Go
    # from the settings page. BirdWeather ids are ~11 billion, so the cursor it
    # left behind sat above every BirdNET-Go rowid and the frame froze. A new
    # source now drops everything that was about the old one and shows its own
    # latest detection.
    svc.source = _StubSource(max_rowid=11_215_147_198, latest=[])
    svc._frame_bytes = b"resident"
    svc._meta = {"species_key": "x x", "label": "X", "mode": "single"}
    svc._set_cursor(11_215_147_198)
    svc._cursor_verified = True                        # the startup check already ran
    svc.db.set("quiet_collage_for", "2026-09-17")
    svc.db.set("user_hold", {"since": "2026-09-18T09:00:00", "until": None, "label": "X"})
    svc._set_pending({"key": "y y", "common": "Y"})
    rendered = []
    monkeypatch.setattr(svc, "_render_single",
                        lambda det, now, reason, **kw: rendered.append((det.common_name, reason)))

    latest = _det(455716, "Black-capped Chickadee", "Poecile atricapillus")
    _switch_source(svc, monkeypatch, _StubSource(max_rowid=455716, latest=[latest]))

    for key in ("quiet_collage_for", "user_hold", "pending_species"):
        assert svc.db.get(key) is None, key
    assert svc.user_hold(datetime(2026, 9, 18, 9, 37, 0)) is None

    svc._single_tick(datetime(2026, 9, 18, 9, 37, 0))
    assert rendered == [("Black-capped Chickadee", "source-switch")]
    assert svc._cursor() == 455716

    svc._single_tick(datetime(2026, 9, 18, 9, 37, 3))   # once, not every tick
    assert len(rendered) == 1


def test_saving_other_settings_resets_nothing(svc, monkeypatch):
    from featherframe.config import load_config, save_config

    svc.source = _StubSource(max_rowid=455000, latest=[_det(455000, "X", "x x")])
    svc._set_cursor(455000)
    svc.db.set("user_hold", {"since": "2026-09-18T09:00:00", "until": None, "label": "X"})
    source = svc.source

    cfg = load_config(svc.db)
    cfg.collage_species_max = 12
    save_config(svc.db, cfg)
    svc.reload_config()

    assert svc.source is source
    assert svc._cursor() == 455000
    assert svc.db.get("user_hold") is not None
