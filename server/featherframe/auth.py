"""The page's optional password (W-773).

Off unless the owner turns it on: a LAN box has no login by default. When one
is set, every route asks for a signed-in session except the ones screens use
— a kit, a TRMNL, the kiosk page — which carry no secrets and could not sign
in. Signing in is a page (`/login`: the owner's email and the password, which
a password manager fills) that sets a session cookie. A hosted household's
page has its own sign-in (the Worker), so this is never asked there.

The password is kept only as a salted PBKDF2 hash, in our kv store and not in
`Config`, so it never reaches `status()`, the page, or a hosted push. A
session is a signed expiry, bound to that hash's salt: a new password signs
every browser out.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Optional

KV_KEY = "page_password"
_SECRET_KEY = "page_session_secret"
_ITERATIONS = 200_000
COOKIE = "ff_session"
SESSION_DAYS = 30
# Wrong passwords before the sign-in page refuses for a while.
_FAILS_MAX = 10
_FAILS_WINDOW_S = 600

# What a screen asks for, and the sign-in itself. Exact paths, then prefixes.
_OPEN_PATHS = frozenset({
    "/login", "/logout",
    "/api/frame",            # kit
    "/api/firmware",         # kit OTA
    "/api/setup", "/api/display", "/api/log",   # TRMNL's protocol
    "/view", "/view.webmanifest", "/api/view/state",  # the kiosk page
    "/api/hosted/run",       # hosted only, where this gate is off anyway
    "/favicon.ico", "/fonts/script.ttf",
})
_OPEN_PREFIXES = (
    "/api/frame/",           # the push socket
    "/api/viewers/",         # a viewer's image
    "/api/ingest/apprise",   # BirdNET-Pi's and BirdNET-Go's webhooks,
    "/api/ingest/birdnet-go",  # which carry their own token
    "/static/",              # icons
)


def is_open(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


def hash_password(password: str) -> dict:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return {"salt": salt.hex(), "hash": digest.hex(), "iterations": _ITERATIONS}


def verify_password(stored: dict, password: str) -> bool:
    try:
        salt = bytes.fromhex(stored["salt"])
        want = bytes.fromhex(stored["hash"])
        n = int(stored.get("iterations") or _ITERATIONS)
    except (KeyError, TypeError, ValueError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
    return hmac.compare_digest(got, want)


def safe_next(target: Optional[str]) -> str:
    """Where to go after signing in: a path on this server, never elsewhere."""
    t = target or "/"
    return t if t.startswith("/") and not t.startswith("//") and "\\" not in t else "/"


class PasswordGate:
    def __init__(self, db) -> None:
        self._db = db
        self._lock = threading.Lock()
        stored = db.get(KV_KEY, None)
        self._stored: Optional[dict] = stored if isinstance(stored, dict) else None
        self._fails: list[float] = []

    @property
    def on(self) -> bool:
        return self._stored is not None

    def set(self, password: Optional[str]) -> None:
        """A new password, or None to turn it off. Either signs everyone out."""
        stored = hash_password(password) if password else None
        with self._lock:
            self._db.set(KV_KEY, stored)
            self._stored = stored

    def check(self, password: str) -> bool:
        """The password is right. Blocking (a hash): call it from a thread."""
        stored = self._stored
        return bool(stored) and verify_password(stored, password or "")

    # -- wrong guesses -----------------------------------------------------
    def throttled(self) -> bool:
        cutoff = time.monotonic() - _FAILS_WINDOW_S
        with self._lock:
            self._fails = [t for t in self._fails if t > cutoff]
            return len(self._fails) >= _FAILS_MAX

    def failed(self) -> None:
        with self._lock:
            self._fails.append(time.monotonic())

    # -- sessions ------------------------------------------------------------
    def _secret(self) -> bytes:
        s = self._db.get(_SECRET_KEY, None)
        if not isinstance(s, str) or len(s) < 32:
            s = secrets.token_hex(32)
            self._db.set(_SECRET_KEY, s)
        return bytes.fromhex(s)

    def _sign(self, expires: int, salt: str) -> str:
        return hmac.new(self._secret(), f"{expires}.{salt}".encode(), hashlib.sha256).hexdigest()

    def new_session(self, now: Optional[float] = None) -> str:
        """A cookie value for a browser that just signed in."""
        stored = self._stored or {}
        expires = int((now or time.time()) + SESSION_DAYS * 86400)
        return f"{expires}.{self._sign(expires, stored.get('salt', ''))}"

    def allows(self, cookie: Optional[str], now: Optional[float] = None) -> bool:
        """No password is set, or this cookie is a live session under it."""
        stored = self._stored
        if stored is None:
            return True
        try:
            exp_s, sig = (cookie or "").split(".", 1)
            expires = int(exp_s)
        except ValueError:
            return False
        if expires < (now or time.time()):
            return False
        return hmac.compare_digest(sig, self._sign(expires, stored.get("salt", "")))
