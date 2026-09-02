"""The running service: state + the render scheduler.

One background thread polls BirdNET on a short interval, decides whether a new
frame is warranted (mode, confidence, debounce, same-species, quiet hours), and
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
import socket
import threading
from dataclasses import asdict, dataclass
from datetime import date as ddate
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Optional

from . import paths
from .config import Config, load_config, save_config
from .sources import Detection, make_source
from .db import Database
from .render import collage as collage_mod
from .render import framebuffer
from .render import pipeline
from .render import statuspage
from .render.compose import SingleSpec
from .render.genart import GeneratedArtProvider, make_image_model, make_text_model
from .render.pipeline import RenderResult
from .render.provider import ArtProvider, AudubonProvider, ChainedProvider

log = logging.getLogger("featherframe.service")

_CURRENT_FFF = "current.fff"
_CURRENT_PNG = "current.png"

# History thumbnails: 1/8-scale previews keyed by ETag, capped on disk (a
# Pi's SD card) and matched to what /api/history can list.
_HISTORY_MAX = 60
_HISTORY_SCALE = 8
_ETAG_RE = re.compile(r"^[0-9a-f]{16}$")

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
# How much of the day's tally / render log a novelty check reads. A busy
# feeder is a few dozen species a day; the render log holds 200 rows.
_TODAY_SCAN = 200


def _as_time(v) -> dtime:
    if isinstance(v, dtime):
        return v
    hh, mm = (int(x) for x in v.split(":"))
    return dtime(hh, mm)


def review_date_for(now: datetime, quiet_start, quiet_end) -> ddate:
    """The date a day-in-review covers: the day the quiet window started.
    With a midnight-wrapping window (the default 22:00-06:00), a tick after
    00:00 still reviews yesterday. The bounds are "HH:MM" strings or times."""
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
    log string ("day in review (5 species)", "6-species collage",
    "Northern Cardinal (test)"); the page shows this instead."""
    label = meta.get("label")
    if not label:
        return None
    label = str(label)
    m = re.match(r"^day in review \((\d+) species\)$", label)
    if m:
        return f"Day in review · {m.group(1)} species"
    m = re.match(r"^(\d+)-species collage$", label)
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


def _served_words(result: Optional[str]) -> Optional[str]:
    if result == "304":
        return "up to date (304)"
    if result == "frame":
        return "new frame"
    return f"{result} view" if result else None


def frame_card(device: DeviceStatus, wake_interval_minutes: int,
               now: Optional[datetime] = None,
               battery_history: Optional[list[dict]] = None,
               battery_live: Optional[list[dict]] = None) -> dict:
    """The wall frame's health, pre-chewed for the config page: ready-to-print
    strings plus one overdue flag. Overdue means the device has missed two
    consecutive wake intervals — one 304 skipped is normal jitter, two is a
    dead battery or lost Wi-Fi."""
    now = now or datetime.now()
    card = {"seen": False, "overdue": False,
            "expected_minutes": wake_interval_minutes, "last_seen": None,
            "last_checkin_iso": None, "battery": None, "battery_low": False,
            "power": {"state": "unknown", "text": ""},
            "served": None, "wifi_rssi": None}
    try:
        then = datetime.fromisoformat(device.last_checkin or "")
    except (ValueError, TypeError):
        return card
    card["seen"] = True
    card["last_seen"] = _ago(then, now)
    card["last_checkin_iso"] = then.isoformat(timespec="seconds")
    card["overdue"] = (now - then).total_seconds() > 2 * wake_interval_minutes * 60
    if device.battery_voltage is not None and device.battery_voltage >= _BATTERY_ABSENT_V:
        # Display the last few minutes' median, not the single newest reading,
        # so the percent stops flickering between two values every 15 s.
        volts = live_voltage(battery_live or [], now) or device.battery_voltage
        pcts = [r["percent"] for r in (battery_live or [])
                if r.get("percent") is not None
                and now - datetime.fromisoformat(str(r["at"])) <= _LIVE_WINDOW]
        percent = int(_median(pcts)) if pcts else device.battery_percent
        card["battery_volts"] = round(volts, 3)
        card["battery_percent"] = percent
        pct = f" · {percent}%" if percent is not None else ""
        card["battery"] = f"{volts:.2f} V{pct}"
        card["battery_low"] = ((percent is not None and percent <= 20) or volts <= _BATTERY_LOW_V)
        card["power"] = power_state(battery_history or [], now, battery_live)
        if card["power"]["text"]:
            card["battery"] += f" · {card['power']['text']}"
        # Full and held there by the charger is not "low", whatever the percent says.
        if card["power"]["state"] in ("usb", "charging"):
            card["battery_low"] = False
    card["served"] = _served_words(device.last_result)
    card["wifi_rssi"] = device.wifi_rssi
    return card


