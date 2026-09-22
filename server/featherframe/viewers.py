"""Viewers (W-822): the frames that are fed over plain HTTP. A TRMNL, an
e-reader running one of TRMNL's clients, a tablet on the kiosk page. They show
a picture like every other frame, so all a viewer has of its own is how that
picture sits on *its* screen: a size, a depth, a rotation, a name. What is
shown (the source, quiet hours, the blocklist) is the household's.

Their rows are in the one frame registry (`frames.py`) like every other
frame's, and the service does the reading and writing. This module is what
knows how a viewer's picture is sized, turned and dithered, and how a device's
own report is read.
"""
from __future__ import annotations

import re
import secrets
from typing import Any, Optional

from . import frames
from .render.pipeline import View

# How often a viewer is told to come back (TRMNL's `refresh_rate`, seconds).
# Constants, not settings (W-821): a battery e-ink screen that asks four times
# an hour lasts months and is never more than a quarter hour behind the wall;
# in quiet hours the plate does not change, so it may as well sleep.
REFRESH_SECONDS = 900
QUIET_REFRESH_SECONDS = 3600
# A screen waiting to be added comes back soon, so it picks the picture up
# moments after the owner says yes; one that was turned away asks rarely.
WAITING_REFRESH_SECONDS = 300
IGNORED_REFRESH_SECONDS = 3600

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
OWNER_FIELDS = ("name", "width", "height", "rotation", "shows")
SHOWS = ("plates", "collage")   # else: whatever the frame shows

# The transports a viewer is fed over: everything that is not a kit.
KINDS = ("trmnl", "page")

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
    """The View a viewer is drawn for: its size is the owner's choice, else the
    report, else the default for what it seems to be. How deep it is drawn is
    never the owner's: it follows what the DEVICE reported, and a lit screen is
    always colour."""
    own, rep = record.get("set") or {}, record.get("reported") or {}
    width = own.get("width") or rep.get("width")
    height = own.get("height") or rep.get("height")
    sized = bool(width and height)
    if not sized:
        width, height = _DEFAULT_SIZE
    page = frames.transport_of(record) == "page"
    if page:
        fmt = "color"       # a lit colour screen
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


def page_size(width, height) -> Optional[tuple[int, int]]:
    """A browser's device pixels, scaled down to PAGE_MAX_SIDE on the long side."""
    w, h = _int(width, 64, 16384), _int(height, 64, 16384)
    if w is None or h is None:
        return None
    scale = min(1.0, PAGE_MAX_SIDE / max(w, h))
    return max(64, round(w * scale)), max(64, round(h * scale))


def new_row(viewer_id: str, kind: str, stamp: str) -> dict:
    """A viewer nobody has seen before. It asks, like every other frame: a
    screen that found the server on the LAN is not the owner saying so, and it
    shows that it is waiting until they answer on the page."""
    row = frames.new_row(viewer_id, kind if kind in KINDS else "trmnl", stamp)
    row["token"] = secrets.token_hex(16)
    return row


def own_settings(own: dict, fields: dict[str, Any]) -> dict:
    """One viewer's `set` with the owner's choices applied. A blank or invalid
    value clears that choice (back to the report / the default)."""
    own = dict(own)
    for key in OWNER_FIELDS:
        if key not in fields:
            continue
        raw = fields[key]
        if key == "name":
            value = str(raw or "").strip()[:frames.MAX_NAME] or None
        elif key == "shows":
            value = raw if raw in SHOWS else None
        elif key == "rotation":
            value = _int(raw, 0, 270)
            value = value if value in (0, 90, 180, 270) else None
        else:
            value = _int(raw, 64, 4096)
        if value is None:
            own.pop(key, None)
        else:
            own[key] = value
    return own


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


# -- how a viewer is named on the page -------------------------------------------
# `Model` header values (TRMNL firmware's platformio.ini) as an owner knows them.
_MODEL_NAMES = {"x": "TRMNL X", "og": "TRMNL", "og_4clr": "TRMNL (color)",
                "reterminal_e1001": "reTerminal E1001", "reterminal_e1002": "reTerminal E1002",
                "reterminal_e1003": "reTerminal E1003", "m5_papers3": "M5PaperS3",
                "xteink_x4": "Xteink X4", "seeed_sticky": "Seeed Sticky"}
_DEPTH_NAMES = {"gray16": "16 grays", "gray2": "4 grays", "mono": "black and white",
                "gray256": "grayscale", "color": "color"}


def model_name(row: dict) -> str:
    """What to call this screen when the owner has not named it."""
    model = str((row.get("reported") or {}).get("model") or "")
    if frames.transport_of(row) == "page":
        return model or "Tablet"
    return _MODEL_NAMES.get(model.lower(), model) or "TRMNL client"


def depth_word(fmt: str) -> str:
    """How a picture is drawn for this screen, in an owner's words."""
    return _DEPTH_NAMES.get(fmt, fmt)
