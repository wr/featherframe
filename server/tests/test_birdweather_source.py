"""BirdWeather source parsing: the nested species{} object, the confidence
fallback, cursor filtering, and availability semantics."""
from __future__ import annotations

from featherframe.sources.birdweather import BirdWeatherSource


def _rec(id=10, conf=0.83, **over):
    # A naive (no-Z) timestamp keeps the local-date assertion timezone-stable.
    rec = {"id": id, "timestamp": "2026-08-31T13:14:15",
           "confidence": conf,
           "species": {"commonName": "Northern Cardinal",
                       "scientificName": "Cardinalis cardinalis"}}
    rec.update(over)
    return rec


def test_to_detection_parses_nested_species():
    d = BirdWeatherSource._to_detection(_rec())
    assert d is not None
    assert d.common_name == "Northern Cardinal"
    assert d.scientific_name == "Cardinalis cardinalis"
    assert d.confidence == 0.83
    assert d.rowid == 10
    assert d.date == "2026-08-31" and d.time == "13:14:15"


def test_confidence_falls_back_to_probability_then_score():
    r1 = _rec(); del r1["confidence"]; r1["probability"] = 0.7
    assert BirdWeatherSource._to_detection(r1).confidence == 0.7
    r2 = _rec(); del r2["confidence"]; r2["score"] = 0.6
    assert BirdWeatherSource._to_detection(r2).confidence == 0.6


def test_to_detection_soft_fails_on_junk():
    assert BirdWeatherSource._to_detection({"id": "x"}) is None
    assert BirdWeatherSource._to_detection({}) is None


def test_new_since_filters_by_confidence_and_cursor(monkeypatch):
    s = BirdWeatherSource("tok")
    page = [_rec(12, 0.9), _rec(11, 0.2), _rec(10, 0.9)]
    monkeypatch.setattr(s, "_get", lambda path, params=None: page)
    out = s.new_since(cursor=10, min_confidence=0.5)
    assert [d.rowid for d in out] == [12]  # 11 below bar, 10 == cursor


def test_available_false_on_error(monkeypatch):
    s = BirdWeatherSource("tok")
    monkeypatch.setattr(s, "_get", lambda *a, **k: None)  # 404 / unreachable
    assert s.available() is False


def test_available_true_on_empty_but_valid(monkeypatch):
    s = BirdWeatherSource("tok")
    monkeypatch.setattr(s, "_get", lambda *a, **k: {"detections": []})
    assert s.available() is True


def test_available_false_without_station_id():
    assert BirdWeatherSource("").available() is False
