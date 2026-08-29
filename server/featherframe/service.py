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
from .render import pipeline
from .render import statuspage
from .render.compose import SingleSpec
from .render.genart import GeneratedArtProvider, make_image_model, make_text_model
from .render.pipeline import RenderResult
from .render.provider import ArtProvider, AudubonProvider, ChainedProvider

log = logging.getLogger("featherframe.service")

_CURRENT_FFF = "current.fff"
_CURRENT_PNG = "current.png"


def review_date_for(now: datetime, quiet_start: str, quiet_end: str) -> ddate:
    """The date a day-in-review covers: the day the quiet window started.
    With a midnight-wrapping window (the default 22:00-06:00), a tick after
    00:00 still reviews yesterday."""
    try:
        sh, sm = (int(x) for x in quiet_start.split(":"))
        eh, em = (int(x) for x in quiet_end.split(":"))
        start, end = dtime(sh, sm), dtime(eh, em)
    except (ValueError, TypeError):
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
            "battery": None, "served": None, "wifi_rssi": None}
    try:
        then = datetime.fromisoformat(device.last_checkin or "")
    except ValueError:
        return card
    card["seen"] = True
    card["last_seen"] = _ago(then, now)
    card["overdue"] = (now - then).total_seconds() > 2 * wake_interval_minutes * 60
    if device.battery_voltage is not None:
        pct = f" · {device.battery_percent}%" if device.battery_percent is not None else ""
        card["battery"] = f"{device.battery_voltage:.2f} V{pct}"
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
        self.source = make_source(self.config)

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

        # in-memory current frame
        self._frame_bytes: Optional[bytes] = None
        self._etag: Optional[str] = None
        self._meta: dict = self.db.get("current_frame", {}) or {}
        self._load_current_from_disk()

        self.device = DeviceStatus(**(self.db.get("device_status", {}) or {}))

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
            self._stop.wait(self.config.poll_interval_seconds)

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
                config.imagegen_text_model, config.imagegen_api_key)

    # -- config ------------------------------------------------------------
    def reload_config(self) -> None:
        with self._lock:
            new = load_config(self.db)
            if (new.detection_backend != self.config.detection_backend
                    or new.birdnet_db_path != self.config.birdnet_db_path
                    or new.birdnet_go_url != self.config.birdnet_go_url):
                self.source = make_source(new)
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

        if self.config.in_quiet_hours(now.time()):
            # Quiet hours: hold the image. Optionally render one day-in-review
            # collage at the start of the window (also implied by 'auto' mode).
            if self.config.quiet_hours_render_collage or self.config.mode == "auto":
                self._maybe_quiet_collage(now)
            return

        if not self.source.available():
            return  # soft fail; keep serving the current frame

        if self.config.mode == "collage":
            self._maybe_daytime_collage(now)
        else:  # single or auto -> single during the day
            self._single_tick(now)

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

        new = self.source.new_since(cursor, self.config.confidence_threshold)
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
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=det.timestamp if det.timestamp != datetime.min else now,
                          plate_number=ordinal, first_seen=first_seen)
        result = pipeline.render_single(spec, self.provider, self.config)
        self._commit(result, now, mode="single", species_key=det.key,
                     label=det.common_name)
        log.info("rendered single %s (%s), etag=%s", det.common_name, reason, result.etag)

    # -- collage mode ------------------------------------------------------
    def _maybe_daytime_collage(self, now: datetime) -> None:
        interval = 24 * 3600 / max(1, self.config.collage_rebuilds_per_day)
        last = self._meta.get("collage_at")
        if last and (now - datetime.fromisoformat(last)).total_seconds() < interval \
                and self._meta.get("mode") == "collage":
            return
        self._build_collage(now, ddate.today())

    def _maybe_quiet_collage(self, now: datetime) -> None:
        # The review covers the day the quiet window STARTED: after midnight
        # (default quiet hours wrap it) tonight's review is yesterday's day.
        # Keying by now.date() would clobber the held sheet at 00:00, buy a
        # pre-dawn sheet of two owls, and skip the real review every evening.
        review = review_date_for(now, self.config.quiet_hours_start,
                                 self.config.quiet_hours_end)
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
        review = review_date_for(now, self.config.quiet_hours_start,
                                 self.config.quiet_hours_end)
        return self._build_collage(now, review, title="Sightings",
                                   generated_ok=True, force_generated=repaint)

    def _build_collage(self, now: datetime, on_date: ddate,
                       title: str = "A Day in the Garden",
                       generated_ok: bool = False,
                       force_generated: bool = False) -> bool:
        rows = self.source.top_species_today(on_date, self.config.confidence_threshold, limit=6)
        rows = [r for r in rows if not self.config.is_blocked(r["common"], r["scientific"])]
        if len(rows) < 2:
            # Not enough for a grid: fall back to single for the day.
            latest = self._first_allowed(self.source.latest_many(self.config.confidence_threshold))
            if latest:
                self._render_single(latest, now, reason="collage-fallback")
            return False
        cells = [collage_mod.CollageCell(r["common"], r["scientific"], r["count"]) for r in rows]
        total = sum(r["count"] for r in rows)

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
                    total_detections=sum(c.count for c in painted), title=title)
                label = f"day in review ({len(painted)} species)"
        if img is None:
            img = collage_mod.render_collage(cells, self.provider, when=on_date,
                                             total_detections=total, title=title)
        result = pipeline.render_image(img, self.config, "collage", label)
        self._commit(result, now, mode="collage", species_key=None, label=label)
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
        spec = SingleSpec(common_name=det.common_name, scientific_name=det.scientific_name,
                          when=now, plate_number=ordinal or 1, first_seen=now.strftime("%Y-%m-%d"))
        result = pipeline.render_single(spec, self.provider, self.config)
        self._commit(result, now, mode="single", species_key=det.key,
                     label=f"{det.common_name} (test)")
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
            on_date = (review_date_for(now, self.config.quiet_hours_start,
                                       self.config.quiet_hours_end)
                       if is_review else ddate.today())
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
                            wifi_rssi: Optional[int] = None) -> None:
        with self._lock:
            etag = self._etag
        self._record_checkin(user_agent, battery_voltage, battery_percent, etag,
                             served=view, rssi=wifi_rssi)

    # -- device-facing -----------------------------------------------------
    def get_frame(self, if_none_match: Optional[str], user_agent: str = "",
                  battery_voltage: Optional[float] = None,
                  battery_percent: Optional[int] = None,
                  wifi_rssi: Optional[int] = None) -> tuple[int, Optional[bytes], Optional[str]]:
        """Return (http_status, body_or_None, etag). Records the check-in."""
        with self._lock:
            etag = self._etag
            body = self._frame_bytes
        self._record_checkin(user_agent, battery_voltage, battery_percent, etag,
                             served="304" if (etag and if_none_match == etag) else "frame",
                             rssi=wifi_rssi)
        if etag is None or body is None:
            return 503, None, None
        if if_none_match is not None and if_none_match == etag:
            return 304, None, etag
        return 200, body, etag

    def current_png_bytes(self) -> Optional[bytes]:
        png = paths.frames_dir() / _CURRENT_PNG
        return png.read_bytes() if png.exists() else None

    def status(self) -> dict:
        with self._lock:
            meta = dict(self._meta)
        latest = self.source.latest(self.config.confidence_threshold)
        return {
            "current": {
                "etag": self._etag,
                "mode": meta.get("mode"),
                "label": meta.get("label"),
                "rendered_at": meta.get("rendered_at"),
            },
            "last_detection": {
                "common": latest.common_name, "scientific": latest.scientific_name,
                "confidence": round(latest.confidence, 3),
                "at": f"{latest.date} {latest.time}",
            } if latest else None,
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
        key = cfg.get("imagegen_api_key") or ""
        cfg["imagegen_api_key"] = f"…{key[-4:]}" if key else ""
        return cfg

    # -- internal state helpers -------------------------------------------
    def _commit(self, result: RenderResult, now: datetime, mode: str,
                species_key: Optional[str], label: str) -> None:
        with self._lock:
            self._frame_bytes = result.frame
            self._etag = result.etag
            self._meta = {
                "etag": result.etag, "mode": mode, "label": label,
                "species_key": species_key, "rendered_at": now.isoformat(timespec="seconds"),
                "collage_at": now.isoformat(timespec="seconds") if mode == "collage"
                else self._meta.get("collage_at"),
            }
            frames = paths.frames_dir()
            (frames / _CURRENT_FFF).write_bytes(result.frame)
            result.preview.save(frames / _CURRENT_PNG)
            self.db.set("current_frame", self._meta)
            self.db.set("last_render_at", now.isoformat())
        self.db.log_render(now.isoformat(timespec="seconds"), mode, label, result.etag)

    def _load_current_from_disk(self) -> None:
        fff = paths.frames_dir() / _CURRENT_FFF
        if fff.exists() and self._meta.get("etag"):
            self._frame_bytes = fff.read_bytes()
            self._etag = self._meta.get("etag")

    def _ensure_initial_frame(self) -> None:
        if self._frame_bytes is not None:
            return
        self.tick()

    def _record_checkin(self, ua, volt, pct, etag, served, rssi=None) -> None:
        with self._lock:
            self.device = DeviceStatus(
                last_checkin=datetime.now().isoformat(timespec="seconds"),
                battery_voltage=volt, battery_percent=pct, wifi_rssi=rssi,
                last_result=served, etag_served=etag, user_agent=ua[:120] if ua else None)
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

    def _first_allowed(self, detections: list[Detection]) -> Optional[Detection]:
        for d in detections:
            if not self.config.is_blocked(d.common_name, d.scientific_name):
                return d
        return None

    def _first_seen(self, scientific_name: str) -> Optional[str]:
        return self.source.first_seen_date(scientific_name)
