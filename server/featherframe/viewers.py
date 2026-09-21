"""Viewers (W-822): screens that are not the frame. A TRMNL, an e-reader
running one of TRMNL's clients, a tablet on the kiosk page. They show what the
frame shows, so all a viewer has is how the picture sits on *its* screen: a
size, a depth, a rotation, a name. What is shown (source, mode, quiet hours,
blocklist) is the server's and is not here.

A record keeps what the device reported apart from what the owner set, so a
check-in never undoes a choice made on the page.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any, Optional

from .render.pipeline import VIEW_FORMATS, View

# How often a viewer is told to come back (TRMNL's `refresh_rate`, seconds).
# Constants, not settings (W-821): a battery e-ink screen that asks four times
# an hour lasts months and is never more than a quarter hour behind the wall;
# in quiet hours the plate does not change, so it may as well sleep.
REFRESH_SECONDS = 900
QUIET_REFRESH_SECONDS = 3600

MAX_VIEWERS = 32   # the LAN is untrusted: junk IDs must not grow the row forever

# TRMNL firmware builds whose glass is 16-gray (FastEPD, 4 bpp); every other
# build of that firmware paints 2-bit gray at best and truncates anything
# deeper, so it gets a 4-level dither. `Model` header values, from the
# firmware's platformio.ini.
_GRAY16_MODELS = {"x", "reterminal_e1003", "m5_papers3", "lilygo_t5pro"}
_GRAY16_SIZES = {(1872, 1404), (1404, 1872)}   # the EE03's glass, whatever it is called

# A client that reports no size (TRMNL's Kobo and Kindle scripts send only an
# ID and a battery) gets a 3:4 e-reader page, smooth: fbink/eips scale and
# dither it themselves. The owner sets the real size on the page.
_DEFAULT_SIZE = (1072, 1448)

_ID_RE = re.compile(r"^[0-9A-Za-z:._-]{1,40}$")
_OWNER_FIELDS = ("name", "width", "height", "fmt", "rotation")


def clean_id(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value.upper() if _ID_RE.match(value) else None


def _int(value, lo: int, hi: int) -> Optional[int]:
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):   # "nan", "inf"
        return None
    return n if lo <= n <= hi else None


def _float(value, lo: float, hi: float) -> Optional[float]:
    try:
        n = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None   # NaN fails both comparisons


def view_of(record: dict) -> View:
    """The View a viewer is drawn for: the owner's choice, else the report,
    else the default for what it seems to be."""
    own, rep = record.get("set") or {}, record.get("reported") or {}
    width = own.get("width") or rep.get("width")
    height = own.get("height") or rep.get("height")
    sized = bool(width and height)
    if not sized:
        width, height = _DEFAULT_SIZE
    fmt = own.get("fmt")
    if fmt not in VIEW_FORMATS:
        if not sized:
            fmt = "gray256"
        elif str(rep.get("model") or "").lower() in _GRAY16_MODELS or (width, height) in _GRAY16_SIZES:
            fmt = "gray16"
        else:
            fmt = "gray2"
    rotation = own.get("rotation")
    if rotation not in (0, 90, 180, 270):
        # As panels._rotations: a landscape canvas hangs portrait. The plate is
        # a portrait sheet; upright on a landscape screen is a postage stamp.
        rotation = 90 if width > height else 0
    return View(int(width), int(height), fmt, rotation)


class Viewers:
    """The viewer rows, one JSON blob in our kv store (they are few)."""

    KEY = "viewers"

    def __init__(self, db) -> None:
        self.db = db

    def all(self) -> dict[str, dict]:
        rows = self.db.get(self.KEY)
        return rows if isinstance(rows, dict) else {}

    def get(self, viewer_id: str) -> Optional[dict]:
        return self.all().get(viewer_id)

    def checkin(self, viewer_id: str, now: datetime, kind: str = "trmnl",
                reported: Optional[dict[str, Any]] = None, ip: Optional[str] = None) -> dict:
        """Record that a viewer asked, and what it said about itself. Absent
        facts leave the last report standing."""
        rows = self.all()
        row = rows.get(viewer_id) or {"id": viewer_id, "kind": kind, "set": {}, "reported": {},
                                      "token": secrets.token_hex(16),
                                      "first_seen": now.isoformat(timespec="seconds")}
        row["reported"] = {**(row.get("reported") or {}),
                           **{k: v for k, v in (reported or {}).items() if v is not None}}
        row["last_seen"] = now.isoformat(timespec="seconds")
        if ip:
            row["ip"] = ip
        rows[viewer_id] = row
        if len(rows) > MAX_VIEWERS:
            for stale in sorted(rows, key=lambda k: rows[k].get("last_seen", ""))[:len(rows) - MAX_VIEWERS]:
                rows.pop(stale, None)
        self.db.set(self.KEY, rows)
        return row

    def update(self, viewer_id: str, fields: dict[str, Any]) -> Optional[dict]:
        """The owner's choices. A blank or invalid value clears that choice
        (back to the report / the default)."""
        rows = self.all()
        row = rows.get(viewer_id)
        if row is None:
            return None
        own = dict(row.get("set") or {})
        for key in _OWNER_FIELDS:
            if key not in fields:
                continue
            raw = fields[key]
            if key == "name":
                value = str(raw or "").strip()[:60] or None
            elif key == "fmt":
                value = raw if raw in VIEW_FORMATS else None
            elif key == "rotation":
                value = _int(raw, 0, 270)
                value = value if value in (0, 90, 180, 270) else None
            else:
                value = _int(raw, 64, 4096)
            if value is None:
                own.pop(key, None)
            else:
                own[key] = value
        row["set"] = own
        rows[viewer_id] = row
        self.db.set(self.KEY, rows)
        return row

    def forget(self, viewer_id: str) -> bool:
        rows = self.all()
        if rows.pop(viewer_id, None) is None:
            return False
        self.db.set(self.KEY, rows)
        return True


def trmnl_report(headers) -> dict[str, Any]:
    """What a TRMNL client says about itself (firmware request_headers.cpp).
    Untrusted: only plausible, finite values are kept."""
    return {
        "model": (headers.get("model") or "").strip()[:40] or None,
        "fw_version": (headers.get("fw-version") or "").strip()[:40] or None,
        "width": _int(headers.get("width"), 64, 4096),
        "height": _int(headers.get("height"), 64, 4096),
        "battery_volts": _float(headers.get("battery-voltage"), 0.0, 6.0),
        "battery_percent": _int(headers.get("percent-charged"), 0, 100),
        "rssi": _int(headers.get("rssi"), -120, 0),
    }


def public(row: dict) -> dict:
    """A row for the page and /api/viewers: never the token."""
    view = view_of(row)
    return {"id": row["id"], "kind": row.get("kind"), "name": (row.get("set") or {}).get("name"),
            "reported": row.get("reported") or {}, "set": row.get("set") or {},
            "view": {"width": view.width, "height": view.height, "format": view.fmt,
                     "rotation": view.rotation},
            "first_seen": row.get("first_seen"), "last_seen": row.get("last_seen"),
            "ip": row.get("ip")}