class FeatherframeService:
    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or Database()
        self.config: Config = load_config(self.db)
        self.audubon = AudubonProvider()
        self.genart: GeneratedArtProvider = GeneratedArtProvider(None)
        self.provider: ArtProvider = self._build_provider(self.config)
        self.source = make_source(self.config, self.db)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Background regenerations (config page). The page polls the listing
        # for this state, so it must be readable from any thread — and it is
        # the server's memory of an in-flight repaint, which is what lets a
        # refreshed page pick the indicator back up.
        self._regen_lock = threading.Lock()
        self._regen_inflight: set[str] = set()
        self._regen_errors: dict[str, str] = {}

        # Background one-shot jobs (test detection, day-in-review) the config
        # page kicks off and polls — same fire-and-forget contract as repaints,
        # so a ~2-minute generation never blocks (and never 504s) the request.
        self._task_lock = threading.Lock()
        self._tasks_inflight: set[str] = set()
        self._task_errors: dict[str, str] = {}

        # in-memory current frame
        self._frame_bytes: Optional[bytes] = None
        self._etag: Optional[str] = None
        self._meta: dict = self.db.get("current_frame", {}) or {}
        self._load_current_from_disk()
        # Verify the persisted ingest cursor isn't stale on the first single-tick
        # after start (see _single_tick); cheaper than checking every tick.
        self._cursor_verified = False

        # The last few minutes of raw check-ins (the battery log keeps one row
        # per 5 min): the display median and the USB test read from here.
        self._battery_live: list[dict] = []

        # Gone-quiet alarm, computed once per tick (status() is polled, and
        # the walk plus a source query is not free). None = no alarm.
        self._quiet: Optional[dict] = None
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
        self._started_at = datetime.now()

        # Restore the last device check-in, filtering to known fields so a rollback
        # to a build with fewer DeviceStatus fields can't crash startup on an
        # unexpected key — and sanitising values, so a row poisoned by an older
        # build (a NaN voltage) heals on start instead of 500ing /api/status.
        self.device = DeviceStatus(**_clean_device_fields(self.db.get("device_status", {})))

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
        interval = self.config.poll_interval_seconds
        if self.config.detection_backend == "birdweather":
            return max(interval, 60)
        return interval

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
            if self._imagegen_fields(new) != self._imagegen_fields(self.config):
                self.provider = self._build_provider(new)
            self.config = new

    def update_config(self, config: Config) -> None:
        with self._lock:
            save_config(self.db, config)
        self.reload_config()

    # -- the decision loop -------------------------------------------------
    def tick(self) -> None:
        self.reload_config()
        now = datetime.now()
        available = self.source.available()
        # Computed first so any render this tick — including the flips below —
        # carries the right footnote.
        self._quiet = self.quiet_state(now, available=available)
        resident = self._frame_bytes is not None and bool(self._meta.get("label"))

        # Dark-mode "quiet" inverts only during quiet hours: when the effective
        # state no longer matches the resident frame, re-flip it once. Requires
        # a rendered subject (a label) so rerender_current actually commits the
        # updated "dark" marker — otherwise the guard would fire every tick and
        # starve the decision path below. Runs before the quiet-hours hold so
        # the transition itself gets applied.
        if resident and self._meta.get("dark") != self.config.dark_now(now.time()):
            self.rerender_current()
            return

        # Gone-quiet footnote: re-render the resident subject once when the
        # alarm flips on. When it flips off, prefer the decision path's own
        # render (a fresh bird is what usually clears it) and only re-render
        # to drop the note if nothing else replaced the frame — one render
        # per tick either way. An outage reads as "unknown", so the note is
        # never dropped (or added) while the source is unreachable.
        want = self._quiet is not None
        have = bool(self._meta.get("quiet_note"))
        if resident and want and not have:
            self.rerender_current()
            return
        if resident and have and not want and available:
            before = self._etag
            self._decide(now, available)
            if self._etag == before:
                self.rerender_current()
            return

        self._decide(now, available)

    def _decide(self, now: datetime, available: bool) -> None:
        """The decision tree proper: quiet hours, mode, detections."""
        if self.config.in_quiet_hours(now.time()):
            # Quiet hours: hold the image. Optionally render one day-in-review
            # collage at the start of the window (also implied by 'auto' mode).
            if self.config.quiet_hours_render_collage:
                self._maybe_quiet_collage(now)
            return

        if not available:
            return  # soft fail; keep serving the current frame

        if self.config.mode == "collage":
            self._maybe_daytime_collage(now)
        else:  # single or auto -> single during the day
            self._single_tick(now)

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
        hours = int(self.config.quiet_alarm_hours)
        if hours <= 0:
            return None
        if available is None:
            available = self.source.available()
        if not available:
            return None
        latest = self.source.latest(self.config.confidence_threshold)
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

    def _note_text(self) -> Optional[str]:
        """The plate footnote while the alarm is on. Derived from the state
        cached at the top of this tick, which already reflects any detection
        the tick is about to render — so a fresh bird never carries it."""
        return f"Nothing heard since {self._quiet['since_text']}" if self._quiet else None

    # -- single mode -------------------------------------------------------
    def _single_tick(self, now: datetime) -> None:
        self._memo(now)
        self._expire_pending(now)
        cursor = self._cursor()
        if cursor is None:
            # First run: start at the tail so we don't replay history, but show
            # the most recent existing detection once.
            self._set_cursor(self.source.max_rowid())
            if self._frame_bytes is None:
                latest = self._first_showable(
                    self.source.latest_many(self.config.confidence_threshold), now)
                if latest:
                    self._render_single(latest, now, reason="startup")
            return

        # One-time stale-cursor guard. A cursor left *ahead* of every real rowid
        # can never see anything "new", freezing the frame forever — it happens
        # when the detection id scheme changes under the stored cursor (e.g. a
        # BirdNET SQLite → BirdNET-Go REST switch leaves a huge timestamp-like
        # value behind). That is a between-runs condition (mid-run the cursor only
        # advances to real rowids), so check it once at startup rather than paying
        # a max_rowid() call — a network round-trip for the live source — on every
        # tick. Guard on max_rowid > 0 so a transient source blip (soft-fails to 0)
        # never trips it.
        if not self._cursor_verified:
            self._cursor_verified = True
            max_rowid = self.source.max_rowid()
            if max_rowid > 0 and cursor > max_rowid:
                self._set_cursor(max_rowid)
                latest = self._first_showable(
                    self.source.latest_many(self.config.confidence_threshold), now)
                if latest:
                    self._render_single(latest, now, reason="cursor-reset")
                return

        # The cursor always advances past the page, corroborated or not: a
        # held-back bird gets its second chance because its second detection
        # is a NEW row that arrives later and, corroborated by the first via
        # latest_many, passes the gate then.
        new = self.source.new_since(cursor, self.config.confidence_threshold,
                                    limit=_INGEST_PAGE)
        if len(new) >= _INGEST_PAGE:
            # Backlog: the page is full, so its newest row is not the newest
            # bird. Show the actual latest detection and jump the cursor to
            # the tail, instead of a stale bird now and another one per tick.
            # The cursor moves only once the tail is in hand: latest_many
            # soft-fails to [] on a blip, and jumping first would swallow the
            # whole backlog and render nothing.
            latest = self.source.latest_many(self.config.confidence_threshold)
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
            return

        # Dwell: a new bird keeps the frame against repeats of common birds.
        # Only a repeat of ANOTHER species is turned away — the held bird may
        # re-render (the clock moves with it), and a novel bird takes over
        # (newest novel wins). The cursor has already advanced: the repeat is
        # simply not shown, which is the point.
        holding = self._holding(self._meta, now)
        if (holding and self._novelty(candidate, now) == "repeat"
                and candidate.key != self._meta.get("species_key")):
            log.info("holding %s (%s) against %s for %d more min",
                     self._meta.get("label"), self._meta.get("novelty"),
                     candidate.common_name, holding["minutes_left"])
            return

        if not self.config.single_show_latest:
            # skip if the same species is already shown
            if candidate.key == self._meta.get("species_key"):
                return
            if self._within_debounce(now):
                return
        self._render_single(candidate, now, reason="detection")

    def _render_single(self, det: Detection, now: datetime, reason: str) -> None:
        ordinal = self.source.species_ordinal(det.scientific_name) if self.config.show_plate_number else None
        first_seen = self._first_seen(det.scientific_name)
        novelty = self._novelty(det, now)
        note = self._note_text()
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=det.timestamp if det.timestamp != datetime.min else now,
                          plate_number=ordinal, first_seen=first_seen, note=note,
                          first_ever=novelty == "first-ever")
        result = pipeline.render_single(spec, self.provider, self.config)
        self._commit(result, now, mode="single", species_key=det.key,
                     label=det.common_name, note=note, novelty=novelty)
        log.info("rendered single %s (%s, %s), etag=%s", det.common_name, reason, novelty,
                 result.etag)
        # The bird the page said it was waiting on is now on the wall.
        if self._pending and self._pending.get("key") == det.key:
            self._set_pending(None)

    # -- collage mode ------------------------------------------------------
    def _maybe_daytime_collage(self, now: datetime) -> None:
        interval = 24 * 3600 / max(1, self.config.collage_rebuilds_per_day)
        last = self._meta.get("collage_at")
        try:
            last_at = datetime.fromisoformat(last) if last else None
        except (ValueError, TypeError):
            last_at = None  # an unreadable stamp must not kill every tick
        if last_at and (now - last_at).total_seconds() < interval \
                and self._meta.get("mode") == "collage":
            return
        self._build_collage(now, ddate.today())

    def _review_date(self, now: datetime) -> ddate:
        """The day tonight's review covers, per the ACTIVE quiet window — in
        "sun" mode that is sunset->sunrise, not the custom start/end fields
        (which may be left at a non-wrapping daytime window)."""
        start, end = self.config.quiet_window(now.date())
        return review_date_for(now, start, end)

    def _maybe_quiet_collage(self, now: datetime) -> None:
        # The review covers the day the quiet window STARTED: after midnight
        # (default quiet hours wrap it) tonight's review is yesterday's day.
        # Keying by now.date() would clobber the held sheet at 00:00, buy a
        # pre-dawn sheet of two owls, and skip the real review every evening.
        review = self._review_date(now)
        stamp = review.isoformat()
        if self.db.get("quiet_collage_for") == stamp:
            return  # already rendered this window's review
        if self._build_collage(now, review, title="Sightings",
                               generated_ok=True):
            self.db.set("quiet_collage_for", stamp)

    def _collage_result(self, on_date: ddate,
                        title: str = "A Day in the Garden") -> Optional[RenderResult]:
        """Render a plain (non-generated) collage for one day, or None if
        fewer than 2 species. Used by the transient button view."""
        rows = self.source.top_species_today(on_date, self.config.confidence_threshold, limit=6)
        rows = [r for r in rows if not self.config.is_blocked(r["common"], r["scientific"])]
        if len(rows) < 2:
            return None
        cells = [collage_mod.CollageCell(r["common"], r["scientific"], r["count"]) for r in rows]
        img = collage_mod.render_collage(cells, self.provider, when=on_date,
                                         total_detections=sum(r["count"] for r in rows),
                                         title=title)
        return pipeline.render_image(img, self.config, "collage", f"{len(cells)} species")

    def force_day_review(self, repaint: bool = False) -> bool:
        """The config-page button: render today's day-in-review now. Reuses
        today's cached sheet unless repaint buys a fresh one."""
        now = datetime.now()
        review = self._review_date(now)
        return self._build_collage(now, review, title="Sightings",
                                   generated_ok=True, force_generated=repaint)

    def _build_collage(self, now: datetime, on_date: ddate,
                       title: str = "A Day in the Garden",
                       generated_ok: bool = False,
                       force_generated: bool = False) -> bool:
        rows = self.source.top_species_today(on_date, self.config.confidence_threshold, limit=6)
        rows = [r for r in rows if not self.config.is_blocked(r["common"], r["scientific"])]
        rows = self._corroborated_rows(rows, on_date)
        if len(rows) < 2:
            # Not enough for a grid: fall back to single for the day. This
            # runs on every tick while the day has one species, so skip the
            # render when that bird is already on the glass — otherwise it is
            # a full-panel render every 20 s all day (and all night in quiet
            # hours), and last_render_at churn breaks the debounce.
            latest = self._first_showable(
                self.source.latest_many(self.config.confidence_threshold), now)
            if latest and not self._showing_single(latest):
                self._render_single(latest, now, reason="collage-fallback")
            return False
        cells = [collage_mod.CollageCell(r["common"], r["scientific"], r["count"]) for r in rows]
        total = sum(r["count"] for r in rows)
        note = self._note_text()

        img = None
        label = f"{len(cells)}-species collage"
        # The generated composite is reserved for the nightly review (and the
        # explicit button): daytime collage rebuilds stay free.
        if generated_ok and self.config.collage_generated and self.genart is not None:
            top = cells[:5]  # the totem manner holds four or five species well
            sheet = self.genart.day_composite(top, on_date, force=force_generated)
            if sheet is not None:
                # The key must name what was PAINTED: on a cache hit the cells
                # come from the sheet's sidecar, not tonight's fresh tally.
                art, painted = sheet
                img = collage_mod.render_generated_collage(
                    art, painted, when=on_date,
                    total_detections=sum(c.count for c in painted), title=title,
                    note=note)
                label = f"day in review ({len(painted)} species)"
        if img is None:
            img = collage_mod.render_collage(cells, self.provider, when=on_date,
                                             total_detections=total, title=title,
                                             note=note)
        result = pipeline.render_image(img, self.config, "collage", label)
        self._commit(result, now, mode="collage", species_key=None, label=label, note=note)
        log.info("rendered collage (%s), etag=%s", label, result.etag)
        return True

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
        if ok and current and self._meta.get("species_key") == current:
            now = datetime.now()
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

    def start_day_review(self, repaint: bool = False) -> bool:
        return self._start_task("day-review", self.force_day_review, repaint)

    def task_status(self) -> dict:
        """Live state of the background one-shot jobs, for the config page's
        poller: which are running and any last error per job."""
        with self._task_lock:
            return {"running": sorted(self._tasks_inflight),
                    "errors": dict(self._task_errors)}

    # -- test detection ----------------------------------------------------
    def force_test_detection(self, common_name: str = "Northern Cardinal",
                             scientific_name: str = "Cardinalis cardinalis") -> RenderResult:
        """Inject a fake detection and render it now (bypasses debounce). The
        default is the Cardinal; any species name exercises the full provider
        chain, including AI generation for plate-less species. It also bypasses
        the new-species corroboration gate: this is a deliberate injection
        from the config page, not a detector guess, so a plate may be bought."""
        now = datetime.now()
        det = Detection(rowid=-1, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M:%S"),
                        common_name=common_name, scientific_name=scientific_name,
                        confidence=0.99)
        ordinal = self.source.species_ordinal(det.scientific_name) if self.config.show_plate_number else 1
        # A test bird is injected, not heard: the source is still silent, so
        # the footnote (if on) stays truthful.
        note = self._note_text()
        # The plate says "first recorded today" when the source has never
        # heard the species; the meta carries no novelty, so a test bird
        # never holds the frame against the real ones (and bypasses any hold).
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=now, plate_number=ordinal or 1, first_seen=now.strftime("%Y-%m-%d"),
                          note=note, first_ever=self._is_new_species(det.scientific_name, now.date()))
        result = pipeline.render_single(spec, self.provider, self.config)
        self._commit(result, now, mode="single", species_key=det.key,
                     label=f"{det.common_name} (test)", note=note, novelty=None)
        log.info("rendered TEST detection, etag=%s", result.etag)
        return result

    def rerender_current(self) -> None:
        """Re-render the current subject after a config change (e.g. dither/gray)."""
        with self._lock:
            meta = dict(self._meta)
        now = datetime.now()
        if meta.get("mode") == "collage":
            # Preserve what is showing: a day-in-review re-renders as one
            # (reusing the cached sheet for free), a grid as a grid.
            is_review = str(meta.get("label") or "").startswith("day in review")
            on_date = self._review_date(now) if is_review else ddate.today()
            self._build_collage(now, on_date,
                                title="Sightings" if is_review
                                else "A Day in the Garden",
                                generated_ok=is_review)
            return
        if not meta.get("label"):
            return
        # At a settings save no fresh detection is waiting (the tick already
        # consumed the cursor), so rebuild the subject the frame is showing.
        common = str(meta["label"]).removesuffix(" (test)")
        key = str(meta.get("species_key") or "")
        sci = (key[:1].upper() + key[1:]) if " " in key else ""
        det = Detection(rowid=-1, date=now.strftime("%Y-%m-%d"),
                        time=now.strftime("%H:%M:%S"), common_name=common,
                        scientific_name=sci, confidence=1.0)
        self._render_single(det, now, reason="settings")

    def refresh_now(self) -> None:
        """Manual Refresh button: re-render the frame that *should* be showing
        right now, per config. Unlike rerender_current (which preserves the
        resident subject for a settings-driven re-render), this re-decides — so
        it also recovers from a stale held collage once single mode is due
        again, instead of re-committing the collage."""
        self.reload_config()
        now = datetime.now()
        if self.config.in_quiet_hours(now.time()):
            # held overnight: keep the day-in-review if enabled, else the image
            if self.config.quiet_hours_render_collage:
                self._maybe_quiet_collage(now)
            else:
                self.rerender_current()
            return
        if self.config.mode == "collage":
            self._build_collage(now, ddate.today())
            return
        # single: commit the most recent qualifying detection now
        latest = self._first_showable(
            self.source.latest_many(self.config.confidence_threshold), now)
        if latest is not None:
            self._render_single(latest, now, reason="refresh")
        else:
            self.rerender_current()

    # -- on-demand views (frame buttons) -----------------------------------
    def render_collage_on_demand(self) -> Optional[RenderResult]:
        """Button view: yesterday's day-in-review, falling back to today.

        Transient — never committed as the current frame, so the next timer
        wake restores the resident bird.
        """
        today = ddate.today()
        for day in (today - timedelta(days=1), today):
            result = self._collage_result(day)
            if result is not None:
                log.info("on-demand collage for %s (%s)", day, result.label)
                return result
        return None

    def render_status_page(self, battery_voltage: Optional[float] = None,
                           battery_percent: Optional[int] = None,
                           wifi_rssi: Optional[int] = None) -> RenderResult:
        """Button view: a status plate. Transient, like the collage view."""
        last = self.source.latest(self.config.confidence_threshold)
        today_rows = self.source.top_species_today(
            ddate.today(), self.config.confidence_threshold, limit=50)
        info = statuspage.StatusInfo(
            battery_voltage=battery_voltage,
            battery_percent=battery_percent,
            wifi_rssi=wifi_rssi,
            last_common=last.common_name if last else None,
            last_when=last.timestamp if last else None,
            species_today=len(today_rows),
            species_all_time=self.source.all_time_species_count(),
            server_label=socket.gethostname(),
            wake_minutes=self.config.wake_interval_minutes,
        )
        img = statuspage.render_status(info)
        result = pipeline.render_image(img, self.config, "status", "status page")
        log.info("on-demand status page, etag=%s", result.etag)
        return result

    def record_view_checkin(self, user_agent: str,
                            battery_voltage: Optional[float],
                            battery_percent: Optional[int], view: str,
                            wifi_rssi: Optional[int] = None,
                            ip: Optional[str] = None,
                            device_extra: Optional[dict] = None) -> None:
        with self._lock:
            etag = self._etag
        self._record_checkin(user_agent, battery_voltage, battery_percent, etag,
                             served=view, rssi=wifi_rssi, ip=ip, extra=device_extra)

    # -- device-facing -----------------------------------------------------
    def get_frame(self, if_none_match: Optional[str], user_agent: str = "",
                  battery_voltage: Optional[float] = None,
                  battery_percent: Optional[int] = None,
                  wifi_rssi: Optional[int] = None,
                  ip: Optional[str] = None,
                  device_extra: Optional[dict] = None) -> tuple[int, Optional[bytes], Optional[str]]:
        """Return (http_status, body_or_None, etag). Records the check-in."""
        with self._lock:
            etag = self._etag
            body = self._frame_bytes
        self._record_checkin(user_agent, battery_voltage, battery_percent, etag,
                             served="304" if (etag and if_none_match == etag) else "frame",
                             rssi=wifi_rssi, ip=ip, extra=device_extra)
        if etag is None or body is None:
            return 503, None, None
        if if_none_match is not None and if_none_match == etag:
            return 304, None, etag
        return 200, body, etag

    def current_png_bytes(self) -> Optional[bytes]:
        png = paths.frames_dir() / _CURRENT_PNG
        return png.read_bytes() if png.exists() else None

    def current_etag(self) -> Optional[str]:
        with self._lock:
            return self._etag

    def _battery_recent(self, hours: float = 2) -> list[dict]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        try:
            return self.db.battery_history(since)
        except Exception:  # noqa: BLE001
            return []

    def _battery_live_copy(self) -> list[dict]:
        with self._lock:
            return list(self._battery_live)

    def battery_view(self, hours: int = 24) -> dict:
        """Readings for the trend line plus the inferred power state. The
        line ends on the live median so it agrees with the row above it."""
        now = datetime.now()
        rows = self._battery_recent(hours)
        live = self._battery_live_copy()
        items = [{"at": r["at"], "voltage": round(float(r["voltage"]), 3),
                  "percent": r.get("percent")} for r in rows]
        level = live_voltage(live, now)
        if level is not None and live:
            items.append({"at": live[-1]["at"], "voltage": round(level, 3),
                          "percent": live[-1].get("percent")})
        return {"items": items, "power": power_state(rows, now, live),
                "usb_v": _USB_V, "hours": hours}

    def current_info(self) -> dict:
        """etag/rendered_at of the resident frame without the source probes
        that status() makes."""
        with self._lock:
            return {"etag": self._etag, "rendered_at": self._meta.get("rendered_at")}

    def render_history(self, limit: int = _HISTORY_MAX) -> list[dict]:
        """Newest-first past frames for the config page: the render log rows
        with a display title and, where the file still exists, a thumb URL."""
        now = datetime.now()
        hist = paths.history_dir()
        out = []
        for row in self.db.render_history(limit):
            etag = str(row.get("etag") or "")
            has_thumb = bool(_ETAG_RE.match(etag)) and (hist / f"{etag}.png").exists()
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
                "when_text": when_short(then, now) if then else "",
            })
        return out

    def status(self) -> dict:
        with self._lock:
            meta = dict(self._meta)
            quiet = dict(self._quiet) if self._quiet else None
        now = datetime.now()
        latest = self.source.latest(self.config.confidence_threshold)
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
            "pending": self.pending_view(now),
            "birdnet_available": self.source.available(),
            "species_all_time": self.source.all_time_species_count(),
            "plates_loaded": self.audubon.species_count,
            "generated_cached": len(self.genart.cached_species()) if self.genart else 0,
            "device": asdict(self.device),
            "frame_card": frame_card(self.device, self.config.wake_interval_minutes,
                                     battery_history=self._battery_recent(),
                                     battery_live=self._battery_live_copy()),
            "config": self._masked_config(),
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

    # -- internal state helpers -------------------------------------------
    def _commit(self, result: RenderResult, now: datetime, mode: str,
                species_key: Optional[str], label: str,
                note: Optional[str] = None, novelty: Optional[str] = None) -> None:
        with self._lock:
            prev = self._meta
            # The dwell clock (held_since) starts when a novel bird takes the
            # frame and carries across its own re-renders: a first-today robin
            # calling again at 8:05 is classed a repeat, but it must not lose
            # the hold it earned at 8:00 — nor restart it. Its label carries
            # too, so the page keeps saying what earned the hold.
            same = (mode == "single" and species_key is not None
                    and prev.get("mode") == "single" and prev.get("species_key") == species_key)
            carried = same and prev.get("held_since") and prev.get("novelty") in _NOVEL
            if novelty in _NOVEL:
                held_since = prev["held_since"] if carried else now.isoformat(timespec="seconds")
            elif carried:
                novelty, held_since = prev["novelty"], prev["held_since"]
            else:
                held_since = None
            self._frame_bytes = result.frame
            self._etag = result.etag
            self._meta = {
                "etag": result.etag, "mode": mode, "label": label,
                "species_key": species_key, "rendered_at": now.isoformat(timespec="seconds"),
                "novelty": novelty, "held_since": held_since,
                "dark": self.config.dark_now(now.time()),
                "quiet_note": note is not None,   # what the glass says, for tick()'s flip
                "collage_at": now.isoformat(timespec="seconds") if mode == "collage"
                else self._meta.get("collage_at"),
            }
            frames = paths.frames_dir()
            # Write-then-rename: a power cut mid-write must leave the previous
            # complete frame on disk, never a torn one.
            tmp = frames / (_CURRENT_FFF + ".tmp")
            tmp.write_bytes(result.frame)
            os.replace(tmp, frames / _CURRENT_FFF)
            result.preview.save(frames / _CURRENT_PNG)
            self.db.set("current_frame", self._meta)
            self.db.set("last_render_at", now.isoformat())
        self.db.log_render(now.isoformat(timespec="seconds"), mode, label, result.etag)
        self._save_history_thumb(result)

    @staticmethod
    def _save_history_thumb(result: RenderResult) -> None:
        """A 1/8-scale thumbnail under frames/history/<etag>.png, pruning the
        oldest past _HISTORY_MAX. Best-effort: a full card or a bad file must
        never block the commit the device is waiting on."""
        try:
            hist = paths.history_dir()
            # Same content, same file: a re-render of identical pixels is free.
            target = hist / f"{result.etag}.png"
            if not target.exists():
                result.preview.reduce(_HISTORY_SCALE).save(target)
            thumbs = sorted((p for p in hist.glob("*.png") if _ETAG_RE.match(p.stem)),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in thumbs[_HISTORY_MAX:]:
                stale.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — a thumbnail is never worth a failed commit
            log.warning("history thumbnail for %s not saved", result.etag, exc_info=True)

    def _load_current_from_disk(self) -> None:
        fff = paths.frames_dir() / _CURRENT_FFF
        if fff.exists() and self._meta.get("etag"):
            data = fff.read_bytes()
            if not framebuffer.is_complete(data):
                # Torn or foreign file: serving it would hand the device a
                # container it rejects on every wake. Leave _frame_bytes None
                # so _ensure_initial_frame renders a fresh one.
                log.warning("%s is not a complete frame (%d bytes); re-rendering",
                            fff.name, len(data))
                return
            self._frame_bytes = data
            # The ETag is a content hash; derive it from the bytes actually on
            # disk rather than trusting the meta row (a crash between the two
            # writes in _commit would otherwise serve a tag for other pixels).
            self._etag = framebuffer.etag_for(self._frame_bytes)
            self._meta["etag"] = self._etag

    def _ensure_initial_frame(self) -> None:
        if self._frame_bytes is not None:
            return
        self.tick()

    def _record_checkin(self, ua, volt, pct, etag, served, rssi=None, ip=None,
                        extra=None) -> None:
        # `extra` carries the optional device-reported fields (fw_version, last_wake,
        # counters, panel/board) parsed from headers; unknown/missing keys default to
        # None on DeviceStatus, so old firmware simply leaves them unset. Every
        # value is re-validated here (finite, in range, bounded) — this row is
        # persisted and rendered, and the headers come off the LAN.
        fields = _clean_device_fields({
            **(extra or {}),
            "last_checkin": datetime.now().isoformat(timespec="seconds"),
            "battery_voltage": volt, "battery_percent": pct, "wifi_rssi": rssi,
            "last_result": served, "etag_served": etag, "user_agent": ua or None,
            "ip": ip or None})
        with self._lock:
            self.device = DeviceStatus(**fields)
            self.db.set("device_status", asdict(self.device))
        volt = fields.get("battery_voltage")
        if volt is not None and volt >= _BATTERY_ABSENT_V:
            stamp = fields["last_checkin"]
            with self._lock:
                cutoff = datetime.fromisoformat(stamp) - _LIVE_KEEP
                self._battery_live = [r for r in self._battery_live
                                      if datetime.fromisoformat(r["at"]) >= cutoff]
                self._battery_live.append({"at": stamp, "voltage": volt,
                                           "percent": fields.get("battery_percent")})
            try:
                self.db.log_battery(stamp, volt, fields.get("battery_percent"))
            except Exception:  # noqa: BLE001 — a log row must never fail a check-in
                log.debug("battery log write failed", exc_info=True)

    def _cursor(self) -> Optional[int]:
        return self.db.get("ingest_cursor", None)

    def _set_cursor(self, rowid: int) -> None:
        self.db.set("ingest_cursor", int(rowid))

    def _within_debounce(self, now: datetime) -> bool:
        last = self.db.get("last_render_at")
        if not last:
            return False
        try:
            elapsed = (now - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            return False
        return elapsed < self.config.refresh_debounce_minutes * 60

    def _showing_single(self, det: Detection) -> bool:
        """True if the resident frame is already a single plate of this species."""
        return (self._frame_bytes is not None
                and self._meta.get("mode") == "single"
                and self._meta.get("species_key") == det.key)

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
            rows = self.source.top_species_today(now.date(), self.config.confidence_threshold,
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

    def _holding(self, meta: dict, now: datetime) -> Optional[dict]:
        """The dwell hold on the resident frame, or None: a single plate of a
        novel species, held since less than dwell_minutes ago."""
        dwell = int(self.config.dwell_minutes)
        if dwell <= 0 or meta.get("mode") != "single" or meta.get("novelty") not in _NOVEL:
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
                "reason": "new species"}

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
        alone at/above corroborate_confidence, or with two detections inside
        the window at least the minimum gap apart — a genuine bird calls again;
        a car horn's two triggers land seconds apart."""
        cfg = self.config
        if not cfg.corroborate_new_species:
            return True, None
        if not self._is_new_species(det.scientific_name, now.date()):
            return True, None
        if det.confidence >= cfg.corroborate_confidence:
            return True, None
        window = timedelta(hours=cfg.corroborate_window_hours)
        recent = self.source.latest_many(min_confidence=cfg.confidence_threshold,
                                         limit=_CORROBORATE_SCAN)
        # The candidate itself counts once (latest_many may not include it —
        # a page fed from new_since, or a source whose tail lags).
        hits = {d.rowid: d for d in recent if d.key == det.key}
        hits.setdefault(det.rowid, det)
        stamps = [d.timestamp for d in hits.values()
                  if d.timestamp != datetime.min and d.timestamp >= now - window]
        best = max(d.confidence for d in hits.values())
        if best >= cfg.corroborate_confidence:
            return True, None   # a confident hit in the window vouches for the rest
        if len(stamps) >= 2 and (max(stamps) - min(stamps)) >= timedelta(
                minutes=cfg.corroborate_min_gap_minutes):
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
        that day, whose best detection never reached corroborate_confidence.
        One latest_many scan per build, not per row. force_test_detection is
        not a row here, so it is unaffected."""
        cfg = self.config
        if not cfg.corroborate_new_species:
            return rows
        suspects = [r for r in rows
                    if int(r.get("count") or 0) == 1
                    and self._is_new_species(r["scientific"], on_date)]
        if not suspects:
            return rows
        recent = self.source.latest_many(min_confidence=cfg.confidence_threshold,
                                         limit=_CORROBORATE_SCAN)
        best: dict[str, float] = {}
        for d in recent:
            best[d.key] = max(best.get(d.key, 0.0), d.confidence)
        keep = []
        for r in rows:
            key = str(r["scientific"]).strip().lower()
            if any(r is s for s in suspects) and best.get(key, 0.0) < cfg.corroborate_confidence:
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
        now = now or datetime.now()
        try:
            last_at = datetime.fromisoformat(str(p.get("last_at") or p.get("first_at")))
        except (TypeError, ValueError):
            return None
        if now - last_at > timedelta(hours=self.config.corroborate_window_hours):
            return None
        hits = int(p.get("hits") or 1)
        conf = float(p.get("confidence") or 0.0)
        if hits <= 1:
            waiting = f"1 hit at {conf:.2f} · waiting for a second"
        else:
            waiting = (f"{hits} hits at {conf:.2f} · waiting for one "
                       f"{self.config.corroborate_min_gap_minutes} min apart")
        return {"common": p.get("common"), "scientific": p.get("scientific"),
                "hits": hits, "confidence": conf, "first_at": p.get("first_at"),
                "waiting_text": waiting}
