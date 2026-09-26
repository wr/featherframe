"""Shared test fixtures. Puts server/ on the path so `featherframe` imports."""
from __future__ import annotations

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1]
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

import pytest  # noqa: E402

from tests._fixtures import create_birds_db  # noqa: E402


@pytest.fixture
def birds_db(tmp_path) -> str:
    """A fixture birds.db with the default day's detections."""
    return str(create_birds_db(tmp_path / "birds.db"))


@pytest.fixture
def missing_db(tmp_path) -> str:
    return str(tmp_path / "does_not_exist.db")


@pytest.fixture(autouse=True)
def _panel_dither_restored():
    """Tests that want a cheap render set pipeline.DITHER_OVERRIDE = "none";
    the panel's own dither comes back after each one."""
    from featherframe.render import pipeline
    yield
    pipeline.DITHER_OVERRIDE = None


@pytest.fixture(autouse=True)
def _no_mdns(monkeypatch):
    """No test advertises _featherframe._tcp on the real LAN (W-827): a wall
    frame whose server is briefly down adopts whatever answers. Tests of the
    advertiser itself set FEATHERFRAME_MDNS=1 over a fake zeroconf."""
    monkeypatch.setenv("FEATHERFRAME_MDNS", "0")
    monkeypatch.delenv("FEATHERFRAME_NO_MDNS", raising=False)


@pytest.fixture(autouse=True)
def _no_weather(monkeypatch):
    """No test asks Open-Meteo for a day's weather (W-882); the weather tests
    lift it over a fake."""
    monkeypatch.setenv("FEATHERFRAME_WEATHER", "off")


@pytest.fixture(autouse=True)
def _no_release_check(monkeypatch):
    """No test asks GitHub for a firmware release (W-838). Tests of the check
    point FEATHERFRAME_RELEASES_URL at a fake."""
    monkeypatch.setenv("FEATHERFRAME_RELEASES_URL", "off")
