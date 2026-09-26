"""The generated collage's bough carries the season of its date (W-881): the
season and its stage from the date and hemisphere, the hemisphere from the
source's latitude or the Region, and the prompt and sidecar that carry it."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from featherframe import paths
from featherframe.birdnet import BirdNetDB
from featherframe.db import Database
from featherframe.render import genart
from featherframe.render.genart import GeneratedArtProvider, build_composite_prompt
from featherframe.render.season import (TREE_STATE, is_southern, season_of,
                                        season_phrase, tree_state)
from featherframe.sources.birdweather import BirdWeatherSource
from featherframe.sources.pushed import PushedSource
from tests._fixtures import create_birds_db, make_row
from tests.test_day_composite import CELLS
from tests.test_genart import FakeModel
from tests.test_pushed_source import _go


# -- the season of a date ------------------------------------------------------
@pytest.mark.parametrize("day, want", [
    (date(2025, 12, 1), ("early", "winter")),
    (date(2026, 1, 15), ("mid", "winter")),
    (date(2026, 2, 28), ("late", "winter")),
    (date(2026, 3, 1), ("early", "spring")),
    (date(2026, 5, 31), ("late", "spring")),
    (date(2026, 6, 1), ("early", "summer")),
    (date(2026, 8, 31), ("late", "summer")),
    (date(2026, 9, 1), ("early", "autumn")),
    (date(2026, 11, 30), ("late", "autumn")),
])
def test_season_boundaries_in_the_north(day, want):
    assert season_of(day) == want


@pytest.mark.parametrize("day, want", [
    (date(2026, 1, 15), ("mid", "summer")),
    (date(2026, 3, 1), ("early", "autumn")),
    (date(2026, 6, 1), ("early", "winter")),
    (date(2026, 7, 15), ("mid", "winter")),
    (date(2026, 9, 1), ("early", "spring")),
    (date(2026, 11, 30), ("late", "spring")),
    (date(2026, 12, 1), ("early", "summer")),
])
def test_southern_hemisphere_is_six_months_on(day, want):
    assert season_of(day, southern=True) == want


def test_wells_days():
    assert season_phrase(date(2026, 2, 16)) == "late winter"
    assert season_phrase(date(2026, 4, 7)) == "mid spring"
    assert season_phrase(date(2026, 6, 1)) == "early summer"
    assert season_phrase(date(2026, 9, 23)) == "early autumn"


def test_every_stage_has_a_tree_state():
    for m in range(1, 13):
        for south in (False, True):
            day = date(2026, m, 10)
            assert tree_state(day, south).startswith(season_phrase(day, south) + ", ")
    assert len(TREE_STATE) == 12


def test_hemisphere_from_latitude_then_region():
    assert is_southern(-33.9) and not is_southern(41.7)
    assert not is_southern(0.0, "australia")            # a reported latitude decides
    assert is_southern(None, "australia")
    assert not is_southern(None, "north-america")
    assert not is_southern(None, "europe") and not is_southern(None, "")


# -- where a latitude comes from ------------------------------------------------
def test_birdnet_pi_reads_lat_from_its_newest_detection(tmp_path):
    rows = [make_row(datetime(2026, 5, 1, 6), "Blue Jay", "Cyanocitta cristata")]
    row = list(make_row(datetime(2026, 5, 1, 7), "Blue Jay", "Cyanocitta cristata"))
    row[5], row[6] = -34.5, 138.6
    db = create_birds_db(tmp_path / "birds.db", rows + [tuple(row)])
    assert BirdNetDB(str(db)).location() == (-34.5, 138.6)
    assert BirdNetDB(str(db)).latitude() == -34.5
    assert BirdNetDB(str(tmp_path / "missing.db")).location() is None


def test_birdweather_reads_the_station_coords(monkeypatch):
    s = BirdWeatherSource("tok")
    asked = []

    def get(path, params=None):
        asked.append(path)
        return {"id": 1, "coords": {"lat": -37.8, "lon": 145.0}}
    monkeypatch.setattr(s, "_get", get)
    assert s.location() == (-37.8, 145.0)
    assert s.latitude() == -37.8
    assert asked == ["/stations/tok"]                    # asked once


def test_birdweather_without_coords_is_unknown(monkeypatch):
    s = BirdWeatherSource("tok")
    monkeypatch.setattr(s, "_get", lambda *a, **k: None)
    assert s.location() is None


def test_birdnet_go_push_carries_its_latitude(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    s = PushedSource("birdnet_go", db=db)
    assert s.location() is None
    body = _go()
    body["metadata"].update(bg_latitude=-41.3, bg_longitude=174.8)
    s.ingest(body)
    assert s.location() == (-41.3, 174.8)
    assert PushedSource("birdnet_go", db=db).location() == (-41.3, 174.8)   # kept
    junk = _go(note=99)
    junk["metadata"].update(bg_latitude="north", bg_longitude=174.8)
    s.ingest(junk)
    assert s.location() == (-41.3, 174.8)


# -- the prompt and the sheet ---------------------------------------------------
SUBJECTS = [(c.common_name, c.scientific_name) for c in CELLS]


@pytest.mark.parametrize("n", [3, 12])
def test_prompt_sets_the_bough_in_its_season(n):
    subjects = (SUBJECTS * 4)[:n]
    state = tree_state(date(2026, 2, 16))
    p = build_composite_prompt(subjects, season=state)
    assert state in p
    assert "botanically bare" not in p and "bare bough" not in p
    assert "every numeral sits on open paper" in p


@pytest.mark.parametrize("n", [3, 12])
def test_prompt_without_a_season_keeps_the_bare_bough(n):
    p = build_composite_prompt((SUBJECTS * 4)[:n])
    assert "bare" in p and "temperate woodland" not in p


def test_sheet_records_its_season_and_prompt_version(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider._describe = lambda common, sci: ("", None)
    provider.day_composite(CELLS, date(2026, 7, 10), southern=True)
    meta = json.loads((paths.collages_dir() / "2026-07-10.json").read_text())
    assert meta["season"] == "mid winter"
    assert meta["prompt_version"] == genart.COLLAGE_PROMPT_VERSION > genart.PROMPT_VERSION
    assert "mid winter, deep in dormancy" in model.prompts[-1]


def test_service_asks_the_source_then_the_region():
    from types import SimpleNamespace
    from featherframe.config import Config
    from featherframe.service import FeatherframeService as Service

    class Src:
        def __init__(self, loc=None, boom=False):
            self.loc, self.boom = loc, boom

        def location(self):
            if self.boom:
                raise RuntimeError("down")
            return self.loc
    au = Config(region="australia")

    def station(src, config):
        return Service._station(SimpleNamespace(source=src, config=config))
    assert station(Src((-27.5, 153.0)), Config()) == (True, (-27.5, 153.0))
    assert station(Src((41.7, -72.6)), au) == (False, (41.7, -72.6))
    assert station(Src(None), au) == (True, None)
    assert station(Src(boom=True), au) == (True, None)
    assert station(Src(None), Config()) == (False, None)


# -- the day's weather (W-882) ---------------------------------------------------
from featherframe.render import weather as weather_mod  # noqa: E402


@pytest.mark.parametrize("daily, kind", [
    ({"snowfall_sum": 24.9, "snow_depth_max": 0.43, "rain_sum": 0.1}, "snowing"),
    ({"snowfall_sum": 0.0, "snow_depth_max": 0.25, "rain_sum": 0.0}, "snow"),
    ({"snowfall_sum": 0.5, "snow_depth_max": 0.0, "rain_sum": 22.7}, "rain"),
    ({"snowfall_sum": 0.0, "snow_depth_max": 0.04, "rain_sum": 9.9}, ""),
    ({"snowfall_sum": None, "snow_depth_max": None, "rain_sum": None}, ""),
    ({"rain_sum": True}, ""),
])
def test_kind_of_a_day(daily, kind):
    assert weather_mod.kind_of(daily) == kind


def test_weather_decides_the_snow():
    feb = date(2026, 2, 16)
    assert "snow" in tree_state(feb)                      # unknown: the season's own
    assert "snow" not in tree_state(feb, weather="")      # a dry, bare day: bare wood
    assert "snow of the days before" in tree_state(feb, weather="snow")
    assert "snow falling" in tree_state(date(2026, 1, 25), weather="snowing")
    jul = tree_state(date(2026, 7, 29), weather="rain")
    assert jul.startswith("mid summer, in deep, full summer leaf, ") and "rain" in jul
    assert tree_state(date(2026, 9, 24), weather="") == tree_state(date(2026, 9, 24))


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_day_weather_asks_forecast_then_archive(monkeypatch):
    monkeypatch.delenv("FEATHERFRAME_WEATHER")
    asked = []

    def get(url, params=None, timeout=None):
        asked.append((url, params))
        return _Resp(body={"daily": {"time": [params["start_date"]], "snowfall_sum": [2.0],
                                     "snow_depth_max": [0.1], "rain_sum": [0.0]}})
    monkeypatch.setattr(weather_mod.requests, "get", get)
    today = date(2026, 9, 25)
    assert weather_mod.day_weather(41.69642, -72.6072, date(2026, 9, 1), today) == {
        "snowfall_sum": 2.0, "snow_depth_max": 0.1, "rain_sum": 0.0}
    weather_mod.day_weather(41.69642, -72.6072, date(2026, 1, 25), today)
    assert "api.open-meteo" in asked[0][0] and "archive-api" in asked[1][0]
    assert asked[0][1]["latitude"] == 41.7 and asked[0][1]["longitude"] == -72.61
    assert weather_mod.kind_for(41.7, -72.6, date(2026, 1, 25)) == "snowing"


def test_day_weather_soft_fails(monkeypatch):
    monkeypatch.delenv("FEATHERFRAME_WEATHER")
    monkeypatch.setattr(weather_mod.requests, "get", lambda *a, **k: _Resp(503))
    assert weather_mod.day_weather(41.7, -72.6, date(2026, 1, 25)) is None
    assert weather_mod.kind_for(41.7, -72.6, date(2026, 1, 25)) is None

    def boom(*a, **k):
        raise weather_mod.requests.ConnectionError("down")
    monkeypatch.setattr(weather_mod.requests, "get", boom)
    assert weather_mod.day_weather(41.7, -72.6, date(2026, 1, 25)) is None
    monkeypatch.setattr(weather_mod.requests, "get",
                        lambda *a, **k: _Resp(body={"daily": {"time": ["x"], "rain_sum": [None]}}))
    assert weather_mod.day_weather(41.7, -72.6, date(2026, 1, 25)) is None


def test_weather_off_never_asks(monkeypatch):
    monkeypatch.setattr(weather_mod.requests, "get", lambda *a, **k: 1 / 0)
    assert weather_mod.day_weather(41.7, -72.6, date(2026, 1, 25)) is None


def test_sheet_records_the_days_weather(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider._describe = lambda common, sci: ("", None)
    asked = []
    provider.day_composite(CELLS, date(2026, 1, 25),
                           weather=lambda: asked.append(1) or "snowing")
    meta = json.loads((paths.collages_dir() / "2026-01-25.json").read_text())
    assert meta["weather"] == "snowing" and "snow falling" in model.prompts[-1]
    provider.day_composite(CELLS, date(2026, 1, 25), weather=lambda: asked.append(1) or "")
    assert asked == [1]                                   # a cached sheet asks nothing


def test_a_failing_weather_ask_keeps_the_season(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider._describe = lambda common, sci: ("", None)

    def boom():
        raise RuntimeError("down")
    assert provider.day_composite(CELLS, date(2026, 2, 16), weather=boom) is not None
    meta = json.loads((paths.collages_dir() / "2026-02-16.json").read_text())
    assert meta["weather"] is None and "late snow" in model.prompts[-1]
