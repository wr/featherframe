"""The detection-source interface.

BirdNET-Pi's SQLite, BirdWeather's API and the pushed feeds (BirdNET-Pi's
Apprise, BirdNET-Go's webhook) implement ``DetectionSource``; ``service.tick()``
uses it without knowing the backend. Every method soft-fails to a safe default
(None / [] / 0). The cursor is an opaque monotonic ``int`` (a rowid for the
SQLite, a detection id for BirdWeather, a queue id for a push), persisted by
the service.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Detection:
    """One bird, normalised across every source.

    ``rowid`` is the opaque cursor id (BirdNET-Pi rowid, BirdWeather id, push queue id).
    ``date`` / ``time`` are local strings ('YYYY-MM-DD', 'HH:MM:SS'), matching how
    both BirdNET-Pi and BirdNET-Go report wall-clock time.
    """
    rowid: int
    date: str
    time: str
    common_name: str
    scientific_name: str
    confidence: float

    @property
    def timestamp(self) -> datetime:
        try:
            return datetime.strptime(f"{self.date} {self.time}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min

    @property
    def key(self) -> str:
        """Stable species identity for debounce / same-species comparison."""
        return self.scientific_name.strip().lower()


def _degrees(value, limit: float) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if -limit <= v <= limit else None


def valid_location(lat, lon) -> Optional[tuple[float, float]]:
    """(latitude, longitude) in degrees, or None unless both are one."""
    la, lo = _degrees(lat, 90.0), _degrees(lon, 180.0)
    return None if la is None or lo is None else (la, lo)


class DetectionSource(abc.ABC):
    """The interface every backend implements. Names match the scheduler's calls.

    Implementations must never raise from these methods — catch backend errors
    and return the documented safe default.
    """

    #: short backend id, for logging / status
    name: str = "source"

    @abc.abstractmethod
    def available(self) -> bool:
        """True if the source is reachable and looks usable."""

    @abc.abstractmethod
    def max_rowid(self) -> int:
        """Current tail cursor, for starting without replaying history. 0 on failure."""

    @abc.abstractmethod
    def new_since(self, cursor: int, min_confidence: float = 0.0,
                  limit: int = 500) -> list[Detection]:
        """Detections newer than ``cursor`` at/above ``min_confidence``, oldest
        first. Empty on failure."""

    @abc.abstractmethod
    def latest_many(self, min_confidence: float = 0.0, limit: int = 25) -> list[Detection]:
        """Recent detections, newest first. Empty on failure."""

    def latest(self, min_confidence: float = 0.0, scan: int = 25) -> Optional[Detection]:
        """Most recent detection at/above the threshold, or None. Concrete
        implementations may override."""
        recent = self.latest_many(min_confidence=min_confidence, limit=scan)
        return recent[0] if recent else None

    @abc.abstractmethod
    def top_species_today(self, on_date=None, min_confidence: float = 0.0,
                          limit: int = 6) -> list[dict]:
        """Day's most-frequent species: [{common, scientific, count}], desc. [] on
        failure or if the backend can't answer it."""

    @abc.abstractmethod
    def all_time_species_count(self) -> int:
        """Distinct species ever seen. 0 on failure."""

    @abc.abstractmethod
    def first_seen_date(self, scientific_name: str) -> Optional[str]:
        """Earliest date ('YYYY-MM-DD') this species was recorded. None if unknown."""

    def location(self) -> Optional[tuple[float, float]]:
        """The station's (latitude, longitude), where the source reports one:
        what sets the collage's season in its hemisphere (W-881) and asks for
        its day's weather (W-882). None if unknown."""
        return None

    def latitude(self) -> Optional[float]:
        loc = self.location()
        return loc[0] if loc else None

    def heard_before(self, scientific_name: str, on_date) -> Optional[bool]:
        """Whether this species had been recorded before `on_date` (a date):
        what makes a detection "first-ever". None when the source can't say.
        From first_seen_date unless a source knows better (BirdWeather has no
        first dates, but has all-time totals)."""
        first = self.first_seen_date(scientific_name)
        return None if first is None else str(first) < on_date.isoformat()
