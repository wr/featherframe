"""The nightly day-in-review composite plate: per-day cache semantics, grid
fallback, date-scoped species data, and the render layout. No network."""
from __future__ import annotations

import json
from datetime import date

import pytest
from PIL import Image

from featherframe.config import Config
from featherframe.render import collage as collage_mod
from featherframe.render import theme
from featherframe.render.collage import CollageCell
from featherframe.render.genart import GeneratedArtProvider, build_composite_prompt
from featherframe.sources.birdnet_go import BirdNetGoSource
from tests.test_genart import FakeModel, _plate_png  # shared fakes


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


CELLS = [
    CollageCell("Great Horned Owl", "Bubo virginianus", 979),
    CollageCell("Carolina Wren", "Thryothorus ludovicianus", 44),
    CollageCell("House Sparrow", "Passer domesticus", 12),
]
DAY = date(2026, 8, 28)


# -- per-day cache ----------------------------------------------------------
def test_composite_generates_once_per_day(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)

    img = provider.day_composite(CELLS, DAY)
    assert img is not None and img.mode == "L"
    assert model.calls == 1
    png = data_dir / "collages" / "2026-08-28.png"
    sidecar = data_dir / "collages" / "2026-08-28.json"
    assert png.exists() and sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert [c["scientific"] for c in meta["cells"]][0] == "Bubo virginianus"

    # Same day again: cache, no second purchase — even with different cells.
    assert provider.day_composite(CELLS[:2], DAY) is not None
    assert model.calls == 1


def test_composite_force_regenerates(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.day_composite(CELLS, DAY)
    assert provider.day_composite(CELLS, DAY, force=True) is not None
    assert model.calls == 2


def test_composite_cache_only_without_model(data_dir):
    assert GeneratedArtProvider(None).day_composite(CELLS, DAY) is None
    GeneratedArtProvider(FakeModel()).day_composite(CELLS, DAY)
    assert GeneratedArtProvider(None).day_composite(CELLS, DAY) is not None


def test_composite_failure_cools_down(data_dir):
    model = FakeModel(fail=True)
    provider = GeneratedArtProvider(model)
    assert provider.day_composite(CELLS, DAY) is None
    assert model.calls == 1
    assert provider.day_composite(CELLS, DAY) is None
    assert model.calls == 1  # cooldown: the nightly tick must not re-bill


# -- composite prompt -------------------------------------------------------
def test_composite_prompt_names_all_species_in_order():
    p = build_composite_prompt([(c.common_name, c.scientific_name) for c in CELLS])
    assert p.index("Great Horned Owl") < p.index("Carolina Wren") < p.index("House Sparrow")
    assert "no title" in p.lower() or "no text" in p.lower()


# -- render layout ----------------------------------------------------------
def test_render_generated_collage_layout(data_dir):
    art = Image.open(__import__("io").BytesIO(_plate_png())).convert("L")
    field = collage_mod.render_generated_collage(
        art, CELLS, when=DAY, total_detections=1035, title="The Day in Review")
    assert field.size == (theme.WIDTH, theme.HEIGHT)
    assert field.mode == "L"


# -- date-scoped species data from BirdNET-Go -------------------------------
def test_birdnet_go_top_species_today(monkeypatch):
    src = BirdNetGoSource("http://x", defer_confidence=False)
    payload = [
        {"scientific_name": "Bubo virginianus", "common_name": "Great Horned Owl",
         "count": 979, "max_confidence": 1.0},
        {"scientific_name": "Corvus corax", "common_name": "Common Raven",
         "count": 3, "max_confidence": 0.4},   # never crossed the bar
        {"scientific_name": "Passer domesticus", "common_name": "House Sparrow",
         "count": 12, "max_confidence": 0.9},
    ]
    seen = {}

    def fake_get(path, params=None):
        seen["path"], seen["params"] = path, params
        return payload

    monkeypatch.setattr(src, "_get", fake_get)
    rows = src.top_species_today(date(2026, 8, 28), min_confidence=0.7, limit=6)
    assert seen["params"]["start_date"] == "2026-08-28"
    assert seen["params"]["end_date"] == "2026-08-28"
    assert [r["scientific"] for r in rows] == ["Bubo virginianus", "Passer domesticus"]
    assert rows[0] == {"common": "Great Horned Owl",
                       "scientific": "Bubo virginianus", "count": 979}


def test_birdnet_go_top_species_today_soft_fails(monkeypatch):
    src = BirdNetGoSource("http://x", defer_confidence=False)
    monkeypatch.setattr(src, "_get", lambda path, params=None: None)
    assert src.top_species_today(date(2026, 8, 28)) == []


# -- config -----------------------------------------------------------------
def test_config_collage_generated_roundtrip():
    c = Config()
    assert c.collage_generated is True
    again = Config.from_dict(Config(collage_generated=False).to_dict())
    assert again.collage_generated is False
