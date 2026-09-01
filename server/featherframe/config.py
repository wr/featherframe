"""Featherframe configuration.

One flat, typed settings object persisted as a JSON blob in Featherframe's own
SQLite DB. Everything the config UI touches lives here. Defaults match the spec.
"""
from __future__ import annotations

import dataclasses
import math
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Any


def _parse_hhmm(value: str, fallback: str) -> dtime:
    try:
        hh, mm = value.strip().split(":")
        return dtime(int(hh) % 24, int(mm) % 60)
    except (ValueError, AttributeError):
        hh, mm = fallback.split(":")
        return dtime(int(hh), int(mm))


# Default latitude for the timezone-derived "sunset -> sunrise" window. The
# timezone alone can't give latitude, so we assume a temperate mid-latitude;
# seasonal drift is still modeled. A configurable lat/long is the follow-up
# (W-601) for exact times.
_SUN_LAT_DEG = 40.0


def _sun_window(on_date: date | None = None) -> tuple[dtime, dtime]:
    """Approximate (sunset, sunrise) local times for the given day — the night
    window for "sunset -> sunrise" quiet hours. Zero-config and network-free:
    models the seasonal swing at a default mid-latitude. Falls back to a fixed
    22:00 -> 06:00 window inside the polar day/night edge cases."""
    n = (on_date or date.today()).timetuple().tm_yday
    lat = math.radians(_SUN_LAT_DEG)
    decl = math.radians(23.44) * math.sin(2 * math.pi / 365.0 * (n + 284))
    cos_h = -math.tan(lat) * math.tan(decl)
    if cos_h <= -1.0 or cos_h >= 1.0:
        return dtime(22, 0), dtime(6, 0)  # sun never sets / never rises here
    h = math.degrees(math.acos(cos_h)) / 15.0  # half-day length in hours
    sunrise = 12.0 - h
    sunset = 12.0 + h

    def _t(hours: float) -> dtime:
        hours %= 24
        return dtime(int(hours), int(hours * 60) % 60)

    return _t(sunset), _t(sunrise)


