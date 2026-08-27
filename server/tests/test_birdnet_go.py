"""BirdNET-Go DetectionSource: cursor walking, confidence filter, stats from the
species summary, and soft-fail — all against a stubbed HTTP layer (no network)."""
from __future__ import annotations

import pytest

from featherframe.sources.birdnet_go import BirdNetGoSource


class _FakeHTTP:
    """Stands in for requests.get, keyed by path."""

    def __init__(self, detections, summary):
        # detections: list newest-first (dicts with id/date/time/names/confidence)
        self.detections = detections
        self.summary = summary
        self.threshold = None   # BirdNET-Go's /birdnet/threshold, if set
        self.fail = False

    def get(self, url, params=None, timeout=None):
        params = params or {}
        return _FakeResp(self, url, params)


class _FakeResp:
    def __init__(self, http, url, params):
        self._http = http
        self._url = url
        self._params = params
        self.status_code = 200 if not http.fail else 500

    def json(self):
        if "/settings" in self._url:
            return {"birdnet": {"threshold": self._http.threshold}} if self._http.threshold is not None else {}
        if "/analytics/species/summary" in self._url:
            return self._http.summary
        # /api/v2/detections — honor offset + numResults, newest-first
        offset = int(self._params.get("offset", 0))
        num = int(self._params.get("numResults", 100))
        window = self._http.detections[offset:offset + num]
        return {"data": window, "total": len(self._http.detections)}


def _det(i, common, sci, conf, date="2026-05-17", time="06:00:00"):
    return {"id": i, "date": date, "time": time,
            "commonName": common, "scientificName": sci, "confidence": conf}


@pytest.fixture
def go(monkeypatch):
    dets = [  # newest-first
        _det(3, "American Robin", "Turdus migratorius", 0.80, time="06:14:00"),
        _det(2, "Blue Jay", "Cyanocitta cristata", 0.85, time="06:07:00"),
        _det(1, "Northern Cardinal", "Cardinalis cardinalis", 0.90, time="06:00:00"),
    ]
    summary = [
        {"scientific_name": "Cardinalis cardinalis", "common_name": "Northern Cardinal",
         "count": 10, "first_heard": "2026-01-16T15:00:00Z"},
        {"scientific_name": "Cyanocitta cristata", "common_name": "Blue Jay",
         "count": 5, "first_heard": "2026-02-01T09:00:00Z"},
        {"scientific_name": "Turdus migratorius", "common_name": "American Robin",
         "count": 3, "first_heard": "2026-03-10T07:00:00Z"},
    ]
    fake = _FakeHTTP(dets, summary)
    src = BirdNetGoSource("http://go.local:8080")
    monkeypatch.setattr("featherframe.sources.birdnet_go.requests", fake)
    return src, fake


def test_available(go):
    src, _ = go
    assert src.available() is True


def test_available_false_on_http_error(go):
    src, fake = go
    fake.fail = True
    assert src.available() is False


def test_max_rowid_is_newest_id(go):
    src, _ = go
    assert src.max_rowid() == 3


def test_new_since_walks_forward_oldest_first(go):
    src, _ = go
    got = src.new_since(0, min_confidence=0.7)
    assert [d.common_name for d in got] == ["Northern Cardinal", "Blue Jay", "American Robin"]
    assert [d.rowid for d in got] == [1, 2, 3]
    assert src.new_since(3) == []
    assert [d.common_name for d in src.new_since(1)] == ["Blue Jay", "American Robin"]


def test_confidence_filter(go):
    src, fake = go
    fake.detections.insert(0, _det(4, "Song Sparrow", "Melospiza melodia", 0.40, time="06:20:00"))
    got = src.new_since(0, min_confidence=0.7)
    assert "Song Sparrow" not in [d.common_name for d in got]


def test_latest_is_newest(go):
    src, _ = go
    assert src.latest(min_confidence=0.7).common_name == "American Robin"


def test_all_time_species_count(go):
    src, _ = go
    assert src.all_time_species_count() == 3


def test_species_ordinal_by_first_heard(go):
    src, _ = go
    assert src.species_ordinal("Cardinalis cardinalis") == 1
    assert src.species_ordinal("Cyanocitta cristata") == 2
    assert src.species_ordinal("Turdus migratorius") == 3
    assert src.species_ordinal("Unknown species") is None


def test_first_seen_date(go):
    src, _ = go
    # 2026-01-16 UTC; local date may differ by a day at the extreme, so just check shape
    d = src.first_seen_date("Cardinalis cardinalis")
    assert d and d.startswith("2026-01-1")


def test_soft_fail_returns_safe_defaults(go):
    src, fake = go
    fake.fail = True
    assert src.new_since(0) == []
    assert src.latest() is None
    assert src.max_rowid() == 0
    assert src.all_time_species_count() == 0
    assert src.species_ordinal("x") is None
    assert src.top_species_today() == []


def test_defer_confidence_uses_birdnet_threshold(go):
    src, fake = go
    fake.threshold = 0.88          # BirdNET-Go's own bar
    # defer_confidence is on by default -> the passed 0.0 is ignored, 0.88 applies.
    got = [d.common_name for d in src.latest_many(0.0)]
    assert got == ["Northern Cardinal"]          # only the 0.90 clears 0.88


def test_defer_off_uses_passed_confidence(monkeypatch):
    from featherframe.sources.birdnet_go import BirdNetGoSource
    dets = [_det(2, "Blue Jay", "Cyanocitta cristata", 0.85),
            _det(1, "Northern Cardinal", "Cardinalis cardinalis", 0.90)]
    fake = _FakeHTTP(dets, []); fake.threshold = 0.88
    src = BirdNetGoSource("http://x", defer_confidence=False)
    monkeypatch.setattr("featherframe.sources.birdnet_go.requests", fake)
    # defer off -> BirdNET-Go's 0.88 ignored; the passed 0.5 applies (both pass).
    assert len(src.latest_many(0.5)) == 2
