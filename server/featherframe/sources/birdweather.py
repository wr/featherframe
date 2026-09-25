"""BirdWeather as a DetectionSource, over its REST v1 API.

A station is addressed by its ID/token, which goes straight in the path:
  * ``GET /api/v1/stations/{token}/detections`` — newest-first; each row has an
    integer ``id`` (the cursor), an ISO ``timestamp``, a nested ``species``
    object (commonName/scientificName), and a top-level ``confidence``.
  * ``GET /api/v1/stations/{token}/species?period=day&sort=top`` — the day's
    species with counts.
  * ``GET /api/v1/stations/{token}/stats?period=all`` — {detections, species}.

Every method soft-fails to a safe default.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

import requests

from .base import Detection, DetectionSource, valid_latitude

log = logging.getLogger("featherframe.birdweather")

_BASE = "https://app.birdweather.com/api/v1"
_SUMMARY_TTL_S = 60.0
_PAGE = 100          # BirdWeather caps limit at 100
_MAX_PAGES = 5


class BirdWeatherSource(DetectionSource):
    name = "birdweather"

    def __init__(self, station_id: str, timeout_s: float = 5.0,
                 base_url: str = _BASE) -> None:
        self.station_id = (station_id or "").strip()
        self.base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._stats: Optional[dict] = None
        self._stats_at: float = 0.0
        self._totals: Optional[dict[str, int]] = None
        self._totals_at: float = 0.0
        self._lat: Optional[float] = None
        self._lat_at: float = 0.0

    # -- HTTP --------------------------------------------------------------
    def _get(self, path: str, params: Optional[dict] = None) -> Optional[Any]:
        if not self.station_id:
            return None
        try:
            resp = requests.get(self.base_url + path, params=params, timeout=self._timeout_s)
            if resp.status_code != 200:
                log.debug("birdweather %s -> HTTP %s", path, resp.status_code)
                return None
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.debug("birdweather %s failed: %s", path, exc)
            return None

    def _detections_page(self, cursor: Optional[str] = None,
                         limit: int = _PAGE) -> tuple[list[dict], Optional[str]]:
        """Return (rows, next_cursor). Tolerates a bare list or an enveloped
        {detections|data, cursor} response."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        payload = self._get(f"/stations/{self.station_id}/detections", params)
        if isinstance(payload, list):
            return payload, None
        if isinstance(payload, dict):
            rows = payload.get("detections")
            if not isinstance(rows, list):
                rows = payload.get("data") if isinstance(payload.get("data"), list) else []
            nxt = payload.get("cursor")
            if not nxt and isinstance(payload.get("pagination"), dict):
                nxt = payload["pagination"].get("nextCursor") or payload["pagination"].get("cursor")
            return rows, (str(nxt) if nxt else None)
        return [], None

    @staticmethod
    def _to_detection(d: dict) -> Optional[Detection]:
        try:
            sp = d.get("species") or {}
            ts = str(d.get("timestamp") or "")
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
            conf = d.get("confidence")
            if conf is None:
                conf = d.get("probability")
            if conf is None:
                conf = d.get("score")
            return Detection(
                rowid=int(d["id"]),
                date=dt.date().isoformat(),
                time=dt.strftime("%H:%M:%S"),
                common_name=str(sp.get("commonName") or "").strip(),
                scientific_name=str(sp.get("scientificName") or "").strip(),
                confidence=float(conf or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    # -- reads -------------------------------------------------------------
    def available(self) -> bool:
        # Inspect the raw response, not the error-swallowed page: a wrong or
        # unreachable station 404s (-> _get None) and must read as unavailable,
        # while a valid station with no detections yet ({"detections": []})
        # must read as available.
        if not self.station_id:
            return False
        payload = self._get(f"/stations/{self.station_id}/detections", {"limit": 1})
        if isinstance(payload, list):
            return True
        if isinstance(payload, dict):
            return isinstance(payload.get("detections"), list) or isinstance(payload.get("data"), list)
        return False

    def max_rowid(self) -> int:
        rows, _ = self._detections_page(limit=1)
        if not rows:
            return 0
        det = self._to_detection(rows[0])
        return det.rowid if det else 0

    def new_since(self, cursor: int, min_confidence: float = 0.0,
                  limit: int = 500) -> list[Detection]:
        """Walk newest-first pages, collecting id > cursor, until we cross the
        cursor or hit the page cap; return oldest-first, confidence-filtered."""
        collected: list[Detection] = []
        next_cursor: Optional[str] = None
        for _ in range(_MAX_PAGES):
            rows, next_cursor = self._detections_page(cursor=next_cursor, limit=_PAGE)
            if not rows:
                break
            crossed = False
            for row in rows:
                det = self._to_detection(row)
                if det is None:
                    continue
                if det.rowid <= cursor:
                    crossed = True
                    break
                if det.confidence >= min_confidence:
                    collected.append(det)
            if crossed or not next_cursor or len(rows) < _PAGE:
                break
        collected.sort(key=lambda d: d.rowid)
        return collected[:limit]

    def latest_many(self, min_confidence: float = 0.0, limit: int = 25) -> list[Detection]:
        rows, _ = self._detections_page(limit=min(_PAGE, max(limit * 4, 25)))
        out: list[Detection] = []
        for row in rows:
            det = self._to_detection(row)
            if det and det.confidence >= min_confidence:
                out.append(det)
            if len(out) >= limit:
                break
        return out

    # -- summaries ---------------------------------------------------------
    def _stats_all(self) -> dict:
        now = time.time()
        if self._stats is not None and (now - self._stats_at) < _SUMMARY_TTL_S:
            return self._stats
        payload = self._get(f"/stations/{self.station_id}/stats", {"period": "all"})
        self._stats = payload if isinstance(payload, dict) else {}
        self._stats_at = now
        return self._stats

    def all_time_species_count(self) -> int:
        try:
            return int(self._stats_all().get("species") or 0)
        except (TypeError, ValueError):
            return 0

    def latitude(self) -> Optional[float]:
        """The station's own coordinates (`GET /stations/{token}`), asked once;
        a failed ask is tried again after an hour."""
        if self._lat is not None or time.time() - self._lat_at < 3600:
            return self._lat
        self._lat_at = time.time()
        payload = self._get(f"/stations/{self.station_id}")
        coords = payload.get("coords") if isinstance(payload, dict) else None
        self._lat = valid_latitude(coords.get("lat")) if isinstance(coords, dict) else None
        return self._lat

    def first_seen_date(self, scientific_name: str) -> Optional[str]:
        return None  # not cheaply available from the station API

    def heard_before(self, scientific_name: str, on_date) -> Optional[bool]:
        """The station has no first dates, but it has every species' all-time
        total: one with more detections than today's was heard before today."""
        if on_date != datetime.now().date():
            return None
        key = (scientific_name or "").strip().lower()
        ever = self._all_time_totals().get(key)
        if ever is None:
            return None     # not in the all-time list (yet): can't say
        today = {r["scientific"].strip().lower(): r["count"]
                 for r in self.top_species_today(on_date, limit=_PAGE)}.get(key, 0)
        return ever > today

    def _all_time_totals(self) -> dict[str, int]:
        """{scientific name, lowercased: all-time detections}, a page of 100 at
        a time, cached like the stats."""
        now = time.time()
        if self._totals is not None and (now - self._totals_at) < _SUMMARY_TTL_S:
            return self._totals
        totals: dict[str, int] = {}
        for page in range(1, 11):
            payload = self._get(f"/stations/{self.station_id}/species",
                                {"period": "all", "limit": _PAGE, "page": page})
            rows = payload.get("species") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                if page == 1:
                    return self._totals or {}   # a blip: keep what we had
                break
            for s in rows:
                if isinstance(s, dict) and s.get("scientificName"):
                    totals[str(s["scientificName"]).strip().lower()] = _count(s)
            if len(rows) < _PAGE:
                break
        self._totals, self._totals_at = totals, now
        return totals

    def top_species_today(self, on_date=None, min_confidence: float = 0.0,
                          limit: int = 6) -> list[dict]:
        # period=day is TODAY. The station API has no cheap per-date tally, so
        # a request for another day is "can't answer" ([]) — never today's
        # birds under yesterday's date on the sheet.
        if on_date is not None and on_date != datetime.now().date():
            return []
        payload = self._get(f"/stations/{self.station_id}/species",
                            {"period": "day", "sort": "top", "order": "desc", "limit": limit})
        rows = payload if isinstance(payload, list) else (
            payload.get("species") if isinstance(payload, dict) else None)
        if not isinstance(rows, list):
            return []
        out = []
        for s in rows:
            if not isinstance(s, dict):
                continue
            count = _count(s)
            if count <= 0:
                continue
            out.append({"common": str(s.get("commonName") or "").strip(),
                        "scientific": str(s.get("scientificName") or "").strip(),
                        "count": count})
        out.sort(key=lambda r: -r["count"])
        return out[:limit]


def _count(s: dict) -> int:
    """A species row's detections. `detections` is a breakdown by certainty
    ({"total": 88, "almostCertain": 88, ...}); a plain number is taken as is."""
    raw = s.get("detections") or s.get("count") or s.get("total") or 0
    if isinstance(raw, dict):
        raw = raw.get("total") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0
