"""One registry for every screen this server draws for (W-833).

A frame is a frame: the kit on the wall, a second kit beside it, a TRMNL, a
tablet on the kiosk page. They differ in how they are fed — `transport` — and
in what their glass can do, and the second follows from what the device itself
reported, never from a table of model names. Before this there were three
stores (the `frames` kv, the `viewers` kv, and `Config` for the wall frame);
this module is the one they all now go through.

Step 1 keeps the old vocabulary alive at the edges. `primary` marks the single
kit the legacy single-frame path — `Config` plus the resident framebuffer —
still draws for; it goes away once every frame owns its own settings.
"""
from __future__ import annotations

import logging
import secrets
import threading
from contextlib import contextmanager
from typing import Any, Optional

from . import panels

log = logging.getLogger("featherframe.frames")

KEY = "frame_rows"
# The stores this one replaces. They are read once, by `migrate`, and never
# written again; they stay in the DB so a rollback still finds them.
LEGACY_FRAMES_KEY = "frames"
LEGACY_VIEWERS_KEY = "viewers"

# How a screen is fed, which is NOT a kind of frame: "kit" speaks the
# framebuffer protocol (/api/frame), "trmnl" TRMNL's BYOS protocol, "page" the
# kiosk page in a browser.
TRANSPORTS = ("kit", "trmnl", "page")

# A screen is waiting to be let in, let in, or turned away.
ASKING, ON, IGNORED = "asking", "on", "ignored"

MAX_NAME = 60

# A battery reading, under either spelling: a kit reports `battery_voltage`
# (DeviceStatus, from the X-Battery-* headers), a TRMNL `battery_volts`.
_BATTERY_KEYS = ("battery_percent", "battery_volts", "battery_voltage")


# -- one row ---------------------------------------------------------------
def new_row(frame_id: str, transport: str, stamp: str, status: str = ASKING) -> dict:
    """A screen nobody has answered for yet."""
    return {"id": str(frame_id), "transport": transport, "status": status, "primary": False,
            "reported": {}, "set": {}, "first_seen": stamp, "last_seen": stamp}


def transport_of(row: Optional[dict]) -> str:
    """How this screen is fed. `kind` is the viewer code's older name for it."""
    value = (row or {}).get("transport") or (row or {}).get("kind")
    return value if value in TRANSPORTS else "kit"


def reported_of(row: Optional[dict]) -> dict:
    """What the device said about itself, last time it asked."""
    rep = (row or {}).get("reported")
    return rep if isinstance(rep, dict) else {}


def settings_of(row: Optional[dict]) -> dict:
    """What the owner chose for it on the page."""
    own = (row or {}).get("set")
    return own if isinstance(own, dict) else {}


def name_of(row: Optional[dict]) -> str:
    return str(settings_of(row).get("name") or "")


def panel_of(row: Optional[dict]) -> Optional["panels.Panel"]:
    """The panel a kit reported (X-Panel, or its X-Panel-* facts), or None."""
    rep = reported_of(row)
    return panels.from_report(rep.get("panel"), rep.get("facts"))


def seat(row: Optional[dict]) -> str:
    """The word the frame endpoints still answer in: "active" (the primary
    kit), "added" (a second kit), "pending", "ignored"."""
    status = (row or {}).get("status")
    if status == ON:
        return "active" if (row or {}).get("primary") else "added"
    return "pending" if status == ASKING else "ignored"


# -- what a screen can be asked for ----------------------------------------
def capabilities(row: dict, config: Any = None) -> dict:
    """What this screen can do, derived from its transport and from what it
    reported. `config` is only consulted for a kit that has not reported a
    panel yet — the wall frame on a fresh install, whose panel lives in the
    config until step 2 moves it onto the row."""
    transport = transport_of(row)
    kit, page = transport == "kit", transport == "page"
    rep = reported_of(row)
    panel = panel_of(row) if kit else None
    if kit and panel is None and config is not None:
        panel = config.panel_spec
    if kit:
        # Only what the firmware accepts: any other rotation is another native
        # size, which it rejects.
        rotations = tuple((panel or panels.DEFAULT).rotations)
    elif page:
        # A tablet turns its own picture when it is turned; there is nothing
        # for the owner to set.
        rotations = ()
    else:
        # A viewer's picture is turned in the render, so any quarter turn is
        # drawable whatever way the screen is hung.
        rotations = (0, 90, 180, 270)
    return {
        "renamable": True,          # every screen is the owner's to name
        "shows": True,              # and every screen shows plates or the collage
        "rotations": rotations,
        "mat": kit,                 # only glass behind a real mat
        "look": page,               # colour or paper: a lit screen's choice
        "dark_quiet": page,         # and only a lit screen can go dark at night
        "power": kit,               # the power model is the kit firmware's
        # A client that says nothing about its screen (TRMNL's Kobo and Kindle
        # scripts send only an ID): the owner has to.
        "needs_size": transport == "trmnl" and not (rep.get("width") and rep.get("height")),
        "has_battery": any(rep.get(k) is not None for k in _BATTERY_KEYS),
        "colour": bool(page or (kit and panel is not None and panel.color)),
    }


# -- the store -------------------------------------------------------------
class _Rows(dict):
    """The rows inside `FrameRegistry.mutate`: an ordinary dict, plus
    `unchanged()` for the common case where a check-in changed nothing and must
    not cost a write — an always-awake frame asks every three seconds."""

    _write = True

    def unchanged(self) -> None:
        self._write = False


