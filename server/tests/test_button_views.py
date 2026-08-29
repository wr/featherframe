"""On-demand button views (W-574): collage and status page.

These are transient renders — they must come back as valid FFF bytes and must
NOT replace the resident frame.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from featherframe.service import FeatherframeService
from tests._fixtures import create_birds_db, make_row


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    service = FeatherframeService()
    yield service


def _db_with_today(path, species_count):
    base = datetime.now().replace(hour=8, minute=0, second=0)
    species = [("Northern Cardinal", "Cardinalis cardinalis"),
               ("Blue Jay", "Cyanocitta cristata"),
               ("American Goldfinch", "Spinus tristis"),
               ("Tufted Titmouse", "Baeolophus bicolor")][:species_count]
    rows = [make_row(base + timedelta(minutes=i), c, s, 0.9)
            for i, (c, s) in enumerate(species)]
    return create_birds_db(path, rows)


def test_collage_on_demand_from_todays_birds(svc, tmp_path):
    svc.source.db_path = str(_db_with_today(tmp_path / "birds.db", 4))
    result = svc.render_collage_on_demand()
    assert result is not None
    assert result.frame[:4] == b"FFF1"
    assert result.mode == "collage"
    # Transient: the resident frame is untouched.
    assert svc._etag != result.etag


def test_collage_on_demand_none_when_too_few(svc, tmp_path):
    svc.source.db_path = str(_db_with_today(tmp_path / "birds.db", 1))
    assert svc.render_collage_on_demand() is None


def test_status_page_renders_fff(svc, tmp_path):
    svc.source.db_path = str(_db_with_today(tmp_path / "birds.db", 2))
    result = svc.render_status_page(battery_voltage=3.9, battery_percent=62,
                                    wifi_rssi=-64)
    assert result.frame[:4] == b"FFF1"
    assert result.mode == "status"
    assert svc._etag != result.etag


def test_status_page_with_no_birdnet(svc, tmp_path):
    svc.source.db_path = str(tmp_path / "missing.db")
    result = svc.render_status_page()
    assert result.frame[:4] == b"FFF1"
