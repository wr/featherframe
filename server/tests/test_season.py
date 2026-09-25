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
    row[5] = -34.5
    db = create_birds_db(tmp_path / "birds.db", rows + [tuple(row)])
    assert BirdNetDB(str(db)).latitude() == -34.5
    assert BirdNetDB(str(tmp_path / "missing.db")).latitude() is None


def test_birdweather_reads_the_station_coords(monkeypatch):
    s = BirdWeatherSource("tok")
    asked = []

    def get(path, params=None):
        asked.append(path)
        return {"id": 1, "coords": {"lat": -37.8, "lon": 145.0}}
    monkeypatch.setattr(s, "_get", get)
    assert s.latitude() == -37.8
    assert s.latitude() == -37.8
    assert asked == ["/stations/tok"]                    # asked once


def test_birdweather_without_coords_is_unknown(monkeypatch):
    s = BirdWeatherSource("tok")
    monkeypatch.setattr(s, "_get", lambda *a, **k: None)
    assert s.latitude() is None


def test_birdnet_go_push_carries_its_latitude(tmp_path):
    db = Database(str(tmp_path / "ff.db"))
    s = PushedSource("birdnet_go", db=db)
    assert s.latitude() is None
    body = _go()
    body["metadata"]["bg_latitude"] = -41.3
    s.ingest(body)
    assert s.latitude() == -41.3
    assert PushedSource("birdnet_go", db=db).latitude() == -41.3   # kept
    junk = _go(note=99)
    junk["metadata"]["bg_latitude"] = "north"
    s.ingest(junk)
    assert s.latitude() == -41.3


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
        def __init__(self, lat=None, boom=False):
            self.lat, self.boom = lat, boom

        def latitude(self):
            if self.boom:
                raise RuntimeError("down")
            return self.lat
    au = Config(region="australia")
    assert Service._southern(SimpleNamespace(source=Src(-27.5), config=Config()))
    assert not Service._southern(SimpleNamespace(source=Src(41.7), config=au))
    assert Service._southern(SimpleNamespace(source=Src(None), config=au))
    assert Service._southern(SimpleNamespace(source=Src(boom=True), config=au))
    assert not Service._southern(SimpleNamespace(source=Src(None), config=Config()))
