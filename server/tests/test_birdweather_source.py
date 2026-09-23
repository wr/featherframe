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


def test_top_species_today_reads_the_detections_breakdown(monkeypatch):
    """The station API gives `detections` as a breakdown, not a number
    (23 Sep 2026: {"total": 88, "almostCertain": 88, ...}). Read as an int it
    was 0 for every species, so no collage was ever drawn from BirdWeather and
    a frame on the collage showed plates."""
    s = BirdWeatherSource("tok")
    s.station_id = "21613"
    payload = {"success": True, "species": [
        {"commonName": "Blue Jay", "scientificName": "Cyanocitta cristata",
         "detections": {"total": 88, "almostCertain": 88, "veryLikely": 0}},
        {"commonName": "Black-capped Chickadee", "scientificName": "Poecile atricapillus",
         "detections": {"total": 120, "almostCertain": 110, "veryLikely": 10}},
        {"commonName": "Plain Count", "scientificName": "Numerus simplex", "detections": 3},
    ]}
    monkeypatch.setattr(s, "_get", lambda path, params=None: payload)
    rows = s.top_species_today(limit=10)
    assert [(r["common"], r["count"]) for r in rows] == [
        ("Black-capped Chickadee", 120), ("Blue Jay", 88), ("Plain Count", 3)]


def test_heard_before_compares_all_time_totals_with_todays(monkeypatch):
    """No first dates on BirdWeather, but every species' all-time total: more
    than today's means it was heard before today. All-time comes 100 a page."""
    from datetime import datetime as _dt
    s = BirdWeatherSource("21613")
    pages = {1: [{"scientificName": f"Avis {i}", "detections": {"total": 5}} for i in range(100)],
             2: [{"scientificName": "Poecile atricapillus", "detections": {"total": 900}},
                 {"scientificName": "Aquila chrysaetos", "detections": {"total": 2}}]}
    today = [{"scientificName": "Poecile atricapillus", "commonName": "Chickadee", "detections": {"total": 30}},
             {"scientificName": "Aquila chrysaetos", "commonName": "Golden Eagle", "detections": {"total": 2}}]

    def get(path, params=None):
        if params.get("period") == "all":
            return {"species": pages.get(params["page"], [])}
        return {"species": today}
    monkeypatch.setattr(s, "_get", get)
    day = _dt.now().date()
    assert s.heard_before("Poecile atricapillus", day) is True
    assert s.heard_before("Aquila chrysaetos", day) is False     # every detection is today's
    assert s.heard_before("Avis 7", day) is True
    assert s.heard_before("Nowhere nobody", day) is None
