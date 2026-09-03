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
    # Catalog expansion (2026-08). The mirror's data.json 'name' column is
    # shifted +1 for plates 361-399; these pins follow TRUE Havell numbering
    # (matching the mirror's fileName column, which is what downloads use).
    "Redpoll": 375,               # "Lesser Red-Poll" — mirror name sits at 376
    "Dickcissel": 384,            # "Black-throated Bunting" — mirror name at 385
    "Bank Swallow": 385,          # "Bank Swallow and Violet-green Swallow"
    "Chimney Swift": 158,         # "American Swift"
    "Cooper's Hawk": 36,          # "Stanley Hawk"
    "Eastern Phoebe": 120,        # "Pewit Flycatcher"
    "Willow Flycatcher": 45,      # both split from "Traill's Flycatcher"
    "Alder Flycatcher": 45,
    "Winter Wren": 360,           # "Winter Wren and Rock Wren"
    # Heard at the house, added 2026-09. Plate 386's mirror name is shifted
    # (reads "Bank Swallow..."); its fileName plate-386-white-heron.jpg is right.
    "Common Nighthawk": 147,      # "Night Hawk"
    "Least Bittern": 210,
    "Upland Sandpiper": 303,      # "Bartram Sandpiper"
    "Great Egret": 386,           # "White Heron" (281 is the Great Blue's white morph)
    "Northern Parula": 15,        # "Blue Yellow-backed Warbler" — typed by its 1830s title on 3 Sep, got an AI plate
    # Never a wrong bird: confirmed plate-less -> AI generation candidates.
    "Veery": None,                # plate 164's bird is disputed (Halley 2018)
    "Rock Pigeon": None,          # not painted
    "Southeastern myotis": None,  # a bat
    "Mute Swan": None,            # introduced
    "Least Flycatcher": None,     # described 1843, post-Havell
    "Ring-necked Pheasant": None, # introduced 1881
    "European Goldfinch": None,   # introduced
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
                 "Red-bellied Woodpecker", "Bank Swallow", "Winter Wren",
                 "Scarlet Tanager", "Brown Creeper"):
        assert by_common[name].get("composite") is True, f"{name} should be composite"


def test_hairy_woodpecker_matches_birdnet_taxonomy():
    """BirdNET-Go reports the Hairy Woodpecker as Leuconotopicus villosus;
    the entry must carry it as a synonym or the live match misses."""
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    hairy = next(s for s in doc["species"] if s["common"] == "Hairy Woodpecker")
    assert "Leuconotopicus villosus" in (hairy.get("sci_synonyms") or [])
