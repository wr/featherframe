"""The page's optional password (W-773): off by default, switched on from the
settings form, signed in to on a page of its own, and never asked of a screen."""
from __future__ import annotations

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


def _settings(client, **extra):
    form = {"detection_backend": "custom", "quiet_hours_mode": "off", **extra}
    return client.post("/settings", data=form, follow_redirects=False)


def _sign_in(client, password, email=""):
    return client.post("/login", data={"email": email, "password": password, "next": "/"},
                       follow_redirects=False)


def test_off_by_default(client, svc):
    assert not svc.password.on
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="pw-on"' in r.text and 'name="page_password_on" >' in r.text  # unchecked
    assert "General settings" in r.text
    assert client.get("/api/status").status_code == 200
    # No sign-in page when there is nothing to sign in to.
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_switch_on_without_a_password_stays_off(client, svc):
    assert _settings(client, page_password_on="on").status_code == 303
    assert not svc.password.on


def test_setting_it_keeps_this_browser_signed_in(client, svc):
    r = _settings(client, page_password_on="on", page_password="wren",
                  owner_email="Owner@Example.com")
    assert svc.password.on and svc.config.owner_email == "owner@example.com"
    assert auth.COOKIE in r.cookies or client.cookies.get(auth.COOKIE)
    assert client.get("/").status_code == 200


def test_on_asks_for_it_everywhere_but_screens(client, svc):
    _settings(client, page_password_on="on", page_password="wren", owner_email="o@example.com")
    client.cookies.clear()
    # The hash is kept, never the password, and not in the config.
    assert "wren" not in str(svc.db.get(auth.KV_KEY))

    r = client.get("/api/history", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fapi%2Fhistory"
    assert client.get("/", headers={"accept": "text/html"},
                      follow_redirects=False).headers["location"] == "/login"
    for path in ("/api/status", "/api/history", "/api/frames/x/preview.png"):
        assert client.get(path).status_code == 401, path
    # A POST without it changes nothing.
    assert _settings(client).status_code == 401
    assert svc.password.on

    # The sign-in page offers the owner's email for a password manager.
    page = client.get("/login")
    assert page.status_code == 200
    assert 'autocomplete="username" value="o@example.com"' in page.text
    assert 'autocomplete="current-password"' in page.text

    assert _sign_in(client, "nope", "o@example.com").status_code == 401
    assert _sign_in(client, "wren", "someone@else.com").status_code == 401
    r = _sign_in(client, "wren", "O@example.com")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/api/status").status_code == 200
    assert "wren" not in client.get("/api/status").text

    # Screens never sign in.
    client.cookies.clear()
    for path in ("/api/frame", "/api/firmware", "/api/setup", "/api/display",
                 "/view", "/view.webmanifest", "/api/view/state", "/favicon.ico"):
        assert client.get(path).status_code not in (401, 303), path
    assert client.post("/api/ingest/apprise/nope").status_code != 401


def test_sign_out(client, svc):
    _settings(client, page_password_on="on", page_password="wren")
    assert "Sign out" in client.get("/").text
    r = client.post("/logout", follow_redirects=False)
    assert r.headers["location"] == "/login"
    client.cookies.clear()
    assert client.get("/api/status").status_code == 401


def test_next_stays_on_this_server():
    assert auth.safe_next("/api/history") == "/api/history"
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", None, ""):
        assert auth.safe_next(bad) == "/"


def test_blank_keeps_it_new_one_signs_out_off_clears(client, svc):
    _settings(client, page_password_on="on", page_password="wren")
    old_cookie = client.cookies.get(auth.COOKIE)
    # Saving other settings with the field blank keeps the password.
    _settings(client, page_password_on="on")
    assert svc.password.on and client.get("/api/status").status_code == 200
    # A new one replaces it: this browser stays in, any other is signed out.
    _settings(client, page_password_on="on", page_password="jay")
    assert client.get("/api/status").status_code == 200
    assert not svc.password.allows(old_cookie)
    client.cookies.clear()
    assert _sign_in(client, "wren").status_code == 401
    assert _sign_in(client, "jay").status_code == 303
    # Switched off, it's gone.
    _settings(client)
    assert not svc.password.on
    client.cookies.clear()
    assert client.get("/api/status").status_code == 200


def test_wrong_guesses_are_throttled(client, svc):
    svc.password.set("wren")
    for _ in range(10):
        assert _sign_in(client, "nope").status_code == 401
    assert _sign_in(client, "wren").status_code == 429


def test_sessions_expire_and_survive_a_restart(svc):
    svc.password.set("wren")
    cookie = svc.password.new_session(now=1000.0)
    again = auth.PasswordGate(svc.db)
    assert again.allows(cookie, now=2000.0)
    assert not again.allows(cookie, now=1000.0 + auth.SESSION_DAYS * 86400 + 1)
    assert not again.allows("garbage")
    assert not again.allows(None)


def test_hosted_page_never_asks_and_shows_the_account_email(client, svc):
    svc.password.set("wren")
    from featherframe.app import app
    app.state.hosted = type("Link", (), {"settle": lambda *a: None})()
    try:
        assert client.get("/api/status").status_code == 200
        r = client.get("/", headers={"x-ff-account-email": "me@example.com"})
        assert 'value="me@example.com"' in r.text
        assert 'id="pw-on"' not in r.text
        # The account's email is the Worker's to change, not this form's.
        _settings(client, owner_email="other@example.com")
        assert svc.config.owner_email == ""
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
