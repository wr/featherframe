"""A detector pushing each detection to us: BirdNET-Pi through Apprise, or
BirdNET-Go through a webhook channel (W-865).

A push is *push*, but ``DetectionSource`` is *poll* (cursor-based). This source
bridges the two: the app's ``/api/ingest/<kind>`` webhook calls ``ingest()``
for each pushed body, which appends the detection to a bounded, DB-persisted
queue with a monotonic id; the scheduler drains that queue through the normal
poll methods. The two detectors differ only in the body they post (``_parse_*``).

Persisting the queue (and its counter) matters: the service persists its cursor
across restarts, so the counter must resume above it or freshly-pushed
detections would look "old" and never show.

A push feed knows only what it has received, not the detector's history, so it
keeps what the rest of the app asks of history as it goes: a tally per day and
species (the collage's counts, however many detections the day had) and, when
the body says it (BirdNET-Go's ``days_since_first_seen``), each species' first
date. BirdNET-Pi's body says neither, so its first-seen answers stay unknown.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date as ddate, datetime, timedelta
from typing import Optional

from .base import Detection, DetectionSource, valid_location

log = logging.getLogger("featherframe.pushed")

KINDS = ("apprise", "birdnet_go")
_MAX_ITEMS = 1000   # retained window; oldest trimmed past this
_TALLY_DAYS = 7     # days of per-species counts kept
_NAME_MAX = 200     # a species name; anything longer is not one
# What BirdNET-Go's *Test* button on a notification channel posts. It is how an
# owner checks the channel; it is never a bird.
_GO_TEST_SPECIES = "testus birdicus"

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


def _confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    return c if 0.0 <= c <= 1.0 else 0.0


def _local(stamp) -> Optional[datetime]:
    """An RFC 3339 time as local wall-clock time, or None."""
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        t = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


# -- the two bodies --------------------------------------------------------
def _parse_apprise(payload) -> Optional[dict]:
    """BirdNET-Pi's Apprise notification. Apprise posts {version, title,
    message, type} with our JSON body in `message`; BirdNET-Pi may append text
    after it, so extract the {...} span rather than parse whole. Falls back to
    a top-level object if someone posts the fields directly."""
    if not isinstance(payload, dict):
        return None
    body = payload
    msg = payload.get("message")
    if isinstance(msg, str) and "{" in msg and "}" in msg:
        try:
            obj = json.loads(msg[msg.index("{"): msg.rindex("}") + 1])
            if isinstance(obj, dict):
                body = obj
        except ValueError:
            pass
    now = datetime.now()
    return {"common": _name(body.get("comname"), body.get("common"), body.get("commonName")),
            "scientific": _name(body.get("sciname"), body.get("scientific"),
                                body.get("scientificName")),
            "confidence": _confidence(body.get("confidence")),
            "date": _norm_date(str(body.get("date") or "").strip(), now),
            "time": _norm_time(str(body.get("time") or "").strip(), now)}


def _parse_birdnet_go(payload) -> Optional[dict]:
    """BirdNET-Go's webhook channel, as it posts by default: {id, type, title,
    message, timestamp, metadata}, the detection in `metadata`. Anything that
    is not a detection (a stream warning on the same channel) is None."""
    if not isinstance(payload, dict) or payload.get("type") != "detection":
        return None
    md = payload.get("metadata")
    if not isinstance(md, dict):
        return None
    now = _local(payload.get("timestamp")) or datetime.now()
    item = {"common": _name(md.get("species")),
            "scientific": _name(md.get("scientific_name")),
            "confidence": _confidence(md.get("confidence")),
            "date": _norm_date(str(md.get("bg_detection_date") or "").strip(), now),
            "time": _norm_time(str(md.get("bg_detection_time") or "").strip(), now)}
    note = md.get("note_id")
    if isinstance(note, (int, str)) and not isinstance(note, bool) and str(note).strip():
        item["note"] = str(note).strip()[:40]
    loc = valid_location(md.get("bg_latitude"), md.get("bg_longitude"))
    if loc is not None:
        item["loc"] = list(loc)
    days = md.get("days_since_first_seen")
    if isinstance(days, int) and not isinstance(days, bool) and 0 <= days < 100000:
        item["first_days"] = days
    return item


def _tally_row(r) -> bool:
    t = r.get("tenths") if isinstance(r, dict) else None
    return (isinstance(t, list) and len(t) == 10
            and all(isinstance(n, int) and not isinstance(n, bool) for n in t))


_PARSERS = {"apprise": _parse_apprise, "birdnet_go": _parse_birdnet_go}


class PushedSource(DetectionSource):

    def __init__(self, kind: str = "apprise", db=None) -> None:
        self.kind = kind if kind in KINDS else "apprise"
        self.name = self.kind
        self._parse = _PARSERS[self.kind]
        # Each kind keeps its own queue ("apprise_queue" predates W-865).
        self._store_key = f"{self.kind}_queue"
        # BirdNET-Pi and BirdNET-Go notify only what they stored, which is only
        # what passed their own threshold; BirdNET-Go's always wins (W-821).
        self._defer_confidence = self.kind == "birdnet_go"
        self._db = db
        self._lock = threading.Lock()
        self._counter = 0
        self._items: list[dict] = []
        self._tally: dict[str, dict[str, dict]] = {}
        self._first: dict[str, str] = {}
        self.test_at: Optional[str] = None
        self._loc: Optional[tuple[float, float]] = None
        if db is not None:
            self._load(db.get(self._store_key, {}) or {})

    def _load(self, saved) -> None:
        if not isinstance(saved, dict):
            return
        try:
            self._counter = int(saved.get("counter") or 0)
        except (TypeError, ValueError):
            return
        # Keep only items every reader can turn into a Detection: one malformed
        # persisted item would otherwise raise out of new_since on every tick.
        for i in (saved.get("items") or []):
            if isinstance(i, dict):
                try:
                    self._to_detection(i)
                except (KeyError, TypeError, ValueError):
                    continue
                self._items.append(i)
        tally = saved.get("tally")
        if isinstance(tally, dict):
            self._tally = {d: {k: r for k, r in rows.items() if _tally_row(r)}
                           for d, rows in tally.items() if isinstance(rows, dict)}
        else:   # a queue from before the tally: count what it holds
            for i in self._items:
                self._count(i)
        first = saved.get("first")
        if isinstance(first, dict):
            self._first = {k: v for k, v in first.items() if isinstance(v, str)}
        if isinstance(saved.get("test_at"), str):
            self.test_at = saved["test_at"]
        loc = saved.get("loc")
        if isinstance(loc, list) and len(loc) == 2:
            self._loc = valid_location(*loc)

    # -- ingest (called by the webhook, off the scheduler thread) ----------
    def ingest(self, payload) -> Optional[Detection]:
        """Normalize one pushed body, queue its detection, and return it — or
        None if it carries no usable species (guards 'never a wrong bird')."""
        item = self._parse(payload)
        if not item or not (item["common"] or item["scientific"]):
            return None
        if item["scientific"].lower() == _GO_TEST_SPECIES:
            with self._lock:
                self.test_at = datetime.now().isoformat(timespec="seconds")
                self._persist()
            return None
        with self._lock:
            note = item.get("note")
            if note:
                # BirdNET-Go pushes one detection once per rule that matches
                # it (every detection, and again as a new species).
                for old in reversed(self._items):
                    if old.get("note") == note:
                        return self._to_detection(old)
            self._counter += 1
            item["id"] = self._counter
            loc = item.pop("loc", None)
            if loc is not None:
                self._loc = tuple(loc)
            days = item.pop("first_days", None)
            if days is not None:
                self._learn_first(item, days)
            self._items.append(item)
            if len(self._items) > _MAX_ITEMS:
                self._items = self._items[-_MAX_ITEMS:]
            self._count(item)
            self._persist()
        return self._to_detection(item)

    def _learn_first(self, item: dict, days: int) -> None:
        key = item["scientific"].lower()
        if not key:
            return
        try:
            first = (ddate.fromisoformat(item["date"]) - timedelta(days=days)).isoformat()
        except ValueError:
            return
        if key not in self._first or first < self._first[key]:
            self._first[key] = first

    def _count(self, item: dict) -> None:
        """Tally a detection under its day and species, in confidence tenths so
        a floor can still be applied to the counts."""
        key = str(item.get("scientific") or item.get("common")).lower()
        day = self._tally.setdefault(str(item["date"]), {})
        row = day.setdefault(key, {"common": item.get("common") or "",
                                   "scientific": item.get("scientific") or "",
                                   "tenths": [0] * 10})
        row["tenths"][min(9, int(float(item["confidence"]) * 10))] += 1
        for old in sorted(self._tally)[:-_TALLY_DAYS]:
            del self._tally[old]

    def _persist(self) -> None:
        if self._db is None:
            return
        try:
            self._db.set(self._store_key, {"counter": self._counter, "items": self._items,
                                           "tally": self._tally, "first": self._first,
                                           "test_at": self.test_at,
                                           "loc": list(self._loc) if self._loc else None})
        except Exception:  # persistence is best-effort; never break ingest
            log.debug("%s queue persist failed", self.kind, exc_info=True)

    @staticmethod
    def _to_detection(i: dict) -> Detection:
        return Detection(rowid=int(i["id"]), date=str(i["date"]), time=str(i["time"]),
                         common_name=str(i["common"]), scientific_name=str(i["scientific"]),
                         confidence=float(i["confidence"]))

    def location(self) -> Optional[tuple[float, float]]:
        """The last location a push carried (BirdNET-Go's `bg_latitude` and
        `bg_longitude`)."""
        return self._loc

    def _floor(self, min_confidence: float) -> float:
        return 0.0 if self._defer_confidence else min_confidence

    # -- reads (scheduler thread) ------------------------------------------
    def available(self) -> bool:
        return True  # the webhook is always up; an empty queue just yields nothing

    def max_rowid(self) -> int:
        with self._lock:
            return self._counter

    def new_since(self, cursor: int, min_confidence: float = 0.0,
                  limit: int = 500) -> list[Detection]:
        floor = self._floor(min_confidence)
        with self._lock:
            items = list(self._items)
        out = [self._to_detection(i) for i in items
               if int(i["id"]) > cursor and float(i["confidence"]) >= floor]
        out.sort(key=lambda d: d.rowid)   # oldest first
        return out[:limit]

    def latest_many(self, min_confidence: float = 0.0, limit: int = 25) -> list[Detection]:
        floor = self._floor(min_confidence)
        with self._lock:
            items = list(self._items)
        out = [self._to_detection(i) for i in reversed(items)
               if float(i["confidence"]) >= floor]
        return out[:limit]

    def all_time_species_count(self) -> int:
        with self._lock:
            seen = {str(i["scientific"]).lower() for i in self._items if i.get("scientific")}
            return len(seen | set(self._first))

    def first_seen_date(self, scientific_name: str) -> Optional[str]:
        with self._lock:
            return self._first.get((scientific_name or "").strip().lower())

    def top_species_today(self, on_date=None, min_confidence: float = 0.0,
                          limit: int = 6) -> list[dict]:
        day = (on_date or datetime.now().date()).isoformat()
        first_tenth = min(9, max(0, int(round(self._floor(min_confidence) * 10, 6))))
        with self._lock:
            rows = [{"common": str(r.get("common") or "").strip(),
                     "scientific": str(r.get("scientific") or "").strip(),
                     "count": sum(r["tenths"][first_tenth:])}
                    for r in (self._tally.get(day) or {}).values()]
        rows = [r for r in rows if r["count"] > 0]
        rows.sort(key=lambda r: -r["count"])
        return rows[:limit]
