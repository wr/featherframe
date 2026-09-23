"""The running service: state + the render scheduler.

One background thread polls BirdNET on a short interval, decides whether a new
frame is warranted (mode, quiet hours, corroboration, the dwell hold), and
renders at most one frame per decision. Everything else — the web handlers —
just reads the current frame. Priority #3 (few panel refreshes) lives here: the
default path is to do nothing.

The current frame is persisted to disk so a server restart doesn't blank the
device, and the ingest cursor is persisted so we don't replay history.
"""
from __future__ import annotations

import logging
import math
import os
import re
import shutil
import socket
import threading
from dataclasses import dataclass
from datetime import date as ddate
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Optional
from urllib.parse import quote

from PIL import Image

from . import firmware_release
from . import frames as frames_mod
from . import panels, paths
from . import pictures as pictures_mod
from .push import PushHub
from .pictures import COLLAGE, PLATES, Pictures
from .config import Config, load_config, save_config
from .frames import FrameRegistry
from .sources import Detection, make_source
from .db import Database
from . import viewers as viewers_mod
from .render import collage as collage_mod
from .render import compose as compose_mod
from .render import framebuffer
from .render import pipeline
from .render import statuspage
from .render import welcome as welcome_mod
from .render.compose import SingleSpec
from .render.genart import GeneratedArtProvider, make_image_model, make_text_model
from .render.pipeline import RenderResult
from .render.provider import ArtProvider, AudubonProvider, ChainedProvider

log = logging.getLogger("featherframe.service")

_OUT_KEY = "frame_outputs"            # frame id -> {"etag", "src"}
_USER_HOLD_KEY = "user_hold"          # W-735: the owner's pin on the current plate
_HOLD_DAYS = {"day": 1, "week": 7, "forever": None}
_VIEWS_MAX = 8                         # cached renders of one picture
# A picture is drawn only while some frame shows it, and a screen that has not
# asked in this long is not a frame any more — it was unplugged.
VIEWER_SHOWS_DAYS = 30

# A gray frame's server composes the colour twin only while a colour viewer (a
# tablet) has asked within this long: nobody watching in colour, nothing paid.
COLOR_VIEWER_DAYS = 30

# History thumbnails: 1/8-scale previews keyed by ETag, capped on disk (a
# Pi's SD card) and matched to what the page's strip shows. Each has a
# full-size JPEG beside it for the zoom (~0.4 MB; 24 of them is ~10 MB).
_HISTORY_MAX = 24
# The finished collage of each of the last week's days, kept to download.
COLLAGE_DAYS_KEPT = 7
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HISTORY_SCALE = 8
_HISTORY_FULL_QUALITY = 85
_ETAG_RE = re.compile(r"^[0-9a-f]{16}$")

# What an owner never tuned is a constant, not a setting (W-821).
#
# How often the detection source is checked. BirdWeather is a public API and
# is never polled faster than a minute.
POLL_SECONDS = 5
_CLOUD_POLL_SECONDS = 60
# The confidence a detection needs when the source has no threshold of its
# own. BirdNET-Go filters by its own setting and only falls back to this.
CONFIDENCE_FLOOR = 0.7
# A frame that shows the collage checks in when the collage is next redrawn
# (W-833): this long after it, so the draw has landed, and never sooner than
# this between checks.
COLLAGE_CHECK_MARGIN_S = 90
COLLAGE_CHECK_FLOOR_S = 60
# Dwell: a first-ever or first-today species keeps the frame this long against
# repeats of common species (another new one can still take over, and the held
# one may re-render). Without it a first-ever species lost the glass to the
# next cardinal within minutes.
DWELL_MINUTES = 90
# Gone-quiet alarm: a plate footnote and a page banner once nothing has been
# heard for this many ACTIVE hours. Hours inside quiet hours don't count, so a
# silent night never trips it. The common month-two failure (mic unplugged,
# BirdNET stopped) is otherwise silent everywhere.
QUIET_ALARM_HOURS = 6
# Source-outage note: the plate on the glass is re-rendered once with a footnote
# after this long unreachable. An hour: a router reboot or a BirdNET restart
# must not repaint the wall.
SOURCE_ALARM_MINUTES = 60
# New-species corroboration. BirdNET routinely produces single-shot false
# positives of rare species (a car horn as a Bald Eagle). Unchecked, one such
# hit becomes the wall and, for a species with no plate, BUYS a generated
# plate of a species that was never there. A species heard for the first time
# today must earn the wall: one detection at or above the confidence, or two
# inside the window at least the gap apart. Known species are unaffected.
CORROBORATE_CONFIDENCE = 0.85
CORROBORATE_WINDOW = timedelta(hours=24)
CORROBORATE_MIN_GAP = timedelta(minutes=10)

# Gone-quiet alarm: the active-minutes walk steps at this granularity, and
# never further back than this — anything older is an alarm regardless, and
# the walk must stay cheap on a tick.
_QUIET_STEP = timedelta(minutes=10)
_QUIET_MAX_SPAN = timedelta(days=30)

# One new_since page. A FULL page means a backlog (the server was down a
# while): the newest row of that page is hours stale, and paging through the
# rest one tick at a time would show a parade of old birds. See _single_tick.
_INGEST_PAGE = 500

# New-species corroboration looks for a second hit among this many recent
# detections. A day of a busy feeder is a few hundred rows; the scan is one
# query and only runs for a first-seen-today species below the confidence bar.
_CORROBORATE_SCAN = 200

# Novelty classes, best first. A first-ever species (never heard before today)
# outranks a first-today one (known, but its first call today), which outranks
# a repeat. Selection among a tick's detections is by class, then newest —
# so a first-ever bird is not lost to the cardinal that called after it.
_NOVELTY_RANK = {"first-ever": 2, "first-today": 1, "repeat": 0}
_NOVEL = ("first-ever", "first-today")
# The page's tooltip on "holding N min": why this plate is not changing.
_HOLD_WHY = {
    "first-ever": "First time this species has been heard, so it stays up {} min. "
                  "Only another new species replaces it sooner.",
    "first-today": "First time this species has been heard today, so it stays up {} min. "
                   "Only another new species replaces it sooner.",
}
# How much of the day's tally / render log a novelty check reads. A busy
# feeder is a few dozen species a day; the render log holds 200 rows.
_TODAY_SCAN = 200


def _as_time(v) -> dtime:
    if isinstance(v, dtime):
        return v
    hh, mm = (int(x) for x in v.split(":"))
    return dtime(hh, mm)


def collage_date_for(now: datetime, quiet_start, quiet_end) -> ddate:
    """The date a nightly collage covers: the day the quiet window started.
    With a midnight-wrapping window (the default 22:00-06:00), a tick after
    00:00 still covers yesterday. The bounds are "HH:MM" strings or times."""
    try:
        start, end = _as_time(quiet_start), _as_time(quiet_end)
    except (ValueError, TypeError, AttributeError):
        return now.date()
    if start > end and now.time() < end:  # wrapped window, after midnight
        return now.date() - timedelta(days=1)
    return now.date()


@dataclass
class DeviceStatus:
    last_checkin: Optional[str] = None
    battery_voltage: Optional[float] = None
    battery_percent: Optional[int] = None
    wifi_rssi: Optional[int] = None
    last_result: Optional[str] = None      # "304" | "frame" | view name
    etag_served: Optional[str] = None
    user_agent: Optional[str] = None
    ip: Optional[str] = None
    # Device-reported identity/telemetry (all optional on the wire — old firmware
    # omits them and the card degrades gracefully). See docs/firmware-device-stats.md.
    fw_version: Optional[str] = None       # §1 X-FF-Version, e.g. "2026.09.01+a1b2c3d"
    sketch_md5: Optional[str] = None       # §1 X-FF-Sketch-MD5 (exact binary id)
    last_wake: Optional[str] = None        # §3 X-Wake token: timer|button|coldboot
    wake_detail: Optional[str] = None      # §3 X-Wake-Detail: cause=N keys=0xM
    boot_count: Optional[int] = None       # §5 X-Boot-Count
    refresh_count: Optional[int] = None    # §5 X-Refresh-Count
    panel: Optional[str] = None            # §6 X-Panel
    board: Optional[str] = None            # §6 X-Board
    # X-FF-Push (W-841): the frame speaks push. "0" = no socket open right now
    # (it polls as before); N > 0 = a socket is open and its next plain check-in
    # is N seconds away, a heartbeat — a change reaches it over the socket.
    push_s: Optional[int] = None


# Plausible telemetry, (lo, hi). Values are device-reported over the LAN and
# are persisted, then serialised to JSON for the config page: a NaN or an
# absurd number must never reach the row — an old build let "nan" through and
# every /api/status 500'd until the DB was hand-edited.
_DEVICE_RANGES = {
    "battery_voltage": (0.0, 6.0),
    "battery_percent": (0, 100),
    "wifi_rssi": (-120, 0),
    "boot_count": (0, 2**31),
    "refresh_count": (0, 2**31),
    "push_s": (0, 86400),
}
_DEVICE_STR_MAX = 120


def _clean_device_fields(raw: Optional[dict]) -> dict:
    """DeviceStatus kwargs from an untrusted dict (a request's headers or a
    persisted row): known fields only, numbers finite and in range, strings
    bounded. Anything else is dropped — reads as "not reported"."""
    allowed = DeviceStatus.__dataclass_fields__
    out: dict = {}
    for k, v in (raw or {}).items():
        if k not in allowed or v is None:
            continue
        if k in _DEVICE_RANGES:
            lo, hi = _DEVICE_RANGES[k]
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(f) or not (lo <= f <= hi):
                continue
            out[k] = f if k == "battery_voltage" else int(f)
        elif isinstance(v, str):
            out[k] = v[:_DEVICE_STR_MAX] or None
    return out


# A resting 1S cell at this voltage is ~15%: the config page flags it so the
# owner charges before the firmware's own low-battery hold kicks in (3.45 V).
_BATTERY_LOW_V = 3.55
# Critical: the last stretch before that hold (FF_LOW_BATT_V; per panel, see
# Panel.low_battery_volts), where the frame stops checking in until charged. The page says so in a banner, and
# keeps saying it while the frame is silent: the last reading stands.
_BATTERY_CRITICAL_V = 3.45
_BATTERY_CRITICAL_PCT = 10
# Below this the divider is reading an empty JST socket (USB-only unit), which
# the firmware also ignores (FF_BATT_ABSENT_V): no pack, not a flat one.
_BATTERY_ABSENT_V = 2.5

# Power-state inference. Nothing on the EE03 tells the XIAO whether USB is
# plugged in (the charger's status pins go to an LED and a test point), but the
# voltage does: on USB the BQ24070 holds the pack at ~4.2 V and the reading
# never falls; a cell on its own never sits at or above _USB_V for long and
# drifts down. "Charging" is a rise over the last hour that a cell can't do by
# itself. The baseline is a median over a window, because the calibrated ADC
# path wanders ±30 mV between check-ins.
_USB_V = 4.19
# Charging = a SUSTAINED climb: the last four 10-minute medians each higher
# than the one before, at least _CHARGE_RISE_V in all. A cell recovering after
# a heavy load (a panel refresh, a Wi-Fi burst) also rises — but as one step
# that then goes flat, which fails the "each bin higher" test.
_CHARGE_RISE_V = 0.06
_CHARGE_BINS = 4
_CHARGE_BIN = timedelta(minutes=10)
_TREND_STALE = timedelta(minutes=20)
# The display value and the USB test use a median of the last few minutes of
# check-ins: single readings alternate by ±15 mV with the radio's duty cycle.
_LIVE_WINDOW = timedelta(minutes=3)
_LIVE_KEEP = timedelta(minutes=15)

_POWER_TEXT = {"usb": "on USB", "charging": "charging", "battery": "on battery", "unknown": ""}


def _median(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def _points(rows) -> list[tuple[datetime, float]]:
    pts = []
    for r in rows or []:
        try:
            pts.append((datetime.fromisoformat(str(r["at"])), float(r["voltage"])))
        except (KeyError, TypeError, ValueError):
            continue
    pts.sort()
    return pts


def live_voltage(live: list[dict], now: datetime) -> Optional[float]:
    """Median of the check-ins in the last _LIVE_WINDOW, or None."""
    pts = _points(live)
    return _median([v for t, v in pts if now - t <= _LIVE_WINDOW])


def power_state(history: list[dict], now: datetime, live: Optional[list[dict]] = None) -> dict:
    """{"state": usb|charging|battery|unknown, "text": ...}. `history` is the
    5-minute battery log (oldest first, ISO `at` + `voltage`); `live` the last
    few minutes of raw check-ins, which decide the USB test so a plug or unplug
    shows within a minute instead of a log interval later."""
    pts = _points(history) + _points(live)
    pts.sort()
    if not pts or now - pts[-1][0] > _TREND_STALE:
        return {"state": "unknown", "text": ""}
    level = live_voltage(live or [], now)
    if level is None:
        level = pts[-1][1]
    if level >= _USB_V:
        state = "usb"
    else:
        bins = []
        for i in range(_CHARGE_BINS, 0, -1):
            lo, hi = now - i * _CHARGE_BIN, now - (i - 1) * _CHARGE_BIN
            bins.append(_median([v for t, v in pts if lo <= t < hi]))
        climbing = (all(b is not None for b in bins)
                    and all(bins[i] > bins[i - 1] for i in range(1, len(bins)))
                    and bins[-1] - bins[0] >= _CHARGE_RISE_V)
        state = "charging" if climbing else "battery"
    return {"state": state, "text": _POWER_TEXT[state]}


def frame_title(meta: dict) -> Optional[str]:
    """A display-ready name for what is on the glass. The stored label is a
    log string ("combined collage (5 species)", "6-species collage",
    "Northern Cardinal (test)"); the page shows this instead."""
    label = meta.get("label")
    if not label:
        return None
    label = str(label)
    # "day in review" is the combined collage's label from before the rename.
    m = (re.match(r"^(?:combined collage|day in review) \((\d+) species\)$", label)
         or re.match(r"^(\d+)-species collage$", label))
    if m:
        return f"Collage · {m.group(1)} species"
    m = re.match(r"^(.*) \(test\)$", label)
    if m:
        return f"{m.group(1)} (test)"
    return label


def when_text(then: datetime, now: Optional[datetime] = None) -> str:
    """Clock time for the page and the plate: "11:27 pm" today, "1 Sep
    11:27 pm" otherwise — the date only when it says something."""
    now = now or datetime.now()
    hour = then.hour % 12 or 12
    clock = f"{hour}:{then.minute:02d} {'am' if then.hour < 12 else 'pm'}"
    if then.date() == now.date():
        return clock
    return f"{then.day} {then.strftime('%b')} {clock}"


def when_short(then: datetime, now: Optional[datetime] = None) -> str:
    """History-strip caption: the time today, else just the date."""
    now = now or datetime.now()
    if then.date() == now.date():
        return when_text(then, now)
    return f"{then.day} {then.strftime('%b')}"


def _hours_text(hours: float) -> str:
    return f"{int(hours)} h" if float(hours).is_integer() else f"{hours:.1f} h"


def _ago(then: datetime, now: datetime) -> str:
    """Relative time for the config page: "just now", "7 min ago", …"""
    secs = max(0.0, (now - then).total_seconds())
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} h ago"
    days = hours // 24
    return f"{days} day ago" if days == 1 else f"{days} days ago"


def _within(stamp: Optional[str], now: datetime, seconds: float) -> bool:
    """Was `stamp` (an ISO time, or nothing) this recently?"""
    try:
        return (now - datetime.fromisoformat(str(stamp))).total_seconds() <= seconds
    except (ValueError, TypeError):
        return False


def _served_words(result: Optional[str]) -> Optional[str]:
    if result == "304":
        return "up to date (304)"
    if result == "frame":
        return "new frame"
    return f"{result} view" if result else None


# An always-awake frame polls every 15 s (FF_POLL_INTERVAL_MS) and backs off
# after repeated failures; a few minutes of silence is a real outage.
_AWAKE_OVERDUE_MINUTES = 5

# What each picture is called on the page. The stored values stay "plates" and
# "collage"; these are the words an owner reads.
SHOWS_WORDS = {PLATES: "Individual detections", COLLAGE: "Collage"}

