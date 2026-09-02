"""BirdNET-Pi (or anything) pushing detections to us via Apprise.

Apprise is *push*, but ``DetectionSource`` is *poll* (cursor-based). This source
bridges the two: the app's ``/api/ingest/apprise`` webhook calls ``ingest()`` for
each pushed detection, which appends it to a bounded, DB-persisted queue with a
monotonic id; the scheduler drains that queue through the normal poll methods.

Persisting the queue (and its counter) matters: the service persists its cursor
across restarts, so the counter must resume above it or freshly-pushed
detections would look "old" and never show.

Because a push feed only knows what it has received (not BirdNET-Pi's full
history), all-time/first-seen answers are best-effort over the retained window,
and the plate ordinal is left unknown (None) rather than shown wrong.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

from .base import Detection, DetectionSource

log = logging.getLogger("featherframe.apprise")

_STORE_KEY = "apprise_queue"
_MAX_ITEMS = 1000   # retained window; oldest trimmed past this
_NAME_MAX = 200     # a species name; anything longer is not one

# BirdNET-Pi's $date/$time formats aren't guaranteed; normalize to the canonical
# strings the rest of the app (and top_species_today's day match) expect.
_DATE_FMTS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d")
_TIME_FMTS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")


def _norm_date(value: str, now: datetime) -> str:
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return now.strftime("%Y-%m-%d")


def _norm_time(value: str, now: datetime) -> str:
    for fmt in _TIME_FMTS:
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return now.strftime("%H:%M:%S")


def _name(*candidates) -> str:
    """The first candidate that is a non-blank string, trimmed and bounded.
    A list or object under a name key is not a bird (str() of it would be
    rendered as one) — it is skipped, never coerced."""
    for v in candidates:
        if isinstance(v, str) and v.strip():
            return v.strip()[:_NAME_MAX]
    return ""


class AppriseSource(DetectionSource):
    name = "apprise"

    def __init__(self, db=None) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._counter = 0
        self._items: list[dict] = []
        if db is not None:
            saved = db.get(_STORE_KEY, {}) or {}
            try:
                self._counter = int(saved.get("counter") or 0)
                # Keep only items every reader can turn into a Detection: one
                # malformed persisted item would otherwise raise out of
                # new_since on every tick, forever.
                for i in (saved.get("items") or []):
                    if isinstance(i, dict):
                        try:
                            self._to_detection(i)
                        except (KeyError, TypeError, ValueError):
                            continue
                        self._items.append(i)
            except (TypeError, ValueError):
                self._counter, self._items = 0, []

    # -- ingest (called by the webhook, off the scheduler thread) ----------
    def ingest(self, payload: dict) -> Optional[Detection]:
        """Normalize one pushed detection, queue it, and return it — or None if
        the payload lacks a usable species (guards 'never a wrong bird')."""
        common = _name(payload.get("comname"), payload.get("common"),
                       payload.get("commonName"))
        sci = _name(payload.get("sciname"), payload.get("scientific"),
                    payload.get("scientificName"))
        if not common and not sci:
            return None
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        now = datetime.now()
        date = _norm_date(str(payload.get("date") or "").strip(), now)
        tm = _norm_time(str(payload.get("time") or "").strip(), now)
        with self._lock:
            self._counter += 1
            item = {"id": self._counter, "date": date, "time": tm,
                    "common": common, "scientific": sci, "confidence": confidence}
            self._items.append(item)
            if len(self._items) > _MAX_ITEMS:
                self._items = self._items[-_MAX_ITEMS:]
            self._persist()
        return self._to_detection(item)

    def _persist(self) -> None:
        if self._db is None:
            return
        try:
            self._db.set(_STORE_KEY, {"counter": self._counter, "items": self._items})
        except Exception:  # persistence is best-effort; never break ingest
            log.debug("apprise queue persist failed", exc_info=True)

    @staticmethod
    def _to_detection(i: dict) -> Detection:
        return Detection(rowid=int(i["id"]), date=str(i["date"]), time=str(i["time"]),
                         common_name=str(i["common"]), scientific_name=str(i["scientific"]),
                         confidence=float(i["confidence"]))

    # -- reads (scheduler thread) ------------------------------------------
    def available(self) -> bool:
        return True  # the webhook is always up; an empty queue just yields nothing

    def max_rowid(self) -> int:
        with self._lock:
            return self._counter

    def new_since(self, cursor: int, min_confidence: float = 0.0,
                  limit: int = 500) -> list[Detection]:
        with self._lock:
            items = list(self._items)
        out = [self._to_detection(i) for i in items
               if int(i["id"]) > cursor and float(i["confidence"]) >= min_confidence]
        out.sort(key=lambda d: d.rowid)   # oldest first
        return out[:limit]

    def latest_many(self, min_confidence: float = 0.0, limit: int = 25) -> list[Detection]:
        with self._lock:
            items = list(self._items)
        out = [self._to_detection(i) for i in reversed(items)
               if float(i["confidence"]) >= min_confidence]
        return out[:limit]

    def all_time_species_count(self) -> int:
        with self._lock:
            return len({str(i["scientific"]).lower() for i in self._items if i.get("scientific")})

    def first_seen_date(self, scientific_name: str) -> Optional[str]:
        return None  # a push window isn't authoritative for first-seen

    def species_ordinal(self, scientific_name: str) -> Optional[int]:
        return None  # no authoritative ordering -> no plate number

    def top_species_today(self, on_date=None, min_confidence: float = 0.0,
                          limit: int = 6) -> list[dict]:
        day = (on_date or datetime.now().date()).isoformat()
        with self._lock:
            items = list(self._items)
        counts: dict[str, dict] = {}
        for i in items:
            if str(i.get("date")) != day or float(i["confidence"]) < min_confidence:
                continue
            key = str(i.get("scientific") or i.get("common")).lower()
            row = counts.setdefault(key, {"common": str(i.get("common") or "").strip(),
                                          "scientific": str(i.get("scientific") or "").strip(),
                                          "count": 0})
            row["count"] += 1
        rows = sorted(counts.values(), key=lambda r: -r["count"])
        return rows[:limit]
