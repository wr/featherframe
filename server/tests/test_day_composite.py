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

    art, painted = provider.day_composite(CELLS, DAY)
    assert art is not None and art.mode == "L"
    assert model.calls == 1
    png = data_dir / "collages" / "2026-08-28.png"
    sidecar = data_dir / "collages" / "2026-08-28.json"
    assert png.exists() and sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert [c["scientific"] for c in meta["cells"]][0] == "Bubo virginianus"

    # Same day again with a DIFFERENT tally: cache, no second purchase — and
    # the returned cells are the ones the sheet was painted from, so the key
    # can never name birds that are not in the painting.
    art2, painted2 = provider.day_composite(CELLS[:1], DAY)
    assert art2 is not None
    assert model.calls == 1
    assert [c.scientific_name for c in painted2] == [c.scientific_name for c in CELLS]


def test_composite_force_regenerates(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.day_composite(CELLS, DAY)
    # Age the sheet past the repaint debounce.
    sidecar = data_dir / "collages" / "2026-08-28.json"
    meta = json.loads(sidecar.read_text()); meta["created_ts"] = 0
    sidecar.write_text(json.dumps(meta))
    assert provider.day_composite(CELLS, DAY, force=True) is not None
    assert model.calls == 2


def test_repaint_debounce_prevents_double_billing(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.day_composite(CELLS, DAY, force=True)
    # A second repaint racing the first (two tabs) reuses the fresh sheet.
    assert provider.day_composite(CELLS, DAY, force=True) is not None
    assert model.calls == 1


def test_failed_repaint_keeps_the_good_sheet(data_dir):
    provider = GeneratedArtProvider(FakeModel())
    provider.day_composite(CELLS, DAY)
    sidecar = data_dir / "collages" / "2026-08-28.json"
    meta = json.loads(sidecar.read_text()); meta["created_ts"] = 0
    sidecar.write_text(json.dumps(meta))
    provider._model = FakeModel(fail=True)
    sheet = provider.day_composite(CELLS, DAY, force=True)
    assert sheet is not None  # the cached sheet, not the grid fallback


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


def test_prune_keeps_newest_sheets(data_dir, monkeypatch):
    from datetime import date as d
    monkeypatch.setattr(GeneratedArtProvider, "_KEEP_SHEETS", 2)
    provider = GeneratedArtProvider(FakeModel())
    for day in (d(2026, 8, 26), d(2026, 8, 27), d(2026, 8, 28)):
        provider.day_composite(CELLS, day)
    kept = sorted(p.name for p in (data_dir / "collages").glob("*.png"))
    assert kept == ["2026-08-27.png", "2026-08-28.png"]


def test_review_date_wraps_midnight():
    from datetime import datetime
    from featherframe.service import review_date_for
    # evening tick reviews today; after-midnight tick reviews yesterday
    assert review_date_for(datetime(2026, 8, 28, 22, 30), "22:00", "06:00") == date(2026, 8, 28)
    assert review_date_for(datetime(2026, 8, 29, 0, 30), "22:00", "06:00") == date(2026, 8, 28)
    assert review_date_for(datetime(2026, 8, 29, 7, 0), "22:00", "06:00") == date(2026, 8, 29)
    # non-wrapping window never shifts
    assert review_date_for(datetime(2026, 8, 29, 1, 0), "12:00", "14:00") == date(2026, 8, 29)


def test_key_line_fits_long_names():
    from featherframe.render.collage import _fit_key
    from featherframe.render import typography, theme
    entries = [f"{i}. {n} ×{c}" for i, (n, c) in enumerate([
        ("Northern Rough-winged Swallow", 142), ("Black-throated Green Warbler", 87),
        ("Red-breasted Nuthatch", 31), ("Yellow-bellied Sapsucker", 12),
        ("Great Crested Flycatcher", 9)], start=1)]
    size, texts = _fit_key(entries, theme.WIDTH - 2 * 60)
    tracking_px = size * 0.05
    for t in texts:
        w = typography.smallcaps_width(
            typography.smallcaps_plan(t, typography.FONTS, size, 520, 520), tracking_px)
        assert w <= theme.WIDTH - 2 * 60


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


# -- how many species the sheet carries ------------------------------------
def _many(n):
    return [CollageCell(f"Species Number {i}", f"Genus species{i}", 100 - i)
            for i in range(1, n + 1)]


def test_config_review_species_max_clamps_and_roundtrips():
    assert Config().review_species_max == 10
    assert Config(review_species_max=-3).review_species_max == 0   # 0 = every species
    assert Config(review_species_max=999).review_species_max == 60
    again = Config.from_dict(Config(review_species_max=0).to_dict())
    assert again.review_species_max == 0


def test_composite_prompt_counts_every_subject():
    p = build_composite_prompt([(c.common_name, c.scientific_name) for c in _many(12)])
    assert "12 different species" in p
    assert "12. Species Number 12" in p
    # A crowded sheet is told so; the five-bird sheet is not.
    assert "crowded" in p.lower()
    assert "crowded" not in build_composite_prompt(
        [(c.common_name, c.scientific_name) for c in CELLS]).lower()


def test_key_never_eats_the_art(data_dir):
    """A 37-species key must leave the art most of the sheet, and every key
    line must still fit the width."""
    from featherframe.render.collage import _fit_key
    from featherframe.render import typography
    entries = [f"{i}. {c.common_name.upper()}" for i, c in enumerate(_many(37), start=1)]
    size, rows = _fit_key(entries, theme.CONTENT_W, max_h=theme.KEY_MAX_H)
    line_h = round(size * theme.KEY_LINE_H)
    assert (len(rows) - 1) * line_h + size <= theme.KEY_MAX_H
    assert sum(len(r) for r in rows) == 37
    assert [e for r in rows for e in r] != entries or len(rows) == 37  # column-major when packed
    for r in rows:
        w = sum(typography.engraved_width(e, size, theme.KEY_TRACKING) for e in r)
        w += size * theme.KEY_ENTRY_GAP * (len(r) - 1)
        assert w <= theme.CONTENT_W
    art = Image.open(__import__("io").BytesIO(_plate_png())).convert("L")
    field = collage_mod.render_generated_collage(art, _many(37), when=DAY, title="Sightings")
    assert field.size == (theme.WIDTH, theme.HEIGHT)


def test_key_short_list_still_one_centered_column():
    from featherframe.render.collage import _fit_key
    entries = [f"{i}. {c.common_name.upper()}" for i, c in enumerate(CELLS, start=1)]
    size, rows = _fit_key(entries, theme.CONTENT_W, max_h=theme.KEY_MAX_H)
    assert size == theme.KEY_SIZES[0]
    assert [e for r in rows for e in r] == entries


# -- the sheet: no header, small packed key, art sized to the box ---------------
def test_sheet_has_no_title_band(data_dir):
    """Flat gray art must reach the top margin — nothing is printed above it."""
    art = Image.new("L", collage_mod.sheet_art_size(CELLS), 128)  # fills the box
    field = collage_mod.render_generated_collage(art, CELLS, when=DAY, title="Sightings")
    y = theme.SHEET_MARGIN_TOP + 10
    xs = range(theme.WIDTH // 3, 2 * theme.WIDTH // 3, 8)
    inked = sum(1 for x in xs if field.getpixel((x, y)) < 200)
    assert inked > 0.9 * len(xs)


def test_sheet_key_is_smaller_and_packed():
    size, rows = collage_mod.sheet_key(_many(24))
    assert size <= theme.SHEET_KEY_SIZES[0] <= 24
    assert len(rows) <= 8


def test_sheet_art_size_matches_the_box():
    for cells in (CELLS, _many(24)):
        w, h = collage_mod.sheet_art_size(cells)
        assert w % 16 == 0 and h % 16 == 0 and w * h >= 655_360
        l, t, r, b = collage_mod.sheet_art_box(cells)
        assert abs(w / h - (r - l) / (b - t)) < 0.03
    assert collage_mod.sheet_art_size(CELLS) != collage_mod.sheet_art_size(_many(24))


def test_day_composite_generates_at_the_sheet_size(data_dir):
    model = FakeModel()
    GeneratedArtProvider(model).day_composite(CELLS, DAY)
    assert model.sizes == ["%dx%d" % collage_mod.sheet_art_size(CELLS)]


def test_crowded_prompt_fills_the_sheet():
    p = build_composite_prompt([(c.common_name, c.scientific_name) for c in _many(12)])
    assert "full width" in p and "no bare margin" in p
    assert "where the bough meets earth" in p          # a station for the ground-dwellers
    assert "sizes follow life alone" in p               # prominence is placement, not size


def test_sheet_key_widens_before_it_deepens():
    """A long key takes more columns rather than more rows: 24 entries sit in
    three columns of eight, while a short key stays a single column."""
    size, rows = collage_mod.sheet_key(_many(24))
    assert max(len(r) for r in rows) == 3
    assert len(rows) == theme.SHEET_KEY_MAX_ROWS
    _, short = collage_mod.sheet_key(CELLS)
    assert max(len(r) for r in short) == 1


def test_sheet_carries_the_date_above_the_key():
    """"SEPTEMBER 2" in wide-tracked engraved caps between the art and the
    key; the art box gives up that line."""
    assert collage_mod.sheet_date_text(date(2026, 9, 2)) == "WEDNESDAY, SEPTEMBER 2, 2026"
    art = Image.new("L", collage_mod.sheet_art_size(CELLS), 128)
    field = collage_mod.render_generated_collage(art, CELLS, when=date(2026, 9, 2))
    baseline = collage_mod.sheet_date_baseline(CELLS)
    y = baseline - 6  # inside the caps
    xs = range(theme.WIDTH // 2 - 200, theme.WIDTH // 2 + 200, 2)
    assert any(field.getpixel((x, y)) < 100 for x in xs)      # ink from the date
    assert all(field.getpixel((x, baseline + 4)) > 200 for x in xs)  # clear below it
    box = collage_mod.sheet_art_box(CELLS)
    assert box[3] < baseline - theme.SHEET_KEY_SIZES[0]        # art ends above the date
