"""Viewers (W-822): screens that are not the frame. A TRMNL, an e-reader
running one of TRMNL's clients, a tablet on the kiosk page. They show what the
frame shows, so all a viewer has is how the picture sits on *its* screen: a
size, a depth, a rotation, a name. What is shown (source, mode, quiet hours,
blocklist) is the server's and is not here.

A record keeps what the device reported apart from what the owner set, so a
check-in never undoes a choice made on the page.

The rows live in the one frame registry (W-833): a viewer is a frame fed over
HTTP instead of over the kit protocol, and `kind` here is that row's
`transport`. This module is what knows how a viewer's picture is sized, turned
and dithered; it no longer owns a store.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from typing import Any, Optional

from . import frames
from .render.pipeline import VIEW_FORMATS, View

log = logging.getLogger("featherframe.viewers")

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
_OWNER_FIELDS = ("name", "width", "height", "fmt", "rotation", "dark_quiet", "shows")
SHOWS = ("plates", "collage")   # else: whatever the frame shows

# The kiosk page (W-825) on a lit screen. It asks often because asking is
# free on mains power, and a tablet should follow the wall within moments.
PAGE_POLL_SECONDS = 20
# A page is never drawn larger than this on its long side: the sheet is 1872
# tall, so more is only upscaling, paid for in render time and megabytes.
PAGE_MAX_SIDE = 2048


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
    page = frames.transport_of(record) == "page"
    fmt = own.get("fmt")
    if fmt not in VIEW_FORMATS:
        if page:
            fmt = "color"   # a lit colour screen; "paper" on the page is gray256
        elif not (rep.get("width") and rep.get("height")):
            # Only TRMNL's firmware reports a size. A script client (Kobo,
            # Kindle) paints with fbink/eips, which want the smooth page even
            # once the owner has told us how big the screen is.
            fmt = "gray256"
        elif str(rep.get("model") or "").lower() in _GRAY16_MODELS or (width, height) in _GRAY16_SIZES:
            fmt = "gray16"
        else:
            fmt = "gray2"
    rotation = own.get("rotation")
    if rotation not in (0, 90, 180, 270):
        # As panels._rotations: a landscape canvas hangs portrait. The plate is
        # a portrait sheet; upright on a landscape screen is a postage stamp.
        # A page is upright in whatever the browser gives it: a tablet turns
        # its own picture when it is turned.
        rotation = 0 if page else (90 if width > height else 0)
    return View(int(width), int(height), fmt, rotation)


def shows_of(record: dict) -> Optional[str]:
    """"plates" | "collage", or None: whatever the frame shows."""
    shows = (record.get("set") or {}).get("shows")
    return shows if shows in SHOWS else None


def dark_in_quiet_hours(record: dict) -> bool:
    """A lit screen goes black in quiet hours unless the owner says otherwise:
    paper can sit in a dark room showing a plate, a tablet glows."""
    return (record.get("set") or {}).get("dark_quiet", True) is not False


def page_size(width, height) -> Optional[tuple[int, int]]:
    """A browser's device pixels, scaled down to PAGE_MAX_SIDE on the long side."""
    w, h = _int(width, 64, 16384), _int(height, 64, 16384)
    if w is None or h is None:
        return None
    scale = min(1.0, PAGE_MAX_SIDE / max(w, h))
    return max(64, round(w * scale)), max(64, round(h * scale))


_KINDS = ("trmnl", "page")


def _out(row: dict) -> dict:
    """A registry row as the viewer code reads it: a copy carrying `kind`, so a
    caller that holds on to it cannot reach back into the store."""
    return {**row, "kind": frames.transport_of(row)}


def _new(viewer_id: str, kind: str, stamp: str) -> dict:
    """A viewer nobody has seen before. Viewers are never parked: pointing a
    screen at the server is the whole of the approval."""
    row = frames.new_row(viewer_id, kind if kind in _KINDS else "trmnl", stamp,
                         status=frames.ON)
    row["token"] = secrets.token_hex(16)
    return row


