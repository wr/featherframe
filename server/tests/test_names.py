"""Tests for species-name matching — the mapping most likely to break in the
field. The guiding rule under test: when unsure, return None (fallback), never
a wrong bird."""
from __future__ import annotations

import pytest

from featherframe.names import SpeciesIndex, fuzzy_resolve_plate, normalize


def _index_with_images(tmp_path):
    """Build an index whose images actually exist on disk."""
    img = tmp_path / "img"
    img.mkdir()
    for name in ("cardinal.jpg", "junco.jpg", "chickadee.jpg"):
        (img / name).write_bytes(b"\xff\xd8\xff\xd9")  # tiny stand-in JPEG
    entries = [
        {"common": "Northern Cardinal", "scientific": "Cardinalis cardinalis",
         "plate": 159, "image": "cardinal.jpg", "audubon_title": "Cardinal Grosbeak"},
        {"common": "Dark-eyed Junco", "scientific": "Junco hyemalis",
         "plate": 13, "image": "junco.jpg"},
        {"common": "Downy Woodpecker", "scientific": "Dryobates pubescens",
         "plate": 112, "image": "cardinal.jpg", "sci_synonyms": ["Picoides pubescens"]},
        {"common": "Black-capped Chickadee", "scientific": "Poecile atricapillus",
         "plate": 353, "image": "chickadee.jpg", "composite": True},
        {"common": "European Starling", "scientific": "Sturnus vulgaris",
         "plate": None},  # deliberately no plate
        {"common": "Missing Image Bird", "scientific": "Nonexistus fakus",
         "plate": 99, "image": "not_on_disk.jpg"},
    ]
    return SpeciesIndex(entries, images_dir=img)


def test_normalize():
    assert normalize("Dark-eyed Junco") == "dark eyed junco"
    assert normalize("Cardinalis  cardinalis") == "cardinalis cardinalis"
    assert normalize("Bewick's Wren") == "bewicks wren"
    assert normalize("") == ""


def test_match_by_scientific(tmp_path):
    idx = _index_with_images(tmp_path)
    m = idx.match("whatever", "Cardinalis cardinalis")
    assert m is not None
    assert m.plate_number == 159
    assert m.has_image


def test_match_by_common_name(tmp_path):
    idx = _index_with_images(tmp_path)
    m = idx.match("Dark-eyed Junco", "")
    assert m is not None and m.plate_number == 13


def test_match_by_scientific_synonym(tmp_path):
    idx = _index_with_images(tmp_path)
    # BirdNET might still emit the old Picoides binomial
    m = idx.match("Downy Woodpecker", "Picoides pubescens")
    assert m is not None and m.plate_number == 112


def test_composite_flag(tmp_path):
    idx = _index_with_images(tmp_path)
    m = idx.match("Black-capped Chickadee", "Poecile atricapillus")
    assert m is not None and m.composite is True


def test_no_plate_species_returns_none(tmp_path):
    idx = _index_with_images(tmp_path)
    # European Starling: Audubon never painted it -> fallback, never a wrong bird
    assert idx.match("European Starling", "Sturnus vulgaris") is None


def test_unknown_species_returns_none(tmp_path):
    idx = _index_with_images(tmp_path)
    assert idx.match("Emperor Penguin", "Aptenodytes forsteri") is None


def test_missing_image_degrades_to_none(tmp_path):
    idx = _index_with_images(tmp_path)
    # entry exists but its image isn't on disk -> None (fallback), not a crash
    assert idx.match("Missing Image Bird", "Nonexistus fakus") is None


def test_count(tmp_path):
    idx = _index_with_images(tmp_path)
    assert idx.count == 6


# -- fuzzy resolver (build-time helper) -----------------------------------
CATALOG = [
    {"plate": 159, "name": "Cardinal Grosbeak"},
    {"plate": 13, "name": "Snow Bird"},
    {"plate": 67, "name": "Red winged Starling, or Marsh Blackbird"},
    {"plate": 131, "name": "American Robin"},
]


def test_fuzzy_resolves_modern_to_archaic():
    got = fuzzy_resolve_plate("Northern Cardinal", CATALOG)
    assert got and got[0]["plate"] == 159  # matched via 'cardinal'


def test_fuzzy_exact_modern_name():
    got = fuzzy_resolve_plate("American Robin", CATALOG)
    assert got and got[0]["plate"] == 131


def test_fuzzy_would_mismatch_starling_hence_not_used_live():
    # Demonstrates the trap: 'European Starling' fuzzy-hits the Red-winged
    # Blackbird plate via 'starling'. This is exactly why live matching uses the
    # curated index (with an explicit no-plate entry), not the fuzzy resolver.
    got = fuzzy_resolve_plate("European Starling", CATALOG)
    assert got and got[0]["plate"] == 67


def test_fuzzy_no_match_returns_empty():
    assert fuzzy_resolve_plate("Emperor Penguin", CATALOG) == []


def test_load_ignores_foreign_images_dir(tmp_path):
    # index.json generated on another machine records an absolute images_dir
    # that doesn't exist here; load() must fall back to img/ beside the index.
    import json
    img = tmp_path / "img"
    img.mkdir()
    (img / "cardinal.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "images_dir": "/Users/someone-else/does/not/exist",
        "species": [{"common": "Northern Cardinal",
                     "scientific": "Cardinalis cardinalis",
                     "plate": 159, "image": "cardinal.jpg"}],
    }))
    idx = SpeciesIndex.load(index_path)
    m = idx.match("Northern Cardinal", "Cardinalis cardinalis")
    assert m is not None and m.has_image