# A browser tab asks every PAGE_POLL_SECONDS. Three missed asks and it is not
# open any more — which is not a device in trouble, just a closed tab.
_PAGE_OPEN_SECONDS = 3 * viewers_mod.PAGE_POLL_SECONDS


def _kit_order(row: dict) -> tuple:
    """Frames in the order they first asked. There is no ranking among them;
    this is only so a list reads the same way twice."""
    return (str(row.get("first_seen") or ""), str(row.get("id") or ""))


def _what_it_is(row: dict, panel) -> str:
    """What to call a frame the owner has not named: its panel, or what the
    device said it is."""
    transport = frames_mod.transport_of(row)
    if transport != "kit":
        return viewers_mod.model_name(row)
    if panel is not None:
        return panel.name
    return str(frames_mod.reported_of(row).get("panel") or "") or "unknown panel"


def device_of(reported: Optional[dict], last_seen: Optional[str] = None) -> DeviceStatus:
    """What one frame reported, in the single telemetry shape. A viewer says
    the same things in TRMNL's words (battery-voltage, rssi); a frame is a
    frame, so they land in the same fields."""
    fields = dict(reported or {})
    for theirs, ours in (("battery_volts", "battery_voltage"), ("rssi", "wifi_rssi")):
        if fields.get(ours) is None and fields.get(theirs) is not None:
            fields[ours] = fields[theirs]
    if not fields.get("last_checkin") and last_seen:
        fields["last_checkin"] = last_seen
    return DeviceStatus(**_clean_device_fields(fields))


def frame_card(reported: dict, wake_interval_minutes: int,
               now: Optional[datetime] = None,
               battery_history: Optional[list[dict]] = None,
               battery_live: Optional[list[dict]] = None,
               power_mode: str = "sleep",
               critical_volts: Optional[float] = None,
               told_seconds: Optional[int] = None) -> dict:
    """One frame's health, pre-chewed for the config page: ready-to-print
    strings plus one overdue flag. `reported` is that frame's row's report (a
    kit's check-in headers, a viewer's own). In deep sleep, overdue means the
    device has missed two consecutive wake intervals — one 304 skipped is
    normal jitter, two is a dead battery or lost Wi-Fi. Always awake, it polls
    every 15 s, so the bar is a fixed few minutes and the interval does not
    apply. `told_seconds` is the wait the server gave the frame at its last
    check-in (a frame on the collage is told to come back after the next
    redraw, hours away): measured against that when known."""
    now = now or datetime.now()
    device = device_of(reported)
    awake = (power_mode == "awake")
    if told_seconds:
        told_min = told_seconds / 60
        expected = (told_min + _AWAKE_OVERDUE_MINUTES) if awake else 2 * told_min
    else:
        expected = _AWAKE_OVERDUE_MINUTES if awake else 2 * wake_interval_minutes
    card = {"seen": False, "overdue": False, "state": "off",
            "expected_minutes": wake_interval_minutes,
            "overdue_text": ("Overdue — checks in every few seconds" if awake
                             else f"Overdue — wakes every {wake_interval_minutes} min"),
            "last_seen": None,
            "last_checkin_iso": None, "battery": None, "battery_low": False,
            "battery_critical": False,
            "battery_percent": None, "battery_voltage": None,
            "power": {"state": "unknown", "text": ""},
            "served": None, "wifi_rssi": None}
    try:
        then = datetime.fromisoformat(device.last_checkin or "")
    except (ValueError, TypeError):
        return card
    card["seen"] = True
    card["last_seen"] = _ago(then, now)
    card["last_checkin_iso"] = then.isoformat(timespec="seconds")
    card["overdue"] = (now - then).total_seconds() > expected * 60
    if device.battery_voltage is not None and device.battery_voltage >= _BATTERY_ABSENT_V:
        # Display the last few minutes' median, not the single newest reading,
        # so the percent stops flickering between two values every 15 s.
        volts = live_voltage(battery_live or [], now) or device.battery_voltage
        pcts = [r["percent"] for r in (battery_live or [])
                if r.get("percent") is not None
                and now - datetime.fromisoformat(str(r["at"])) <= _LIVE_WINDOW]
        percent = int(_median(pcts)) if pcts else device.battery_percent
        card["battery_volts"] = round(volts, 3)
        card["battery_voltage"] = round(volts, 3)
        card["battery_percent"] = percent
        pct = f" · {percent}%" if percent is not None else ""
        card["battery"] = f"{volts:.2f} V{pct}"
        card["battery_low"] = ((percent is not None and percent <= 20) or volts <= _BATTERY_LOW_V)
        card["battery_critical"] = ((percent is not None and percent <= _BATTERY_CRITICAL_PCT)
                                    or volts <= (critical_volts or _BATTERY_CRITICAL_V))
        card["power"] = power_state(battery_history or [], now, battery_live)
        if card["power"]["text"]:
            card["battery"] += f" · {card['power']['text']}"
        # Full and held there by the charger is not "low", whatever the percent says.
        if card["power"]["state"] in ("usb", "charging"):
            card["battery_low"] = card["battery_critical"] = False
    elif device.battery_voltage is None and device.battery_percent is not None:
        # A screen that reports a percent and no voltage (an EE02, a TRMNL
        # client): the reading is still its battery, so the row says so. There
        # is no trend to infer a power state from. A voltage that WAS reported
        # and sits under _BATTERY_ABSENT_V is an empty socket, not a flat cell,
        # and never reaches here.
        card["battery_percent"] = device.battery_percent
        card["battery"] = f"{device.battery_percent}%"
        card["battery_low"] = device.battery_percent <= 20
        card["battery_critical"] = device.battery_percent <= _BATTERY_CRITICAL_PCT
    card["served"] = _served_words(device.last_result)
    card["wifi_rssi"] = device.wifi_rssi
    return card


