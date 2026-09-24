"""The page's optional password (W-773): off by default, switched on from the
settings form, asked by HTTP Basic for the page and never for a screen."""
from __future__ import annotations

import base64

import pytest
from starlette.testclient import TestClient

from featherframe import auth
from featherframe.render import pipeline
from tests._frames import add_kit


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.service import FeatherframeService
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    add_kit(service)
    pipeline.DITHER_OVERRIDE = "none"
    yield service


@pytest.fixture
def client(svc):
    from featherframe.app import app
    app.state.service = svc
    app.state.hosted = None
    return TestClient(app, raise_server_exceptions=False)


def _basic(password: str, user: str = "") -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _settings(client, **extra):
    form = {"detection_backend": "custom", "quiet_hours_mode": "off", **extra}
    return client.post("/settings", data=form, follow_redirects=False)


def test_off_by_default(client, svc):
    assert not svc.password.on
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="pw-on"' in r.text and 'name="page_password_on" >' in r.text  # unchecked
    assert client.get("/api/status").status_code == 200


def test_switch_on_without_a_password_stays_off(client, svc):
    assert _settings(client, page_password_on="on").status_code == 303
    assert not svc.password.on


def test_on_asks_for_it_everywhere_but_screens(client, svc):
    _settings(client, page_password_on="on", page_password="wren")
    assert svc.password.on
    # The hash is kept, never the password, and not in the config.
    stored = svc.db.get(auth.KV_KEY)
    assert "wren" not in str(stored)
    assert "wren" not in client.get("/api/status", headers=_basic("wren")).text

    for path in ("/", "/api/status", "/api/history", "/api/frames/x/preview.png"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"].startswith("Basic ")
    assert client.get("/", headers=_basic("nope")).status_code == 401
    assert client.get("/", headers=_basic("wren")).status_code == 200
    assert client.get("/", headers=_basic("wren", user="anyone")).status_code == 200
    # A POST without it changes nothing.
    assert _settings(client).status_code == 401
    assert svc.password.on

    # Screens never answer a prompt.
    for path in ("/api/frame", "/api/firmware", "/api/setup", "/api/display",
                 "/view", "/view.webmanifest", "/api/view/state", "/favicon.ico"):
        assert client.get(path).status_code != 401, path
    assert client.post("/api/ingest/apprise/nope").status_code != 401


def test_blank_keeps_it_and_switch_off_clears_it(client, svc):
    _settings(client, page_password_on="on", page_password="wren")
    auth_h = _basic("wren")
    # Saving other settings with the field blank keeps the password.
    client.post("/settings", data={"detection_backend": "custom", "quiet_hours_mode": "off",
                                   "page_password_on": "on"},
                headers=auth_h, follow_redirects=False)
    assert svc.password.on
    assert client.get("/", headers=auth_h).status_code == 200
    # A new one replaces it.
    client.post("/settings", data={"detection_backend": "custom", "quiet_hours_mode": "off",
                                   "page_password_on": "on", "page_password": "jay"},
                headers=auth_h, follow_redirects=False)
    assert client.get("/", headers=auth_h).status_code == 401
    assert client.get("/", headers=_basic("jay")).status_code == 200
    # Switched off, it's gone.
    client.post("/settings", data={"detection_backend": "custom", "quiet_hours_mode": "off"},
                headers=_basic("jay"), follow_redirects=False)
    assert not svc.password.on
    assert client.get("/").status_code == 200


def test_survives_a_restart(client, svc):
    _settings(client, page_password_on="on", page_password="wren")
    again = auth.PasswordGate(svc.db)
    assert again.on
    assert again.allows(_basic("wren")["Authorization"])
    assert not again.allows(_basic("nope")["Authorization"])
    assert not again.allows(None)
    assert not again.allows("Basic !!!")


def test_hosted_page_never_asks(client, svc):
    svc.password.set("wren")
    from featherframe.app import app
    app.state.hosted = object()
    try:
        r = client.get("/api/status")
        assert r.status_code == 200
    finally:
        app.state.hosted = None


def test_regenerate_is_rate_limited(svc, monkeypatch):
    from featherframe import service as service_mod
    monkeypatch.setattr(service_mod, "REGEN_PER_HOUR", 2)
    assert not svc.regen_limited()
    svc._regen_started = [service_mod.time.monotonic()] * 2
    assert svc.regen_limited()
    svc._regen_started = [service_mod.time.monotonic() - 3601] * 2
    assert not svc.regen_limited()
