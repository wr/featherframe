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
from .render.compose import SingleSpec
from .render.genart import GeneratedArtProvider, make_image_model
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
    last_result: Optional[str] = None      # "304" | "frame"
    etag_served: Optional[str] = None
    user_agent: Optional[str] = None


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
        self.genart = GeneratedArtProvider(make_image_model(config))
        return ChainedProvider([self.audubon, self.genart])

    @staticmethod
    def _imagegen_fields(config: Config) -> tuple:
        return (config.imagegen_enabled, config.imagegen_provider,
                config.imagegen_model, config.imagegen_quality,
                config.imagegen_api_key)

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
        if self._build_collage(now, review, title="The Day in Review",
                               generated_ok=True):
            self.db.set("quiet_collage_for", stamp)

    def force_day_review(self, repaint: bool = False) -> bool:
        """The config-page button: render today's day-in-review now. Reuses
        today's cached sheet unless repaint buys a fresh one."""
        now = datetime.now()
        review = review_date_for(now, self.config.quiet_hours_start,
                                 self.config.quiet_hours_end)
        return self._build_collage(now, review, title="The Day in Review",
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
                                title="The Day in Review" if is_review
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

    # -- device-facing -----------------------------------------------------
    def get_frame(self, if_none_match: Optional[str], user_agent: str = "",
                  battery_voltage: Optional[float] = None,
                  battery_percent: Optional[int] = None) -> tuple[int, Optional[bytes], Optional[str]]:
        """Return (http_status, body_or_None, etag). Records the check-in."""
        with self._lock:
            etag = self._etag
            body = self._frame_bytes
        self._record_checkin(user_agent, battery_voltage, battery_percent, etag,
                             served="304" if (etag and if_none_match == etag) else "frame")
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

    def _record_checkin(self, ua, volt, pct, etag, served) -> None:
        with self._lock:
            self.device = DeviceStatus(
                last_checkin=datetime.now().isoformat(timespec="seconds"),
                battery_voltage=volt, battery_percent=pct,
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