class Viewers:
    """The viewers, kept in the one frame registry (W-833). Everything here is
    scoped to the viewer transports: a kit on the same server is not a viewer
    and must never be listed, renamed or pruned as one."""

    def __init__(self, registry: "frames.FrameRegistry") -> None:
        self.registry = registry

    def all(self) -> dict[str, dict]:
        return {r["id"]: _out(r) for r in self.registry.by_transport(*_KINDS)}

    def get(self, viewer_id: str) -> Optional[dict]:
        row = self.registry.get(viewer_id)
        return _out(row) if row is not None and frames.transport_of(row) in _KINDS else None

    def checkin(self, viewer_id: str, now: datetime, kind: str = "trmnl",
                reported: Optional[dict[str, Any]] = None, ip: Optional[str] = None) -> dict:
        """Record that a viewer asked, and what it said about itself. Absent
        facts leave the last report standing."""
        stamp = now.isoformat(timespec="seconds")
        with self.registry.mutate() as rows:
            row = rows.get(viewer_id)
            if row is not None and frames.transport_of(row) not in _KINDS:
                # A kit already holds this id. It is being served; something on
                # the LAN claiming its MAC must not take its seat. Serve the
                # picture, record nothing.
                rows.unchanged()
                log.warning("viewer %s has the same id as a frame; not recorded", viewer_id)
                return _out(_new(viewer_id, kind, stamp))
            if row is None:
                row = rows[viewer_id] = _new(viewer_id, kind, stamp)
            row["reported"] = {**frames.reported_of(row),
                               **{k: v for k, v in (reported or {}).items() if v is not None}}
            row["last_seen"] = stamp
            if ip:
                row["ip"] = ip
            # The LAN is untrusted: junk IDs must not grow the row forever. Only
            # viewers are ever dropped — a kit is answered for, not aged out.
            mine = [k for k, r in rows.items() if frames.transport_of(r) in _KINDS]
            if len(mine) > MAX_VIEWERS:
                mine.sort(key=lambda k: rows[k].get("last_seen") or "")
                for stale in mine[:len(mine) - MAX_VIEWERS]:
                    rows.pop(stale, None)
        return _out(row)

    def update(self, viewer_id: str, fields: dict[str, Any]) -> Optional[dict]:
        """The owner's choices. A blank or invalid value clears that choice
        (back to the report / the default)."""
        with self.registry.mutate() as rows:
            row = rows.get(viewer_id)
            if row is None or frames.transport_of(row) not in _KINDS:
                rows.unchanged()
                return None
            own = dict(frames.settings_of(row))
            for key in _OWNER_FIELDS:
                if key not in fields:
                    continue
                raw = fields[key]
                if key == "name":
                    value = str(raw or "").strip()[:60] or None
                elif key == "fmt":
                    value = raw if raw in VIEW_FORMATS else None
                elif key == "shows":
                    value = raw if raw in SHOWS else None
                elif key == "dark_quiet":
                    value = None if raw in (None, "") else bool(raw) and str(raw).lower() not in ("0", "false", "off")
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
        return _out(row)

    def forget(self, viewer_id: str) -> bool:
        if self.get(viewer_id) is None:
            return False
        return self.registry.forget(viewer_id)


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
    kind = frames.transport_of(row)
    return {"id": row["id"], "kind": kind, "name": (row.get("set") or {}).get("name"),
            "reported": row.get("reported") or {}, "set": row.get("set") or {},
            "view": {"width": view.width, "height": view.height, "format": view.fmt,
                     "rotation": view.rotation},
            "shows": shows_of(row),
            "dark_quiet": dark_in_quiet_hours(row) if kind == "page" else None,
            "first_seen": row.get("first_seen"), "last_seen": row.get("last_seen"),
            "ip": row.get("ip")}


# -- the page's Viewers card (W-826) ---------------------------------------------
# `Model` header values (TRMNL firmware's platformio.ini) as an owner knows them.
_MODEL_NAMES = {"x": "TRMNL X", "og": "TRMNL", "og_4clr": "TRMNL (color)",
                "reterminal_e1001": "reTerminal E1001", "reterminal_e1002": "reTerminal E1002",
                "reterminal_e1003": "reTerminal E1003", "m5_papers3": "M5PaperS3",
                "xteink_x4": "Xteink X4", "seeed_sticky": "Seeed Sticky"}
_DEPTH_NAMES = {"gray16": "16 grays", "gray2": "4 grays", "mono": "black and white",
                "gray256": "grayscale", "color": "color"}


def card_row(row: dict, ago) -> dict:
    """One viewer as the page shows it. `ago(iso) -> "7 min ago"`."""
    rep, own = row.get("reported") or {}, row.get("set") or {}
    view = view_of(row)
    page = frames.transport_of(row) == "page"
    model = str(rep.get("model") or "")
    what = model if page else _MODEL_NAMES.get(model.lower(), model)
    what = what or ("Browser" if page else "TRMNL client")
    pct, volts = rep.get("battery_percent"), rep.get("battery_volts")
    battery = f"{pct}%" if pct is not None else (f"{volts:.2f} V" if volts else None)
    return {
        "id": row["id"], "page": page, "name": own.get("name") or "", "title": own.get("name") or what,
        "what": what if own.get("name") else "",
        "size": f"{view.width}×{view.height}", "depth": _DEPTH_NAMES.get(view.fmt, view.fmt),
        "last_seen": ago(row.get("last_seen")), "battery": None if page else battery,
        "ip": row.get("ip"), "rotation": view.rotation, "landscape": view.width > view.height,
        "fmt": view.fmt, "dark_quiet": dark_in_quiet_hours(row), "shows": shows_of(row) or "",
        "needs_size": frames.capabilities(row)["needs_size"],
        "width": own.get("width") or "", "height": own.get("height") or "",
    }
