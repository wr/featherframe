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
    assert "bare" in p and "one living tree" not in p


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
from featherframe.config import Config  # noqa: E402
from featherframe.render import weather as weather_mod  # noqa: E402
from featherframe.render.genart import OpenAITextModel  # noqa: E402

SRC = "https://www.wunderground.com/history/daily/us/ct/glastonbury/KCTGLAST75"


@pytest.mark.parametrize("answer, kind", [
    ({"snowfall_cm": 45.7, "snow_on_ground_cm": 15.2, "rain_mm": 0, "source": SRC}, "heavy_snow"),
    ({"snowfall_cm": 6.0, "snow_on_ground_cm": 15.2, "rain_mm": 0, "source": SRC}, "snowing"),
    ({"snowfall_cm": 0, "snow_on_ground_cm": 25, "rain_mm": 0, "source": SRC}, "snow"),
    ({"snowfall_cm": 0.5, "snow_on_ground_cm": 0, "rain_mm": 95.3, "source": SRC}, "rain"),
    ({"snowfall_cm": 0, "snow_on_ground_cm": 4, "rain_mm": 9.9, "source": SRC}, ""),
    ({"snowfall_cm": None, "snow_on_ground_cm": None, "rain_mm": True, "source": SRC}, ""),
    ({"snowfall_cm": 45.7}, None),                                  # names no source
    ({"snowfall_cm": 45.7, "source": "my guess"}, None),
    ("snowy", None),
])
def test_kind_from_an_answer(answer, kind):
    assert weather_mod.kind_from_answer(answer) == kind


def test_the_prompt_names_the_day_and_the_place():
    p = weather_mod.prompt(41.69642, -72.6072, date(2026, 9, 25), today=date(2026, 9, 25))
    assert "Friday 25 September 2026 (today)" in p and "41.70" in p and "-72.61" in p
    assert "(today)" not in weather_mod.prompt(41.7, -72.6, date(2026, 1, 25),
                                               today=date(2026, 9, 25))


def test_weather_decides_the_snow():
    feb = date(2026, 2, 16)
    assert "snow" in tree_state(feb)                      # unknown: the season's own
    assert "snow" not in tree_state(feb, weather="")      # a dry, bare day: bare wood
    assert "snow of the days before" in tree_state(feb, weather="snow")
    assert "snow falling" in tree_state(date(2026, 1, 25), weather="snowing")
    assert "whole tree white" in tree_state(date(2026, 1, 25), weather="heavy_snow")
    jul = tree_state(date(2026, 7, 29), weather="rain")
    assert jul.startswith("mid summer, in deep, full summer leaf, ") and "rain" in jul
    assert tree_state(date(2026, 9, 24), weather="") == tree_state(date(2026, 9, 24))


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._body


def test_openai_search_reads_the_responses_api(monkeypatch):
    sent = {}

    def post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, body=json)
        return _Resp({"output": [
            {"type": "web_search_call"}, {"type": "web_search_call"},
            {"type": "message", "content": [{"type": "output_text",
                                             "text": '{"rain_mm": 22.7, "source": "%s"}' % SRC}]}],
            "usage": {"input_tokens": 38968, "output_tokens": 1141}})
    monkeypatch.setattr(genart.requests, "post", post)
    m = OpenAITextModel("k", model="gpt-5.6-luna")
    assert m.search_json("?") == {"rain_mm": 22.7, "source": SRC}
    assert sent["url"].endswith("/responses")
    assert sent["body"]["tools"] == [{"type": "web_search"}]
    assert sent["body"]["max_tool_calls"] == 2
    assert m.last_usage == {"input_tokens": 38968, "output_tokens": 1141, "web_searches": 2}
    cost = genart.estimate_text_cost_usd("gpt-5.6-luna", m.last_usage)
    assert cost == pytest.approx(38968 * 0.2e-6 + 1141 * 0.75e-6 + 2 * genart.WEB_SEARCH_USD)


class FakeSearch:
    name = "gpt-5.6-luna"
    last_usage = None

    def __init__(self, answer=None, boom=False):
        self.answer, self.boom, self.asked = answer, boom, []

    def complete_json(self, prompt):
        return {"description": "", "plants": []}

    def search_json(self, prompt):
        self.asked.append(prompt)
        self.last_usage = {"input_tokens": 1000, "output_tokens": 100, "web_searches": 1}
        if self.boom:
            raise RuntimeError("down")
        return self.answer


def _provider(tmp_path, monkeypatch, text=None):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    model = FakeModel()
    provider = GeneratedArtProvider(model, text_model=text)
    provider._describe = lambda common, sci: ("", None)
    return model, provider


def _meta(day):
    return json.loads((paths.collages_dir() / f"{day}.json").read_text())


HERE = (41.69642, -72.6072)


