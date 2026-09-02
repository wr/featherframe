"""Read-only ingest from BirdNET-Pi's SQLite database.

Opens the DB read-only (``file:...?mode=ro``) and polls with a rowid high-water
mark. A missing DB or unexpected schema is a soft failure.

Schema (Nachtzuster/BirdNET-Pi fork):
    detections(Date TEXT 'YYYY-MM-DD', Time TEXT 'HH:MM:SS', Sci_Name, Com_Name,
               Confidence REAL, Lat, Lon, Cutoff, Week, Sens, Overlap, File_Name)
There is no primary key; rows are appended in detection order, so
``WHERE rowid > :cursor`` is the cursor.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date as ddate
from typing import Iterator, Optional

from .sources.base import Detection, DetectionSource

log = logging.getLogger("featherframe.birdnet")

# Columns we require to consider the schema usable.
_REQUIRED_COLUMNS = {"Date", "Time", "Sci_Name", "Com_Name", "Confidence"}


class SchemaError(RuntimeError):
    pass


class BirdNetDB(DetectionSource):
    """BirdNET-Pi's read-only SQLite database as a DetectionSource."""

    name = "birdnet_pi"

    def __init__(self, db_path: str, query_timeout_s: float = 5.0) -> None:
        self.db_path = os.path.expanduser(db_path)
        self._query_timeout_s = query_timeout_s

    # -- connection --------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(self.db_path)
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self._query_timeout_s)
        try:
            conn.row_factory = sqlite3.Row
            # Don't block if BirdNET is mid-write; we'll just retry next poll.
            conn.execute("PRAGMA busy_timeout = 2000")
            yield conn
        finally:
            conn.close()

    def available(self) -> bool:
        """True if the DB exists and the detections schema looks as expected."""
        try:
            with self._connect() as conn:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(detections)")}
            if not cols:
                return False
            missing = _REQUIRED_COLUMNS - cols
            if missing:
                log.warning("BirdNET detections table missing columns: %s", missing)
                return False
            return True
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.debug("BirdNET DB unavailable: %s", exc)
            return False

    # -- reads (all soft-fail to a safe default) ---------------------------
    def _row_to_detection(self, r: sqlite3.Row) -> Optional[Detection]:
        """One row, or None if it can't be read as a detection. SQLite is
        dynamically typed: text in the FLOAT Confidence column sorts above
        every number, passes `Confidence >= ?`, and then float() raises —
        which, uncaught, would kill every tick until the cursor moved past
        it (it never would). A bad row is skipped, never fatal."""
        try:
            return Detection(
                rowid=int(r["rowid"]),
                date=str(r["Date"] or ""),
                time=str(r["Time"] or ""),
                common_name=str(r["Com_Name"] or "").strip(),
                scientific_name=str(r["Sci_Name"] or "").strip(),
                confidence=float(r["Confidence"] or 0.0),
            )
        except (TypeError, ValueError, IndexError) as exc:
            log.debug("skipping malformed detection row: %s", exc)
            return None

    def _rows_to_detections(self, rows) -> list[Detection]:
        out = []
        for r in rows:
            det = self._row_to_detection(r)
            if det is not None:
                out.append(det)
        return out

    def max_rowid(self) -> int:
        """Current tail rowid, for initialising the cursor without replaying
        history. Returns 0 on any failure."""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(rowid) AS m FROM detections").fetchone()
            return int(row["m"]) if row and row["m"] is not None else 0
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.debug("max_rowid failed: %s", exc)
            return 0

    def new_since(self, cursor: int, min_confidence: float = 0.0,
                  limit: int = 500) -> list[Detection]:
        """Detections with rowid > cursor and confidence >= threshold, oldest
        first. Empty on failure."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT rowid, Date, Time, Com_Name, Sci_Name, Confidence "
                    "FROM detections WHERE rowid > ? AND Confidence >= ? "
                    "ORDER BY rowid ASC LIMIT ?",
                    (cursor, min_confidence, limit),
                ).fetchall()
            return self._rows_to_detections(rows)
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.warning("new_since failed (keeping current frame): %s", exc)
            return []

    def latest(self, min_confidence: float = 0.0, scan: int = 25) -> Optional[Detection]:
        """Most recent detection at/above the confidence threshold. None on
        failure or empty DB. Returns a small window so the caller can skip
        blocklisted species without another query."""
        recent = self.latest_many(min_confidence=min_confidence, limit=scan)
        return recent[0] if recent else None

    def latest_many(self, min_confidence: float = 0.0, limit: int = 25) -> list[Detection]:
        """Recent detections, newest first."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT rowid, Date, Time, Com_Name, Sci_Name, Confidence "
                    "FROM detections WHERE Confidence >= ? "
                    "ORDER BY Date DESC, Time DESC, rowid DESC LIMIT ?",
                    (min_confidence, limit),
                ).fetchall()
            return self._rows_to_detections(rows)
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.warning("latest_many failed: %s", exc)
            return []

    def top_species_today(self, on_date: Optional[ddate] = None,
                          min_confidence: float = 0.0, limit: int = 6) -> list[dict]:
        """Day's most-frequent species: [{common, scientific, count}], desc."""
        day = (on_date or ddate.today()).strftime("%Y-%m-%d")
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT Com_Name AS common, Sci_Name AS scientific, COUNT(*) AS count "
                    "FROM detections WHERE Date = ? AND Confidence >= ? "
                    "GROUP BY Sci_Name ORDER BY count DESC, MAX(Time) DESC LIMIT ?",
                    (day, min_confidence, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.warning("top_species_today failed: %s", exc)
            return []

    def all_time_species_count(self) -> int:
        """Distinct species ever seen. 0 on failure."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT Sci_Name) AS c FROM detections"
                ).fetchone()
            return int(row["c"]) if row else 0
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.debug("all_time_species_count failed: %s", exc)
            return 0

    def first_seen_date(self, scientific_name: str) -> Optional[str]:
        """Earliest date ('YYYY-MM-DD') this species was recorded. None if unknown."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT MIN(Date) AS d FROM detections WHERE Sci_Name = ?",
                    (scientific_name,),
                ).fetchone()
            return row["d"] if row and row["d"] else None
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.debug("first_seen_date failed: %s", exc)
            return None

    def species_ordinal(self, scientific_name: str) -> Optional[int]:
        """1-based rank of a species among all-time-unique species ordered by
        first appearance — the 'No. 47' plate number. None if unknown/failure."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    WITH firsts AS (
                        SELECT Sci_Name, MIN(Date || ' ' || Time) AS first_seen
                        FROM detections GROUP BY Sci_Name
                    )
                    SELECT COUNT(*) AS ordinal FROM firsts
                    WHERE first_seen <= (SELECT first_seen FROM firsts WHERE Sci_Name = ?)
                    """,
                    (scientific_name,),
                ).fetchone()
            n = int(row["ordinal"]) if row else 0
            return n or None
        except (sqlite3.Error, FileNotFoundError) as exc:
            log.debug("species_ordinal failed: %s", exc)
            return None


# Source-neutral alias (the DetectionSource factory prefers this name).
BirdNetPiSource = BirdNetDB
