"""The push sources: ingest → queue → poll, DB persistence, and the webhooks
BirdNET-Pi (Apprise) and BirdNET-Go post to."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, save_config
from featherframe.db import Database
from featherframe.sources.pushed import PushedSource


def _det(common="Northern Cardinal", sci="Cardinalis cardinalis", conf=0.9):
    return {"comname": common, "sciname": sci, "confidence": conf,
            "date": "2026-08-31", "time": "08:14:00"}


def test_ingest_and_cursor():
    s = PushedSource("apprise")
    assert s.max_rowid() == 0
    d = s.ingest(_det())
    assert d is not None and d.common_name == "Northern Cardinal"
    assert s.max_rowid() == 1
    assert [x.common_name for x in s.new_since(0)] == ["Northern Cardinal"]
    assert s.new_since(1) == []  # nothing past the cursor


def test_rejects_speciesless_payload():
    s = PushedSource("apprise")
    assert s.ingest({"confidence": 0.9}) is None
    assert s.max_rowid() == 0


def test_confidence_filter():
    s = PushedSource("apprise")
    s.ingest(_det(conf=0.1))
    assert s.new_since(0, min_confidence=0.5) == []
    assert len(s.new_since(0, min_confidence=0.0)) == 1


def test_top_species_today_counts():
    s = PushedSource("apprise")
    for _ in range(3):
        s.ingest(_det("Blue Jay", "Cyanocitta cristata"))
    s.ingest(_det("American Robin", "Turdus migratorius"))
    top = s.top_species_today(on_date=__import__("datetime").date(2026, 8, 31))
    assert top[0]["common"] == "Blue Jay" and top[0]["count"] == 3


def test_persists_counter_and_items_across_restart(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    s1 = PushedSource("apprise", db=db)
    s1.ingest(_det("Blue Jay", "Cyanocitta cristata"))
    # A fresh instance (restart) resumes the counter and items from the store,
    # so a freshly-pushed detection still lands above the persisted cursor.
    s2 = PushedSource("apprise", db=db)
    assert s2.max_rowid() == 1
    assert s2.new_since(0)[0].common_name == "Blue Jay"
    s2.ingest(_det("American Robin", "Turdus migratorius"))
    assert s2.max_rowid() == 2


@pytest.fixture
def apprise_client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    # ingest_token="" opts into the accept-any-LAN-post path these tests probe;
    # the bad-token test sets its own secret. (Fresh configs now get a random
    # default token — see test_config.)
    save_config(svc.db, Config(detection_backend="apprise", ingest_token=""))
    svc.reload_config()
    app.state.service = svc
    return TestClient(app), svc


def test_webhook_parses_apprise_envelope(apprise_client):
    client, svc = apprise_client
    body = {"version": "1.0", "title": "New BirdNET-Pi Detection", "type": "info",
            "message": '{"comname":"Northern Cardinal","sciname":"Cardinalis cardinalis",'
                       '"confidence":0.91,"date":"2026-08-31","time":"08:14:00"}'}
    r = client.post("/api/ingest/apprise", json=body)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert svc.source.max_rowid() == 1
    assert svc.source.latest_many()[0].scientific_name == "Cardinalis cardinalis"


def test_webhook_tolerates_trailing_text(apprise_client):
    client, svc = apprise_client
    # BirdNET-Pi sometimes appends "(first time today)" after the JSON body.
    body = {"message": '{"comname":"Blue Jay","sciname":"Cyanocitta cristata",'
                       '"confidence":0.8} (first time today)'}
    r = client.post("/api/ingest/apprise", json=body)
    assert r.json()["ok"] is True
    assert svc.source.max_rowid() == 1


def test_webhook_bad_token_rejected(apprise_client, tmp_path):
    client, svc = apprise_client
    save_config(svc.db, Config(detection_backend="apprise", ingest_token="secret"))
    svc.reload_config()
    assert client.post("/api/ingest/apprise/wrong", json={"message": "{}"}).status_code == 403
    ok = client.post("/api/ingest/apprise/secret", json={"message": '{"comname":"Robin"}'})
    assert ok.status_code == 200 and ok.json()["ok"] is True


def test_ingest_normalizes_nonstandard_date_time():
    s = PushedSource("apprise")
    d = s.ingest({"comname": "Blue Jay", "sciname": "Cyanocitta cristata",
                  "confidence": 0.8, "date": "08/31/2026", "time": "1:14 PM"})
    assert d.date == "2026-08-31" and d.time == "13:14:00"
    # And that normalized date groups under "today" for the collage path.
    top = s.top_species_today(on_date=__import__("datetime").date(2026, 8, 31))
    assert top and top[0]["common"] == "Blue Jay"


def test_webhook_rejects_foreign_origin(apprise_client):
    client, svc = apprise_client
    r = client.post("/api/ingest/apprise", json={"message": '{"comname":"Robin"}'},
                    headers={"Origin": "http://evil.example", "Host": "testserver"})
    assert r.status_code == 403
    assert svc.source.max_rowid() == 0


# -- source connection test (W-625) -----------------------------------------
class _StubSource:
    def __init__(self, avail=True, latest=None, raises=False):
        self._a, self._l, self._raises = avail, latest, raises

    def available(self):
        if self._raises:
            raise RuntimeError("boom")
        return self._a

    def latest(self, min_confidence=0.0):
        return self._l


def test_source_test_reachable_with_latest():
    from featherframe.app import _source_test
    from featherframe.sources.base import Detection
    det = Detection(rowid=1, date="2026-08-31", time="08:00:00",
                    common_name="American Robin", scientific_name="Turdus migratorius", confidence=0.9)
    out = _source_test(_StubSource(True, det), "birdweather")
    assert out["ok"] is True and "American Robin" in out["detail"]


def test_source_test_unreachable():
    from featherframe.app import _source_test
    out = _source_test(_StubSource(False), "birdweather")
    assert out["ok"] is False and "reachable" in out["detail"].lower()


def test_source_test_error_path():
    from featherframe.app import _source_test
    out = _source_test(_StubSource(raises=True), "birdweather")
    assert out["ok"] is False


def test_source_test_apprise_reports_count():
    from featherframe.app import _source_test

    class _Q:
        def max_rowid(self):
            return 7
    out = _source_test(_Q(), "apprise")
    assert out["ok"] is True and "7 detection" in out["detail"]


def test_source_test_endpoint_uses_typed_values(apprise_client):
    # Posted connection fields are tested, not the saved config.
    client, _ = apprise_client
    r = client.post("/api/source/test",
                    data={"backend": "custom", "birdnet_db_path": "/nope/missing.db"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "reachable" in body["detail"].lower()


def test_source_test_endpoint_refuses_cross_origin(apprise_client):
    client, _ = apprise_client
    r = client.post("/api/source/test", data={"backend": "custom"},
                    headers={"Origin": "http://evil.example", "Host": "testserver"})
    assert r.status_code == 403


# -- BirdNET-Go's webhook (W-865) --------------------------------------------
def _go(common="Northern Cardinal", sci="Cardinalis cardinalis", conf=0.91, note=4711,
        first_days=12, date="2026-09-24", time="08:14:00", kind="detection"):
    """BirdNET-Go's default webhook body for a detection, as push_webhook.go
    marshals it (metadata from alerting/init.go's enrichFromEventProps)."""
    return {"id": "a1b2", "type": kind, "priority": "high", "title": f"{common} detected",
            "message": "", "component": "detection", "timestamp": "2026-09-24T08:14:03-04:00",
            "metadata": {"species": common, "scientific_name": sci, "confidence": conf,
                         "location": "Backyard", "is_new_species": first_days == 0,
                         "days_since_first_seen": first_days, "note_id": note,
                         "bg_detection_date": date, "bg_detection_time": time,
                         "bg_confidence_percent": str(round(conf * 100))}}


def test_go_detection_is_queued():
    s = PushedSource("birdnet_go")
    d = s.ingest(_go())
    assert (d.common_name, d.scientific_name, d.confidence) == (
        "Northern Cardinal", "Cardinalis cardinalis", 0.91)
    assert (d.date, d.time) == ("2026-09-24", "08:14:00")
    assert s.max_rowid() == 1


def test_go_twelve_hour_time_is_normalized():
    d = PushedSource("birdnet_go").ingest(_go(time="1:14:05 PM"))
    assert d.time == "13:14:05"


def test_go_same_detection_twice_is_one():
    # The built-in "New species" rule pushes the detection "Detection Occurred"
    # already pushed: one detection, one note id.
    s = PushedSource("birdnet_go")
    s.ingest(_go(note=9, first_days=0))
    s.ingest(_go(note=9, first_days=0))
    assert s.max_rowid() == 1
    s.ingest(_go(note=10))
    assert s.max_rowid() == 2


def test_go_warning_on_the_channel_is_not_a_bird():
    s = PushedSource("birdnet_go")
    assert s.ingest({"type": "warning", "title": "Stream disconnected",
                     "metadata": {"species": "Northern Cardinal"}}) is None
    assert s.ingest(_go(kind="error")) is None
    assert s.ingest({"type": "detection"}) is None
    assert s.ingest("not a dict") is None
    assert s.max_rowid() == 0


def test_go_test_button_is_never_a_bird():
    s = PushedSource("birdnet_go")
    assert s.ingest(_go("Test Bird Species", "Testus birdicus", note=1, first_days=0)) is None
    assert s.max_rowid() == 0 and s.test_at


def test_go_first_seen_from_the_push():
    from datetime import date
    s = PushedSource("birdnet_go")
    s.ingest(_go(first_days=12))
    assert s.first_seen_date("Cardinalis cardinalis") == "2026-09-12"
    assert s.heard_before("Cardinalis cardinalis", date(2026, 9, 24)) is True
    s.ingest(_go("Snowy Owl", "Bubo scandiacus", note=5, first_days=0))
    assert s.heard_before("Bubo scandiacus", date(2026, 9, 24)) is False
    assert s.all_time_species_count() == 2
    # BirdNET-Pi's body says nothing of history: unknown, never guessed.
    assert PushedSource("apprise").first_seen_date("Cardinalis cardinalis") is None


def test_go_defers_to_birdnet_go_threshold():
    # BirdNET-Go pushes only what passed its own threshold, which always wins.
    s = PushedSource("birdnet_go")
    s.ingest(_go(conf=0.55))
    assert len(s.new_since(0, min_confidence=0.7)) == 1
    from datetime import date
    assert s.top_species_today(date(2026, 9, 24), min_confidence=0.7)[0]["count"] == 1


def test_day_counts_outlast_the_window(monkeypatch):
    # A busy day has more detections than the queue keeps; the collage's
    # counts still see all of them.
    import featherframe.sources.pushed as pushed
    from datetime import date
    monkeypatch.setattr(pushed, "_MAX_ITEMS", 5)
    s = PushedSource("birdnet_go")
    for n in range(12):
        s.ingest(_go(note=n, conf=0.9 if n % 2 else 0.5))
    assert len(s.latest_many(limit=50)) == 5
    top = s.top_species_today(date(2026, 9, 24))
    assert top == [{"common": "Northern Cardinal", "scientific": "Cardinalis cardinalis", "count": 12}]
    apprise = PushedSource("apprise")
    for n in range(12):
        apprise.ingest(_det(conf=0.9 if n % 2 else 0.5))
    assert apprise.top_species_today(date(2026, 8, 31), min_confidence=0.7)[0]["count"] == 6


def test_go_state_survives_a_restart(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    PushedSource("birdnet_go", db=db).ingest(_go(note=3))
    again = PushedSource("birdnet_go", db=db)
    again.ingest(_go(note=3))                     # still the same detection
    assert again.max_rowid() == 1
    assert again.first_seen_date("Cardinalis cardinalis") == "2026-09-12"
    # Each detector keeps its own queue.
    assert PushedSource("apprise", db=db).max_rowid() == 0


def test_old_apprise_queue_gets_its_day_counts(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    db.set("apprise_queue", {"counter": 2, "items": [
        {"id": 1, "date": "2026-08-31", "time": "08:00:00", "common": "Blue Jay",
         "scientific": "Cyanocitta cristata", "confidence": 0.9},
        {"id": 2, "date": "2026-08-31", "time": "08:05:00", "common": "Blue Jay",
         "scientific": "Cyanocitta cristata", "confidence": 0.8}]})
    from datetime import date
    top = PushedSource("apprise", db=db).top_species_today(date(2026, 8, 31))
    assert top[0]["count"] == 2


@pytest.fixture
def go_client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    save_config(svc.db, Config(detection_backend="birdnet_go", ingest_token="tok"))
    svc.reload_config()
    app.state.service = svc
    return TestClient(app), svc


def test_go_webhook(go_client):
    client, svc = go_client
    assert client.post("/api/ingest/birdnet-go/wrong", json=_go()).status_code == 403
    r = client.post("/api/ingest/birdnet-go/tok", json=_go())
    assert r.status_code == 200 and r.json()["ok"] is True
    assert svc.source.latest_many()[0].scientific_name == "Cardinalis cardinalis"
    # The page's other detector's path is refused: it is not the source.
    assert client.post("/api/ingest/apprise/tok", json={"comname": "Robin"}).status_code == 409
    assert client.post("/api/ingest/nonsense/tok", json={}).status_code == 404


def test_go_status_says_a_test_arrived(go_client):
    client, _ = go_client
    client.post("/api/ingest/birdnet-go/tok",
                json=_go("Test Bird Species", "Testus birdicus", note=1, first_days=0))
    body = client.post("/api/source/test", data={"backend": "birdnet_go"}).json()
    assert body["ok"] is True
    assert "0 detection" in body["detail"] and "Test received" in body["detail"]


def test_birdnet_go_rule_file_is_what_birdnet_go_imports():
    """The page's Download rule (W-865): BirdNET-Go's Rules → Import takes
    {version, rules} as its own Export writes it. The rule must push every
    detection with no cooldown, or BirdNET-Go sends one per five minutes."""
    import json
    from featherframe import paths
    data = json.loads((paths.static_dir() / "featherframe-birdnet-go-rule.json").read_text())
    assert data["version"] == 1 and len(data["rules"]) == 1
    rule = data["rules"][0]
    assert (rule["object_type"], rule["trigger_type"], rule["event_name"]) == (
        "detection", "event", "detection.occurred")
    assert rule["cooldown_sec"] == 0 and rule["enabled"] is True
    assert [a["target"] for a in rule["actions"]] == ["push"]
