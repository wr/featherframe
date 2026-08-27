"""BirdNET-Go as a DetectionSource, over its REST API.

We talk to BirdNET-Go's ``/api/v2`` HTTP API, never its database — the SQLite
schema is normalised and actively churning, while the API is stable and, on
localhost, auth-free for reads.

Two endpoints cover everything:
  * ``GET /api/v2/detections`` — newest-first, paginated. Each row carries an
    integer ``id`` (our cursor), local ``date``/``time``, names, and confidence.
  * ``GET /api/v2/analytics/species/summary`` — per-species ``count`` and
    ``first_heard``, which give the all-time count, first-seen date, and the
    'No. 47' ordinal (rank by first appearance).

Every method soft-fails to a safe default, exactly like the BirdNET-Pi source:
a flaky network keeps the current frame on the wall.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

import requests

from .base import Detection, DetectionSource

log = logging.getLogger("featherframe.birdnet_go")

_SUMMARY_TTL_S = 60.0   # the summary is all-species; don't refetch it every render
_PAGE = 100             # detections page size when walking the cursor
_MAX_PAGES = 5          # bound the work per poll (500 rows) — a 20s poll never nears this


class BirdNetGoSource(DetectionSource):
    name = "birdnet_go"

    def __init__(self, base_url: str, timeout_s: float = 5.0,
                 defer_confidence: bool = True) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self._timeout_s = timeout_s
        # Defer the confidence bar to BirdNET-Go itself: read its own
        # /birdnet/threshold and filter by that, so there's one place to tune it
        # (BirdNET-Go) rather than a second, redundant bar here.
        self.defer_confidence = defer_confidence
        self._summary: Optional[list[dict]] = None
        self._summary_at: float = 0.0
        self._threshold: Optional[float] = None
        self._threshold_at: float = 0.0

    def _birdnet_threshold(self) -> Optional[float]:
        """BirdNET-Go's own configured confidence threshold (settings.birdnet.threshold),
        cached. None if unavailable."""
        now = time.time()
        if self._threshold is not None and (now - self._threshold_at) < _SUMMARY_TTL_S:
            return self._threshold
        payload = self._get("/api/v2/settings")
        try:
            self._threshold = float(payload["birdnet"]["threshold"])
        except (TypeError, KeyError, ValueError):
            self._threshold = None
        self._threshold_at = now
        return self._threshold

    def _eff_min(self, min_confidence: float) -> float:
        """The effective confidence floor: BirdNET-Go's own threshold when deferring
        (falling back to the caller's value if it can't be read), else the caller's."""
        if self.defer_confidence:
            t = self._birdnet_threshold()
            if t is not None:
                return t
        return min_confidence

    # -- HTTP --------------------------------------------------------------
    def _get(self, path: str, params: Optional[dict] = None) -> Optional[Any]:
        """GET and parse JSON, or None on any failure (never raises)."""
        try:
            resp = requests.get(self.base_url + path, params=params, timeout=self._timeout_s)
            if resp.status_code != 200:
                log.debug("birdnet-go %s -> HTTP %s", path, resp.status_code)
                return None
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.debug("birdnet-go %s failed: %s", path, exc)
            return None

    def _detections_page(self, offset: int = 0, num: int = _PAGE) -> list[dict]:
        payload = self._get("/api/v2/detections",
                            {"numResults": num, "offset": offset})
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        return []

    @staticmethod
    def _to_detection(d: dict) -> Optional[Detection]:
        try:
            return Detection(
                rowid=int(d["id"]),
                date=str(d.get("date") or ""),
                time=str(d.get("time") or ""),
                common_name=str(d.get("commonName") or "").strip(),
                scientific_name=str(d.get("scientificName") or "").strip(),
                confidence=float(d.get("confidence") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    # -- reads -------------------------------------------------------------
    def available(self) -> bool:
        payload = self._get("/api/v2/detections", {"numResults": 1})
        return isinstance(payload, dict) and isinstance(payload.get("data"), list)

    def max_rowid(self) -> int:
        page = self._detections_page(num=1)
        if not page:
            return 0
        det = self._to_detection(page[0])
        return det.rowid if det else 0

    def new_since(self, cursor: int, min_confidence: float = 0.0,
                  limit: int = 500) -> list[Detection]:
        """Walk newest-first pages, collecting id > cursor, until we cross the
        cursor or hit the page cap; return oldest-first, confidence-filtered."""
        min_confidence = self._eff_min(min_confidence)
        collected: list[Detection] = []
        for page_idx in range(_MAX_PAGES):
            page = self._detections_page(offset=page_idx * _PAGE, num=_PAGE)
            if not page:
                break
            crossed = False
            for row in page:
                det = self._to_detection(row)
                if det is None:
                    continue
                if det.rowid <= cursor:
                    crossed = True
                    break
                if det.confidence >= min_confidence:
                    collected.append(det)
            if crossed or len(page) < _PAGE:
                break
        collected.sort(key=lambda d: d.rowid)   # oldest first, like the SQL cursor
        return collected[:limit]

    def latest_many(self, min_confidence: float = 0.0, limit: int = 25) -> list[Detection]:
        min_confidence = self._eff_min(min_confidence)
        # Over-fetch so confidence filtering still leaves ~limit rows.
        page = self._detections_page(num=min(200, max(limit * 4, 25)))
        out: list[Detection] = []
        for row in page:
            det = self._to_detection(row)
            if det and det.confidence >= min_confidence:
                out.append(det)
            if len(out) >= limit:
                break
        return out

    # -- species summary (cached) -----------------------------------------
    def _species_summary(self) -> list[dict]:
        now = time.time()
        if self._summary is not None and (now - self._summary_at) < _SUMMARY_TTL_S:
            return self._summary
        payload = self._get("/api/v2/analytics/species/summary")
        self._summary = payload if isinstance(payload, list) else []
        self._summary_at = now
        return self._summary

    def all_time_species_count(self) -> int:
        return len(self._species_summary())

    @staticmethod
    def _first_heard_local_date(iso_utc: str) -> Optional[str]:
        """'2026-01-16T15:54:33Z' -> local 'YYYY-MM-DD'."""
        try:
            dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
            return dt.astimezone().date().isoformat()
        except (ValueError, AttributeError):
            return None

    def first_seen_date(self, scientific_name: str) -> Optional[str]:
        for s in self._species_summary():
            if s.get("scientific_name") == scientific_name:
                return self._first_heard_local_date(str(s.get("first_heard") or ""))
        return None

    def species_ordinal(self, scientific_name: str) -> Optional[int]:
        summary = self._species_summary()
        ordered = sorted(
            (s for s in summary if s.get("first_heard")),
            key=lambda s: s["first_heard"],
        )
        for i, s in enumerate(ordered, start=1):
            if s.get("scientific_name") == scientific_name:
                return i
        return None

    def top_species_today(self, on_date=None, min_confidence: float = 0.0,
                          limit: int = 6) -> list[dict]:
        # The summary endpoint is all-time, and grouping a busy day's detections
        # client-side is too heavy for a poll. Collage mode degrades to single on
        # BirdNET-Go until a date-scoped endpoint is wired up.
        return []
