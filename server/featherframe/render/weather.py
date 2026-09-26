"""The day's own weather at the station, for the collage's branch (W-882).

Asked of the household's own text model with its web search (the owner's key,
the owner's use: no weather service of ours to license), for the collage's
date at the source's location rounded to two decimals (about a kilometre).
The answer is kept only when it names its source. Every failure is None: the
season's own look stands.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# What makes a day that kind of day, first match wins.
SNOWFALL_CM = 1.0
SNOW_DEPTH_CM = 5.0
RAIN_MM = 10.0

# A day's answer is asked again after this long, so the nightly sheet sees
# the snow that fell after a morning one was painted.
REASK_S = 6 * 3600


def prompt(lat: float, lon: float, day: date, today: Optional[date] = None) -> str:
    today = today or date.today()
    when = f"{day:%A} {day.day} {day:%B %Y}" + (" (today)" if day == today else "")
    return (f"What is the weather on {when} at latitude {lat:.2f}, longitude {lon:.2f}? "
            "Search for that day's recorded observations and conditions near there. "
            "Reply with JSON only: {\"snowfall_cm\": number or null, "
            "\"snow_on_ground_cm\": number or null, \"rain_mm\": number or null, "
            "\"source\": \"the URL the numbers come from\"}")


def _num(daily: dict, key: str) -> float:
    v = daily.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def kind_of(daily: dict) -> str:
    """'snowing', 'snow' (lying), 'rain', or '' for a quiet day."""
    if _num(daily, "snowfall_cm") >= SNOWFALL_CM:
        return "snowing"
    if _num(daily, "snow_on_ground_cm") >= SNOW_DEPTH_CM:
        return "snow"
    if _num(daily, "rain_mm") >= RAIN_MM:
        return "rain"
    return ""


def kind_from_answer(answer) -> Optional[str]:
    """The kind of day a search answered, or None when the answer names no
    source (a guess is not weather)."""
    if not isinstance(answer, dict):
        return None
    source = answer.get("source")
    if not (isinstance(source, str) and source.startswith("http")):
        return None
    return kind_of(answer)
