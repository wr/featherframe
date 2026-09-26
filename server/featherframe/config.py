"""Featherframe configuration.

One flat, typed settings object persisted as a JSON blob in Featherframe's own
SQLite DB. Everything the config UI touches lives here. Defaults match the spec.
"""
from __future__ import annotations

import dataclasses
import math
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, time as dtime
from typing import Any

from . import panels


def _parse_hhmm(value: str, fallback: str) -> dtime:
    """"HH:MM" -> time, or the fallback when it isn't a real clock time. An
    out-of-range field is rejected, not wrapped: "99:99" used to become
    03:39 silently, which is nobody's quiet hours."""
    t = valid_hhmm(value)
    if t is not None:
        return t
    hh, mm = fallback.split(":")
    return dtime(int(hh), int(mm))


def valid_hhmm(value) -> dtime | None:
    """The time a "HH:MM" string names, or None if it doesn't name one."""
    try:
        hh, mm = value.strip().split(":")
        return dtime(int(hh), int(mm))  # dtime() range-checks both
    except (ValueError, AttributeError, TypeError):
        return None


def valid_email(value) -> str:
    """The address, trimmed and lower-cased, or "" when it isn't one."""
    s = str(value or "").strip().lower()
    return s if (len(s) <= 254 and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", s)) else ""


# Blocklist bounds: the config blob is loaded on every tick, so it must stay
# small; a species name is never this long, and nobody blocks 500 species.
_BLOCKLIST_MAX_ENTRIES = 500
_BLOCKLIST_MAX_CHARS = 64


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


COLLAGE_BRANCHES = ("season", "weather", "bare")

# The regions the Region setting offers, each first in its own folios.
REGIONS = ("north-america", "europe", "australia", "asia")


@dataclass
class Config:
    """The household's settings, plus the shape a render takes.

    Persisted whole. The render fields below (`mode`, `panel`, `panel_rotation`,
    `mat_*`, `power_mode`, `wake_interval_minutes`, `device_poll_seconds`) are
    each frame's own, not the household's: `frames.frame_config` builds a Config
    per frame from that frame's row, and the stored values for those fields are
    not read for any frame (W-833).
    """

    # Display behaviour ----------------------------------------------------
    # "single" | "collage" (legacy "auto" migrates to single). A fresh install
    # starts in its panel's own mode (panels.py): a plate per detection on the
    # gray panel, a collage every few hours on the slow colour one.
    mode: str = field(default_factory=lambda: panels.get(
        os.environ.get("FEATHERFRAME_PANEL", "ee03")).mode)
    # Served to the device on every /api/frame response (W-456/W-736): the
    # power model and, in deep sleep, how long it sleeps between check-ins.
    # "awake": stays on Wi-Fi and polls every 15 s (USB); "sleep": deep-sleeps
    # and wakes on the interval or a button (battery).
    power_mode: str = "awake"
    wake_interval_minutes: int = 15
    # Always awake: how often the frame asks for a new plate (a conditional
    # GET, 304 with no body when nothing changed). Served as X-Poll-Seconds.
    device_poll_seconds: int = 3

    # Quiet hours ----------------------------------------------------------
    # "off" | "custom" (the start/end below) | "sun" (sunset -> sunrise,
    # derived from the system timezone; see _sun_window). A legacy
    # quiet_hours_enabled bool is migrated to this in from_dict().
    quiet_hours_mode: str = "custom"
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "06:00"

    # Curation -------------------------------------------------------------
    # Common or scientific names, matched case-insensitively.
    species_blocklist: list[str] = field(default_factory=list)

    # Ingest ---------------------------------------------------------------
    # Where detections come from:
    #   "birdnet_go"  — BirdNET-Go pushes each detection to our webhook (W-865)
    #   "apprise"     — BirdNET-Pi pushes each detection through Apprise
    #   "birdweather" — poll a BirdWeather station by its ID/token
    #   "custom"      — read a local BirdNET-Pi SQLite DB directly
    # The legacy id "birdnet_pi" is migrated to "custom" in sanitize().
    detection_backend: str = "custom"
    birdnet_db_path: str = "~/BirdNET-Pi/scripts/birds.db"   # custom (SQLite) backend
    birdweather_station_id: str = ""    # birdweather backend: station token / ID
    # The secret in both push paths (/api/ingest/apprise/<token>,
    # /api/ingest/birdnet-go/<token>); hosted routes a push by it. Empty
    # accepts any LAN post, matching the app's no-auth LAN posture.
    ingest_token: str = field(default_factory=lambda: secrets.token_urlsafe(9))

    # Rendering ------------------------------------------------------------
    # The panel a render is for (panels.py): "ee03" (10.3" gray), "ee02"
    # (13.3" Spectra 6 colour), or a key spelling out a reported panel's facts
    # ("custom:800x480:gray16:0,180", W-813). State, not a setting: every frame
    # is drawn for the panel IT reports, and `frames.frame_config` sets this
    # field per frame (W-833). The stored value is never read for a frame;
    # FEATHERFRAME_PANEL still seeds a fresh install.
    panel: str = field(default_factory=lambda: os.environ.get("FEATHERFRAME_PANEL", "ee03"))
    # The panel's native canvas is landscape 1872x1404 and its setRotation() is a
    # no-op, so we rotate the portrait art into native orientation server-side.
    # Which way depends on how the frame is hung — fix it here, no reflash needed.
    # The colour panel's canvas is natively portrait: there it is 0 | 180.
    panel_rotation: int = 90  # per panel (panels.py rotations; see sanitize)

    # Shrink the composition by this percent per edge and center it on white.
    # 0 (the default) disables it: a frame with no mat, or one whose opening
    # the art already meets, needs no allowance.
    mat_inset_pct: float = 0.0
    # The physical mat is rarely mounted dead-center; shift the inset
    # composition to meet it. Positive = right / down, in panel pixels.
    mat_offset_x_px: int = 0
    mat_offset_y_px: int = 0
    # A 2 px line around the composition, drawn on the glass while the mat's
    # inset and offset are being set against it. Off by default.
    mat_guide: bool = False

    # Collage --------------------------------------------------------------
    # Collage mode draws a new sheet this often. On the colour panel, where a
    # refresh takes half a minute, that beats a plate per detection.
    collage_interval_hours: int = 6   # one of the page's choices: 1, 4, 6, 12, 24

    # AI-generated plates --------------------------------------------------
    # For species no folio has. A plate is generated once on first
    # detection and cached forever; only a manual regenerate replaces it.
    # Without an API key this degrades to serving already-cached plates.
    imagegen_enabled: bool = True
    # "openai" | "gemini" | "replicate" (aggregator) | "a1111" (self-hosted).
    imagegen_provider: str = "openai"
    imagegen_model: str = "gpt-image-2.5-sunburst"   # provider-specific model id
    imagegen_quality: str = "medium"       # low|medium|high|auto, +xhigh|max on gpt-image-2.5
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
    # Every collage as one generated composite plate (the folio's totem
    # manner), bought again only when the day's species change; the grid
    # collage is the fallback.
    collage_generated: bool = True
    # How many of the day's species the generated sheet carries, most-heard
    # first. 0 = every species heard that day. The grid fallback always holds six.
    collage_species_max: int = 10
    # The generated collage's branch (W-882): "season" (the season of its
    # date, W-881), "weather" (that, plus the day's own weather, asked of the
    # text model's web search) or "bare" (the bare branch of before).
    collage_branch: str = "season"
    # Install each official firmware release on every frame already on an
    # official one, without a press (W-838). A dev build is never replaced.
    firmware_auto_update: bool = False
    # The owner's email (W-773): the name the page's sign-in asks for when a
    # password is set. A hosted household's is its account's, kept by the
    # Worker, and this one is not read there.
    owner_email: str = ""
    # Which folio a plate is looked for in first (W-702): a region names its
    # folios in their headers (Havell: north-america, Gould: europe). It
    # reorders, never filters: a species only another region's folio has is
    # still drawn from it.
    region: str = "north-america"

    def __post_init__(self) -> None:
        self.sanitize()

    # -- validation --------------------------------------------------------
    def sanitize(self) -> "Config":
        # "auto" was single-by-day + overnight review; it's now plain Single
        # mode. The overnight collage is an opt-in toggle (default off).
        if self.mode == "auto":
            self.mode = "single"
        if self.mode not in ("single", "collage"):
            self.mode = "single"
        if self.region not in REGIONS:
            self.region = "north-america"
        # NaN slips through float() and then through _clamp (every comparison
        # is False) — and can't be serialised for the status JSON. Refuse it.
        self.wake_interval_minutes = int(_clamp(_finite(self.wake_interval_minutes, 15), 1, 1440))
        if self.power_mode not in ("awake", "sleep"):
            self.power_mode = "awake"
        self.device_poll_seconds = int(_clamp(_finite(self.device_poll_seconds, 3), 2, 86400))
        # The raw SQLite reader is now "custom"; migrate the legacy id.
        if self.detection_backend == "birdnet_pi":
            self.detection_backend = "custom"
        if self.detection_backend not in ("birdnet_go", "apprise", "birdweather", "custom"):
            self.detection_backend = "custom"
        # Accept a full station URL (…/stations/XXXXX) or a bare ID/token.
        bw = str(self.birdweather_station_id or "").strip()
        if "/" in bw:
            bw = bw.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        self.birdweather_station_id = bw
        self.ingest_token = str(self.ingest_token or "").strip()
        self.collage_interval_hours = int(_clamp(_finite(self.collage_interval_hours, 6), 1, 24))
        self.collage_species_max = int(_clamp(self.collage_species_max, 0, 60))
        if self.collage_branch not in COLLAGE_BRANCHES:
            self.collage_branch = "season"
        # The panel's native canvas is landscape and the firmware rejects a
        # portrait frame (pushImage would clip it into garbage), so only the
        # two landscape orientations are valid. Old 0/180 values migrate to
        # the landscape orientation with the same relative flip.
        self.panel = panels.get(self.panel).key
        valid = panels.get(self.panel).rotations
        try:
            rot = int(self.panel_rotation)
        except (TypeError, ValueError):
            rot = valid[0]
        if rot not in valid:
            # The same relative flip on the other panel's axes (0<->90, 180<->270).
            rot = {0: 90, 180: 270, 90: 0, 270: 180}.get(rot, valid[0])
        self.panel_rotation = rot if rot in valid else valid[0]
        self.mat_inset_pct = _clamp(_finite(self.mat_inset_pct, 0.0), 0.0, 20.0)
        self.mat_offset_x_px = int(_clamp(int(self.mat_offset_x_px), -120, 120))
        self.mat_offset_y_px = int(_clamp(int(self.mat_offset_y_px), -120, 120))
        self.mat_guide = bool(self.mat_guide)
        self.firmware_auto_update = bool(self.firmware_auto_update)
        self.owner_email = valid_email(self.owner_email)
        if self.imagegen_provider not in ("openai", "gemini", "replicate", "a1111"):
            self.imagegen_provider = "openai"
        self.imagegen_base_url = str(self.imagegen_base_url or "").strip().rstrip("/")
        self.imagegen_model = (str(self.imagegen_model or "").strip()
                               or "gpt-image-2.5-sunburst")
        # xhigh/max exist only on gpt-image-2.5; they are kept here so the
        # choice survives a model switch, and clamped to high at request time
        # by OpenAIImageModel for anything older.
        if self.imagegen_quality not in ("low", "medium", "high", "auto",
                                         "xhigh", "max"):
            self.imagegen_quality = "medium"
        self.imagegen_api_key = str(self.imagegen_api_key or "").strip()
        self.imagegen_text_model = (str(self.imagegen_text_model or "").strip()
                                    or "gpt-5.6-luna")
        if self.imagegen_text_provider not in ("", "openai", "gemini", "anthropic", "local"):
            self.imagegen_text_provider = ""
        self.imagegen_text_key = str(self.imagegen_text_key or "").strip()
        self.imagegen_text_base_url = str(self.imagegen_text_base_url or "").strip().rstrip("/")
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
            s = str(s).strip()[:_BLOCKLIST_MAX_CHARS]
            key = s.lower()
            if s and key not in seen:
                seen.add(key)
                cleaned.append(s)
            if len(cleaned) >= _BLOCKLIST_MAX_ENTRIES:
                break
        self.species_blocklist = cleaned
        return self

    # -- derived -----------------------------------------------------------
    @property
    def quiet_hours_render_collage(self) -> bool:
        """Quiet hours ARE the overnight collage: the day's collage is drawn
        once at the start of the window and held until it ends. It stopped
        being a toggle of its own — turning quiet hours on is the whole of it.
        """
        return self.quiet_hours_mode != "off"

    @property
    def bit_depth(self) -> int:
        spec = self.panel_spec
        if spec.color:
            return 4          # ink nibbles
        if spec.fmt == "mono":
            return 1
        return 2 if spec.fmt == "gray2" else 4

    @classmethod
    def defaults_for(cls, panel: str) -> "Config":
        """A factory-fresh config for `panel` (mode and rotation follow it)."""
        fresh = cls(panel=panels.get(panel).key)
        fresh.mode = fresh.panel_spec.mode
        fresh.panel_rotation = fresh.panel_spec.rotations[0]
        return fresh.sanitize()

    def panel_settings_off_default(self) -> list[str]:
        """The panel-dependent settings that differ from this panel's defaults."""
        mine, fresh = self.to_dict(), Config.defaults_for(self.panel).to_dict()
        return [k for k in panels.PANEL_SETTINGS if mine.get(k) != fresh.get(k)]

    @property
    def panel_spec(self) -> "panels.Panel":
        return panels.get(self.panel)

    def is_blocked(self, common_name: str, sci_name: str) -> bool:
        block = {b.lower() for b in self.species_blocklist}
        return common_name.lower() in block or sci_name.lower() in block

    def quiet_window(self, on_date: date | None = None) -> tuple[dtime, dtime]:
        """(start, end) of the ACTIVE quiet window: sunset -> sunrise in "sun"
        mode, else the custom start/end fields. The one place that knows which
        applies — callers that reason about the window (is it wrapping
        midnight? which day did it start?) must use this, not the raw fields,
        which may hold a stale non-wrapping window while "sun" is selected."""
        if self.quiet_hours_mode == "sun":
            return _sun_window(on_date)
        return (_parse_hhmm(self.quiet_hours_start, "22:00"),
                _parse_hhmm(self.quiet_hours_end, "06:00"))

    def in_quiet_hours(self, now: dtime) -> bool:
        """True if `now` (a datetime.time) falls in quiet hours.

        "sun" mode uses the timezone-derived sunset -> sunrise window; "custom"
        uses the start/end below. Handles the wrap-around-midnight window
        (e.g. 22:00 -> 06:00), which the night window always is.
        """
        if self.quiet_hours_mode == "off":
            return False
        start, end = self.quiet_window()
        if start == end:
            return False
        if start < end:
            return start <= now < end
        # wraps past midnight
        return now >= start or now < end

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
        # "N rebuilds a day" became "every N hours" (W-821).
        if "collage_interval_hours" not in data and "collage_rebuilds_per_day" in data:
            rebuilds = _clamp(_finite(data["collage_rebuilds_per_day"], 3), 1, 24)
            data = {**data, "collage_interval_hours": round(24 / rebuilds)}
        # The "day in review" is a collage like any other (one name for it).
        if "collage_species_max" not in data and "review_species_max" in data:
            data = {**data, "collage_species_max": data["review_species_max"]}
        # One secret for both push sources (W-865): it was Apprise's alone.
        if "ingest_token" not in data and "apprise_token" in data:
            data = {**data, "ingest_token": data["apprise_token"]}
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


def _finite(v: Any, default: float) -> float:
    """float(v) if it is a finite number, else default."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _fmt(t: dtime) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"