def test_daily_weather_asks_the_text_model_once(tmp_path, monkeypatch):
    text = FakeSearch({"snowfall_cm": 8, "snow_on_ground_cm": 43, "source": SRC})
    model, provider = _provider(tmp_path, monkeypatch, text)
    provider.day_composite(CELLS, date(2026, 1, 25), branch="weather", location=HERE)
    meta = _meta("2026-01-25")
    assert meta["branch"] == "weather" and meta["weather"] == "snowing"
    assert "snow falling" in model.prompts[-1]
    assert "25 January 2026" in text.asked[0] and "41.70" in text.asked[0]
    provider.day_composite(CELLS, date(2026, 1, 25), branch="weather", location=HERE, force=True)
    assert len(text.asked) == 1                            # kept for the day
    spend = [json.loads(l) for l in paths.spend_ledger_path().read_text().splitlines()]
    assert [e["kind"] for e in spend].count("weather") == 1
    assert genart.spend_for_month(datetime.now())["images"] == 1   # weather is no image


def test_daily_weather_asks_again_after_hours(tmp_path, monkeypatch):
    text = FakeSearch({"rain_mm": 0, "source": SRC})
    model, provider = _provider(tmp_path, monkeypatch, text)
    now = [1_000_000.0]
    monkeypatch.setattr(genart.time, "time", lambda: now[0])
    provider._day_weather(HERE, date(2026, 9, 25))
    now[0] += weather_mod.REASK_S + 1
    provider._day_weather(HERE, date(2026, 9, 25))
    assert len(text.asked) == 2


@pytest.mark.parametrize("text", [
    FakeSearch(boom=True),
    FakeSearch({"snowfall_cm": 30}),                        # no source
    None,                                                   # no text model
])
def test_unknown_weather_keeps_the_season(tmp_path, monkeypatch, text):
    model, provider = _provider(tmp_path, monkeypatch, text)
    assert provider.day_composite(CELLS, date(2026, 2, 16), branch="weather",
                                  location=HERE) is not None
    assert _meta("2026-02-16")["weather"] is None
    assert "late snow" in model.prompts[-1]


def test_daily_weather_without_a_location_keeps_the_season(tmp_path, monkeypatch):
    text = FakeSearch({"rain_mm": 50, "source": SRC})
    model, provider = _provider(tmp_path, monkeypatch, text)
    provider.day_composite(CELLS, date(2026, 7, 29), branch="weather", location=None)
    assert text.asked == [] and _meta("2026-07-29")["weather"] is None


def test_a_bare_branch_is_the_old_bough(tmp_path, monkeypatch):
    text = FakeSearch({"rain_mm": 50, "source": SRC})
    model, provider = _provider(tmp_path, monkeypatch, text)
    provider.day_composite(CELLS, date(2026, 7, 29), branch="bare", location=HERE)
    assert text.asked == [] and "one living tree" not in model.prompts[-1]
    assert _meta("2026-07-29")["season"] is None


def test_changing_the_branch_repaints_the_day(tmp_path, monkeypatch):
    model, provider = _provider(tmp_path, monkeypatch)
    provider.day_composite(CELLS, date(2026, 7, 29), branch="season")
    provider.day_composite(CELLS, date(2026, 7, 29), branch="season")
    assert model.calls == 1
    sidecar = paths.collages_dir() / "2026-07-29.json"
    meta = json.loads(sidecar.read_text())
    meta["created_ts"] -= 3600                              # past the repaint debounce
    sidecar.write_text(json.dumps(meta))
    provider.day_composite(CELLS, date(2026, 7, 29), branch="bare")
    assert model.calls == 2


def test_a_sheet_from_before_the_choice_is_kept(tmp_path, monkeypatch):
    model, provider = _provider(tmp_path, monkeypatch)
    provider.day_composite(CELLS, date(2026, 7, 29))
    sidecar = paths.collages_dir() / "2026-07-29.json"
    meta = json.loads(sidecar.read_text())
    del meta["branch"]
    sidecar.write_text(json.dumps(meta))
    provider.day_composite(CELLS, date(2026, 7, 29), branch="weather", location=HERE)
    assert model.calls == 1


def test_the_branch_setting():
    assert Config().collage_branch == "season"
    assert Config(collage_branch="weather").collage_branch == "weather"
    assert Config(collage_branch="snowglobe").collage_branch == "season"


@pytest.mark.parametrize("n", [3, 12])
@pytest.mark.parametrize("season", [None, "late winter, still dormant, its buds just swelling",
                                    tree_state(date(2026, 7, 29), weather="rain")])
def test_every_collage_is_drawn_by_hand(n, season):
    """The sparse, hand-placed branch on clean bark is every collage's, whatever
    it carries and however crowded (W-882)."""
    p = build_composite_prompt((SUBJECTS * 4)[:n], season=season)
    assert genart._P_COMPOSITE_HAND in p


def test_no_moss_nest_among_the_references():
    refs = genart._PREFERRED_COMPOSITE_REFS
    assert "Poecile atricapillus" not in refs and "Dryobates villosus" not in refs
    assert refs[:2] == ["Piranga ludoviciana", "Setophaga virens"]