@dataclass
class Config:
    """All user-facing settings. Persisted whole; edited via the config page."""

    # Display behaviour ----------------------------------------------------
    mode: str = "single"  # "single" | "collage" (legacy "auto" migrates to single)
    confidence_threshold: float = 0.7
    # Single mode shows the most recent qualifying detection. False applies
    # refresh_debounce_minutes and same-species suppression instead.
    single_show_latest: bool = True
    refresh_debounce_minutes: int = 15  # applies when single_show_latest is False
    wake_interval_minutes: int = 15  # advisory: how often the device wakes

    # Quiet hours ----------------------------------------------------------
    # "off" | "custom" (the start/end below) | "sun" (sunset -> sunrise,
    # derived from the system timezone; see _sun_window). A legacy
    # quiet_hours_enabled bool is migrated to this in from_dict().
    quiet_hours_mode: str = "custom"
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "06:00"
    # If true, render a "day in review" collage once at quiet-hours start,
    # then hold it overnight. If false, just hold whatever was showing.
    quiet_hours_render_collage: bool = False

    # Curation -------------------------------------------------------------
    # Common or scientific names, matched case-insensitively.
    species_blocklist: list[str] = field(default_factory=list)

    # Ingest ---------------------------------------------------------------
    # Where detections come from:
    #   "birdnet_go"  — poll BirdNET-Go's REST API
    #   "apprise"     — BirdNET-Pi pushes each detection to our webhook (push)
    #   "birdweather" — poll a BirdWeather station by its ID/token
    #   "custom"      — read a local BirdNET-Pi SQLite DB directly
    # The legacy id "birdnet_pi" is migrated to "custom" in sanitize().
    detection_backend: str = "custom"
    birdnet_db_path: str = "~/BirdNET-Pi/scripts/birds.db"   # custom (SQLite) backend
    birdnet_go_url: str = "http://localhost:8080"            # birdnet_go backend
    # When on, the BirdNET-Go source filters by BirdNET-Go's own confidence
    # threshold and ignores confidence_threshold.
    birdnet_go_defer_confidence: bool = True
    birdweather_station_id: str = ""    # birdweather backend: station token / ID
    # Optional shared secret in the Apprise webhook path (/api/ingest/apprise/<token>).
    # Empty accepts any LAN post, matching the app's no-auth LAN posture.
    apprise_token: str = field(default_factory=lambda: secrets.token_urlsafe(9))
    poll_interval_seconds: int = 20  # 10-30s per spec

    # Rendering ------------------------------------------------------------
    gray_mode: str = "16"  # "16" (4bpp) or "1" (1-bit fallback)
    dither: str = "bluenoise"  # "bluenoise" (fast, Pi-friendly) | "stucki" | "none"
    show_plate_number: bool = True
    # The panel's native canvas is landscape 1872x1404 and its setRotation() is a
    # no-op, so we rotate the portrait art into native orientation server-side.
    # Which way depends on how the frame is hung — fix it here, no reflash needed.
    panel_rotation: int = 90  # 0 | 90 | 180 | 270 degrees

    # Shrink the composition by this percent per edge and center it on white.
    mat_inset_pct: float = 4.0  # 0 disables
    # The physical mat is rarely mounted dead-center; shift the inset
    # composition to meet it. Positive = right / down, in panel pixels.
    mat_offset_x_px: int = 0
    mat_offset_y_px: int = 0

    # Invert the finished frame end-to-end: black field, white ink. The device
    # is told the effective state via X-FF-Invert so its baked boot screens
    # match. "off" | "on" | "quiet" (inverted only during quiet hours). A
    # legacy bool is migrated in sanitize().
    dark_mode: str = "off"

    # Collage --------------------------------------------------------------
    collage_rebuilds_per_day: int = 3

    # AI-generated plates --------------------------------------------------
    # For species Audubon never painted. A plate is generated once on first
    # detection and cached forever; only a manual regenerate replaces it.
    # Without an API key this degrades to serving already-cached plates.
    imagegen_enabled: bool = True
    # "openai" | "gemini" | "replicate" (aggregator) | "a1111" (self-hosted).
    imagegen_provider: str = "openai"
    imagegen_model: str = "gpt-image-2"    # provider-specific model id
    imagegen_quality: str = "high"         # low | medium | high | auto (OpenAI)
    imagegen_api_key: str = ""             # user-provided; lives only in our DB
    # Base URL for the self-hosted ("a1111") provider — an AUTOMATIC1111 /
    # ComfyUI-compatible /sdapi endpoint. Ignored by hosted providers.
    imagegen_base_url: str = "http://localhost:7860"
    imagegen_text_model: str = "gpt-5.6-luna"  # writes the naturalist's brief
    # The text ("brief") model can use a different provider/key than the image
    # model. "" means "follow the image provider" (back-compat). Otherwise:
    # "openai" | "gemini" | "anthropic" | "local" (OpenAI-compatible endpoint).
    imagegen_text_provider: str = ""
    imagegen_text_key: str = ""                # key for the text provider when it differs
    imagegen_text_base_url: str = "http://localhost:11434"  # "local" text provider base URL
    # The nightly "day in review" as one generated composite plate (the
    # folio's totem manner). Once per date; the grid collage is the fallback.
    collage_generated: bool = True

    def __post_init__(self) -> None:
        self.sanitize()

    # -- validation --------------------------------------------------------
    def sanitize(self) -> "Config":
        # "auto" was single-by-day + overnight review; it's now plain Single
        # mode. The overnight "day in review" is an opt-in toggle (default off).
        if self.mode == "auto":
            self.mode = "single"
        if self.mode not in ("single", "collage"):
            self.mode = "single"
        self.confidence_threshold = _clamp(float(self.confidence_threshold), 0.0, 1.0)
        self.refresh_debounce_minutes = int(_clamp(self.refresh_debounce_minutes, 1, 720))
        self.wake_interval_minutes = int(_clamp(self.wake_interval_minutes, 1, 720))
        self.poll_interval_seconds = int(_clamp(self.poll_interval_seconds, 5, 300))
        # The raw SQLite reader is now "custom"; migrate the legacy id.
        if self.detection_backend == "birdnet_pi":
            self.detection_backend = "custom"
        if self.detection_backend not in ("birdnet_go", "apprise", "birdweather", "custom"):
            self.detection_backend = "custom"
        self.birdnet_go_url = str(self.birdnet_go_url or "").strip().rstrip("/") or "http://localhost:8080"
        # Accept a full station URL (…/stations/XXXXX) or a bare ID/token.
        bw = str(self.birdweather_station_id or "").strip()
        if "/" in bw:
            bw = bw.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        self.birdweather_station_id = bw
        self.apprise_token = str(self.apprise_token or "").strip()
        if self.gray_mode not in ("16", "1"):
            self.gray_mode = "16"
        if self.dither not in ("stucki", "bluenoise", "none"):
            self.dither = "stucki"
        self.collage_rebuilds_per_day = int(_clamp(self.collage_rebuilds_per_day, 1, 24))
        if self.panel_rotation not in (0, 90, 180, 270):
            self.panel_rotation = 90
        self.mat_inset_pct = _clamp(float(self.mat_inset_pct), 0.0, 20.0)
        self.mat_offset_x_px = int(_clamp(int(self.mat_offset_x_px), -120, 120))
        self.mat_offset_y_px = int(_clamp(int(self.mat_offset_y_px), -120, 120))
        if self.imagegen_provider not in ("openai", "gemini", "replicate", "a1111"):
            self.imagegen_provider = "openai"
        self.imagegen_base_url = str(self.imagegen_base_url or "").strip().rstrip("/")
        self.imagegen_model = str(self.imagegen_model or "").strip() or "gpt-image-2"
        if self.imagegen_quality not in ("low", "medium", "high", "auto"):
            self.imagegen_quality = "high"
        self.imagegen_api_key = str(self.imagegen_api_key or "").strip()
        self.imagegen_text_model = (str(self.imagegen_text_model or "").strip()
                                    or "gpt-5.6-luna")
        if self.imagegen_text_provider not in ("", "openai", "gemini", "anthropic", "local"):
            self.imagegen_text_provider = ""
        self.imagegen_text_key = str(self.imagegen_text_key or "").strip()
        self.imagegen_text_base_url = str(self.imagegen_text_base_url or "").strip().rstrip("/")
        # Dark mode: migrate a legacy bool, then validate the enum.
        if isinstance(self.dark_mode, bool):
            self.dark_mode = "on" if self.dark_mode else "off"
        if self.dark_mode not in ("off", "on", "quiet"):
            self.dark_mode = "off"
        # Quiet hours: mode drives behaviour; migrate the legacy enabled flag,
        # then keep enabled in sync as a mirror of (mode != "off").
        if self.quiet_hours_mode not in ("off", "custom", "sun"):
            self.quiet_hours_mode = "custom"
        # Normalise quiet-hours strings to HH:MM
        self.quiet_hours_start = _fmt(_parse_hhmm(self.quiet_hours_start, "22:00"))
        self.quiet_hours_end = _fmt(_parse_hhmm(self.quiet_hours_end, "06:00"))
        # Blocklist: strip + drop blanks, keep order, dedupe case-insensitively
        seen = set()
        cleaned = []
        for s in self.species_blocklist or []:
            s = str(s).strip()
            key = s.lower()
            if s and key not in seen:
                seen.add(key)
                cleaned.append(s)
        self.species_blocklist = cleaned
        return self

    # -- derived -----------------------------------------------------------
    @property
    def bit_depth(self) -> int:
        return 4 if self.gray_mode == "16" else 1

    def is_blocked(self, common_name: str, sci_name: str) -> bool:
        block = {b.lower() for b in self.species_blocklist}
        return common_name.lower() in block or sci_name.lower() in block

    def in_quiet_hours(self, now: dtime) -> bool:
        """True if `now` (a datetime.time) falls in quiet hours.

        "sun" mode uses the timezone-derived sunset -> sunrise window; "custom"
        uses the start/end below. Handles the wrap-around-midnight window
        (e.g. 22:00 -> 06:00), which the night window always is.
        """
        if self.quiet_hours_mode == "off":
            return False
        if self.quiet_hours_mode == "sun":
            start, end = _sun_window()
        else:
            start = _parse_hhmm(self.quiet_hours_start, "22:00")
            end = _parse_hhmm(self.quiet_hours_end, "06:00")
        if start == end:
            return False
        if start < end:
            return start <= now < end
        # wraps past midnight
        return now >= start or now < end

    def dark_now(self, now: dtime | None = None) -> bool:
        """Effective inversion right now: always in "on", never in "off", and
        only during quiet hours in "quiet"."""
        if self.dark_mode == "on":
            return True
        if self.dark_mode == "quiet":
            return self.in_quiet_hours(now or datetime.now().time())
        return False

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Config":
        data = data or {}
        # Migrate a legacy quiet_hours_enabled bool (configs from before the
        # mode enum) into quiet_hours_mode.
        if "quiet_hours_mode" not in data and "quiet_hours_enabled" in data:
            data = {**data,
                    "quiet_hours_mode": "custom" if data["quiet_hours_enabled"] else "off"}
        fields = {f.name for f in dataclasses.fields(cls)}
        known = {k: v for k, v in data.items() if k in fields}
        return cls(**known)


def load_config(db) -> Config:
    """Load config from a db-like object exposing get(key, default)."""
    return Config.from_dict(db.get("config", {}))


def save_config(db, config: Config) -> None:
    """Persist config via a db-like object exposing set(key, value)."""
    db.set("config", config.sanitize().to_dict())


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _fmt(t: dtime) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"
