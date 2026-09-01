"""The Apprise push source: ingest → queue → poll, DB persistence, and the
webhook that BirdNET-Pi posts to."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, save_config
from featherframe.db import Database
from featherframe.sources.apprise_push import AppriseSource


def _det(common="Northern Cardinal", sci="Cardinalis cardinalis", conf=0.9):
    return {"comname": common, "sciname": sci, "confidence": conf,
            "date": "2026-08-31", "time": "08:14:00"}


def test_ingest_and_cursor():
    s = AppriseSource()
    assert s.max_rowid() == 0
    d = s.ingest(_det())
    assert d is not None and d.common_name == "Northern Cardinal"
    assert s.max_rowid() == 1
    assert [x.common_name for x in s.new_since(0)] == ["Northern Cardinal"]
    assert s.new_since(1) == []  # nothing past the cursor


def test_rejects_speciesless_payload():
    s = AppriseSource()
    assert s.ingest({"confidence": 0.9}) is None
    assert s.max_rowid() == 0


def test_confidence_filter():
    s = AppriseSource()
    s.ingest(_det(conf=0.1))
    assert s.new_since(0, min_confidence=0.5) == []
    assert len(s.new_since(0, min_confidence=0.0)) == 1


def test_top_species_today_counts():
    s = AppriseSource()
    for _ in range(3):
        s.ingest(_det("Blue Jay", "Cyanocitta cristata"))
    s.ingest(_det("American Robin", "Turdus migratorius"))
    top = s.top_species_today(on_date=__import__("datetime").date(2026, 8, 31))
    assert top[0]["common"] == "Blue Jay" and top[0]["count"] == 3


def test_persists_counter_and_items_across_restart(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    s1 = AppriseSource(db=db)
    s1.ingest(_det("Blue Jay", "Cyanocitta cristata"))
    # A fresh instance (restart) resumes the counter and items from the store,
    # so a freshly-pushed detection still lands above the persisted cursor.
    s2 = AppriseSource(db=db)
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
    # apprise_token="" opts into the accept-any-LAN-post path these tests probe;
    # the bad-token test sets its own secret. (Fresh configs now get a random
    # default token — see test_config.)
    save_config(svc.db, Config(detection_backend="apprise", apprise_token=""))
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
    save_config(svc.db, Config(detection_backend="apprise", apprise_token="secret"))
    svc.reload_config()
    assert client.post("/api/ingest/apprise/wrong", json={"message": "{}"}).status_code == 403
    ok = client.post("/api/ingest/apprise/secret", json={"message": '{"comname":"Robin"}'})
    assert ok.status_code == 200 and ok.json()["ok"] is True


def test_ingest_normalizes_nonstandard_date_time():
    s = AppriseSource()
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
    out = _source_test(_StubSource(True, det), "birdnet_go")
    assert out["ok"] is True and "American Robin" in out["detail"]


def test_source_test_unreachable():
    from featherframe.app import _source_test
    out = _source_test(_StubSource(False), "birdnet_go")
    assert out["ok"] is False and "reachable" in out["detail"].lower()


def test_source_test_error_path():
    from featherframe.app import _source_test
    out = _source_test(_StubSource(raises=True), "birdnet_go")
    assert out["ok"] is False


def test_source_test_apprise_reports_count():
    from featherframe.app import _source_test

    class _Q:
        def max_rowid(self):
            return 7
    out = _source_test(_Q(), "apprise")
    assert out["ok"] is True and "7 detection" in out["detail"]
