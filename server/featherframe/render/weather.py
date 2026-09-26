"""The day's own weather at the station, for the collage's bough (W-882).

Open-Meteo, free and keyless: its forecast API answers the last three months
(today included), its archive anything older. One ask per painted sheet, at a
location rounded to two decimals (about a kilometre). Every failure is None:
the season's own look stands.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

import requests

log = logging.getLogger("featherframe.weather")

_FORECAST = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_DAYS = 90  # the forecast API's past_days reach, with a margin
_DAILY = "snowfall_sum,snow_depth_max,rain_sum"

# What makes a day that kind of day, first match wins.
SNOWFALL_CM = 1.0
SNOW_DEPTH_M = 0.05
RAIN_MM = 10.0


def kind_of(daily: dict) -> str:
    """'snowing', 'snow' (lying), 'rain', or '' for a quiet day."""
    def val(k):
        v = daily.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
    if val("snowfall_sum") >= SNOWFALL_CM:
        return "snowing"
    if val("snow_depth_max") >= SNOW_DEPTH_M:
        return "snow"
    if val("rain_sum") >= RAIN_MM:
        return "rain"
    return ""


def day_weather(lat: float, lon: float, day: date, today: Optional[date] = None,
                timeout_s: float = 8.0) -> Optional[dict]:
    """The day's daily values at the station, or None. FEATHERFRAME_WEATHER=off
    never asks (the test suite)."""
    if os.environ.get("FEATHERFRAME_WEATHER", "").lower() == "off":
        return None
    today = today or date.today()
    url = _FORECAST if (today - day).days <= _FORECAST_DAYS else _ARCHIVE
    params = {"latitude": round(lat, 2), "longitude": round(lon, 2),
              "start_date": day.isoformat(), "end_date": day.isoformat(),
              "daily": _DAILY, "timezone": "auto"}
    try:
        resp = requests.get(url, params=params, timeout=timeout_s)
        if resp.status_code != 200:
            log.info("weather for %s: HTTP %s", day, resp.status_code)
            return None
        daily = resp.json().get("daily") or {}
        out = {k: (v[0] if isinstance(v, list) and v else None)
               for k, v in daily.items() if k != "time"}
        return out if any(v is not None for v in out.values()) else None
    except (requests.RequestException, ValueError, AttributeError) as exc:
        log.info("weather for %s failed: %s", day, exc)
        return None


def kind_for(lat: float, lon: float, day: date) -> Optional[str]:
    """The day's kind of weather at the station, or None when it is unknown."""
    daily = day_weather(lat, lon, day)
    return kind_of(daily) if daily else None