class FrameRegistry:
    """Every screen this server knows, one JSON blob in our kv store. There are
    a handful of them and they are read on every request, so a dict is the
    whole data structure."""

    def __init__(self, db) -> None:
        self.db = db
        # Innermost lock: the service's own lock is always taken first.
        self._lock = threading.RLock()

    # -- reading -----------------------------------------------------------
    def all(self) -> dict:
        rows = self.db.get(KEY)
        return rows if isinstance(rows, dict) else {}

    def get(self, frame_id: str) -> Optional[dict]:
        return self.all().get(frame_id)

    def by_transport(self, *transport: str) -> list:
        return [r for r in self.all().values() if transport_of(r) in transport]

    def by_status(self, status: str, transport: Optional[str] = None) -> list:
        return [r for r in self.all().values() if r.get("status") == status
                and (transport is None or transport_of(r) == transport)]

    def primary(self) -> Optional[dict]:
        """The one kit the resident frame is drawn for."""
        return next((r for r in self.all().values()
                     if r.get("primary") and transport_of(r) == "kit"), None)

    def added(self) -> list:
        """Kits served beside the primary one (W-832)."""
        return [r for r in self.all().values() if transport_of(r) == "kit"
                and r.get("status") == ON and not r.get("primary")]

    # -- writing -----------------------------------------------------------
    @contextmanager
    def mutate(self):
        """The rows under the registry's lock, written back on the way out
        unless the block calls `rows.unchanged()`."""
        with self._lock:
            rows = _Rows(self.all())
            yield rows
            if rows._write:
                self.db.set(KEY, dict(rows))

    def save(self, row: dict) -> dict:
        with self.mutate() as rows:
            rows[str(row["id"])] = row
        return row

    def forget(self, frame_id: str) -> bool:
        with self.mutate() as rows:
            if rows.pop(frame_id, None) is None:
                rows.unchanged()
                return False
        return True

    def rename(self, frame_id: str, name: Any) -> Optional[dict]:
        """Name any screen — the wall frame, a second kit, a viewer. A blank
        name clears it, and the page falls back to what the panel is called."""
        with self.mutate() as rows:
            row = rows.get(frame_id)
            if row is None:
                rows.unchanged()
                return None
            own = dict(settings_of(row))
            clean = str(name or "").strip()[:MAX_NAME]
            if clean:
                own["name"] = clean
            else:
                own.pop("name", None)
            row["set"] = own
            return row

    # -- migration ---------------------------------------------------------
    def migrate(self) -> None:
        """Build the registry from the stores it replaces, once. Idempotent:
        once `frame_rows` exists it is the only truth, and the legacy keys are
        left untouched so a rollback still reads them."""
        with self._lock:
            if isinstance(self.db.get(KEY), dict):
                return
            rows: dict = {}
            legacy = self.db.get(LEGACY_FRAMES_KEY)
            legacy = legacy if isinstance(legacy, dict) else {}
            known = legacy.get("known")
            active = legacy.get("active")
            for fid, old in (known if isinstance(known, dict) else {}).items():
                if isinstance(old, dict):
                    rows[str(fid)] = _kit_row(str(fid), old, str(fid) == active)
            viewers = self.db.get(LEGACY_VIEWERS_KEY)
            for vid, old in (viewers if isinstance(viewers, dict) else {}).items():
                if not isinstance(old, dict):
                    continue
                if str(vid) in rows:
                    # A viewer and a kit that named themselves the same MAC.
                    # The kit keeps the row: it is the one being served.
                    log.warning("viewer %s has the same id as a frame; not migrated", vid)
                    continue
                rows[str(vid)] = _viewer_row(str(vid), old)
            self.db.set(KEY, rows)
            if rows:
                log.info("frame registry: migrated %d screens", len(rows))


def _kit_row(fid: str, old: dict, is_active: bool) -> dict:
    """A row from W-832's `frames` kv. Its panel, board and telemetry were kept
    in three places on the old row; they are all "what the device reported"."""
    status = {"active": ON, "added": ON, "ignored": IGNORED}.get(old.get("status"), ASKING)
    reported = {k: old[k] for k in ("panel", "board", "facts") if old.get(k)}
    reported.update({k: v for k, v in (old.get("device") or {}).items() if v is not None})
    row = {"id": fid, "transport": "kit", "status": status,
           "primary": bool(is_active and status == ON),
           "reported": reported, "set": dict(old.get("set") or {}),
           "first_seen": old.get("first_seen"), "last_seen": old.get("last_seen")}
    if old.get("ip"):
        row["ip"] = old["ip"]
    return row


def _viewer_row(vid: str, old: dict) -> dict:
    """A row from W-822's `viewers` kv. A viewer is never parked: it was
    already being served, so it is on."""
    kind = old.get("kind")
    row = {"id": vid, "transport": kind if kind in ("trmnl", "page") else "trmnl",
           "status": ON, "primary": False,
           "reported": dict(old.get("reported") or {}), "set": dict(old.get("set") or {}),
           "token": str(old.get("token") or secrets.token_hex(16)),
           "first_seen": old.get("first_seen"), "last_seen": old.get("last_seen")}
    if old.get("ip"):
        row["ip"] = old["ip"]
    return row
