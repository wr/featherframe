"""Featherframe configuration.

One flat, typed settings object persisted as a JSON blob in Featherframe's own
SQLite DB. Everything the config UI touches lives here. Defaults match the spec.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import time as dtime
from typing import Any


def _parse_hhmm(value: str, fallback: str) -> dtime:
    try:
        hh, mm = value.strip().split(":")
        return dtime(int(hh) % 24, int(mm) % 60)
    except (ValueError, AttributeError):
        hh, mm = fallback.split(":")
        return dtime(int(hh), int(mm))


@dataclass
class Config:
    """All user-facing settings. Persisted whole; edited via the config page."""

    # Display behaviour ----------------------------------------------------
    mode: str = "single"  # "single" | "collage" | "auto"
    confidence_threshold: float = 0.7
    refresh_debounce_minutes: int = 15  # never repaint the panel more often
    wake_interval_minutes: int = 15  # advisory: how often the device wakes

    # Quiet hours ----------------------------------------------------------
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "06:00"
    # If true, render a "day in review" collage once at quiet-hours start,
    # then hold it overnight. If false, just hold whatever was showing.
    quiet_hours_render_collage: bool = False

    # Curation -------------------------------------------------------------
    # Common or scientific names, matched case-insensitively.
    species_blocklist: list[str] = field(default_factory=list)

    # Ingest ---------------------------------------------------------------
    # Where detections come from. "birdnet_pi" reads a local SQLite DB;
    # "birdnet_go" polls BirdNET-Go's REST API.
    detection_backend: str = "birdnet_pi"  # "birdnet_pi" | "birdnet_go"
    birdnet_db_path: str = "~/BirdNET-Pi/scripts/birds.db"  # birdnet_pi backend
    birdnet_go_url: str = "http://localhost:8080"           # birdnet_go backend
    poll_interval_seconds: int = 20  # 10-30s per spec

    # Rendering ------------------------------------------------------------
    gray_mode: str = "16"  # "16" (4bpp) or "1" (1-bit fallback)
    dither: str = "bluenoise"  # "bluenoise" (fast, Pi-friendly) | "stucki" | "none"
    show_plate_number: bool = True
    # The panel's native canvas is landscape 1872x1404 and its setRotation() is a
    # no-op, so we rotate the portrait art into native orientation server-side.
    # Which way depends on how the frame is hung — fix it here, no reflash needed.
    panel_rotation: int = 90  # 0 | 90 | 180 | 270 degrees

    # Collage --------------------------------------------------------------
    collage_rebuilds_per_day: int = 3

    def __post_init__(self) -> None:
        self.sanitize()

    # -- validation --------------------------------------------------------
    def sanitize(self) -> "Config":
        if self.mode not in ("single", "collage", "auto"):
            self.mode = "single"
        self.confidence_threshold = _clamp(float(self.confidence_threshold), 0.0, 1.0)
        self.refresh_debounce_minutes = int(_clamp(self.refresh_debounce_minutes, 1, 720))
        self.wake_interval_minutes = int(_clamp(self.wake_interval_minutes, 1, 720))
        self.poll_interval_seconds = int(_clamp(self.poll_interval_seconds, 5, 300))
        if self.detection_backend not in ("birdnet_pi", "birdnet_go"):
            self.detection_backend = "birdnet_pi"
        self.birdnet_go_url = str(self.birdnet_go_url or "").strip().rstrip("/") or "http://localhost:8080"
        if self.gray_mode not in ("16", "1"):
            self.gray_mode = "16"
        if self.dither not in ("stucki", "bluenoise", "none"):
            self.dither = "stucki"
        self.collage_rebuilds_per_day = int(_clamp(self.collage_rebuilds_per_day, 1, 24))
        if self.panel_rotation not in (0, 90, 180, 270):
            self.panel_rotation = 90
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

        Handles the usual wrap-around-midnight window (e.g. 22:00 -> 06:00).
        """
        if not self.quiet_hours_enabled:
            return False
        start = _parse_hhmm(self.quiet_hours_start, "22:00")
        end = _parse_hhmm(self.quiet_hours_end, "06:00")
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
