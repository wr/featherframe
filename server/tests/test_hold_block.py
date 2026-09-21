"""W-735: the two controls a viewer reaches for — hold this plate, and block
what's showing — driven from the dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from featherframe.service import FeatherframeService
from featherframe.sources.base import Detection
from featherframe.render import pipeline as pipeline  # noqa: E402

NOW = datetime(2026, 9, 12, 8, 20, 0)
YESTERDAY = (NOW.date() - timedelta(days=1)).isoformat()
CARDINAL = ("Northern Cardinal", "Cardinalis cardinalis")
ROBIN = ("American Robin", "Turdus migratorius")


def _det(rowid, common, sci, at=NOW, conf=0.95):
    return Detection(rowid=rowid, date=at.strftime("%Y-%m-%d"), time=at.strftime("%H:%M:%S"),
                     common_name=common, scientific_name=sci, confidence=conf)


class _Source:
    def __init__(self, rows):
        self.rows = list(rows)

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
        return []

    def all_time_species_count(self):
        return len({d.key for d in self.rows})

    def first_seen_date(self, sci):
        return YESTERDAY          # every species is known: no corroboration gate

    def species_ordinal(self, sci):
        return None


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    service._clock = lambda: NOW
    pipeline.DITHER_OVERRIDE = "none"
    service.source = _Source([_det(1, *CARDINAL)])
    service._set_cursor(0)
    service._cursor_verified = True
    service.tick()                           # the cardinal takes the glass
    assert service._meta["label"] == CARDINAL[0]
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


def _hear(svc, rowid, species, at=NOW):
    svc.source.rows.append(_det(rowid, *species, at=at))


# -- hold ----------------------------------------------------------------------
def test_a_held_plate_is_not_replaced_by_the_next_bird(svc):
    hold = svc.hold_current("day")
    assert hold["title"] == CARDINAL[0]
    _hear(svc, 2, ROBIN)
    svc.tick()
    assert svc._meta["label"] == CARDINAL[0]
    assert svc.status()["hold"]["until_text"]


def test_a_hold_expires_and_the_frame_catches_up(svc):
    svc.hold_current("day")
    _hear(svc, 2, ROBIN)
    svc.tick()
    assert svc._meta["label"] == CARDINAL[0]
    assert svc._cursor() == 1                 # the held tick consumed nothing
    later = NOW + timedelta(days=1, minutes=1)
    svc._clock = lambda: later
    _hear(svc, 3, ROBIN, at=later)            # a fresh bird, so no gone-quiet footnote competes
    svc.tick()
    assert svc.user_hold() is None
    assert svc._meta["label"] == ROBIN[0]


def test_release_repaints_what_should_be_showing(svc):
    svc.hold_current("forever")
    _hear(svc, 2, ROBIN)
    svc.tick()
    assert svc.user_hold()["until"] is None
    assert svc._meta["label"] == CARDINAL[0]
    svc.release_hold()
    assert svc.user_hold() is None
    assert svc._meta["label"] == ROBIN[0]


def test_hold_endpoints(client, svc):
    r = client.post("/api/hold", data={"duration": "week"})
    assert r.status_code == 200 and r.json()["hold"]["title"] == CARDINAL[0]
    assert client.get("/api/status").json()["hold"]["until"].startswith("2026-09-19")
    r = client.post("/api/hold/release")
    assert r.status_code == 200 and client.get("/api/status").json()["hold"] is None


# -- block ---------------------------------------------------------------------
def test_block_current_adds_the_species_once_and_moves_on(svc):
    _hear(svc, 2, ROBIN)
    assert svc.block_current() == CARDINAL[0]
    assert svc.config.species_blocklist == [CARDINAL[0]]
    assert svc._meta["label"] == ROBIN[0]          # the refresh skipped the blocked bird
    _hear(svc, 3, CARDINAL)
    svc.tick()
    assert svc._meta["label"] == ROBIN[0]
    # Blocking the robin too leaves nothing showable; the frame keeps the robin.
    assert svc.block_current() == ROBIN[0]
    assert svc.config.species_blocklist == [CARDINAL[0], ROBIN[0]]


def test_unblock_undoes_it(svc):
    svc.block_current()
    assert svc.unblock(CARDINAL[0]) is True
    assert svc.config.species_blocklist == []
    assert svc.unblock(CARDINAL[0]) is False


def test_block_endpoints(client, svc):
    _hear(svc, 2, ROBIN)
    r = client.post("/api/block-current")
    assert r.status_code == 200 and r.json()["blocked"] == CARDINAL[0]
    assert svc.config.species_blocklist == [CARDINAL[0]]
    r = client.post("/api/unblock", data={"name": CARDINAL[0]})
    assert r.status_code == 200 and svc.config.species_blocklist == []


def test_nothing_to_hold_or_block_on_a_welcome_plate(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data2"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    fresh = FeatherframeService()
    fresh.source.db_path = str(tmp_path / "missing.db")
    fresh._ensure_initial_frame()
    assert fresh._meta["mode"] == "welcome"
    assert fresh.hold_current("day") is None
    assert fresh.block_current() is None
