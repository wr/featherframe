"""The owner's AI keys, and the way a model's request reaches its vendor (W-937).

Self-hosted, a key is the owner's, in the owner's own DB, and a model sends it
to the vendor itself. On Featherframe Cloud the household's front door holds
the key and this server never does: the DB keeps a stand-in,
``held:<mask>:<fingerprint>``, and a model sends its request to the vendor's
own path on ``<vendor>.vendor.internal``. That host is the front door's gate
(hosted/src/gate.ts): it adds the key on the way out, only for the paths
Featherframe calls, and only for a paid call the spend gate has open.

A key reaches the front door once, when the owner saves it, and an older
household's key moves there at a start (`FeatherframeService.hold_keys`).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

import requests

from . import spend

log = logging.getLogger(__name__)

HELD = "held:"
#: Where a held key's requests go: the front door's gate, intercepted on the
#: Container's own network (Cloudflare's outbound interception), never a
#: real host.
GATE_DOMAIN = "vendor.internal"
VENDORS = {
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "anthropic": "https://api.anthropic.com",
    "replicate": "https://api.replicate.com",
}
#: Each key and the config field that keeps it (or its stand-in).
SLOTS = {"image": "imagegen_api_key", "text": "imagegen_text_key"}
#: A free request each vendor answers with the key alone: a key moves to the
#: front door only once the vendor answers it the same way through the gate.
PROBES = {"openai": "/v1/models", "gemini": "/v1beta/models",
          "anthropic": "/v1/models", "replicate": "/v1/account"}
PROBE_TIMEOUT_S = 15


def is_held(value: str) -> bool:
    return str(value or "").startswith(HELD)


def mask(value: str) -> str:
    """The key as the page shows it: its first and last few characters."""
    value = str(value or "")
    if is_held(value):
        return value[len(HELD):].rsplit(":", 1)[0]
    if len(value) > 12:
        return f"{value[:5]}…{value[-4:]}"
    return f"…{value[-4:]}" if value else ""


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def key_id(value: str) -> str:
    """The same for a key and its stand-in, and for nothing else: moving a key
    to the front door is not a new key (no cooldowns cleared, no collage
    bought again)."""
    value = str(value or "")
    if not value:
        return ""
    return value.rsplit(":", 1)[-1] if is_held(value) else fingerprint(value)


def stand_in(key: str) -> str:
    return f"{HELD}{mask(key)}:{fingerprint(key)}"


def auth_headers(vendor: str, key: str) -> dict:
    """How each vendor is handed a key (the gate does the same with a held one)."""
    if vendor == "openai":
        return {"Authorization": f"Bearer {key}"}
    if vendor == "gemini":
        return {"x-goog-api-key": key}
    if vendor == "anthropic":
        return {"x-api-key": key}
    if vendor == "replicate":
        return {"Authorization": f"Token {key}"}
    raise ValueError(f"unknown vendor {vendor!r}")


class Vendor:
    """Where one model's requests go and how they are signed: straight to the
    vendor with the key, or, with a held key, to the front door's gate, which
    is told which of the household's keys to add (`slot`) and which purchase
    the call belongs to."""

    def __init__(self, name: str, key: str, slot: str) -> None:
        self.name = name
        self.key = str(key or "")
        self.slot = slot

    @property
    def held(self) -> bool:
        return is_held(self.key)

    @property
    def origin(self) -> str:
        return f"http://{self.name}.{GATE_DOMAIN}" if self.held else VENDORS[self.name]

    def url(self, path: str) -> str:
        return self.origin + path

    def rebase(self, url: str) -> str:
        """A URL the vendor handed back (Replicate's prediction): through the
        gate too, when the key is held."""
        real = VENDORS[self.name]
        if self.held and url.startswith(real + "/"):
            return self.origin + url[len(real):]
        return url

    def headers(self) -> dict:
        if not self.held:
            return auth_headers(self.name, self.key)
        out = {"X-FF-Key": self.slot}
        purchase = spend.active()
        if purchase is not None:
            out["X-FF-Purchase"] = purchase.record.id
        return out


def slot_vendor(config, slot: str) -> Optional[str]:
    """The vendor a key is used with now, or None (a self-hosted model, or a
    text key the text model does not follow)."""
    if slot == "image":
        p = getattr(config, "imagegen_provider", "")
        return p if p in ("openai", "gemini", "replicate") else None
    p = getattr(config, "imagegen_text_provider", "")
    return p if p in ("openai", "gemini", "anthropic") else None


def gate_agrees(vendor: str, slot: str, key: str, http=requests) -> bool:
    """Whether the vendor answers a held key through the front door's gate as
    it answers the key itself. A key it rejects moves too: the gate is not
    what refused it."""
    path = PROBES[vendor]
    extra = {"anthropic-version": "2023-06-01"} if vendor == "anthropic" else {}
    try:
        direct = http.get(VENDORS[vendor] + path, headers={**auth_headers(vendor, key), **extra},
                          timeout=PROBE_TIMEOUT_S)
        via = http.get(Vendor(vendor, HELD, slot).url(path),
                       headers={"X-FF-Key": slot, **extra}, timeout=PROBE_TIMEOUT_S)
    except requests.RequestException as exc:
        log.warning("keys: %s through the front door: %s", vendor, exc)
        return False
    if via.status_code != direct.status_code or via.headers.get("X-FF-Gate"):
        log.warning("keys: %s answers %s directly and %s through the front door (%s)",
                    vendor, direct.status_code, via.status_code, via.headers.get("X-FF-Gate") or "-")
        return False
    return True
