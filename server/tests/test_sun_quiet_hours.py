"""Sunset -> sunrise quiet hours at the station's real sunset and sunrise, on
the household's own clock, daylight saving included (W-900)."""
from __future__ import annotations

import time
from datetime import date, datetime, time as dtime

import pytest

from featherframe.config import Config, _sun_window
from featherframe.service import FeatherframeService

GLASTONBURY = (41.7, -72.6)       # Connecticut, US Eastern
ADELAIDE = (-34.93, 138.6)        # South Australia, UTC+9:30 / +10:30
SOLSTICE_JUNE, SOLSTICE_DEC = date(2026, 6, 21), date(2026, 12, 21)


@pytest.fixture
def local_tz(monkeypatch):
    """Run the test on another household's clock (the server's TZ)."""
    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield use
    monkeypatch.undo()
    time.tzset()


def near(t: dtime, hhmm: str, minutes: int = 5) -> bool:
    """Within a few minutes of the almanac's time."""
    hh, mm = map(int, hhmm.split(":"))
    return abs((t.hour * 60 + t.minute) - (hh * 60 + mm)) <= minutes


def night_hours(window: tuple[dtime, dtime]) -> float:
    sunset, sunrise = window
    return ((sunrise.hour * 60 + sunrise.minute) - (sunset.hour * 60 + sunset.minute)) % 1440 / 60


def test_a_northern_summer_night_is_on_daylight_saving_time(local_tz):
    local_tz("America/New_York")
    # The almanac: 8:28 PM -> 5:15 AM EDT; the old fixed model said 7:25 PM.
    sunset, sunrise = _sun_window(SOLSTICE_JUNE, GLASTONBURY)
    assert near(sunset, "20:28") and near(sunrise, "05:15")
    # Winter is on standard time: 4:22 PM -> 7:14 AM EST.
    sunset, sunrise = _sun_window(SOLSTICE_DEC, GLASTONBURY)
    assert near(sunset, "16:22") and near(sunrise, "07:14")


def test_a_southern_summer_night_is_short(local_tz):
    local_tz("Australia/Adelaide")
    # December is summer there (daylight saving, +10:30): 8:28 PM -> 5:58 AM.
    summer = _sun_window(SOLSTICE_DEC, ADELAIDE)
    assert near(summer[0], "20:28") and near(summer[1], "05:58")
    # June is winter (+9:30): 5:11 PM -> 7:23 AM.
    winter = _sun_window(SOLSTICE_JUNE, ADELAIDE)
    assert near(winter[0], "17:11") and near(winter[1], "07:23")
    assert night_hours(summer) < 10 < 13 < night_hours(winter)


def test_without_a_location_the_sun_follows_the_time_zone_and_region(local_tz):
    local_tz("America/New_York")
    north = Config(quiet_hours_mode="sun")
    # Zero-config: 40° N on the zone's meridian, still on daylight saving.
    sunset, sunrise = north.quiet_window(SOLSTICE_JUNE)
    assert (sunset, sunrise) == _sun_window(SOLSTICE_JUNE)
    assert near(sunset, "20:32", 10) and near(sunrise, "05:31", 10)
    assert near(north.quiet_window(SOLSTICE_DEC)[0], "16:37", 10)

    local_tz("Australia/Sydney")
    south = Config(quiet_hours_mode="sun", region="australia")
    # The Region puts it south: December nights are the short ones.
    assert night_hours(south.quiet_window(SOLSTICE_DEC)) < 10
    assert night_hours(south.quiet_window(SOLSTICE_JUNE)) > 13
    # A reported location outranks the Region.
    assert south.quiet_window(SOLSTICE_DEC, GLASTONBURY) != south.quiet_window(SOLSTICE_DEC)


def test_where_the_sun_never_sets_the_night_is_fixed(local_tz):
    local_tz("Europe/Oslo")
    tromso = (69.65, 18.96)
    assert _sun_window(SOLSTICE_JUNE, tromso) == (dtime(22, 0), dtime(6, 0))
    assert _sun_window(SOLSTICE_DEC, tromso) == (dtime(22, 0), dtime(6, 0))


class _Station:
    def __init__(self, loc):
        self.loc, self.asked = loc, 0

    def location(self):
        self.asked += 1
        return self.loc


@pytest.fixture
def svc(tmp_path, monkeypatch, local_tz):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    local_tz("Australia/Adelaide")
    service = FeatherframeService()
    service.config = Config(quiet_hours_mode="sun")      # region: north-america
    service.source = _Station(ADELAIDE)
    return service


def test_the_service_asks_at_the_stations_location(svc):
    assert svc.quiet_window(SOLSTICE_DEC) == _sun_window(SOLSTICE_DEC, ADELAIDE)
    assert svc.in_quiet_hours(datetime(2026, 12, 21, 21, 0))
    assert not svc.in_quiet_hours(datetime(2026, 12, 21, 19, 0))   # still light
    assert svc.in_quiet_hours(datetime(2026, 6, 21, 18, 0))         # winter dark


def test_a_request_never_asks_the_source_only_a_tick_does(svc):
    for _ in range(3):
        svc.in_quiet_hours(datetime(2026, 12, 21, 21, 0))
    assert svc.source.asked == 1          # the first ask, before any tick
    svc._read_station_location()          # what every tick starts with
    assert svc.source.asked == 2
    svc.source.loc = None                 # the station stopped saying: zero-config
    svc._read_station_location()
    assert svc.quiet_window(SOLSTICE_DEC) == svc.config.quiet_window(SOLSTICE_DEC)
