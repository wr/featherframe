"""The combined collage (one generated composite plate): per-day cache semantics, grid
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
    louder = [CollageCell(c.common_name, c.scientific_name, c.count * 3) for c in CELLS]
    art2, painted2 = provider.day_composite(louder, DAY)
    assert art2 is not None
    assert model.calls == 1
    assert [c.scientific_name for c in painted2] == [c.scientific_name for c in CELLS]
    assert [c.count for c in painted2] == [c.count for c in CELLS]


def _age_out(data_dir):
    """Put the day's sheet past the repaint debounce."""
    sidecar = data_dir / "collages" / "2026-08-28.json"
    meta = json.loads(sidecar.read_text())
    meta["created_ts"] = 0
    sidecar.write_text(json.dumps(meta))


def test_a_redraw_of_the_same_species_never_buys_a_second_sheet(data_dir):
    """Every collage is the generated one when the toggle is on, so a redraw
    has to be free: the day's species list is what a sheet is of."""
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.day_composite(CELLS, DAY)
    _age_out(data_dir)
    for _ in range(4):
        assert provider.day_composite(CELLS, DAY) is not None
    assert model.calls == 1


def test_a_reordered_day_keeps_its_sheet_and_its_painted_key(data_dir):
    """Two species trading places in the tally is not a new sheet (W-859):
    the sheet is kept, and the key still numbers the figures as painted."""
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.day_composite(CELLS, DAY)
    _age_out(data_dir)
    swapped = list(reversed(CELLS))
    art, painted = provider.day_composite(swapped, DAY)
    assert art is not None and model.calls == 1
    assert [c.scientific_name for c in painted] == [c.scientific_name for c in CELLS]


def test_a_new_species_in_the_day_buys_one_fresh_sheet(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.day_composite(CELLS, DAY)
    _age_out(data_dir)
    more = CELLS + [CollageCell("Blue Jay", "Cyanocitta cristata", 3)]
    art, painted = provider.day_composite(more, DAY)
    assert art is not None and model.calls == 2
    assert [c.scientific_name for c in painted] == [c.scientific_name for c in more]
    # …and exactly one: that day is now of those species.
    _age_out(data_dir)
    assert provider.day_composite(more, DAY) is not None
    assert model.calls == 2


def test_a_stale_sheet_is_still_better_than_the_grid_while_cooling_down(data_dir):
    provider = GeneratedArtProvider(FakeModel())
    provider.day_composite(CELLS, DAY)
    _age_out(data_dir)
    provider._model = FakeModel(fail=True)
    more = CELLS + [CollageCell("Blue Jay", "Cyanocitta cristata", 3)]
    assert provider.day_composite(more, DAY) is not None      # the buy failed
    assert provider.day_composite(more, DAY) is not None      # …and the cooldown holds
    assert provider._model.calls == 1


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


def test_collage_date_wraps_midnight():
    from datetime import datetime
    from featherframe.service import collage_date_for
    # evening tick covers today; after-midnight tick covers yesterday
    assert collage_date_for(datetime(2026, 8, 28, 22, 30), "22:00", "06:00") == date(2026, 8, 28)
    assert collage_date_for(datetime(2026, 8, 29, 0, 30), "22:00", "06:00") == date(2026, 8, 28)
    assert collage_date_for(datetime(2026, 8, 29, 7, 0), "22:00", "06:00") == date(2026, 8, 29)
    # non-wrapping window never shifts
    assert collage_date_for(datetime(2026, 8, 29, 1, 0), "12:00", "14:00") == date(2026, 8, 29)


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
        art, CELLS, when=DAY, total_detections=1035)
    assert field.size == (theme.WIDTH, theme.HEIGHT)
    assert field.mode == "L"


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


def test_config_collage_species_max_clamps_and_roundtrips():
    assert Config().collage_species_max == 10
    assert Config(collage_species_max=-3).collage_species_max == 0   # 0 = every species
    assert Config(collage_species_max=999).collage_species_max == 60
    again = Config.from_dict(Config(collage_species_max=0).to_dict())
    assert again.collage_species_max == 0


def test_the_day_in_review_is_a_collage_under_its_old_names_too():
    """One name for it: a config saved as review_species_max keeps its number,
    and a frame committed as "day in review" still reads as a collage."""
    from featherframe.service import frame_title
    assert Config.from_dict({"review_species_max": 17}).collage_species_max == 17
    assert Config.from_dict({"review_species_max": 17,
                             "collage_species_max": 4}).collage_species_max == 4
    for label in ("day in review (5 species)", "combined collage (5 species)", "5-species collage"):
        assert frame_title({"label": label}) == "Collage · 5 species"


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
    field = collage_mod.render_generated_collage(art, _many(37), when=DAY)
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
    field = collage_mod.render_generated_collage(art, CELLS, when=DAY)
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
    assert "single bare bough" in p                     # one unified conceit (W-699)
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
    # Clear below it, past the comma's tail and the Fell face's old-style figures.
    assert all(field.getpixel((x, baseline + 14)) > 200 for x in xs)
    box = collage_mod.sheet_art_box(CELLS)
    assert box[3] < baseline - theme.SHEET_KEY_SIZES[0]        # art ends above the date


# -- the grid is set exactly like the generated sheet (W-833 review) -----------
class _NoArt:
    """A provider with no plate for anything: the grid's typographic cell."""

    def artwork(self, common_name, scientific_name):
        return None


def test_the_grid_and_the_sheet_share_one_bottom_block():
    """Same date line, same numbered key, same art box — one helper draws it
    for both, so the two collages are one thing set two ways."""
    import numpy as np
    box = collage_mod.sheet_art_box(CELLS)
    art = Image.new("L", collage_mod.sheet_art_size(CELLS), 200)
    sheet = collage_mod.render_generated_collage(art, CELLS, when=DAY)
    grid = collage_mod.render_collage(CELLS, _NoArt(), when=DAY)
    below = lambda img: np.asarray(img.convert("L"))[box[3]:, :]   # noqa: E731
    assert np.array_equal(below(sheet), below(grid))
    assert collage_mod.sheet_date_text(DAY) == "FRIDAY, AUGUST 28, 2026"


def test_the_grid_has_no_title_line_and_numbers_its_cells():
    import numpy as np
    grid = np.asarray(collage_mod.render_collage(CELLS, _NoArt(), when=DAY))
    # Nothing is printed above the art: the top margin is bare paper.
    assert (grid[:theme.SHEET_MARGIN_TOP, :] == 255).all()
    # Each cell carries its figure numeral at its head, keyed to the key.
    left, top, right, _ = collage_mod.sheet_art_box(CELLS)
    corner = grid[top:top + 2 * theme.COLLAGE_FIGURE_SIZE,
                  left:left + 2 * theme.COLLAGE_FIGURE_SIZE]
    assert corner.min() < 128
    assert [e for r in collage_mod.sheet_key(CELLS)[1] for e in r][0] == "1. GREAT HORNED OWL"


def test_a_colour_grid_keeps_its_type_on_the_gray_field():
    """The colour panel's grid is the same sheet: art in colour, type over it."""
    img = collage_mod.render_collage(CELLS, _NoArt(), when=DAY, color=True)
    assert img.mode == "RGB" and img.size == (theme.WIDTH, theme.HEIGHT)
