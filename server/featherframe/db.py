"""Featherframe's own small SQLite database.

Holds only what BirdNET's DB can't: our config, ingest cursor, render state,
device check-in status, and the current-frame ETag. BirdNET's DB stays the
source of truth for detections and species counts.

A simple typed key/value store keeps the schema trivial to evolve; there is
very little state and no query load worth normalising for.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from . import paths


class Database:
    def __init__(self, path: str | None = None) -> None:
        self._path = str(path) if path else str(paths.db_path())
        # check_same_thread=False + a lock: the scheduler thread and the
        # request handlers both touch this. Writes are tiny and infrequent.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS render_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    rendered_at TEXT NOT NULL,
                    mode        TEXT NOT NULL,
                    species     TEXT,
                    etag        TEXT
                );
                CREATE TABLE IF NOT EXISTS battery_log (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    at      TEXT NOT NULL,
                    voltage REAL NOT NULL,
                    percent INTEGER
                );
                """
            )
            self._conn.commit()

    # -- generic kv --------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    def set(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload),
            )
            self._conn.commit()

    # -- render log --------------------------------------------------------
    def log_render(self, rendered_at: str, mode: str, species: str | None, etag: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO render_log(rendered_at, mode, species, etag) VALUES(?,?,?,?)",
                (rendered_at, mode, species, etag),
            )
            # keep the log small
            self._conn.execute(
                "DELETE FROM render_log WHERE id NOT IN "
                "(SELECT id FROM render_log ORDER BY id DESC LIMIT 200)"
            )
            self._conn.commit()

    def render_history(self, limit: int = 60) -> list[dict[str, Any]]:
        """Newest-first render log rows, for the config page's History strip."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT rendered_at, mode, species, etag FROM render_log "
                "ORDER BY id DESC LIMIT ?", (max(0, int(limit)),)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- battery log -------------------------------------------------------
    # One row per BATTERY_LOG_STEP_S at most (the awake build checks in every
    # 15 s), kept BATTERY_LOG_KEEP_DAYS: enough to draw a day and to read the
    # trend that says whether the frame is on USB.
    BATTERY_LOG_STEP_S = 300
    BATTERY_LOG_KEEP_DAYS = 7

    def log_battery(self, at: str, voltage: float, percent: int | None) -> bool:
        """Append a reading unless the last one is younger than the step.
        Returns True when a row was written."""
        with self._lock:
            row = self._conn.execute(
                "SELECT at FROM battery_log ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                try:
                    from datetime import datetime as _dt
                    if (_dt.fromisoformat(at) - _dt.fromisoformat(row["at"])).total_seconds() \
                            < self.BATTERY_LOG_STEP_S:
                        return False
                except ValueError:
                    pass
            self._conn.execute(
                "INSERT INTO battery_log(at, voltage, percent) VALUES(?,?,?)",
                (at, float(voltage), None if percent is None else int(percent)))
            self._conn.execute(
                "DELETE FROM battery_log WHERE at < datetime(?, ?)",
                (at, f"-{self.BATTERY_LOG_KEEP_DAYS} days"))
            self._conn.commit()
            return True

    def battery_history(self, since: str) -> list[dict[str, Any]]:
        """Readings at or after `since` (ISO), oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT at, voltage, percent FROM battery_log WHERE at >= ? ORDER BY id ASC",
                (since,)).fetchall()
        return [dict(r) for r in rows]

    def last_render(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT rendered_at, mode, species, etag FROM render_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
