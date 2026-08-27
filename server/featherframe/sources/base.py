"""The detection-source seam.

A ``DetectionSource`` is where the birds come from. BirdNET-Pi (local SQLite),
BirdNET-Go (local REST), and later BirdWeather (cloud REST) all implement this
one interface, so ``service.tick()`` never learns which backend it's talking to.

The contract mirrors what the scheduler actually asks for, and every method
*soft-fails* to a safe default (None / [] / 0) — a missing or odd source keeps
the current frame on the wall instead of crashing. Priority: never a broken frame.

The cursor is an opaque monotonic ``int`` (a rowid for Pi, a detection ``id`` for
Go); it's persisted by the service so we never replay history.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Detection:
    """One bird, normalised across every source.

    ``rowid`` is the opaque cursor id (BirdNET-Pi rowid, BirdNET-Go detection id).
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
        """Most recent detection at/above the threshold. None on empty/failure.

        Returns a small window's head so callers can skip blocklisted species
        without another query — a concrete impl may override for efficiency."""
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

    @abc.abstractmethod
    def species_ordinal(self, scientific_name: str) -> Optional[int]:
        """1-based rank among all-time species by first appearance — the 'No. 47'
        plate number. None if unknown/failure."""