class FeatherframeService:
    def __init__(self, db: Optional[Database] = None) -> None:
        # The service's one wall clock. Every "now" inside the class reads
        # it, so tests pin the clock instead of racing the calendar.
        self._clock = datetime.now
        self.db = db or Database()
        # One registry for every screen (W-833): the kit on the wall, a second
        # kit, a TRMNL, a tablet.
        self.frames = FrameRegistry(self.db)
        self.config: Config = load_config(self.db)
        # The latest official firmware release, offered to the kits (W-838).
        self.releases = firmware_release.ReleaseStore(self.db, paths.data_dir())
        # The frames holding a push socket (W-841), woken after every tick.
        self.push = PushHub()
        self.audubon = AudubonProvider()
        self.genart: GeneratedArtProvider = GeneratedArtProvider(None)
        self.provider: ArtProvider = self._build_provider(self.config)
        self.source = make_source(self.config, self.db)

        self._lock = threading.RLock()
        self._view_lock = threading.Lock()   # one viewer render at a time
        self._recolor: set = set()      # pictures owed a redraw (a colour screen arrived)
        # Per picture, the ETag whose colour twin was last attempted: a
        # picture with no colour to give must not be recomposed per ask.
        self._color_tried: dict = {}
        self._color_asked_at: Optional[datetime] = None   # cache of the DB's color_viewer_at
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Background regenerations (config page). The page polls the listing
        # for this state, so it must be readable from any thread — and it is
        # the server's memory of an in-flight repaint, which is what lets a
        # refreshed page pick the indicator back up.
        self._regen_lock = threading.Lock()
        self._regen_inflight: set[str] = set()
        self._regen_errors: dict[str, str] = {}

        # Background one-shot jobs (test detection, collage) the config
        # page kicks off and polls — same fire-and-forget contract as repaints,
        # so a ~2-minute generation never blocks (and never 504s) the request.
        self._task_lock = threading.Lock()
        self._tasks_inflight: set[str] = set()
        self._task_errors: dict[str, str] = {}

        # The two pictures every frame shows one of (W-833), and one output per
        # frame drawn from them. A framebuffer is not state of its own: it is
        # what one frame's picture looks like finished for that frame's panel.
        self.pictures = Pictures(self.db)
        out = self.db.get(_OUT_KEY, {})
        self._out: dict = out if isinstance(out, dict) else {}   # frame id -> {etag, src}
        self._load_outputs()
        # Verify the persisted ingest cursor isn't stale on the first single-tick
        # after start (see _single_tick); cheaper than checking every tick.
        self._cursor_verified = False
        # Set when the detection source changes (see _reset_for_source): the
        # next single-tick shows the new source's latest detection once.
        self._source_switched = False

        # The last few minutes of raw check-ins per frame (the battery log
        # keeps one row per 5 min): the display median and the USB test read
        # from here.
        self._battery_live: dict = {}

        # Gone-quiet alarm, computed once per tick (status() is polled, and
        # the walk plus a source query is not free). None = no alarm.
        self._quiet: Optional[dict] = None
        # The repeat a dwell hold last turned away ({"key", "common"}), named
        # along the plate's foot as "Just now: …" (W-776). In memory only: a
        # restart drops the line on its next tick, which is the honest answer.
        self._just_now: Optional[dict] = None
        # Source-outage note (W-696): when the source first went unreachable
        # (persisted, so a restart mid-outage keeps the clock), and the alarm
        # derived from it once per tick. None = reachable / no alarm.
        self._source_down_since: Optional[datetime] = None
        try:
            stored = self.db.get("source_down_since", None)
            self._source_down_since = datetime.fromisoformat(stored) if stored else None
        except (TypeError, ValueError):
            self._source_down_since = None
        self._outage: Optional[dict] = None
        # A first-seen-today species held back for a second detection (see
        # _corroborated). Persisted so a restart doesn't forget the bird the
        # page says it is waiting on; kept OFF the frame meta because it is
        # not what the glass shows. None = nothing waiting.
        self._pending: Optional[dict] = self.db.get("pending_species", None) or None
        # Per-tick memo for the novelty lookups (first-seen dates, the day's
        # tally, what was rendered today): one source call per question per
        # tick at most, however many candidates a page holds. Reset at the
        # top of every single-mode tick and keyed by the tick's `now`.
        self._tick_memo: dict = {}
        # With no detection on record at all, the alarm clock starts here —
        # the earliest moment we can vouch for silence.
        self._started_at = self._clock()

    # -- the frames this server draws for ----------------------------------
    def _kits_on(self) -> list:
        """Every kit that is on, oldest first."""
        return self.frames.on_kits()

    # -- the picture the page's own tools mean ------------------------------
    # There is no primary frame (W-833): "the picture", where nothing names
    # one, is what the tools act on — Refresh, Hold, Block, the history strip,
    # the sheet /api/preview.png serves. It follows what the screens are
    # showing, never which kit checked in first.
    def _default_shows(self) -> str:
        """What the kits show — plates while any of them is on plates, else the
        collage. With no kit, what the viewers show; with no screen at all, the
        last picture this server drew."""
        viewers_on = [r for r in self.frames.by_transport(*viewers_mod.KINDS)
                      if r.get("status") == frames_mod.ON]
        for rows, asked in ((self._kits_on(), frames_mod.shows_of),
                            (viewers_on, viewers_mod.shows_of)):
            kinds = [k for k in (asked(r) for r in rows) if k in pictures_mod.KINDS]
            if kinds:
                return PLATES if PLATES in kinds else COLLAGE
        return self.pictures.shown if self.pictures.shown in pictures_mod.KINDS else PLATES

    @property
    def _shown(self) -> str:
        """Which picture that is right now — the night rule applies to it too."""
        return self._kind_for(None, self._clock())

    @_shown.setter
    def _shown(self, kind: str) -> None:
        self.pictures.shown = kind

    @property
    def _meta(self) -> dict:
        return self.pictures[self._shown].meta

    @_meta.setter
    def _meta(self, meta: dict) -> None:
        # A test seam as much as anything: assigning a meta says which picture
        # it is, so `mode` picks the picture it belongs to.
        kind = pictures_mod.kind_of_mode((meta or {}).get("mode"))
        self.pictures.shown = kind
        self.pictures[kind].meta = meta

    @property
    def _etag(self) -> Optional[str]:
        return self.pictures[self._shown].etag

    @_etag.setter
    def _etag(self, etag: Optional[str]) -> None:
        self.pictures[self._shown].etag = etag

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ff-scheduler", daemon=True)
        self._thread.start()
        log.info("scheduler started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("scheduler stopped")

    def _run(self) -> None:
        # Give the device something immediately even before the first birds.
        try:
            self._ensure_initial_frame()
        except Exception:  # never let the loop die
            log.exception("initial frame failed")
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed (keeping current frame)")
            self._stop.wait(self._effective_poll_seconds())

    def _effective_poll_seconds(self) -> int:
        """Poll cadence, floored for cloud sources so we don't hammer them —
        BirdWeather is a public API, so never poll it faster than 60s."""
        if self.config.detection_backend == "birdweather":
            return _CLOUD_POLL_SECONDS
        return POLL_SECONDS

    # -- providers ---------------------------------------------------------
    def _build_provider(self, config: Config) -> ArtProvider:
        """Audubon first, AI-generated second, typographic fallback implied.
        The generated link always serves already-bought plates from its cache;
        imagegen_enabled (and a key) only govern whether NEW plates are bought
        — turning the feature off must never hide art the user paid for."""
        self.genart = GeneratedArtProvider(make_image_model(config),
                                           text_model=make_text_model(config))
        return ChainedProvider([self.audubon, self.genart])

    @staticmethod
    def _imagegen_fields(config: Config) -> tuple:
        return (config.imagegen_enabled, config.imagegen_provider,
                config.imagegen_model, config.imagegen_quality,
                config.imagegen_text_model, config.imagegen_api_key,
                config.imagegen_base_url, config.imagegen_text_provider,
                config.imagegen_text_key, config.imagegen_text_base_url)

    # -- config ------------------------------------------------------------
    def reload_config(self) -> None:
        with self._lock:
            new = load_config(self.db)
            if (new.detection_backend != self.config.detection_backend
                    or new.birdnet_db_path != self.config.birdnet_db_path
                    or new.birdnet_go_url != self.config.birdnet_go_url
                    or new.birdweather_station_id != self.config.birdweather_station_id):
                self.source = make_source(new, self.db)
                self._reset_for_source()
            if self._imagegen_fields(new) != self._imagegen_fields(self.config):
                self.provider = self._build_provider(new)
            self.config = new

    def _reset_for_source(self) -> None:
        """A new detection source starts from a clean slate. Everything
        transient was about the old one: the cursor is in its id space
        (BirdWeather ids run ~11 billion, BirdNET-Go's ~450k — a leftover
        froze the frame for hours), and the hold, the collage
        clock, the waiting species and the outage clock all describe birds
        it heard. The next tick shows the new source's latest detection."""
        for key in ("ingest_cursor", "pending_species",
                    "quiet_collage_for", "source_down_since", _USER_HOLD_KEY):
            self.db.set(key, None)
        self._pending = None
        self._source_down_since = None
        self._tick_memo = {}
        self.pictures[COLLAGE].meta.pop("collage_at", None)
        self._source_switched = True
        log.info("detection source changed: starting from a clean slate")

    def update_config(self, config: Config) -> None:
        with self._lock:
            save_config(self.db, config)
        self.reload_config()

    # -- the decision loop -------------------------------------------------
    def tick(self) -> None:
        try:
            self._tick()
        finally:
            # Whatever this tick changed, every frame on a socket hears of it.
            self.push.notify()

    def _tick(self) -> None:
        self._tick_pictures()
        # After drawing, not before: a picture nobody shows may still be the
        # only one a frame has to fall back on until its own is drawn.
        for kind in set(pictures_mod.KINDS) - self._kinds_shown(resolve=False):
            self._drop_picture(kind)
        self._tick_frames()
        try:
            self._tick_firmware()
        except Exception:  # noqa: BLE001 — an update must never stop the frames
            log.warning("firmware tick failed", exc_info=True)

    def _tick_pictures(self) -> None:
        self.reload_config()
        now = self._clock()
        available = self.source.available()
        # Computed first so any render this tick — including the flips below —
        # carries the right footnote.
        self._track_source(now, available)
        self._quiet = self.quiet_state(now, available=available)
        self._outage = self.outage_state(now)

        wanted = self._kinds_shown(now)

        # What the glass itself owes, before either picture's own subject. Each
        # of these settles a picture for this tick — the ones no frame on the
        # wall is waiting on are still drawn, for the screens that show them.
        settled: set = set()
        showing = self._etag is not None
        subject = showing and bool(self._meta.get("label"))
        want, have = self._note_kind(), self._note_showing()

        if showing and self._meta.get("mode") == "welcome":
            # The welcome plate (W-734) is not a subject: no footnotes, no
            # dwell. It re-renders only when what it says would change, else
            # the decision path below may replace it.
            if bool(self._meta.get("source_ok")) != available:
                self._render_welcome(now, available)
                settled = {self._shown}
        elif showing and self.user_hold(now) is not None:
            # The owner pinned this plate (W-735): nothing replaces it until
            # the hold ends, and a hold pins the plate and nothing else
            # (W-830) — the collage keeps being drawn for the screens that
            # show it. The footnotes still track, through the re-render that
            # keeps the subject; an expired hold clears itself in user_hold().
            if want != have and not (want is None and not available):
                self.rerender_current()
            settled = {PLATES, self._shown}
        elif self._meta.pop("dark", False) and subject:
            # A frame rendered inverted before dark mode was removed (W-821) is
            # redrawn once: the firmware is now told not to invert, and its
            # light pills would land on a dark plate until the next detection.
            self.rerender_current()
            settled = {self._shown}
        elif subject and want != have and not (want is None and not available):
            # The footnote (gone-quiet, or a source outage): re-render the
            # subject on the glass once when a note flips on or switches kind.
            # When one flips off, prefer the decision path's own render (a
            # fresh bird is what usually clears it) and only re-render to drop
            # the note if nothing else replaced the frame — one render per
            # picture per tick either way. An outage under its threshold reads
            # as "unknown": nothing is dropped or added while the source is
            # unreachable and no outage note is due.
            if want is not None and (have is None or not available):
                self.rerender_current()
                settled = {self._shown}
            else:
                before = self._etag
                self._draw(wanted, now, available)
                if self._etag == before:
                    self.rerender_current()
                return

        self._draw(wanted - settled, now, available)

    def _note_showing(self) -> Optional[str]:
        """The footnote the glass is actually carrying (older frames only said
        whether there was one)."""
        return self._meta.get("note_kind") or ("quiet" if self._meta.get("quiet_note") else None)

    def _draw(self, wanted: set, now: datetime, available: bool) -> None:
        """Draw every picture some frame shows, at most one render each. The
        one on the glass goes first: if it falls back to the other kind, that
        one is already drawn and is not drawn again."""
        if self.config.in_quiet_hours(now.time()):
            # Quiet hours: the pictures hold still. One nightly collage at the
            # start of the window, which every frame on plates then shows.
            if self.config.quiet_hours_render_collage:
                self._maybe_quiet_collage(now)
            return

        if not available:
            return  # soft fail; keep serving the current frame

        first = self._wall_kind(now)
        order = [first] + [k for k in pictures_mod.KINDS if k != first]
        for kind in order:
            if kind not in wanted:
                continue
            if kind != first and kind == self._shown:
                continue   # the glass took this picture after all: leave it be
            if kind == COLLAGE:
                self._maybe_daytime_collage(now)
            else:
                self._single_tick(now)

    # -- which picture a frame shows ---------------------------------------
    def _nightly_collage_showing(self, now: datetime) -> bool:
        """Tonight's collage has been drawn and the quiet window is still on.
        One collage, the same on every screen (W-830): for the rest of the
        window every frame that shows plates shows it too."""
        return (self.config.quiet_hours_render_collage
                and self.config.in_quiet_hours(now.time())
                and self.db.get("quiet_collage_for") == self._collage_date(now).isoformat())

    def _kind_for(self, shows: Optional[str], now: datetime) -> str:
        """Which picture a frame that shows `shows` gets. The ONE place the
        night rule lives; nothing else may decide it."""
        kind = shows if shows in pictures_mod.KINDS else self._default_shows()
        if kind == PLATES and self._nightly_collage_showing(now):
            kind = COLLAGE
        return kind

    def _wall_kind(self, now: datetime) -> str:
        """The picture drawn first this tick: the one the page's tools mean."""
        return self._kind_for(None, now)

    def picture_for(self, shows: Optional[str] = None,
                    now: Optional[datetime] = None) -> "pictures_mod.Picture":
        """The picture one frame shows. `shows` is "plates", "collage", or
        None for the picture the page's own tools mean. A picture that has
        never been drawn falls back to the other one, so a screen always has
        something."""
        now = now or self._clock()
        pic = self.pictures[self._kind_for(shows, now)]
        if pic.etag:
            return pic
        other = self.pictures[COLLAGE if pic.kind == PLATES else PLATES]
        return other if other.etag else pic

    def picture_etag(self, shows: Optional[str] = None) -> Optional[str]:
        return self.picture_for(shows).etag

    def _shows_of_frames(self, now: datetime) -> list:
        """What every frame this server draws for shows, as each one asked for
        it (None = whatever the page's tools mean). A viewer that has not asked
        in a month is not a frame any more; a server with no frames at all
        still needs a first picture, so it counts as one that says nothing."""
        cutoff = (now - timedelta(days=VIEWER_SHOWS_DAYS)).isoformat(timespec="seconds")
        out = []
        for row in self.frames.all().values():
            if row.get("status") != frames_mod.ON:
                continue   # asking or ignored: nothing is drawn for it
            if frames_mod.transport_of(row) == "kit":
                out.append(frames_mod.shows_of(row))
            elif (row.get("last_seen") or "") >= cutoff:
                out.append(viewers_mod.shows_of(row))
        return out or [None]

    def _kinds_shown(self, now: Optional[datetime] = None, resolve: bool = True) -> set:
        """The pictures some frame shows, and so the only ones worth drawing.
        `resolve` applies the night rule (a frame on plates shows the collage);
        without it, what each frame is SET to — which is what decides whether a
        picture is still wanted at all, so a plate is not thrown away at
        nightfall and redrawn at dawn."""
        now = now or self._clock()
        if resolve:
            return {self._kind_for(s, now) for s in self._shows_of_frames(now)}
        return {s if s in pictures_mod.KINDS else self._default_shows()
                for s in self._shows_of_frames(now)}

    def _drop_picture(self, kind: str) -> None:
        """Nobody is SET to show it any more. Two are kept all the same: the
        one a frame is showing right now (at night that is the collage, which
        no frame is set to), and the last one there is — a frame whose own
        picture has not been drawn yet falls back to it (`picture_for`), and
        dropping either would blank that glass."""
        if not self.pictures[kind].etag or kind == self._shown:
            return
        if all(p.etag is None for k, p in self.pictures.items() if k != kind):
            return
        with self._lock:
            self.pictures[kind].drop()
            self._recolor.discard(kind)
            self.pictures.save()
        log.info("nobody shows the %s picture any more: dropped", kind)

    # -- gone-quiet alarm --------------------------------------------------
    def quiet_state(self, now: datetime,
                    available: Optional[bool] = None) -> Optional[dict]:
        """The alarm, or None. Counts ACTIVE minutes since the last qualifying
        detection — minutes outside quiet hours — so a silent night never
        trips it. With no detection on record the clock starts at service
        start (the earliest silence we can vouch for). An unreachable source
        is "unknown", not "quiet": the Source card already says "Not
        reachable", and alarming on an outage would be a second, wrong
        diagnosis."""
        hours = QUIET_ALARM_HOURS
        if available is None:
            available = self.source.available()
        if not available:
            return None
        latest = self.source.latest(CONFIDENCE_FLOOR)
        since = latest.timestamp if latest else self._started_at
        if since == datetime.min or since > now:
            return None  # unparseable stamp, or a clock skew we can't reason about
        active = self._active_minutes(since, now)
        if active < hours * 60:
            return None
        hours_active = round(active / 60, 1)
        return {"since": since.isoformat(timespec="seconds"),
                "since_text": when_text(since, now),
                "hours": hours_active, "hours_text": _hours_text(hours_active)}

    def _active_minutes(self, since: datetime, now: datetime) -> float:
        """Minutes between `since` and `now` that fall outside quiet hours,
        sampled every _QUIET_STEP. Walks at most _QUIET_MAX_SPAN back: past
        that the alarm is on whatever the answer, and the hours shown are a
        floor."""
        t = max(since, now - _QUIET_MAX_SPAN)
        active = 0.0
        while t < now:
            if not self.config.in_quiet_hours(t.time()):
                active += min(_QUIET_STEP, now - t).total_seconds() / 60
            t += _QUIET_STEP
        return active

    # -- source-outage note ------------------------------------------------
    def _track_source(self, now: datetime, available: bool) -> None:
        """Start the outage clock on the first unreachable tick, stop it on
        the first good one. Persisted so a restart mid-outage (the usual
        shape: the whole box rebooted) keeps counting from the real start."""
        if available:
            if self._source_down_since is not None:
                self._source_down_since = None
                self.db.set("source_down_since", None)
            return
        if self._source_down_since is None:
            self._source_down_since = now
            self.db.set("source_down_since", now.isoformat(timespec="seconds"))

    def outage_state(self, now: datetime) -> Optional[dict]:
        """The source-outage alarm, or None: the source has been unreachable
        for at least SOURCE_ALARM_MINUTES. Same shape as quiet_state so the
        page and the plate share one footnote path."""
        minutes = SOURCE_ALARM_MINUTES
        since = self._source_down_since
        if since is None:
            return None
        if since > now:
            return None  # a clock we can't reason about
        elapsed = (now - since).total_seconds() / 60
        if elapsed < minutes:
            return None
        hours = round(elapsed / 60, 1)
        return {"since": since.isoformat(timespec="seconds"),
                "since_text": when_text(since, now),
                "hours": hours, "hours_text": _hours_text(hours)}

    def _note_kind(self) -> Optional[str]:
        """Which footnote the glass should carry right now: "quiet" (nothing
        heard), "outage" (source unreachable), or None. Mutually exclusive
        by construction — quiet_state is None while the source is down."""
        if self._quiet:
            return "quiet"
        if self._outage:
            return "outage"
        # "latest": the held plate names what it turned away, for as long as
        # the hold lasts and the line is about some other species.
        if (self._just_now and self._just_now.get("key") != self._meta.get("species_key")
                and self._holding(self._meta, self._clock())):
            return "latest"
        return None

    def _note_text(self) -> Optional[str]:
        """The plate footnote while an alarm is on. Derived from the state
        cached at the top of this tick, which already reflects any detection
        the tick is about to render — so a fresh bird never carries it."""
        kind = self._note_kind()
        if kind == "quiet":
            return f"No detections since {self._quiet['since_text']}"
        if kind == "outage":
            return f"Detection source unreachable since {self._outage['since_text']}"
        if kind == "latest":
            return f"Just now: {self._just_now['common']}"
        return None

    # -- the plates picture ------------------------------------------------
    def _single_tick(self, now: datetime) -> None:
        """The plates picture: the bird that was just heard. Runs once a tick,
        whether the picture is on the wall or only on a viewer — a plate is a
        plate, and the cursor, the corroboration gate and the dwell hold are
        the same decision either way."""
        pic = self.pictures[PLATES]
        empty = pic.etag is None
        self._memo(now)
        self._expire_pending(now)
        cursor = self._cursor()
        if cursor is None:
            # First run, or a new source: start at the tail so we don't replay
            # history, but show the most recent existing detection once.
            self._set_cursor(self.source.max_rowid())
            switched, self._source_switched = self._source_switched, False
            if empty or switched:
                latest = self._first_showable(
                    self.source.latest_many(CONFIDENCE_FLOOR), now)
                if latest:
                    self._render_single(latest, now,
                                        reason="source-switch" if switched else "startup")
            return

        # One-time stale-cursor guard. A cursor left *ahead* of every real rowid
        # can never see anything "new", freezing the frame forever — it happens
        # when the detection id scheme changes under the stored cursor (e.g. a
        # BirdNET SQLite → BirdNET-Go REST switch leaves a huge timestamp-like
        # value behind). That is a between-runs condition (mid-run the cursor only
        # advances to real rowids, and a source switch clears it), so check it once
        # at startup rather than paying
        # a max_rowid() call — a network round-trip for the live source — on every
        # tick. Guard on max_rowid > 0 so a transient source blip (soft-fails to 0)
        # never trips it.
        if not self._cursor_verified:
            self._cursor_verified = True
            max_rowid = self.source.max_rowid()
            if max_rowid > 0 and cursor > max_rowid:
                self._set_cursor(max_rowid)
                latest = self._first_showable(
                    self.source.latest_many(CONFIDENCE_FLOOR), now)
                if latest:
                    self._render_single(latest, now, reason="cursor-reset")
                return

        # The cursor always advances past the page, corroborated or not: a
        # held-back bird gets its second chance because its second detection
        # is a NEW row that arrives later and, corroborated by the first via
        # latest_many, passes the gate then.
        new = self.source.new_since(cursor, CONFIDENCE_FLOOR,
                                    limit=_INGEST_PAGE)
        if len(new) >= _INGEST_PAGE:
            # Backlog: the page is full, so its newest row is not the newest
            # bird. Show the actual latest detection and jump the cursor to
            # the tail, instead of a stale bird now and another one per tick.
            # The cursor moves only once the tail is in hand: latest_many
            # soft-fails to [] on a blip, and jumping first would swallow the
            # whole backlog and render nothing.
            latest = self.source.latest_many(CONFIDENCE_FLOOR)
            if latest:
                self._set_cursor(max(self.source.max_rowid(), new[-1].rowid))
                candidate = self._best_showable(latest, now)
            else:
                # Can't see the tail right now: fall back to the page we have.
                self._set_cursor(new[-1].rowid)
                candidate = self._best_showable(list(reversed(new)), now)
        else:
            if new:
                self._set_cursor(new[-1].rowid)
            candidate = self._best_showable(list(reversed(new)), now)  # most novel, then newest
        if candidate is None:
            if empty:
                # A screen has just been pointed at plates while the cursor was
                # already past the tail: start it on the latest bird rather
                # than leave it blank until the next detection.
                latest = self._first_showable(self.source.latest_many(CONFIDENCE_FLOOR), now)
                if latest:
                    self._render_single(latest, now, reason="startup")
            return

        # Dwell: a new bird keeps the frame against repeats of common birds.
        # Only a repeat of ANOTHER species is turned away — the held bird may
        # re-render (the clock moves with it), and a novel bird takes over
        # (newest novel wins). The cursor has already advanced: the repeat is
        # simply not shown, which is the point.
        holding = self._holding(pic.meta, now)
        if (holding and self._novelty(candidate, now) == "repeat"
                and candidate.key != pic.meta.get("species_key")):
            log.info("holding %s (%s) against %s for %d more min",
                     pic.meta.get("label"), pic.meta.get("novelty"),
                     candidate.common_name, holding["minutes_left"])
            # The picture holds, but the frame still says what was just heard
            # (W-776). One repaint per change of species on the line, never
            # per detection: a chatty chickadee must not repaint the panel
            # every 30 s, which is also why the line carries no time.
            if (self._just_now or {}).get("key") != candidate.key:
                self._just_now = {"key": candidate.key, "common": candidate.common_name}
                self._rerender_picture(PLATES)
            return

        self._render_single(candidate, now, reason="detection")

    def _render_single(self, det: Detection, now: datetime, reason: str) -> None:
        """Draw the plates picture of this detection."""
        first_seen = self._first_seen(det.scientific_name)
        novelty = self._novelty(det, now)
        # Any render that isn't the subject redrawn ("settings") is news: a new
        # subject, or the held species heard again — either way the picture now
        # says it, and the "Just now:" line goes.
        if reason != "settings":
            self._just_now = None
        note = self._note_text()
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=det.timestamp if det.timestamp != datetime.min else now,
                          first_seen=first_seen, note=note,
                          note_kind=self._note_kind() if note else None,
                          first_ever=novelty == "first-ever")
        recompose = self._single_in_color(spec)
        etag = self._commit(
            PLATES, now, sheet=compose_mod.render_single(spec, self.provider, color=False),
            mode="single", species_key=det.key, label=det.common_name, note=note,
            novelty=novelty, key=f"{det.key}@{det.rowid}", recompose=recompose)
        log.info("rendered single %s (%s, %s), etag=%s", det.common_name, reason, novelty, etag)
        # The bird the page said it was waiting on is now on a screen.
        if self._pending and self._pending.get("key") == det.key:
            self._set_pending(None)

    # -- the collage picture -----------------------------------------------
    def _maybe_daytime_collage(self, now: datetime) -> None:
        pic = self.pictures[COLLAGE]
        interval = self.config.collage_interval_hours * 3600
        last = pic.meta.get("collage_at")
        try:
            last_at = datetime.fromisoformat(last) if last else None
        except (ValueError, TypeError):
            last_at = None  # an unreadable stamp must not kill every tick
        today = ddate.today().isoformat()
        fresh = (last_at is not None and (now - last_at).total_seconds() < interval
                 and pic.etag is not None and pic.key == today)
        if fresh and COLLAGE not in self._recolor:
            return
        self._build_collage(now, ddate.today())

    def _collage_day(self, now: datetime) -> ddate:
        """The day the collage picture is already of (its key), else today."""
        try:
            return ddate.fromisoformat(str(self.pictures[COLLAGE].key))
        except (TypeError, ValueError):
            return ddate.today()

    def _collage_date(self, now: datetime) -> ddate:
        """The day tonight's collage covers, per the ACTIVE quiet window — in
        "sun" mode that is sunset->sunrise, not the custom start/end fields
        (which may be left at a non-wrapping daytime window)."""
        start, end = self.config.quiet_window(now.date())
        return collage_date_for(now, start, end)

    def _maybe_quiet_collage(self, now: datetime) -> None:
        # The nightly collage covers the day the quiet window STARTED: after
        # midnight (default quiet hours wrap it) tonight's is yesterday's day.
        # Keying by now.date() would clobber the held sheet at 00:00, buy a
        # pre-dawn sheet of two owls, and skip the real one every evening.
        # It is drawn ONCE, as the collage picture, and every frame on plates
        # then shows it for the rest of the window (see `_kind_for`): there is
        # one collage, and it is the same sheet on every screen.
        on_date = self._collage_date(now)
        stamp = on_date.isoformat()
        if self.db.get("quiet_collage_for") == stamp:
            return  # already rendered this window's collage
        if self._build_collage(now, on_date):
            self.db.set("quiet_collage_for", stamp)

    def _collage_result(self, on_date: ddate,
                        config: Optional[Config] = None) -> Optional[RenderResult]:
        """Render a plain (non-generated) collage for one day, or None if
        fewer than 2 species. Used by the transient button view; `config` is
        the asking frame's, else the household's."""
        config = config or self.config
        rows = self.source.top_species_today(on_date, CONFIDENCE_FLOOR,
                                             limit=self.config.collage_species_max or 500)
        rows = [r for r in rows if not self.config.is_blocked(r["common"], r["scientific"])]
        if len(rows) < 2:
            return None
        cells = [collage_mod.CollageCell(r["common"], r["scientific"], r["count"]) for r in rows]
        img = collage_mod.render_collage(cells, self.provider, when=on_date,
                                         total_detections=sum(r["count"] for r in rows),
                                         color=config.panel_spec.color)
        return pipeline.render_image(img, config, "collage", f"{len(cells)} species")

    def force_collage(self, repaint: bool = False) -> bool:
        """The config-page button: render today's collage now, combined into
        one image when that is on. Reuses
        today's cached sheet unless repaint buys a fresh one."""
        now = self._clock()
        on_date = self._collage_date(now)
        return self._build_collage(now, on_date, force_generated=repaint)

    def _build_collage(self, now: datetime, on_date: ddate,
                       force_generated: bool = False) -> bool:
        """Draw the collage picture for `on_date`."""
        composed = self._collage_composer(now, on_date)
        if composed is None:
            # Not enough for a grid: fall back to a plate for the day. This
            # runs on every tick while the day has one species, so skip the
            # render when that bird is already on the glass — otherwise it is
            # a full-panel render every 20 s all day (and all night in quiet
            # hours). A screen on the collage keeps the plate too: there is
            # nothing else to give it.
            latest = self._first_showable(
                self.source.latest_many(CONFIDENCE_FLOOR), now)
            if latest and not self._showing_single(latest):
                self._render_single(latest, now, reason="collage-fallback")
            return False
        compose, note = composed
        sheet, label = compose(False, force=force_generated)
        etag = self._commit(COLLAGE, now, sheet=sheet, mode="collage", species_key=None,
                            note=note, label=label, key=on_date.isoformat(),
                            recompose=lambda: compose(True)[0])
        log.info("rendered collage (%s), etag=%s", label, etag)
        self._keep_collage_day(on_date)
        return True

    def _keep_collage_day(self, on_date: ddate) -> None:
        """The day's finished collage, to download: colour when it was drawn
        in colour, else gray. A redraw replaces it, so the last of the day
        (the nightly one) is what is kept. Best-effort, like a thumbnail."""
        pic = self.pictures[COLLAGE]
        try:
            src = pic.sheets(color=True)
            if not src:
                return
            days = paths.collage_days_dir()
            tmp = days / f"{on_date.isoformat()}.tmp"
            shutil.copyfile(src[0], tmp)
            os.replace(tmp, days / f"{on_date.isoformat()}.png")
            kept = sorted(p for p in days.glob("*.png") if _DATE_RE.match(p.stem))
            for old in kept[:-COLLAGE_DAYS_KEPT]:
                old.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — never worth a failed collage
            log.warning("collage for %s not kept", on_date, exc_info=True)

    def collage_days(self) -> list[dict]:
        """The kept collages, newest first, for the page's download links."""
        out = []
        for p in sorted(paths.collage_days_dir().glob("*.png"), reverse=True):
            if not _DATE_RE.match(p.stem):
                continue
            try:
                day = ddate.fromisoformat(p.stem)
            except ValueError:
                continue
            out.append({"date": p.stem, "url": f"/api/collages/{p.stem}.png",
                        "text": f"{day:%a} {day.day} {day:%b}"})
        return out[:COLLAGE_DAYS_KEPT]

    def _collage_composer(self, now: datetime, on_date: ddate):
        """(compose, note) for the day's collage, or None when fewer than two
        species qualify. `compose(color, force=False) -> (sheet, label)`. The
        one collage (W-830): the frame's and a viewer's are drawn by this."""
        cap = self.config.collage_species_max  # 0 = every species heard today
        rows = self.source.top_species_today(on_date, CONFIDENCE_FLOOR,
                                             limit=cap or 500)
        rows = [r for r in rows if not self.config.is_blocked(r["common"], r["scientific"])]
        rows = self._corroborated_rows(rows, on_date)
        if len(rows) < 2:
            return None
        cells = [collage_mod.CollageCell(r["common"], r["scientific"], r["count"]) for r in rows]
        # The species limit is the collage's, however it is drawn: the grid and
        # the generated sheet show the same species.
        top = cells[:cap] if cap else cells
        note = self._note_text()

        note_kind = self._note_kind() if note else None
        # All or nothing: with the toggle on and image generation to hand,
        # EVERY collage is the generated sheet — the nightly one, a daytime
        # rebuild, the button, a settings re-render. The cost is bounded by
        # genart.day_composite, which buys one sheet per day and reuses it
        # until the day's species list itself changes.
        use_generated = self.config.collage_generated and self.genart is not None

        def compose(color: bool, force: bool = False):
            """(sheet, label) for this day; `color` draws the art's colour twin."""
            if use_generated:
                self.genart.color_sheets = color
                sheet = self.genart.day_composite(top, on_date, force=force)
                if sheet is not None:
                    # The key must name what was PAINTED: on a cache hit the cells
                    # come from the sheet's sidecar, not tonight's fresh tally.
                    art, painted = sheet
                    return (collage_mod.render_generated_collage(
                                art, painted, when=on_date,
                                total_detections=sum(c.count for c in painted),
                                note=note, note_kind=note_kind),
                            f"combined collage ({len(painted)} species)")
            return (collage_mod.render_collage(top, self.provider, when=on_date,
                                               total_detections=sum(c.count for c in top),
                                               note=note, note_kind=note_kind,
                                               color=color),
                    f"{len(top)}-species collage")

        return compose, note

    # -- generated-plate management (config page) --------------------------
    def regenerate_generated(self, slug: str) -> bool:
        """Explicit user request for a fresh AI plate, addressed by cache slug.
        If the frame currently shows this species, re-render with the new art."""
        meta = next((m for m in self.genart.cached_species()
                     if m.get("slug") == slug), None)
        if meta is None:
            return False
        common = meta.get("common") or slug
        sci = meta.get("scientific") or ""
        ok = self.genart.regenerate(common, sci)
        current = (sci or common).strip().lower()
        if ok and current and self.pictures[PLATES].meta.get("species_key") == current:
            now = self._clock()
            det = Detection(rowid=-1, date=now.strftime("%Y-%m-%d"),
                            time=now.strftime("%H:%M:%S"), common_name=common,
                            scientific_name=sci, confidence=1.0)
            self._render_single(det, now, reason="regenerated")
        return ok

    def start_regenerate(self, slug: str) -> bool:
        """Kick off a regeneration in a worker thread and return immediately
        (a generation is a 30-60 s network call — the web handler must not
        wait on it). True means a job is now running for this slug; a request
        while one is already in flight joins it rather than buying a second
        image. False means the slug isn't cached. genart's module-level
        generation lock serializes the actual purchases, so concurrent slugs
        form a queue of one."""
        if not any(m.get("slug") == slug for m in self.genart.cached_species()):
            return False
        with self._regen_lock:
            if slug in self._regen_inflight:
                return True
            self._regen_inflight.add(slug)
            self._regen_errors.pop(slug, None)
        threading.Thread(target=self._regen_worker, args=(slug,),
                         name=f"ff-regen-{slug}", daemon=True).start()
        return True

    def _regen_worker(self, slug: str) -> None:
        """Runs regenerate_generated off the request thread. The in-flight
        flag must clear on every exit path — a stuck flag would pin the page
        on "Repainting…" and block further repaints of the species."""
        error: Optional[str] = None
        try:
            if not self.regenerate_generated(slug):
                error = "generation failed — the previous plate is kept"
        except Exception as exc:
            log.exception("background regeneration failed for %s", slug)
            error = f"{type(exc).__name__}: {exc}"[:200]
        with self._regen_lock:
            self._regen_inflight.discard(slug)
            if error:
                self._regen_errors[slug] = error

    def generated_listing(self) -> list[dict]:
        """cached_species() plus live regeneration state, for the config page
        and its polling. Copies each sidecar dict so the flags never leak
        into the provider's own metadata."""
        with self._regen_lock:
            inflight = set(self._regen_inflight)
            errors = dict(self._regen_errors)
        out = []
        for meta in self.genart.cached_species():
            m = dict(meta)
            slug = str(m.get("slug") or "")
            m["regenerating"] = slug in inflight
            m["regen_error"] = errors.get(slug)
            out.append(m)
        return out

    def delete_generated(self, slug: str) -> bool:
        return self.genart.delete(slug)

    def export_generated(self, dest: Path) -> int:
        """Zip the generated-plate cache to `dest` (W-765); the plate count."""
        return self.genart.export_to(dest)

    def import_generated(self, fileobj) -> dict:
        """Restore plates from a backup zip. Never replaces a newer plate, nor
        one that is being regenerated right now. ValueError if it isn't a zip."""
        with self._regen_lock:
            busy = set(self._regen_inflight)
        return self.genart.import_from(fileobj, busy=busy)

    # -- background one-shot jobs (config page) ----------------------------
    def _start_task(self, key: str, fn, *args) -> bool:
        """Run fn(*args) on a worker thread, tracked under `key` so the page
        can poll task_status(). Starting a job that's already in flight joins
        the running one rather than launching a duplicate (and buying a second
        image); genart's own generation lock serializes the actual purchases."""
        with self._task_lock:
            if key in self._tasks_inflight:
                return True
            self._tasks_inflight.add(key)
            self._task_errors.pop(key, None)
        threading.Thread(target=self._task_worker, args=(key, fn, args),
                         name=f"ff-task-{key}", daemon=True).start()
        return True

    def _task_worker(self, key: str, fn, args: tuple) -> None:
        """Runs a job off the request thread. The in-flight flag clears on
        every exit path — a stuck flag would pin the page on a spinner and
        block the next run of that job."""
        error: Optional[str] = None
        try:
            fn(*args)
        except Exception as exc:
            log.exception("background task %s failed", key)
            error = f"{type(exc).__name__}: {exc}"[:200]
        with self._task_lock:
            self._tasks_inflight.discard(key)
            if error:
                self._task_errors[key] = error

    def start_test_detection(self, common_name: str, scientific_name: str) -> bool:
        return self._start_task("test-detection", self.force_test_detection,
                                common_name, scientific_name)

    def start_collage(self, repaint: bool = False) -> bool:
        return self._start_task("collage", self.force_collage, repaint)

    def task_status(self) -> dict:
        """Live state of the background one-shot jobs, for the config page's
        poller: which are running and any last error per job."""
        with self._task_lock:
            return {"running": sorted(self._tasks_inflight),
                    "errors": dict(self._task_errors)}

    # -- test detection ----------------------------------------------------
    def force_test_detection(self, common_name: str = "Northern Cardinal",
                             scientific_name: str = "Cardinalis cardinalis") -> str:
        """Inject a fake detection and render it now. The
        default is the Cardinal; any species name exercises the full provider
        chain, including AI generation for plate-less species. It also bypasses
        the new-species corroboration gate: this is a deliberate injection
        from the config page, not a detector guess, so a plate may be bought."""
        now = self._clock()
        det = Detection(rowid=-1, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M:%S"),
                        common_name=common_name, scientific_name=scientific_name,
                        confidence=0.99)
        # A test bird is injected, not heard: the source is still silent, so
        # the footnote (if on) stays truthful.
        note = self._note_text()
        # The plate says "first recorded today" when the source has never
        # heard the species; the meta carries no novelty, so a test bird
        # never holds the frame against the real ones (and bypasses any hold).
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=now, first_seen=now.strftime("%Y-%m-%d"),
                          note=note, note_kind=self._note_kind() if note else None,
                          first_ever=self._is_new_species(det.scientific_name, now.date()))
        etag = self._commit(PLATES, now,
                            sheet=compose_mod.render_single(spec, self.provider, color=False),
                            mode="single", species_key=det.key,
                            label=f"{det.common_name} (test)", note=note, novelty=None,
                            recompose=self._single_in_color(spec))
        log.info("rendered TEST detection, etag=%s", etag)
        return etag

    # -- frames ----------------------------------------------------------------
    # A frame is a frame (W-833). Every kit names itself (X-Device-Id, its MAC)
    # and, once it is on, is drawn for exactly like every other: its picture,
    # finished with its own config, kept as one output file. Every frame of
    # every transport is approved on the server first: a screen pointed at this
    # server asks, and shows that it is waiting, until the owner answers on the
    # page — the first kit on a fresh install included. Firmware that predates
    # the header is one frame called "legacy"; it becomes its real ID in place
    # when the same frame (same panel, same address) first sends one after an
    # update.
    LEGACY_FRAME = "legacy"
    _FRAME_TOUCH = timedelta(seconds=60)      # how often a parked frame's row is rewritten

    def admit_frame(self, frame_id: Optional[str], reported_panel: Optional[str],
                    board: Optional[str], ip: Optional[str],
                    facts: Optional[dict] = None) -> str:
        """Who is asking? Returns the row's status: "on" (serve it its own
        frame), "asking" or "ignored" (answer 403: not served, nothing
        recorded). `facts` is the frame's own description of its panel (the
        X-Panel-* headers)."""
        facts = {k: v for k, v in (facts or {}).items() if v} or None
        fid = (frame_id or "").strip()[:40] or self.LEGACY_FRAME
        now = self._clock()
        stamp = now.isoformat(timespec="seconds")
        with self._lock, self.frames.mutate() as rows:
            row = rows.get(fid)
            legacy = rows.get(self.LEGACY_FRAME)
            if legacy is not None and frames_mod.transport_of(legacy) != "kit":
                legacy = None
            dirty = False
            if row is None and fid != self.LEGACY_FRAME and legacy is not None:
                same_panel = (panels.from_report(reported_panel, facts)
                              == frames_mod.panel_of(legacy))
                if same_panel and (not ip or not legacy.get("ip") or ip == legacy.get("ip")):
                    row = rows.pop(self.LEGACY_FRAME)      # the same frame, updated: keep its seat
                    row["id"] = fid
                    rows[fid] = row
                    self._rename_output(self.LEGACY_FRAME, fid)
                    dirty = True
            fresh = row is None
            if fresh:
                row = rows[fid] = frames_mod.new_row(fid, "kit", stamp)
            rep = frames_mod.reported_of(row)
            seen = (("panel", reported_panel), ("board", board), ("facts", facts))
            changed = (fresh or any(rep.get(k) != v for k, v in seen if v)
                       or bool(ip and row.get("ip") != ip))
            try:
                stale = now - datetime.fromisoformat(row.get("last_seen") or "") >= self._FRAME_TOUCH
            except ValueError:
                stale = True
            if dirty or changed or stale:
                rep.update({k: v for k, v in seen if v})
                row["reported"] = rep
                if ip:
                    row["ip"] = ip
                row["last_seen"] = stamp
            else:
                rows.unchanged()
            status = row.get("status")
        if fresh and status == frames_mod.ASKING:
            log.info("a new frame is asking to connect: %s (%s) at %s", fid, reported_panel, ip)
        return status

    def checkin_viewer(self, viewer_id: str, now: datetime, transport: str = "trmnl",
                       reported: Optional[dict] = None, ip: Optional[str] = None) -> dict:
        """The same moment for a frame fed over plain HTTP: record that it
        asked, and what it said about itself. Absent facts leave the last
        report standing. It is answered with a picture rather than a status
        code, so its row comes back instead of the row's status."""
        stamp = now.isoformat(timespec="seconds")
        with self.frames.mutate() as rows:
            row = rows.get(viewer_id)
            if row is not None and frames_mod.transport_of(row) not in viewers_mod.KINDS:
                # A kit already holds this id. It is being served; something on
                # the LAN claiming its MAC must not take its seat. Serve the
                # picture, record nothing.
                rows.unchanged()
                log.warning("viewer %s has the same id as a frame; not recorded", viewer_id)
                return viewers_mod.new_row(viewer_id, transport, stamp)
            if row is None:
                row = rows[viewer_id] = viewers_mod.new_row(viewer_id, transport, stamp)
            row["reported"] = {**frames_mod.reported_of(row),
                               **{k: v for k, v in (reported or {}).items() if v is not None}}
            row["last_seen"] = stamp
            if ip:
                row["ip"] = ip
            # The LAN is untrusted: junk IDs must not grow the row forever. Only
            # these are ever dropped — a kit is answered for, not aged out — and
            # the owner's answer outranks the clock, so a screen they added or
            # ignored is kept and only the ones still asking age out.
            asking = [k for k, r in rows.items()
                      if frames_mod.transport_of(r) in viewers_mod.KINDS
                      and r.get("status") == frames_mod.ASKING]
            if len(asking) > viewers_mod.MAX_VIEWERS:
                asking.sort(key=lambda k: rows[k].get("last_seen") or "")
                for stale in asking[:len(asking) - viewers_mod.MAX_VIEWERS]:
                    rows.pop(stale, None)
        return row

    def frame_config(self, row: Optional[dict]) -> Config:
        """The config one frame is drawn with (frames.frame_config): the
        household's, with this frame's panel, its defaults, and the owner's
        choices for this frame."""
        return frames_mod.frame_config(row, self.config)

    def update_frame(self, frame_id: str, fields: dict) -> bool:
        """The owner's choices for ONE frame, whatever it is fed over: a kit,
        a TRMNL, a tablet. What may be set is `frames.capabilities` and nothing
        else — a page has no panel rotation, a TRMNL no mat — so a field a
        screen does not have is simply not taken. Unknown keys are ignored."""
        row = self.frames.get(frame_id)
        if row is None:
            return False
        caps = frames_mod.capabilities(row)
        if frames_mod.transport_of(row) == "kit":
            return self._update_kit(frame_id, fields, caps)
        allowed = ["name", "shows"]
        if caps["rotations"]:
            allowed.append("rotation")
        if caps["needs_size"]:
            allowed += ["width", "height"]
        take = {k: v for k, v in fields.items() if k in allowed}
        with self.frames.mutate() as rows:
            row = rows.get(frame_id)
            if row is None or frames_mod.transport_of(row) not in viewers_mod.KINDS:
                rows.unchanged()
                return False
            row["set"] = viewers_mod.own_settings(frames_mod.settings_of(row), take)
        return True

    def _update_kit(self, frame_id: str, fields: dict, caps: dict) -> bool:
        """One kit's own settings. Values go through Config's own sanitising —
        a rotation its panel cannot do is clamped here, not on the glass — and
        a blank clears the choice."""
        owned = ["shows", "name", "update_firmware"]
        if caps["rotations"]:
            owned.append("panel_rotation")
        if caps["mat"]:
            owned += ["mat_inset_pct", "mat_offset_x_px", "mat_offset_y_px", "mat_guide"]
        if caps["power"]:
            owned += ["power_mode", "wake_interval_minutes", "device_poll_seconds"]
        with self._lock, self.frames.mutate() as rows:
            row = rows.get(frame_id)
            if row is None or frames_mod.transport_of(row) != "kit":
                rows.unchanged()
                return False
            own = dict(frames_mod.settings_of(row))
            # A kit's "rotation" is its panel rotation; the page speaks one word.
            fields = dict(fields)
            if "rotation" in fields and "panel_rotation" not in fields:
                fields["panel_rotation"] = fields["rotation"]
            for key in owned:
                if key not in fields:
                    continue
                raw = fields[key]
                if raw in (None, ""):
                    own.pop(key, None)
                elif key == "shows":
                    if raw in pictures_mod.KINDS:
                        own[key] = raw
                elif key == "name":
                    own[key] = str(raw).strip()[:frames_mod.MAX_NAME]
                elif key == "update_firmware":
                    if raw is True or str(raw).lower() in ("1", "true", "on"):
                        own[key] = True
                    else:
                        own.pop(key, None)
                else:
                    own[key] = raw
            probe = frames_mod.frame_config({**row, "set": own}, self.config)
            for key in frames_mod.KIT_SETTINGS:      # keep what sanitize() made of it
                if key in own:
                    own[key] = getattr(probe, key)
            row["set"] = own
        return True

    def answer_frame(self, frame_id: str, action: str) -> bool:
        """The owner's answer about a frame that is not on yet: "add" (draw for
        it too), "ignore" (park it), "forget" (drop it, so it asks again the
        next time it checks in). Every screen is answered for, whatever it is
        fed over — a tablet on the kiosk page is let in the same way a kit is.
        There is no "replace": there is no current frame to replace (W-833)."""
        dropped: list = []
        with self._lock, self.frames.mutate() as rows:
            row = rows.get(frame_id)
            if row is None or action not in ("add", "ignore", "forget"):
                rows.unchanged()
                return False
            if action == "add":
                row["status"] = frames_mod.ON
                row.setdefault("set", {})
            elif action == "ignore":
                row["status"] = frames_mod.IGNORED
                dropped.append(frame_id)
            else:
                rows.pop(frame_id, None)
                dropped.append(frame_id)
        for fid in dropped:
            self._drop_output(fid)
        return True

    # -- firmware (W-838) ----------------------------------------------------
    # The frame says nothing about an update: the server offers the latest
    # official release on the frame's row, installs it when the owner presses
    # Update (or, with automatic updates on, for a frame already on an older
    # official release — a dev build is only ever replaced by the button), and
    # the frame takes it on its next check-in through /api/firmware.
    def firmware_view(self, row: dict) -> Optional[dict]:
        """What one kit runs, and what it could run. None for a viewer."""
        if frames_mod.transport_of(row) != "kit":
            return None
        rep = frames_mod.reported_of(row)
        running = str(rep.get("fw_version") or "")
        latest = self.releases.version()
        kit = self.releases.kit_for_board(rep.get("board"))
        available = None
        if (latest and kit and running != latest
                and not firmware_release.is_newer(running, latest)):
            available = latest
        official = firmware_release.parse_version(running) is not None
        auto = bool(available) and self.config.firmware_auto_update and official
        pressed = bool(frames_mod.settings_of(row).get("update_firmware"))
        return {"running": running, "latest": latest, "available": available,
                "pending": bool(available) and (pressed or auto), "auto": auto}

    def firmware_status(self) -> dict:
        """The household's view of the release: which one, and when it was asked."""
        st = self.releases.state()
        kits = [k.get("kit") for k in (self.releases.manifest() or {}).get("kits") or []
                if k.get("parts")]
        return {"latest": self.releases.version(), "checked_at": st.get("checked_at"),
                "error": st.get("error"), "auto": bool(self.config.firmware_auto_update),
                "kits": kits}

    def check_firmware(self) -> dict:
        """Ask for the latest release now (the page's *Check now*)."""
        self.releases.check(self._clock(), force=True)
        self._tick_firmware(check=False)
        return self.firmware_status()

    def _tick_firmware(self, check: bool = True) -> None:
        """Keep the release current, fetch the image a pending frame will ask
        for (so its check-in never waits on GitHub), and clear a pressed
        Update once the frame is on the release."""
        if check:
            self.releases.check(self._clock())
        done = []
        for row in self._kits_on():
            fw = self.firmware_view(row)
            if fw["pending"]:
                self.releases.app_for_board(frames_mod.reported_of(row).get("board"),
                                            download=True)
            elif frames_mod.settings_of(row).get("update_firmware") and not fw["available"]:
                done.append(row["id"])
        if done:
            with self._lock, self.frames.mutate() as rows:
                for fid in done:
                    if fid in rows:
                        own = dict(frames_mod.settings_of(rows[fid]))
                        own.pop("update_firmware", None)
                        rows[fid]["set"] = own

    def release_image_for(self, frame_id: Optional[str], board: Optional[str]):
        """The verified release image a frame is owed right now, or None:
        the frame is on, an update is pending for it, and the image for the
        board it names is already here. Noting it, so a dev image hosted
        before the update does not take the frame straight back."""
        row = self.frames.get(frame_id) if frame_id else None
        if (row is None or row.get("status") != frames_mod.ON
                or frames_mod.transport_of(row) != "kit"):
            return None
        rep = frames_mod.reported_of(row)
        if board and rep.get("board") and board != rep.get("board"):
            return None
        fw = self.firmware_view(row)
        if not fw or not fw["pending"]:
            return None
        path = self.releases.app_for_board(board or rep.get("board"))
        if path is not None:
            stamp = self._clock().isoformat(timespec="seconds")
            with self._lock, self.frames.mutate() as rows:
                if frame_id in rows:
                    rows[frame_id]["release_served_at"] = stamp
        return path

    def dev_image_superseded(self, frame_id: Optional[str], mtime: float) -> bool:
        """Whether a dev-hosted image (`make ota`) is older than the release
        this frame was last handed: the latest thing put in front of a frame
        wins, so an Update is not undone by yesterday's bench build."""
        row = self.frames.get(frame_id) if frame_id else None
        try:
            served = datetime.fromisoformat(str((row or {}).get("release_served_at")))
        except ValueError:
            return False
        return datetime.fromtimestamp(mtime) < served

    def forget_frame(self, frame_id: str) -> bool:
        """Remove any frame, whatever it is fed over. A kit asks to be added
        again the next time it checks in; a viewer simply comes back."""
        with self._lock:
            gone = self.frames.forget(frame_id)
        if gone:
            self._drop_output(frame_id)
        return gone

    # -- one output per frame -------------------------------------------------
    # The picture a frame shows, finished with that frame's own config: its
    # panel, rotation, mat and depth. Drawn in the tick, never in a request,
    # and only when its picture or its settings changed — so a handler that
    # answers /api/frame only ever reads bytes off disk.
    def _out_paths(self, frame_id: str):
        safe = re.sub(r"[^0-9A-Za-z_-]", "_", frame_id)
        d = paths.frames_dir() / "out"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe}.fff", d / f"{safe}.png"

    def _output_bytes(self, frame_id: str) -> Optional[bytes]:
        state = self._out.get(frame_id)
        if not state or not state.get("etag"):
            return None
        fff = self._out_paths(frame_id)[0]
        return fff.read_bytes() if fff.exists() else None

    def _output_etag(self, frame_id: str) -> Optional[str]:
        return (self._out.get(frame_id) or {}).get("etag")

    def _save_outputs(self) -> None:
        self.db.set(_OUT_KEY, self._out)

    def _drop_output(self, frame_id: str) -> None:
        with self._lock:
            if self._out.pop(frame_id, None) is not None:
                self._save_outputs()
        for f in self._out_paths(frame_id):
            f.unlink(missing_ok=True)

    def _rename_output(self, old_id: str, new_id: str) -> None:
        """Older firmware sent its first X-Device-Id: the same frame, so it
        keeps the picture already on its glass instead of a fresh paint."""
        state = self._out.pop(old_id, None)
        if state is None:
            return
        self._out[new_id] = state
        self._save_outputs()
        for src, dst in zip(self._out_paths(old_id), self._out_paths(new_id)):
            if src.exists():
                os.replace(src, dst)

    def _load_outputs(self) -> None:
        """Keep only outputs whose file is on disk and whole. A crash mid-write
        (or a rolled-back data dir) must never hand a device a torn container
        on every wake; without one, the next tick simply draws it again."""
        kept = {}
        for fid, state in self._out.items():
            fff = self._out_paths(fid)[0]
            data = fff.read_bytes() if fff.exists() else b""
            if framebuffer.is_complete(data) and framebuffer.etag_for(data) == state.get("etag"):
                kept[fid] = state
            elif data:
                log.warning("%s is not a complete frame (%d bytes); re-drawing",
                            fff.name, len(data))
        if kept != self._out:
            self._out = kept
            self._save_outputs()

    def _output_src(self, row: dict, now: datetime) -> Optional[str]:
        """What this frame's output was drawn from: its picture, the sheet
        file, and every setting that changes a pixel. Equal means nothing to
        redraw. None when the picture has no sheet to draw from yet."""
        cfg = self.frame_config(row)
        pic = self.picture_for(frames_mod.shows_of(row), now)
        if not pic.etag:
            return None
        source = next(iter(pic.sheets(cfg.panel_spec.color)), None)
        if source is None:
            return None
        return "|".join(str(x) for x in (pic.kind, pic.etag, source.name, cfg.panel,
                                         cfg.panel_rotation, cfg.mat_inset_pct,
                                         cfg.mat_offset_x_px, cfg.mat_offset_y_px,
                                         cfg.mat_guide, cfg.bit_depth))

    def _tick_frames(self) -> None:
        """Keep every frame's output in step with the picture it shows. One
        render at most per frame per tick."""
        now = self._clock()
        rows = self._kits_on()
        for stale in set(self._out) - {r["id"] for r in rows}:
            self._drop_output(stale)
        for row in rows:
            try:
                self._draw_frame(row, now)
            except Exception:  # noqa: BLE001 — one frame never costs another its tick
                log.warning("frame %s not drawn", str(row.get("id"))[-6:], exc_info=True)

    def _draw_frame(self, row: dict, now: datetime) -> None:
        fid = str(row["id"])
        cfg = self.frame_config(row)
        pic = self.picture_for(frames_mod.shows_of(row), now)
        if not pic.etag:
            return
        if cfg.panel_spec.color:
            # A colour frame reads the picture's colour twin; asking keeps it
            # composed. Gray is what a picture is kept in — a twin is extra.
            self._want_color(pic, viewer=False)
        source = next(iter(pic.sheets(cfg.panel_spec.color)), None)
        if source is None:
            return   # a picture from before sheets were kept: the next render has one
        variant = self._output_src(row, now)
        fff, png = self._out_paths(fid)
        if (self._out.get(fid) or {}).get("src") == variant and fff.exists():
            return
        with Image.open(source) as sheet:
            sheet.load()
        result = pipeline.render_image(sheet, cfg, pic.meta.get("mode") or "single",
                                       pic.meta.get("label") or "")
        tmp = fff.with_suffix(".tmp")
        tmp.write_bytes(result.frame)
        os.replace(tmp, fff)
        result.preview.save(png)
        with self._lock:
            self._out[fid] = {"etag": result.etag, "src": variant}
            self._save_outputs()
        log.info("drew the %s picture for frame %s (%s), etag=%s",
                 pic.kind, fid[-6:], cfg.panel, result.etag)

    def push_message(self, frame_id: str) -> Optional[dict]:
        """What a frame on a push socket is told (W-841): everything that
        changes what its next `GET /api/frame` would answer — its output, the
        headers that ride along — and whether a firmware update waits for it.
        Equal messages are not sent twice. None when the frame is not on, which
        closes the socket."""
        row = self.frames.get(frame_id)
        if (row is None or row.get("status") != frames_mod.ON
                or frames_mod.transport_of(row) != "kit"):
            return None
        cfg = self.frame_config(row)
        fw = self.firmware_view(row) or {}
        return {"etag": self._output_etag(frame_id) or "",
                "rotation": cfg.panel_rotation, "power": cfg.power_mode,
                "ota": bool(fw.get("pending"))}

    def mdns_panel(self) -> str:
        """The panel key advertised over mDNS. A frame whose panel no server
        claims takes any that answers, so one kit's key is enough; with none
        yet, the one a fresh install was seeded with."""
        row = next(iter(self.frames.on_kits()), None)
        return frames_mod.panel_for(row).key if row is not None else self.config.panel

    def frame_notices(self, row: dict) -> dict:
        """What THIS frame said about its panel that this server cannot
        honour. A frame's panel is simply what it reports (W-833), so there is
        nothing to answer — but the page still says what is being drawn."""
        out: dict = {"unrecognised": None, "unknown_format": None}
        if frames_mod.transport_of(row) != "kit":
            return out
        rep = frames_mod.reported_of(row)
        spec = frames_mod.panel_for(row)
        if rep.get("panel") and frames_mod.panel_of(row) is None:
            # Taken in, but it names no panel we know and sent no size: every
            # image is drawn for `spec`, which its firmware may well reject.
            out["unrecognised"] = {"label": rep.get("panel"), "drawing_for": spec.name}
        if spec.unknown_format:
            out["unknown_format"] = {"format": spec.unknown_format}
        return out

    # -- device-facing --------------------------------------------------------
    def get_frame(self, frame_id: str, if_none_match: Optional[str],
                  telemetry: Optional[dict] = None) -> tuple:
        """(status, body, etag) for one frame, recording its check-in on its
        own row. Reads bytes; the drawing happened in the tick."""
        etag = self._output_etag(frame_id)
        served = "304" if (etag and if_none_match == etag) else "frame"
        self._record_checkin(frame_id, {**(telemetry or {}), "last_result": served,
                                        "etag_served": etag})
        body = None if served == "304" else self._output_bytes(frame_id)
        if not etag or (served == "frame" and body is None):
            return 503, None, None
        if served == "304":
            return 304, None, etag
        return 200, body, etag

    def record_view_checkin(self, frame_id: str, view: str,
                            telemetry: Optional[dict] = None) -> None:
        """A button view was served: it is a check-in like any other, but the
        frame keeps whatever picture it had."""
        self._record_checkin(frame_id, {**(telemetry or {}), "last_result": view,
                                        "etag_served": self._output_etag(frame_id)})

    def _record_checkin(self, frame_id: str, telemetry: dict) -> None:
        # Telemetry is untrusted input off the LAN: every value is re-validated
        # here (finite, in range, bounded) because the row is persisted and
        # rendered — an old build let a NaN through and every /api/status 500'd.
        stamp = self._clock().isoformat(timespec="seconds")
        fields = _clean_device_fields({**telemetry, "last_checkin": stamp})
        # How long it was just told to wait, so "overdue" is measured against
        # that and not against a clock of the page's own.
        told = None
        known = self.frames.get(frame_id)
        if known is not None and frames_mod.transport_of(known) == "kit":
            poll_s, wake_min = self.frame_intervals(known)
            told = poll_s if self.frame_config(known).power_mode == "awake" else wake_min * 60
            if fields.get("push_s"):
                # On a push socket the frame's plain check-ins are a heartbeat
                # of its own choosing, and it says how far apart they are.
                told = fields["push_s"]
        with self._lock, self.frames.mutate() as rows:
            row = rows.get(frame_id)
            if row is None:
                rows.unchanged()
            else:
                # Merged, not replaced: `reported` also holds what the frame
                # said about its panel and its board.
                row["reported"] = {**frames_mod.reported_of(row),
                                   **{k: v for k, v in fields.items() if v is not None}}
                row["last_seen"] = stamp
                if told is not None:
                    row["told_s"] = int(told)
        volt = fields.get("battery_voltage")
        if volt is None or volt < _BATTERY_ABSENT_V:
            return
        with self._lock:
            live = [r for r in self._battery_live.get(frame_id, [])
                    if datetime.fromisoformat(r["at"]) >= datetime.fromisoformat(stamp) - _LIVE_KEEP]
            live.append({"at": stamp, "voltage": volt, "percent": fields.get("battery_percent")})
            self._battery_live[frame_id] = live
        try:
            self.db.log_battery(stamp, volt, fields.get("battery_percent"), frame_id)
        except Exception:  # noqa: BLE001 — a log row must never fail a check-in
            log.debug("battery log write failed", exc_info=True)

    def current_png_bytes(self) -> Optional[bytes]:
        """The picture itself, as composed: what the page shows before any
        frame is picked, and on a server with no frames at all. One frame's own
        output is `frame_png_bytes`."""
        sheet = self.pictures[self._shown].sheet_path
        return sheet.read_bytes() if sheet.exists() else None

    def frame_png_bytes(self, frame_id: str) -> Optional[bytes]:
        """One kit's own output, as the preview on the page."""
        png = self._out_paths(frame_id)[1]
        return png.read_bytes() if png.exists() else None

    def current_etag(self) -> Optional[str]:
        with self._lock:
            return self._etag

    def _battery_recent(self, frame_id: Optional[str], hours: float = 2) -> list:
        since = (self._clock() - timedelta(hours=hours)).isoformat(timespec="seconds")
        try:
            return self.db.battery_history(since, frame_id)
        except Exception:  # noqa: BLE001
            return []

    def _battery_live_copy(self, frame_id: Optional[str]) -> list:
        with self._lock:
            return list(self._battery_live.get(frame_id, []))

    def battery_view(self, hours: int = 24, frame_id: Optional[str] = None) -> dict:
        """One frame's readings for the trend line plus the inferred power
        state. The line ends on the live median so it agrees with the row
        above it. Every frame is named: without one there is nothing to plot."""
        now = self._clock()
        rows = self._battery_recent(frame_id, hours)
        live = self._battery_live_copy(frame_id)
        items = [{"at": r["at"], "voltage": round(float(r["voltage"]), 3),
                  "percent": r.get("percent")} for r in rows]
        level = live_voltage(live, now)
        if level is not None and live:
            items.append({"at": live[-1]["at"], "voltage": round(level, 3),
                          "percent": live[-1].get("percent")})
        return {"items": items, "power": power_state(rows, now, live),
                "usb_v": _USB_V, "hours": hours}

    def frame_health(self, row: Optional[dict]) -> dict:
        """One frame's health block for the page: battery, power state, Wi-Fi,
        overdue. Every frame that reports those gets the same one."""
        if row is None:
            return frame_card({}, self.config.wake_interval_minutes, self._clock())
        fid = str(row["id"])
        kit = frames_mod.transport_of(row) == "kit"
        cfg = self.frame_config(row) if kit else self.config
        rep = frames_mod.reported_of(row)
        # A kit's check-in is its own record; a viewer never sends one, so for
        # those the last time it asked is the last time it was heard from.
        seen = rep.get("last_checkin") or (None if kit else row.get("last_seen"))
        card = frame_card({**rep, "last_checkin": seen},
                          cfg.wake_interval_minutes, self._clock(),
                          battery_history=self._battery_recent(fid),
                          battery_live=self._battery_live_copy(fid),
                          power_mode=cfg.power_mode if kit else "sleep",
                          critical_volts=frames_mod.panel_for(row).low_battery_volts
                          if kit else None,
                          told_seconds=row.get("told_s") if kit else None)
        if frames_mod.transport_of(row) == "page":
            # A browser tab that was closed is not a device in trouble: a page
            # is never overdue, it was just last open a while ago.
            card["overdue"] = False
            # …and a tab that is not open right now is simply not showing
            # anything, which is the grey dot, not the green one.
            live = _within(card["last_checkin_iso"], self._clock(), _PAGE_OPEN_SECONDS)
        else:
            live = card["seen"]
            if kit and card["seen"] and self.push.connected(fid):
                # A frame on a push socket is being heard from right now
                # (the socket's own pings end it when the frame goes quiet).
                card["overdue"] = False
                card["last_seen"] = "just now"
        # The one place the row's status dot is decided.
        card["state"] = ("bad" if card["battery_critical"] else
                         ("warn" if card["overdue"] else
                          ("good" if live else "off")))
        return card

    def frames_list(self) -> list:
        """Every frame of every transport, all the same shape: what it is, what
        it shows, what it is being served, what it can be asked for, what the
        owner chose, what it reported, and how it is doing. This IS the Frames
        card and the Health card; the page renders one component per row."""
        now = self._clock()
        return [self.frame_view(row, now) for row in
                sorted(self.frames.all().values(), key=_kit_order)]

    def frame_short(self, frame_id: str) -> str:
        """How the page names a frame in its muted meta — and what a screen
        that is waiting to be added shows, so two tablets can be told apart."""
        fid = str(frame_id)
        return "older firmware" if fid == self.LEGACY_FRAME else fid[-6:]

    def frame_view(self, row: dict, now: Optional[datetime] = None) -> dict:
        """One frame, the whole of it, as the page reads it."""
        now = now or self._clock()
        fid = str(row["id"])
        transport = frames_mod.transport_of(row)
        kit = transport == "kit"
        caps = frames_mod.capabilities(row)
        cfg = self.frame_config(row) if kit else None
        view = None if kit else viewers_mod.view_of(row)
        rep = frames_mod.reported_of(row)
        panel = frames_mod.panel_of(row)
        shows = (frames_mod.shows_of(row) if kit
                 else (viewers_mod.shows_of(row) or self._default_shows()))
        name = frames_mod.name_of(row)
        what = _what_it_is(row, panel)
        on = row.get("status") == frames_mod.ON
        try:
            seen = _ago(datetime.fromisoformat(str(row.get("last_seen"))), now)
        except (ValueError, TypeError):
            seen = "never"
        fresh = Config.defaults_for(frames_mod.panel_for(row).key) if kit else None
        return {
            "id": fid,
            "short": self.frame_short(fid),
            # Unnamed, a frame is titled by the short of what it is ("EE03",
            # "iPad") and the summary carries the rest, so the collapsed row
            # never says the same thing twice.
            "name": name, "title": name or what.split(" · ")[0], "what": what,
            "transport": transport, "status": row.get("status"),
            "shows": shows,
            "summary": self._frame_summary(what, shows, named=bool(name)),
            "picture_etag": self.picture_etag(shows) if on else None,
            "output_etag": self._output_etag(fid) if kit else None,
            "preview_url": f"/api/frames/{quote(fid, safe='')}/preview.png" if on else None,
            "capabilities": {**caps, "rotations": list(caps["rotations"])},
            "settings": {
                "shows": shows,
                "rotation": cfg.panel_rotation if kit else (view.rotation if view else None),
                "mat_inset_pct": cfg.mat_inset_pct if kit else None,
                "mat_offset_x_px": cfg.mat_offset_x_px if kit else None,
                "mat_offset_y_px": cfg.mat_offset_y_px if kit else None,
                "mat_guide": cfg.mat_guide if kit else None,
                "power_mode": cfg.power_mode if kit else None,
                "wake_interval_minutes": cfg.wake_interval_minutes if kit else None,
                "device_poll_seconds": cfg.device_poll_seconds if kit else None,
                # On USB with a push socket a change reaches it at once: the
                # interval is only what it falls back to without one.
                "push": bool(kit and cfg.power_mode == "awake" and rep.get("push_s")),
                "panel": cfg.panel if kit else None,
                "width": view.width if view else (cfg.panel_spec.width if kit else None),
                "height": view.height if view else (cfg.panel_spec.height if kit else None),
                "format": view.fmt if view else (cfg.panel_spec.fmt if kit else None),
            },
            # What "Reset to this panel's defaults" fills in, for this panel.
            "defaults": {k: getattr(fresh, k) for k in
                         ("mat_inset_pct", "mat_offset_x_px", "mat_offset_y_px", "mat_guide")}
            if fresh is not None else None,
            "reported": dict(rep),
            "ip": row.get("ip"),
            "first_seen": row.get("first_seen"), "last_seen": row.get("last_seen"),
            "last_seen_text": seen,
            # The identity behind "Details": the same block for every frame
            # that reports one, empty strings where a screen says nothing.
            "details": {
                "ip": row.get("ip") or "",
                "firmware": rep.get("fw_version") or rep.get("user_agent") or "",
                # An official build links to its release notes (W-838).
                "firmware_url": firmware_release.release_url(rep.get("fw_version")) or "",
                "panel": (rep.get("panel") or "") if kit else
                         (f"{view.width}×{view.height} · {viewers_mod.depth_word(view.fmt)}"
                          if view else ""),
                "board": rep.get("board") or "",
                "sketch_md5": rep.get("sketch_md5") or "",
                "last_wake": rep.get("last_wake") or "",
            },
            "card": self.frame_health(row),
            "notices": self.frame_notices(row),
            "firmware": self.firmware_view(row),
        }

    def _frame_summary(self, what: str, shows: str, named: bool = False) -> str:
        """The one line a collapsed row carries: what this screen is, and what
        it shows. An unnamed frame's title already says the short of what it
        is, so the summary carries only the rest."""
        about = what if named else " · ".join(what.split(" · ")[1:])
        bits = [about, SHOWS_WORDS[COLLAGE if shows == COLLAGE else "plates"]]
        return " · ".join(b for b in bits if b)


    def rerender_current(self) -> None:
        """Re-draw the picture the page's tools mean, with the same subject —
        what a settings save or a colour screen's arrival asks for."""
        self._rerender_picture(self._shown)

    def _rerender_picture(self, kind: str) -> None:
        """Draw one picture again, of whatever it is already of."""
        with self._lock:
            meta = dict(self.pictures[kind].meta)
        now = self._clock()
        if meta.get("mode") == "welcome":
            self._render_welcome(now, self.source.available())
            return
        if meta.get("mode") == "collage":
            # The same day it is already of, so a re-render of the nightly
            # collage after midnight does not become this morning's.
            self._build_collage(now, self._collage_day(now))
            return
        if not meta.get("label"):
            return
        # At a settings save no fresh detection is waiting (the tick already
        # consumed the cursor), so rebuild the subject the picture is of.
        common = str(meta["label"]).removesuffix(" (test)")
        key = str(meta.get("species_key") or "")
        sci = (key[:1].upper() + key[1:]) if " " in key else ""
        det = Detection(rowid=-1, date=now.strftime("%Y-%m-%d"),
                        time=now.strftime("%H:%M:%S"), common_name=common,
                        scientific_name=sci, confidence=1.0)
        self._render_single(det, now, reason="settings")

    def refresh_now(self) -> None:
        """Manual Refresh button: re-decide what the page's picture should
        be of and draw that. Unlike rerender_current (which keeps the subject),
        this also recovers from a stale held collage once plates are due again."""
        self.reload_config()
        now = self._clock()
        if self.config.in_quiet_hours(now.time()):
            # held overnight: keep the nightly collage if enabled, else the image
            if self.config.quiet_hours_render_collage:
                self._maybe_quiet_collage(now)
            else:
                self.rerender_current()
            return
        if self._wall_kind(now) == COLLAGE:
            self._build_collage(now, ddate.today())
            return
        # plates: draw the most recent qualifying detection now
        latest = self._first_showable(
            self.source.latest_many(CONFIDENCE_FLOOR), now)
        if latest is not None:
            self._render_single(latest, now, reason="refresh")
        else:
            self.rerender_current()

    # -- on-demand views (frame buttons) -----------------------------------
    def render_collage_on_demand(self, config: Optional[Config] = None) -> Optional[RenderResult]:
        """Button view: yesterday's collage, falling back to today's.

        Transient — never committed as the current frame, so the next timer
        wake restores the picture the frame was showing.
        """
        today = ddate.today()
        for day in (today - timedelta(days=1), today):
            result = self._collage_result(day, config)
            if result is not None:
                log.info("on-demand collage for %s (%s)", day, result.label)
                return result
        return None

    def render_status_page(self, battery_voltage: Optional[float] = None,
                           battery_percent: Optional[int] = None,
                           wifi_rssi: Optional[int] = None,
                           config: Optional[Config] = None) -> RenderResult:
        """Button view: a status plate. Transient, like the collage view."""
        config = config or self.config
        last = self.source.latest(CONFIDENCE_FLOOR)
        today_rows = self.source.top_species_today(
            ddate.today(), CONFIDENCE_FLOOR, limit=50)
        info = statuspage.StatusInfo(
            battery_voltage=battery_voltage,
            battery_percent=battery_percent,
            wifi_rssi=wifi_rssi,
            last_common=last.common_name if last else None,
            last_when=last.timestamp if last else None,
            species_today=len(today_rows),
            species_all_time=self.source.all_time_species_count(),
            server_label=socket.gethostname(),
            wake_minutes=config.wake_interval_minutes,
        )
        img = statuspage.render_status(info)
        result = pipeline.render_image(img, config, "status", "status page")
        log.info("on-demand status page, etag=%s", result.etag)
        return result

    def current_info(self) -> dict:
        """etag/rendered_at of what is on the glass, without the source probes
        that status() makes."""
        with self._lock:
            return {"etag": self._etag, "rendered_at": self._meta.get("rendered_at")}

    def render_history(self, limit: int = _HISTORY_MAX) -> list[dict]:
        """Newest-first past frames for the config page: the render log rows
        with a display title and, where the file still exists, a thumb URL."""
        now = self._clock()
        hist = paths.history_dir()
        out = []
        for row in self.db.render_history(limit):
            etag = str(row.get("etag") or "")
            has_thumb = bool(_ETAG_RE.match(etag)) and (hist / f"{etag}.png").exists()
            has_full = has_thumb and (hist / f"{etag}.jpg").exists()
            try:
                then = datetime.fromisoformat(str(row.get("rendered_at") or ""))
            except ValueError:
                then = None
            out.append({
                "rendered_at": row.get("rendered_at"),
                "mode": row.get("mode"),
                "label": row.get("species"),
                "title": frame_title({"label": row.get("species")}),
                "etag": etag,
                "thumb": f"/api/history/{etag}.png" if has_thumb else None,
                # The zoom: full size, else (a render from before these were
                # kept) the thumbnail itself.
                "full": (f"/api/history/{etag}.jpg" if has_full
                         else f"/api/history/{etag}.png" if has_thumb else None),
                "when_text": when_short(then, now) if then else "",
            })
        return out

    def status(self) -> dict:
        with self._lock:
            meta = dict(self._meta)
            quiet = dict(self._quiet) if self._quiet else None
            outage = dict(self._outage) if self._outage else None
        now = self._clock()
        latest = self.source.latest(CONFIDENCE_FLOOR)
        return {
            "current": {
                "etag": self._etag,
                "mode": meta.get("mode"),
                "label": meta.get("label"),
                "title": frame_title(meta),
                "rendered_at": meta.get("rendered_at"),
                "novelty": meta.get("novelty"),
                "holding": self._holding(meta, now),
            },
            "last_detection": {
                "common": latest.common_name, "scientific": latest.scientific_name,
                "confidence": round(latest.confidence, 3),
                "at": f"{latest.date} {latest.time}",
                "when_text": (when_text(latest.timestamp, now)
                              if latest.timestamp != datetime.min else ""),
            } if latest else None,
            "quiet": quiet,
            "source_outage": outage,
            "pending": self.pending_view(now),
            "hold": self.hold_view(now),
            "birdnet_available": self.source.available(),
            "species_all_time": self.source.all_time_species_count(),
            "plates_loaded": self.audubon.species_count,
            "generated_cached": len(self.genart.cached_species()) if self.genart else 0,
            "config": self._masked_config(),
            # Every screen this server draws for, one shape each. The page
            # renders the same row component for all of them, and the Health
            # card reads the same list.
            "frames": {"list": self.frames_list()},
            "firmware": self.firmware_status(),
        }

    def _masked_config(self) -> dict:
        """Config for display: never leak the API key past this process."""
        cfg = self.config.to_dict()

        def _mask(key: str) -> str:
            if len(key) > 12:
                return f"{key[:5]}…{key[-4:]}"
            return f"…{key[-4:]}" if key else ""

        cfg["imagegen_api_key"] = _mask(cfg.get("imagegen_api_key") or "")
        cfg["imagegen_text_key"] = _mask(cfg.get("imagegen_text_key") or "")
        return cfg

    # -- committing a picture ---------------------------------------------
    def _picture_meta(self, pic: "pictures_mod.Picture", now: datetime, etag: str, mode: str,
                      species_key: Optional[str], label: str, note: Optional[str],
                      novelty: Optional[str], extra: Optional[dict]) -> dict:
        """What this picture is now, from what it was. The dwell clock
        (held_since) starts when a novel bird takes the picture and carries
        across its own re-renders: a first-today robin calling again at 8:05 is
        classed a repeat, but it must not lose the hold it earned at 8:00 — nor
        restart it. Its label carries too, so the page keeps saying what earned
        the hold."""
        prev = pic.meta
        same = (mode == "single" and species_key is not None
                and prev.get("mode") == "single" and prev.get("species_key") == species_key)
        carried = same and prev.get("held_since") and prev.get("novelty") in _NOVEL
        if novelty in _NOVEL:
            held_since = prev["held_since"] if carried else now.isoformat(timespec="seconds")
        elif carried:
            novelty, held_since = prev["novelty"], prev["held_since"]
        else:
            held_since = None
        return {
            "etag": etag, "mode": mode, "label": label,
            "species_key": species_key, "rendered_at": now.isoformat(timespec="seconds"),
            "novelty": novelty, "held_since": held_since,
            "quiet_note": note is not None,   # what the glass says, for the tick's flip
            "note_kind": self._note_kind() if note is not None else None,
            "collage_at": now.isoformat(timespec="seconds") if mode == "collage"
            else prev.get("collage_at"),
            **(extra or {}),
        }

    def _commit(self, kind: str, now: datetime, sheet: Image.Image, mode: str,
                species_key: Optional[str], label: str, key: Optional[str] = None,
                note: Optional[str] = None, novelty: Optional[str] = None,
                extra: Optional[dict] = None, recompose=None) -> str:
        """This picture is now `sheet`. Composing is all a picture is: it is
        fitted, matted, dithered and packed once per frame that shows it, in
        the tick. `extra`: mode-specific keys carried in the meta (the welcome
        plate's `source_ok`). `recompose`: draws this same sheet again with the
        art in colour, for the screens that show it in colour; kept so the
        first one to ask need not draw the subject a second time."""
        # Before the lock: composing takes seconds and a frame is polling.
        twin = (self._compose_color(recompose)
                if (recompose is not None and self._color_wanted(kind, now)) else None)
        etag = pictures_mod.etag_for(sheet)
        with self._lock:
            pic = self.pictures[kind]
            meta = self._picture_meta(pic, now, etag, mode, species_key, label,
                                      note, novelty, extra)
            pic.commit(meta, etag, now, key=key, sheet=sheet, color_sheet=twin,
                       recompose=recompose)
            self._recolor.discard(kind)
            self.pictures.save()
        self.db.log_render(now.isoformat(timespec="seconds"), mode, label, etag)
        self._save_history_thumb(etag, sheet)
        return etag

    # -- viewers (W-822) ---------------------------------------------------
    def viewer_refresh_seconds(self, row: Optional[dict] = None) -> int:
        """How long a viewer is told to sleep: on the collage, until the
        collage is next redrawn; on plates, the constant — which is the hourly
        one in quiet hours, when the plate holds still anyway."""
        now = self._clock()
        if row is not None and viewers_mod.shows_of(row) == COLLAGE:
            return self._collage_check_seconds(now)
        quiet = self.config.in_quiet_hours(now.time())
        return viewers_mod.QUIET_REFRESH_SECONDS if quiet else viewers_mod.REFRESH_SECONDS

    # -- checks that follow the collage -------------------------------------
    def collage_next_at(self, now: datetime) -> Optional[datetime]:
        """When the collage picture is next redrawn, from `_draw`'s own rules:
        in quiet hours nothing is drawn until the window ends (the nightly
        collage aside, which is about to happen if it has not); by day, the
        interval after the last draw, the start of quiet hours, or midnight,
        whichever is first. None means "soon": nothing to wait for."""
        cfg = self.config
        pic = self.pictures[COLLAGE]
        if pic.etag is None:
            return None
        if cfg.in_quiet_hours(now.time()):
            if cfg.quiet_hours_render_collage and \
                    self.db.get("quiet_collage_for") != self._collage_date(now).isoformat():
                return None
            return self._next_time(now, cfg.quiet_window(now.date())[1])
        try:
            last_at = datetime.fromisoformat(pic.meta.get("collage_at") or "")
        except (ValueError, TypeError):
            return None
        soonest = [last_at + timedelta(hours=cfg.collage_interval_hours),
                   datetime.combine(now.date() + timedelta(days=1), dtime.min)]
        if cfg.quiet_hours_mode != "off":
            soonest.append(self._next_time(now, cfg.quiet_window(now.date())[0]))
        return min(soonest)

    @staticmethod
    def _next_time(now: datetime, at: dtime) -> datetime:
        """The next moment the clock reads `at`, after `now`."""
        when = datetime.combine(now.date(), at)
        return when if when > now else when + timedelta(days=1)

    def _collage_check_seconds(self, now: datetime) -> int:
        nxt = self.collage_next_at(now)
        wait = (nxt - now).total_seconds() if nxt is not None else 0.0
        if wait <= 0:
            return COLLAGE_CHECK_FLOOR_S   # nothing to wait for, or overdue: soon
        return int(min(86400, max(COLLAGE_CHECK_FLOOR_S, wait + COLLAGE_CHECK_MARGIN_S)))

    def frame_intervals(self, row: Optional[dict], now: Optional[datetime] = None) -> tuple[int, int]:
        """(poll seconds, wake minutes) served to one kit. On plates they are
        the owner's; on the collage the kit checks in just after the collage's
        next redraw, so its checks follow the collage and not a clock of their
        own — and the row's own interval is not read at all."""
        cfg = self.frame_config(row)
        if frames_mod.shows_of(row) != COLLAGE:
            return cfg.device_poll_seconds, cfg.wake_interval_minutes
        secs = self._collage_check_seconds(now or self._clock())
        return secs, max(1, min(1440, -(-secs // 60)))

    def _single_in_color(self, spec: SingleSpec):
        return lambda: compose_mod.render_single(spec, self.provider, color=True)

    def _color_wanted(self, kind: str, now: datetime) -> bool:
        """Some screen showing this picture draws it in colour: a colour kit,
        or a colour viewer that asked within COLOR_VIEWER_DAYS. A picture is
        kept in gray; the twin is the extra, and only bought when it is seen."""
        for row in self._kits_on():
            if (frames_mod.panel_for(row).color
                    and self._kind_for(frames_mod.shows_of(row), now) == kind):
                return True
        asked = self._color_asked_at
        if asked is None:
            try:
                asked = datetime.fromisoformat(str(self.db.get("color_viewer_at")))
            except (ValueError, TypeError):
                return False
            self._color_asked_at = asked
        return now - asked < timedelta(days=COLOR_VIEWER_DAYS)

    @staticmethod
    def _compose_color(recompose) -> Optional[Image.Image]:
        try:
            sheet = recompose()
        except Exception:  # noqa: BLE001 — colour is a nicety; the frame is the job
            log.warning("colour sheet not composed", exc_info=True)
            return None
        return sheet if sheet is not None and sheet.mode == "RGB" else None

    def _note_color_viewer(self) -> None:
        """A colour viewer is asking: remember it (once a day is enough)."""
        now = self._clock()
        if self._color_asked_at is None or now - self._color_asked_at > timedelta(days=1):
            self._color_asked_at = now
            self.db.set("color_viewer_at", now.isoformat(timespec="seconds"))

    def _want_color(self, pic: "pictures_mod.Picture", viewer: bool = True) -> None:
        """A colour screen shows this picture. Remember a viewer's ask (once a
        day is enough), and get the picture a colour twin: from the render's
        own recompose when this process drew it, else by drawing the subject
        again on the next tick."""
        if viewer:
            self._note_color_viewer()
        if pic.has_color() or self._color_tried.get(pic.kind) == pic.etag:
            return   # there, or this picture has no colour to give: never retry per tick
        self._color_tried[pic.kind] = pic.etag
        with self._view_lock:
            if pic.has_color():
                return
            with self._lock:
                recompose, etag = pic.recompose, pic.etag
                mode = pic.meta.get("mode")
            if recompose is not None:
                sheet = self._compose_color(recompose)
                with self._lock:
                    if sheet is not None and pic.etag == etag:   # still that picture
                        pictures_mod.write_sheet(pic.color_sheet_path, sheet)
                return
        if mode in ("single", "collage"):
            # A restart forgot the recompose: draw the subject again, which
            # composes the twin on the way through.
            self._recolor.add(pic.kind)
            if pic.kind == PLATES:
                self._rerender_picture(PLATES)
                self._color_tried[PLATES] = self.pictures[PLATES].etag

    # A screen waits to be added (W-833). It is fed like any other view — the
    # device's own size, depth and rotation — but the sheet is the waiting
    # plate, not a picture, so it never touches one.
    WAITING_PREFIX = "waiting-"

    def waiting_png(self, view: "pipeline.View",
                    short_id: str = "") -> tuple[int, Optional[bytes], Optional[str]]:
        """(status, png, etag) of the waiting plate for one screen."""
        etag = f"{self.WAITING_PREFIX}{re.sub(r'[^0-9A-Za-z_-]', '', short_id)}-{view.key}"
        views = paths.views_dir()
        cached = views / f"{etag}.png"
        if cached.exists():
            return 200, cached.read_bytes(), etag
        with self._view_lock:
            if cached.exists():
                return 200, cached.read_bytes(), etag
            sheet = welcome_mod.render_waiting(short_id)
            png = pipeline.encode_png(pipeline.render_view(sheet, view), view.fmt)
            try:
                tmp = cached.with_suffix(".tmp")
                tmp.write_bytes(png)
                os.replace(tmp, cached)
                self._prune_views()
            except OSError:
                log.warning("waiting view %s not cached", etag, exc_info=True)
        return 200, png, etag

    def view_png(self, view: "pipeline.View", if_none_match: Optional[str] = None,
                 shows: Optional[str] = None) -> tuple[int, Optional[bytes], Optional[str]]:
        """One picture for one screen: (status, png, etag). Which picture is
        `picture_for`'s decision and nothing else's. Read-only by design: a
        screen fed this way never moves the frame, the device card, the panel
        or the ingest cursor. Rendered once per (picture, variant) and kept on
        disk; a new picture drops the old one's views."""
        pic = self.picture_for(shows)
        if view.fmt == "color":
            self._want_color(pic)
        with self._lock:
            picture = pic.etag
        if not picture:
            return 404, None, None
        etag = f"{picture}-{view.key}"
        if if_none_match == etag:
            return 304, None, etag
        views = paths.views_dir()
        cached = views / f"{etag}.png"
        if cached.exists():
            return 200, cached.read_bytes(), etag
        with self._view_lock:   # one viewer render at a time (Pi Zero: memory)
            if cached.exists():
                return 200, cached.read_bytes(), etag
            sources = pic.sheets(view.fmt == "color")
            source = next((p for p in sources if p.exists()), None)
            if source is None:
                return 404, None, None
            with Image.open(source) as sheet:
                sheet.load()
            png = pipeline.encode_png(pipeline.render_view(sheet, view), view.fmt)
            try:
                tmp = cached.with_suffix(".tmp")
                tmp.write_bytes(png)
                os.replace(tmp, cached)
                self._prune_views()
            except OSError:
                log.warning("view %s not cached", etag, exc_info=True)
        return 200, png, etag

    def _prune_views(self) -> None:
        """A handful of renders per live picture, plus a handful of waiting
        plates — which belong to no picture, and which a screen still asking
        must not re-render on every poll."""
        views = paths.views_dir()
        live = tuple(f"{p.etag}-" for p in self.pictures.values() if p.etag)
        def newest(files):
            return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:_VIEWS_MAX]
        keep = set(newest([f for f in views.glob("*.png") if live and f.name.startswith(live)]))
        keep |= set(newest(list(views.glob(f"{self.WAITING_PREFIX}*.png"))))
        for stale in set(views.glob("*.png")) - keep:
            stale.unlink(missing_ok=True)

    @staticmethod
    def _save_history_thumb(etag: str, sheet: Image.Image) -> None:
        """A 1/8-scale thumbnail under frames/history/<etag>.png and the sheet
        full size as <etag>.jpg, pruning the oldest past _HISTORY_MAX. Best-effort: a full card or a bad file must
        never block the commit the device is waiting on."""
        try:
            hist = paths.history_dir()
            # Same content, same file: a re-render of identical pixels is free.
            target = hist / f"{etag}.png"
            if not target.exists():
                sheet.reduce(_HISTORY_SCALE).save(target)
            full = hist / f"{etag}.jpg"
            if not full.exists():
                sheet.convert("RGB" if sheet.mode == "RGB" else "L").save(
                    full, quality=_HISTORY_FULL_QUALITY, optimize=True)
            thumbs = sorted((p for p in hist.glob("*.png") if _ETAG_RE.match(p.stem)),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in thumbs[_HISTORY_MAX:]:
                stale.unlink(missing_ok=True)
                stale.with_suffix(".jpg").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — a thumbnail is never worth a failed commit
            log.warning("history thumbnail for %s not saved", etag, exc_info=True)

    def _ensure_initial_frame(self) -> None:
        if self._etag is not None:
            return
        self.tick()
        if self._etag is None:
            # Nothing heard yet (or no source): hang the welcome plate rather
            # than answer 503 — the device paints it and 304s on it after.
            self._render_welcome(self._clock(), self.source.available())
            self._tick_frames()

    def _render_welcome(self, now: datetime, available: bool) -> None:
        since = self.db.get("welcome_since")
        if not since:
            since = now.isoformat(timespec="seconds")
            self.db.set("welcome_since", since)
        img = welcome_mod.render_welcome(datetime.fromisoformat(since), available, now)
        etag = self._commit(PLATES, now, sheet=img, mode="welcome", species_key=None,
                            label=welcome_mod.LABEL, extra={"source_ok": available})
        log.info("rendered welcome plate (source %s), etag=%s",
                 "up" if available else "down", etag)

    def _cursor(self) -> Optional[int]:
        return self.db.get("ingest_cursor", None)

    def _set_cursor(self, rowid: int) -> None:
        self.db.set("ingest_cursor", int(rowid))

    def _showing_single(self, det: Detection) -> bool:
        """True if the plates picture is already a plate of this species."""
        pic = self.pictures[PLATES]
        return (pic.etag is not None
                and pic.meta.get("mode") == "single"
                and pic.meta.get("species_key") == det.key)

    def _first_seen(self, scientific_name: str) -> Optional[str]:
        """first_seen_date, asked of the source once per species per tick."""
        memo = self._tick_memo.setdefault("first_seen", {})
        key = scientific_name.strip().lower()
        if key not in memo:
            memo[key] = self.source.first_seen_date(scientific_name)
        return memo[key]

    # -- novelty ------------------------------------------------------------
    def _novelty(self, det: Detection, now: datetime) -> str:
        """Which of "first-ever" / "first-today" / "repeat" this detection is.
        First-ever: the source first heard the species today — or can't say,
        which (as in _is_new_species) counts as new. First-today: a known
        species whose only detection today is this one, per the day's tally;
        when the source can't tally (a push feed), per the render log — no
        plate of it rendered today. Everything else is a repeat."""
        if self._is_new_species(det.scientific_name, now.date()):
            return "first-ever"
        counts = self._today_counts(now)
        if counts is not None:
            return "first-today" if counts.get(det.key, 0) <= 1 else "repeat"
        rendered = self._rendered_today(now)
        return "repeat" if det.common_name.strip().lower() in rendered else "first-today"

    def _memo(self, now: datetime) -> dict:
        """The per-tick memo, fresh whenever the caller's `now` moves on — so
        a Refresh or a settings save between ticks never reads a stale tally."""
        if self._tick_memo.get("now") != now:
            self._tick_memo = {"now": now}
        return self._tick_memo

    def _today_counts(self, now: datetime) -> Optional[dict[str, int]]:
        """{species key: detections today} from the source's tally, memoised
        per tick; None when the source can't answer."""
        memo = self._memo(now)
        if "today_counts" not in memo:
            rows = self.source.top_species_today(now.date(), CONFIDENCE_FLOOR,
                                                 limit=_TODAY_SCAN)
            memo["today_counts"] = ({str(r.get("scientific") or "").strip().lower():
                                     int(r.get("count") or 0) for r in rows}
                                    if rows else None)
        return memo["today_counts"]

    def _rendered_today(self, now: datetime) -> set[str]:
        """Lowercased common names with a single plate in today's render log
        (the log stores the label, not the species key) — the fallback tally
        for a source with no history."""
        memo = self._memo(now)
        if "rendered_today" not in memo:
            today = now.date().isoformat()
            keys: set[str] = set()
            for row in self.db.render_history(_TODAY_SCAN):
                if row.get("mode") != "single" or not str(row.get("rendered_at") or "").startswith(today):
                    continue
                label = str(row.get("species") or "").removesuffix(" (test)").strip().lower()
                if label:
                    keys.add(label)
            memo["rendered_today"] = keys
        return memo["rendered_today"]

    # -- the owner's hold and block (W-735) --------------------------------
    def user_hold(self, now: Optional[datetime] = None) -> Optional[dict]:
        """The owner's pin on the current plate, or None. An expired hold is
        cleared here, so every reader sees the same answer."""
        now = now or self._clock()
        hold = self.db.get(_USER_HOLD_KEY)
        if not hold:
            return None
        until = hold.get("until")
        if until:
            try:
                if datetime.fromisoformat(str(until)) <= now:
                    self.db.set(_USER_HOLD_KEY, None)
                    return None
            except ValueError:
                self.db.set(_USER_HOLD_KEY, None)
                return None
        return hold

    def hold_current(self, duration: str) -> Optional[dict]:
        """Pin what is on the glass for `duration` ("day", "week", or
        "forever" = until released). None when nothing is showing yet."""
        now = self._clock()
        with self._lock:
            meta = dict(self._meta)
            showing = self._etag is not None
        if not showing or meta.get("mode") in (None, "welcome") or not meta.get("label"):
            return None
        days = _HOLD_DAYS.get(duration, 1)
        hold = {
            "since": now.isoformat(timespec="seconds"),
            "until": (now + timedelta(days=days)).isoformat(timespec="seconds") if days else None,
            "label": meta.get("label"),
            "title": frame_title(meta),
        }
        self.db.set(_USER_HOLD_KEY, hold)
        log.info("hold: %s for %s", hold["title"], duration)
        return hold

    def release_hold(self) -> None:
        """Drop the pin and repaint whatever should be showing now."""
        self.db.set(_USER_HOLD_KEY, None)
        self.refresh_now()

    def hold_view(self, now: Optional[datetime] = None) -> Optional[dict]:
        hold = self.user_hold(now)
        if hold is None:
            return None
        until = hold.get("until")
        if until:
            when = datetime.fromisoformat(str(until))
            text = f"until {when.strftime('%a')} {when.day} {when.strftime('%b')}"
        else:
            text = "until released"
        return {**hold, "until_text": text}

    def block_current(self) -> Optional[str]:
        """Add the species on the glass to the blocklist and move past it.
        Returns the blocked name, or None when a single plate is not showing."""
        with self._lock:
            meta = dict(self._meta)
        if meta.get("mode") != "single" or not meta.get("label"):
            return None
        common = str(meta["label"]).removesuffix(" (test)")
        if not self.config.is_blocked(common, common):
            cfg = self.config
            cfg.species_blocklist = [*cfg.species_blocklist, common]
            self.update_config(cfg)
        log.info("blocked %s from the dashboard", common)
        self.refresh_now()
        return common

    def unblock(self, name: str) -> bool:
        """Undo for block_current. True when the name was on the list."""
        cfg = self.config
        kept = [b for b in cfg.species_blocklist if b.lower() != name.strip().lower()]
        if len(kept) == len(cfg.species_blocklist):
            return False
        cfg.species_blocklist = kept
        self.update_config(cfg)
        return True

    def _holding(self, meta: dict, now: datetime) -> Optional[dict]:
        """The dwell hold on a plates picture's meta, or None: a plate of a
        novel species, held since less than DWELL_MINUTES ago."""
        dwell = DWELL_MINUTES
        if meta.get("mode") != "single" or meta.get("novelty") not in _NOVEL:
            return None
        try:
            since = datetime.fromisoformat(str(meta.get("held_since") or meta.get("rendered_at")))
        except (TypeError, ValueError):
            return None
        until = since + timedelta(minutes=dwell)
        if now >= until:
            return None
        return {"until": until.isoformat(timespec="seconds"),
                "minutes_left": int(math.ceil((until - now).total_seconds() / 60)),
                "reason": "new species",
                "why": _HOLD_WHY[meta["novelty"]].format(dwell)}

    # -- new-species corroboration ------------------------------------------
    # One 0.71 hit of a rare species is routinely a car horn. Unchecked it
    # becomes the wall at once and, for a plate-less species, buys a generated
    # plate of a bird that was never there (GeneratedArtProvider.artwork()
    # generates from inside the render, so NOT rendering is what prevents the
    # purchase). A species heard for the first time today must earn the wall.

    def _first_showable(self, detections: list[Detection],
                        now: datetime) -> Optional[Detection]:
        """Newest-first: the first detection that is neither blocked nor held
        back for corroboration. The first held-back one becomes the pending
        record the page shows — even when an older, corroborated bird renders
        this tick, the new one is still waiting on its second hit."""
        return self._pick_showable(detections, now, by_novelty=False)

    def _best_showable(self, detections: list[Detection],
                       now: datetime) -> Optional[Detection]:
        """Newest-first: the showable detection of the highest novelty class,
        newest within a class — a first-ever bird at 8:05 beats the cardinal
        at 8:10. Same corroboration gate and pending bookkeeping as
        _first_showable."""
        return self._pick_showable(detections, now, by_novelty=True)

    def _pick_showable(self, detections: list[Detection], now: datetime,
                       by_novelty: bool) -> Optional[Detection]:
        pending: Optional[dict] = None
        chosen: Optional[Detection] = None
        best = -1
        for d in detections:
            if self.config.is_blocked(d.common_name, d.scientific_name):
                continue
            ok, record = self._corroborated(d, now)
            if not ok:
                if pending is None:
                    pending = record
                continue
            if not by_novelty:
                chosen = d
                break
            rank = _NOVELTY_RANK[self._novelty(d, now)]
            if rank > best:           # strictly: newest wins within a class
                chosen, best = d, rank
            if best == max(_NOVELTY_RANK.values()):
                break                 # nothing older can outrank a first-ever
        if pending is not None:
            self._set_pending(pending)
        return chosen

    def _is_new_species(self, scientific_name: str, on_date: ddate) -> bool:
        """A species is "new" when the source first heard it on `on_date` —
        or can't say (a push feed has no history), which is treated as new
        because an unknown history is exactly when a stray hit can't be
        checked against anything else."""
        first = self._first_seen(scientific_name)
        return first is None or str(first) == on_date.isoformat()

    def _corroborated(self, det: Detection, now: datetime) -> tuple[bool, Optional[dict]]:
        """(True, None) when `det` may be shown; (False, pending) when it is a
        new species still waiting on a second detection. A new species passes
        alone at/above CORROBORATE_CONFIDENCE, or with two detections inside
        the window at least the minimum gap apart — a genuine bird calls again;
        a car horn's two triggers land seconds apart."""
        if not self._is_new_species(det.scientific_name, now.date()):
            return True, None
        if det.confidence >= CORROBORATE_CONFIDENCE:
            return True, None
        window = CORROBORATE_WINDOW
        recent = self.source.latest_many(min_confidence=CONFIDENCE_FLOOR,
                                         limit=_CORROBORATE_SCAN)
        # The candidate itself counts once (latest_many may not include it —
        # a page fed from new_since, or a source whose tail lags).
        hits = {d.rowid: d for d in recent if d.key == det.key}
        hits.setdefault(det.rowid, det)
        stamps = [d.timestamp for d in hits.values()
                  if d.timestamp != datetime.min and d.timestamp >= now - window]
        best = max(d.confidence for d in hits.values())
        if best >= CORROBORATE_CONFIDENCE:
            return True, None   # a confident hit in the window vouches for the rest
        if len(stamps) >= 2 and (max(stamps) - min(stamps)) >= CORROBORATE_MIN_GAP:
            return True, None
        first_at = min(stamps) if stamps else (det.timestamp if det.timestamp != datetime.min else now)
        last_at = max(stamps) if stamps else first_at
        return False, {
            "key": det.key, "common": det.common_name, "scientific": det.scientific_name,
            "hits": max(1, len(stamps)), "confidence": round(best, 3),
            "first_at": first_at.isoformat(timespec="seconds"),
            "last_at": last_at.isoformat(timespec="seconds"),
        }

    def _corroborated_rows(self, rows: list[dict], on_date: ddate) -> list[dict]:
        """Collage gate: drop a species heard once on `on_date`, first heard
        that day, whose best detection never reached CORROBORATE_CONFIDENCE.
        One latest_many scan per build, not per row. force_test_detection is
        not a row here, so it is unaffected."""
        suspects = [r for r in rows
                    if int(r.get("count") or 0) == 1
                    and self._is_new_species(r["scientific"], on_date)]
        if not suspects:
            return rows
        recent = self.source.latest_many(min_confidence=CONFIDENCE_FLOOR,
                                         limit=_CORROBORATE_SCAN)
        best: dict[str, float] = {}
        for d in recent:
            best[d.key] = max(best.get(d.key, 0.0), d.confidence)
        keep = []
        for r in rows:
            key = str(r["scientific"]).strip().lower()
            if any(r is s for s in suspects) and best.get(key, 0.0) < CORROBORATE_CONFIDENCE:
                log.info("collage: holding back new species %s (1 hit, best %.2f)",
                         r["common"], best.get(key, 0.0))
                continue
            keep.append(r)
        return keep

    def _set_pending(self, record: Optional[dict]) -> None:
        with self._lock:
            self._pending = record
            self.db.set("pending_species", record)

    def _expire_pending(self, now: datetime) -> None:
        """Forget a held-back bird once its window has closed without a second
        hit: the page should not keep waiting on yesterday's car horn."""
        if self._pending and self.pending_view(now) is None:
            self._set_pending(None)

    def pending_view(self, now: Optional[datetime] = None) -> Optional[dict]:
        """The pending record for status(), with a ready-to-print line, or
        None when nothing is waiting (or the wait has expired)."""
        with self._lock:
            p = dict(self._pending) if self._pending else None
        if not p:
            return None
        now = now or self._clock()
        try:
            last_at = datetime.fromisoformat(str(p.get("last_at") or p.get("first_at")))
        except (TypeError, ValueError):
            return None
        if now - last_at > CORROBORATE_WINDOW:
            return None
        hits = int(p.get("hits") or 1)
        conf = float(p.get("confidence") or 0.0)
        if hits <= 1:
            waiting = f"1 hit at {conf:.2f} · waiting for a second"
        else:
            waiting = (f"{hits} hits at {conf:.2f} · waiting for one "
                       f"{int(CORROBORATE_MIN_GAP.total_seconds() // 60)} min apart")
        return {"common": p.get("common"), "scientific": p.get("scientific"),
                "hits": hits, "confidence": conf, "first_at": p.get("first_at"),
                "waiting_text": waiting}
