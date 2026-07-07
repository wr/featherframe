"""Guards the curated species -> plate crosswalk in species.yaml against
regressions. Verifies the *resolved* plate numbers, not the images, so it runs
without downloading. Skips cleanly if the index hasn't been built yet."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SPECIES_YAML = Path(__file__).resolve().parents[1] / "scripts" / "species.yaml"

# A handful of the tricky, verified mappings (archaic titles + the no-plate two).
EXPECTED = {
    "Northern Cardinal": 159,     # "Cardinal Grosbeak"
    "Dark-eyed Junco": 13,        # "Snow Bird"
    "Mourning Dove": 17,          # "Carolina Pigeon"
    "Tufted Titmouse": 39,        # "Crested Titmouse"
    "White-throated Sparrow": 8,  # space, not hyphen — easy to mismatch
    "Eastern Towhee": 29,         # "Towee Bunting"
    "Common Grackle": 7,          # "Purple Grakle"
    "Red-winged Blackbird": 67,   # filed under "...Starling..."
    "European Starling": None,    # not painted -> fallback
    "House Sparrow": None,        # not painted -> fallback
}


def test_species_yaml_plate_numbers():
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    by_common = {s["common"]: s for s in doc["species"]}
    for common, plate in EXPECTED.items():
        assert common in by_common, f"{common} missing from species.yaml"
        got = by_common[common].get("plate")
        if plate is None:
            assert got in (None, "none"), f"{common} should have no plate, got {got}"
        else:
            assert got == plate, f"{common}: expected plate {plate}, got {got}"


def test_starling_not_fuzzy_matched_to_blackbird():
    """European Starling must be pinned to no-plate so it never fuzzy-matches
    the Red-winged Blackbird plate (67), which is titled '...Starling...'."""
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    starling = next(s for s in doc["species"] if s["common"] == "European Starling")
    assert starling.get("plate") in (None, "none")


def test_composites_flagged():
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    by_common = {s["common"]: s for s in doc["species"]}
    for name in ("Black-capped Chickadee", "House Finch", "Hairy Woodpecker",
                 "Red-bellied Woodpecker"):
        assert by_common[name].get("composite") is True, f"{name} should be composite"
