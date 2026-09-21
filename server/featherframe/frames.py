"""One registry for every screen this server draws for (W-833).

A frame is a frame: the kit on the wall, a second kit beside it, a TRMNL, a
tablet on the kiosk page. They differ in how they are fed — `transport` — and
in what their glass can do, and the second follows from what the device itself
reported, never from a table of model names. Before this there were three
stores (the `frames` kv, the `viewers` kv, and `Config` for the wall frame);
this module is the one they all now go through.

Every frame owns its settings on its own row (step 2b): `frame_config` is the
one place a frame's effective Config comes from — the household's, with this
frame's panel, that panel's display defaults, and the owner's choices for this
frame on top. There is no primary kit and no "active" seat any more: a kit is
`asking`, `on`, or `ignored`, and every `on` kit is drawn for the same way.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from contextlib import contextmanager
from typing import Any, Optional

from . import panels
from .config import Config

log = logging.getLogger("featherframe.frames")

KEY = "frame_rows"
# The stores this one replaces. They are read once, by `migrate`, and never
# written again; they stay in the DB so a rollback still finds them.
LEGACY_FRAMES_KEY = "frames"
LEGACY_VIEWERS_KEY = "viewers"
# Which kit the single-frame build drew for. Written only by `migrate`, read
# and removed once by the service's settings migration; no live code has it.
LEGACY_PRIMARY = "primary"

# How a screen is fed, which is NOT a kind of frame: "kit" speaks the
# framebuffer protocol (/api/frame), "trmnl" TRMNL's BYOS protocol, "page" the
# kiosk page in a browser.
TRANSPORTS = ("kit", "trmnl", "page")

# A screen is waiting to be let in, let in, or turned away.
ASKING, ON, IGNORED = "asking", "on", "ignored"

MAX_NAME = 60

# What a kit's picture shows. (pictures.KINDS, spelled here so the registry
# does not have to import the render side.)
SHOWS = ("plates", "collage")

# The settings a kit owns. Everything else — the source, quiet hours, the
# blocklist, image generation — is the household's and is shared.
KIT_SETTINGS = ("panel_rotation", "mat_inset_pct", "mat_offset_x_px", "mat_offset_y_px",
                "power_mode", "wake_interval_minutes", "device_poll_seconds")

# A battery reading, under either spelling: a kit reports `battery_voltage`
# (from the X-Battery-* headers), a TRMNL `battery_volts`.
_BATTERY_KEYS = ("battery_percent", "battery_volts", "battery_voltage")


# -- one row ---------------------------------------------------------------
def new_row(frame_id: str, transport: str, stamp: str, status: str = ASKING) -> dict:
    """A screen nobody has answered for yet."""
    return {"id": str(frame_id), "transport": transport, "status": status,
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
    """The panel a kit reported (X-Panel, or its X-Panel-* facts), or None.
    Firmware old enough to report neither falls back to `row["panel"]`, the key
    the single-frame build kept in the config for it; its next report wins."""
    rep = reported_of(row)
    panel = panels.from_report(rep.get("panel"), rep.get("facts"))
    if panel is not None:
        return panel
    key = (row or {}).get("panel")
    return panels.get(key) if key else None


def panel_for(row: Optional[dict]) -> "panels.Panel":
    """The panel this frame is DRAWN for. A kit that has not said yet gets the
    default one — FEATHERFRAME_PANEL seeds a fresh install, so a second server
    comes up right unasked."""
    return panel_of(row) or panels.get(os.environ.get("FEATHERFRAME_PANEL", panels.DEFAULT.key))


def shows_of(row: Optional[dict]) -> str:
    """Which picture this kit shows: the owner's choice, else its panel's own
    default (a refresh that takes half a minute starts on the collage)."""
    shows = settings_of(row).get("shows")
    if shows in SHOWS:
        return shows
    return "collage" if panel_for(row).mode == "collage" else "plates"


def frame_config(row: Optional[dict], household: Config) -> Config:
    """The Config one frame is drawn with: the household's, with THIS frame's
    panel, that panel's own display defaults, and the owner's choices for this
    frame on top. Everything goes through Config's own sanitising, so a bad
    stored value is clamped here and not on the glass."""
    panel = panel_for(row)
    fresh = Config.defaults_for(panel.key).to_dict()
    own = settings_of(row)
    return Config.from_dict({
        **household.to_dict(), "panel": panel.key,
        # `mode` is not a frame's setting any more: `shows_of` answers that.
        **{k: fresh[k] for k in panels.PANEL_SETTINGS if k != "mode"},
        **{k: v for k, v in own.items() if k in KIT_SETTINGS}})


# -- what a screen can be asked for ----------------------------------------
def capabilities(row: dict) -> dict:
    """What this screen can do, derived from its transport and from what it
    reported — never from a table of model names."""
    transport = transport_of(row)
    kit, page = transport == "kit", transport == "page"
    rep = reported_of(row)
    if kit:
        # Only what the firmware accepts: any other rotation is another native
        # size, which it rejects.
        rotations = tuple(panel_for(row).rotations)
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
        "colour": bool(page or (kit and panel_for(row).color)),
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

    def on_kits(self) -> list:
        """Every kit this server draws for, oldest first. There is no primary:
        the order is only so the page (and the single-frame shims that survive
        until step 3) always mean the same one by "the frame"."""
        rows = [r for r in self.all().values()
                if transport_of(r) == "kit" and r.get("status") == ON]
        rows.sort(key=lambda r: (str(r.get("first_seen") or ""), str(r.get("id") or "")))
        return rows

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
           "reported": reported, "set": dict(old.get("set") or {}),
           "first_seen": old.get("first_seen"), "last_seen": old.get("last_seen")}
    if is_active and status == ON:
        # The kit whose settings still live in Config; the service's settings
        # migration moves them onto this row and drops the mark.
        row[LEGACY_PRIMARY] = True
    if old.get("ip"):
        row["ip"] = old["ip"]
    return row


def _viewer_row(vid: str, old: dict) -> dict:
    """A row from W-822's `viewers` kv. A viewer is never parked: it was
    already being served, so it is on."""
    kind = old.get("kind")
    row = {"id": vid, "transport": kind if kind in ("trmnl", "page") else "trmnl",
           "status": ON,
           "reported": dict(old.get("reported") or {}), "set": dict(old.get("set") or {}),
           "token": str(old.get("token") or secrets.token_hex(16)),
           "first_seen": old.get("first_seen"), "last_seen": old.get("last_seen")}
    if old.get("ip"):
        row["ip"] = old["ip"]
    return row
