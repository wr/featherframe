"""A name typed into the dashboard's show-a-critter form becomes the caption
(W-711): it is title-cased like a field-guide name, and a species the curated
index knows is spelled exactly as the index spells it."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe.names import SpeciesIndex, display_common_name


def test_display_common_name_reads_like_a_field_guide():
    assert display_common_name("black-capped chickadee") == "Black-capped Chickadee"
    assert display_common_name("NORTHERN CARDINAL") == "Northern Cardinal"
    assert display_common_name("nelson's sparrow") == "Nelson's Sparrow"
    assert display_common_name("  eastern   wood-pewee ") == "Eastern Wood-pewee"
    assert display_common_name("kraken") == "Kraken"
    assert display_common_name("") == ""


def test_index_spelling_wins_over_the_heuristic():
    index = SpeciesIndex([{"common": "Eastern Wood-Pewee", "scientific": "Contopus virens",
                           "plate": 115, "image": "x.jpg"}], images_dir="/nonexistent")
    assert index.canonical_common("eastern wood pewee") == "Eastern Wood-Pewee"
    assert index.canonical_common("EASTERN WOOD-PEWEE") == "Eastern Wood-Pewee"
    assert index.canonical_common("kraken") is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.service import FeatherframeService
    from featherframe.app import app
    service = FeatherframeService()
    service.source.db_path = str(tmp_path / "missing.db")
    service.audubon._index = SpeciesIndex(  # noqa: SLF001
        [{"common": "Black-capped Chickadee", "scientific": "Poecile atricapillus",
          "plate": 353, "image": "x.jpg"}], images_dir=str(tmp_path))
    calls = []
    monkeypatch.setattr(service, "start_test_detection",
                        lambda common, sci: calls.append((common, sci)))
    app.state.service = service
    return TestClient(app), calls


def test_typed_name_is_captioned_in_the_index_spelling(client):
    c, calls = client
    c.post("/api/test-detection", data={"common": "black capped chickadee"})
    assert calls[-1] == ("Black-capped Chickadee", "Poecile atricapillus")


def test_unknown_typed_name_is_title_cased(client):
    c, calls = client
    c.post("/api/test-detection", data={"common": "nelson's sparrow", "scientific": "ammospiza nelsoni"})
    assert calls[-1] == ("Nelson's Sparrow", "Ammospiza nelsoni")
