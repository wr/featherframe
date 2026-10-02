"""The webapp's side of the spend guards (W-938)."""
from __future__ import annotations

from datetime import datetime

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, save_config
from featherframe.db import Database


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.service import FeatherframeService
    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_api_key="sk-proj-verysecretkey1234"))
    s = FeatherframeService(db)
    s._clock = lambda: datetime(2026, 10, 14, 12, 0)
    return s


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False)


ORIGIN = {"Origin": "http://testserver"}


def test_settings_save_the_limit_and_the_switches(client, svc):
    r = client.post("/settings", data={"section": "imagegen", "ai_monthly_limit_usd": "25",
                                       "imagegen_enabled": ["0"]}, headers=ORIGIN)
    assert r.status_code in (200, 303)
    assert svc.config.ai_monthly_limit_usd == 25 and svc.config.imagegen_enabled is False
    client.post("/settings", data={"illustrations_generated": ["0"]}, headers=ORIGIN)
    assert svc.config.illustrations_generated is False
    assert svc.config.ai_monthly_limit_usd == 25          # a switch keeps the rest


def test_resume(client, svc):
    svc.db.set("ai_pause", {"at": 1.0, "count": 6})
    r = client.post("/api/ai/resume", headers=ORIGIN)
    assert r.json() == {"ok": True}
    assert svc.spend_gate.summary()["paused"] is None


@pytest.mark.parametrize("state, message", [
    ("off", "AI image generation is off."),
    ("paused", "AI generation is paused."),
    ("limit", "This month's AI limit is reached."),
])
def test_regenerate_says_why_it_is_refused(client, svc, monkeypatch, state, message):
    monkeypatch.setattr(svc, "ai_refusal", lambda: state)
    monkeypatch.setattr(svc, "generated_listing", lambda: [{"slug": "tyto-alba"}])
    r = client.post("/api/generated/regenerate", data={"slug": "tyto-alba"}, headers=ORIGIN)
    assert r.json() == {"ok": False, "error": message}


def test_a_collage_repaint_says_why_it_is_refused(client, svc, monkeypatch):
    monkeypatch.setattr(svc, "ai_refusal", lambda: "limit")
    r = client.post("/api/collage/now", data={"repaint": "1"}, headers=ORIGIN)
    assert r.status_code == 409
    assert r.json() == {"ok": False, "error": "this month's AI limit is reached"}
