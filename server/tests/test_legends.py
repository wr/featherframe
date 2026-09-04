"""Audubon plate legends (W-708): the printed figure key and plant line under
each plate, resolved per detected species, threaded from legends.yaml through
the index to the artwork."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from PIL import Image

from featherframe import legends
from featherframe.names import SpeciesIndex
from featherframe.render.provider import AudubonProvider

TITMICE = [
    "Chesnut-backed Titmouse, 1. Male, 2. Female.",
    "Black-capt Titmouse, 3. Male, 4. Female.",
    "Chesnut-crowned Titmouse, 5. Male, 6. Female.",
    "(and Nest.)",
    "Willow Oak ~ Quercus Phellos. L.",
]


def test_single_species_legend_passes_through():
    lines = ["Male, 1. Female, 2.", "Wild Almond."]
    assert legends.resolve("Cardinal Grosbeak", False, lines) == lines


def test_composite_picks_the_detected_species_key_and_the_plant():
    got = legends.resolve("Black-capt Titmouse (composite)", True, TITMICE)
    assert got == ["3. Male, 4. Female.", "Willow Oak ~ Quercus Phellos. L."]


def test_composite_continuation_line_stays_with_its_species():
    got = legends.resolve("Chesnut-crowned Titmouse", True, TITMICE)
    assert got == ["5. Male, 6. Female. (and Nest.)", "Willow Oak ~ Quercus Phellos. L."]


def test_composite_with_no_matching_title_keeps_only_the_plant():
    # Never another bird's figure key under this bird's name.
    got = legends.resolve("Carolina Titmouse", True, TITMICE)
    assert got == ["Willow Oak ~ Quercus Phellos. L."]


def test_title_matching_ignores_case_punctuation_and_the_composite_tag():
    lines = ["Black & Yellow Warbler, 1. Male, 2. Female.", "Black Walnut."]
    assert legends.resolve("black and yellow warbler (composite)", True, lines) == [
        "1. Male, 2. Female.", "Black Walnut."]


def test_load_legends_reads_the_yaml_by_plate(tmp_path):
    p = tmp_path / "legends.yaml"
    p.write_text(yaml.safe_dump({"legends": {159: {"lines": ["Male, 1. Female, 2.", "Wild Almond."]},
                                             353: {"composite": True, "lines": TITMICE}}}))
    got = legends.load(p)
    assert got[159] == {"lines": ["Male, 1. Female, 2.", "Wild Almond."], "composite": False}
    assert got[353]["composite"] is True


def test_shipped_legends_cover_the_curated_plates():
    got = legends.load()
    assert got[159]["lines"] == ["Male, 1. Female, 2.", "Wild Almond."]
    assert got[173]["lines"] == ["Male, 1. Female, 2."]
    assert got[353]["composite"] is True
    assert len(got) >= 390


# -- through the index to the artwork ------------------------------------------
def test_plate_match_and_artwork_carry_the_legend(tmp_path):
    img = tmp_path / "plate-159-cardinal-grosbeak.jpg"
    Image.new("L", (400, 500), 255).save(img)
    idx = SpeciesIndex([{"common": "Northern Cardinal", "scientific": "Cardinalis cardinalis",
                         "plate": 159, "image": img.name,
                         "legend": ["Male, 1. Female, 2.", "Wild Almond."]}],
                       images_dir=tmp_path)
    m = idx.match("Northern Cardinal", "Cardinalis cardinalis")
    assert m.legend == ["Male, 1. Female, 2.", "Wild Almond."]
    art = AudubonProvider(idx).artwork("Northern Cardinal", "Cardinalis cardinalis")
    assert art.legend == ["Male, 1. Female, 2.", "Wild Almond."]


def test_index_entry_without_legend_yields_an_empty_list(tmp_path):
    img = tmp_path / "plate-1.jpg"
    Image.new("L", (400, 500), 255).save(img)
    idx = SpeciesIndex([{"common": "X", "scientific": "Y z", "plate": 1, "image": img.name}],
                       images_dir=tmp_path)
    assert idx.match("X", "Y z").legend == []
