"""The page's optional password (W-773).

Off unless the owner turns it on: a LAN box has no login by default. When
one is set, every route asks for it by HTTP Basic (any user name) except the
ones screens use — a kit, a TRMNL, the kiosk page — which carry no secrets and
could not answer a prompt. A hosted household's page has its own sign-in
(the Worker), so this is never asked there.

The password is kept only as a salted PBKDF2 hash, in our kv store and not in
`Config`, so it never reaches `status()`, the page, or a hosted push.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
from typing import Optional

KV_KEY = "page_password"
_ITERATIONS = 200_000
REALM = "Featherframe"

# What a screen asks for. Exact paths, then prefixes.
_OPEN_PATHS = frozenset({
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
    "/api/ingest/apprise",   # BirdNET-Pi's webhook, which has its own token
    "/static/",              # the kiosk page's icons
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


def _basic_password(header: Optional[str]) -> Optional[str]:
    """The password half of an `Authorization: Basic …` header."""
    if not header or not header[:6].lower() == "basic ":
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    _, sep, password = decoded.partition(":")
    return password if sep else None


def _seen(header: Optional[str]) -> str:
    return hashlib.sha256((header or "").encode()).hexdigest()


class PasswordGate:
    """The stored hash, and the headers already checked against it: a hash is
    ~0.1 s on a Pi, and the page polls, so each header is hashed once."""

    def __init__(self, db) -> None:
        self._db = db
        self._lock = threading.Lock()
        stored = db.get(KV_KEY, None)
        self._stored: Optional[dict] = stored if isinstance(stored, dict) else None
        self._ok: set[str] = set()

    @property
    def on(self) -> bool:
        return self._stored is not None

    def set(self, password: Optional[str]) -> None:
        """A new password, or None to turn it off."""
        stored = hash_password(password) if password else None
        with self._lock:
            self._db.set(KV_KEY, stored)
            self._stored = stored
            self._ok.clear()

    def allows(self, header: Optional[str]) -> bool:
        """True when no password is set or the header carries it. Blocking:
        call it from a thread."""
        with self._lock:
            stored = self._stored
            if stored is None:
                return True
            if _seen(header) in self._ok:
                return True
        password = _basic_password(header)
        if password is None or not verify_password(stored, password):
            return False
        with self._lock:
            if self._stored is stored:
                if len(self._ok) > 32:
                    self._ok.clear()
                self._ok.add(_seen(header))
        return True
