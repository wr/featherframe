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
               now: Optional[datetime] = None) -> dict:
    """The wall frame's health, pre-chewed for the config page: ready-to-print
    strings plus one overdue flag. Overdue means the device has missed two
    consecutive wake intervals — one 304 skipped is normal jitter, two is a
    dead battery or lost Wi-Fi."""
    now = now or datetime.now()
    card = {"seen": False, "overdue": False,
            "expected_minutes": wake_interval_minutes, "last_seen": None,
            "last_checkin_iso": None, "battery": None, "battery_low": False,
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
        pct = f" · {device.battery_percent}%" if device.battery_percent is not None else ""
        card["battery"] = f"{device.battery_voltage:.2f} V{pct}"
        card["battery_low"] = ((device.battery_percent is not None and device.battery_percent <= 20)
                               or device.battery_voltage <= _BATTERY_LOW_V)
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

        # Gone-quiet alarm, computed once per tick (status() is polled, and
        # the walk plus a source query is not free). None = no alarm.
        self._quiet: Optional[dict] = None
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
        cursor = self._cursor()
        if cursor is None:
            # First run: start at the tail so we don't replay history, but show
            # the most recent existing detection once.
            self._set_cursor(self.source.max_rowid())
            if self._frame_bytes is None:
                latest = self._first_allowed(self.source.latest_many(self.config.confidence_threshold))
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
                latest = self._first_allowed(self.source.latest_many(self.config.confidence_threshold))
                if latest:
                    self._render_single(latest, now, reason="cursor-reset")
                return

        new = self.source.new_since(cursor, self.config.confidence_threshold,
                                    limit=_INGEST_PAGE)
        if len(new) >= _INGEST_PAGE:
            # Backlog: the page is full, so its newest row is not the newest
            # bird. Show the actual latest detection and jump the cursor to
            # the tail, instead of a stale bird now and another one per tick.
            # The cursor moves only once a candidate is in hand: latest_many
            # soft-fails to [] on a blip, and jumping first would swallow the
            # whole backlog and render nothing.
            latest = self.source.latest_many(self.config.confidence_threshold)
            candidate = self._first_allowed(latest)
            if candidate is not None:
                self._set_cursor(max(self.source.max_rowid(), new[-1].rowid))
            else:
                # Can't see the tail right now: fall back to the page we have.
                self._set_cursor(new[-1].rowid)
                candidate = self._first_allowed(list(reversed(new)))
        else:
            if new:
                self._set_cursor(new[-1].rowid)
            candidate = self._first_allowed(list(reversed(new)))  # newest allowed
        if candidate is None:
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
        note = self._note_text()
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=det.timestamp if det.timestamp != datetime.min else now,
                          plate_number=ordinal, first_seen=first_seen, note=note)
        result = pipeline.render_single(spec, self.provider, self.config)
        self._commit(result, now, mode="single", species_key=det.key,
                     label=det.common_name, note=note)
        log.info("rendered single %s (%s), etag=%s", det.common_name, reason, result.etag)

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
        if len(rows) < 2:
            # Not enough for a grid: fall back to single for the day. This
            # runs on every tick while the day has one species, so skip the
            # render when that bird is already on the glass — otherwise it is
            # a full-panel render every 20 s all day (and all night in quiet
            # hours), and last_render_at churn breaks the debounce.
            latest = self._first_allowed(self.source.latest_many(self.config.confidence_threshold))
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
        chain, including AI generation for plate-less species."""
        now = datetime.now()
        det = Detection(rowid=-1, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M:%S"),
                        common_name=common_name, scientific_name=scientific_name,
                        confidence=0.99)
        ordinal = self.source.species_ordinal(det.scientific_name) if self.config.show_plate_number else 1
        # A test bird is injected, not heard: the source is still silent, so
        # the footnote (if on) stays truthful.
        note = self._note_text()
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=now, plate_number=ordinal or 1, first_seen=now.strftime("%Y-%m-%d"),
                          note=note)
        result = pipeline.render_single(spec, self.provider, self.config)
        self._commit(result, now, mode="single", species_key=det.key,
                     label=f"{det.common_name} (test)", note=note)
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
        latest = self._first_allowed(
            self.source.latest_many(self.config.confidence_threshold))
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
            },
            "last_detection": {
                "common": latest.common_name, "scientific": latest.scientific_name,
                "confidence": round(latest.confidence, 3),
                "at": f"{latest.date} {latest.time}",
                "when_text": (when_text(latest.timestamp, now)
                              if latest.timestamp != datetime.min else ""),
            } if latest else None,
            "quiet": quiet,
            "birdnet_available": self.source.available(),
            "species_all_time": self.source.all_time_species_count(),
            "plates_loaded": self.audubon.species_count,
            "generated_cached": len(self.genart.cached_species()) if self.genart else 0,
            "device": asdict(self.device),
            "frame_card": frame_card(self.device, self.config.wake_interval_minutes),
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
                note: Optional[str] = None) -> None:
        with self._lock:
            self._frame_bytes = result.frame
            self._etag = result.etag
            self._meta = {
                "etag": result.etag, "mode": mode, "label": label,
                "species_key": species_key, "rendered_at": now.isoformat(timespec="seconds"),
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

    def _first_allowed(self, detections: list[Detection]) -> Optional[Detection]:
        for d in detections:
            if not self.config.is_blocked(d.common_name, d.scientific_name):
                return d
        return None

    def _first_seen(self, scientific_name: str) -> Optional[str]:
        return self.source.first_seen_date(scientific_name)
