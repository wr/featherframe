"""Guards the curated species -> plate crosswalk in folios/havell.yaml against
regressions. Verifies the *resolved* plate numbers, not the images, so it runs
without downloading. Skips cleanly if the index hasn't been built yet."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SPECIES_YAML = Path(__file__).resolve().parents[1] / "scripts" / "folios" / "havell.yaml"

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
    # Full edition (2026-09): archaic titles that a title-match would get wrong.
    "Brown Noddy": 275,           # "Noddy Tern" — Commons had it as Black Noddy
    "Sandhill Crane": 261,        # Audubon's juvenile "Hooping Crane"
    "Whooping Crane": 226,
    "Reddish Egret": 256,         # "Purple Heron"
    "Laughing Gull": 314,         # Audubon's "Black-headed Gull" is NOT Chroicocephalus
    "Yellow Warbler": 95,         # "Yellow-poll Warbler"; 35/65 are juveniles
    "Northern Harrier": 356,      # "Marsh Hawk"
    "Canada Warbler": 103,        # not 5 "Bonaparte's Flycatcher" (a female)
    "Merlin": 92,                 # "Pigeon Hawk", not 75 "Le Petit Caporal"
    "Swainson's Hawk": 372,       # "Common Buzzard"
    "Red Knot": 315,              # "Red-breasted Sandpiper"
    # Plate 50's mirror name ("Black & Yellow Warbler") is its 1828 first-state
    # lettering; the scan is the re-lettered state captioned "Swainson's
    # Warbler". The Black & Yellow (Magnolia) pair is 123 — read off the plate.
    "Magnolia Warbler": 123,      # "Black & Yellow Warbler", Sylvia maculosa
    "Swainson's Warbler": 198,    # "Brown headed Worm eating Warbler"; scan says Swainson's
    # Never a wrong bird: confirmed plate-less -> AI generation candidates.
    "Veery": None,                # plate 164's bird is disputed (Halley 2018)
    "Rock Pigeon": None,          # not painted
    "Southeastern myotis": None,  # a bat
    "Mute Swan": None,            # introduced
    "Least Flycatcher": None,     # described 1843, post-Havell
    "Ring-necked Pheasant": None, # introduced 1881
    "Baikal Teal": None,          # 338 "Bemaculated Duck" is a presumed hybrid — never pinned
    "European Goldfinch": None,   # introduced
}


def test_species_yaml_plate_numbers():
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    by_common = {s["common"]: s for s in doc["species"]}
    for common, plate in EXPECTED.items():
        assert common in by_common, f"{common} missing from folios/havell.yaml"
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


def test_no_dispute_plates_pinned():
    """Audubon's invalid or disputed birds must never be mapped to a species."""
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    pinned = {s["plate"] for s in doc["species"] if isinstance(s.get("plate"), int)}
    for plate in (11, 55, 60, 164, 184, 338, 407):
        assert plate not in pinned, f"plate {plate} is disputed/invalid and must not be pinned"


def test_every_pin_matches_the_mirror_slug():
    """Structural guard against plate-number slips: each pinned entry's
    audubon_title must share a word with the mirror's fileName slug for that
    plate (the slug follows true Havell numbering even where the mirror's
    'name' column is shifted). Skips when the catalog isn't on disk."""
    import json
    import os
    from featherframe import paths
    cat = Path(os.environ.get("FEATHERFRAME_PLATES_DIR") or paths.plates_dir()) / "data.json"
    if not cat.exists():
        pytest.skip("mirror catalog not downloaded")
    slugs = {e["plate"]: set(e["fileName"][:-4].split("-")[2:]) for e in json.loads(cat.read_text())}
    # The mirror titles three plates by an alternative name for the same bird.
    aliases = {170: {"gray", "tyrant"}, 230: {"ruddy", "plover"}, 245: {"thick", "billed", "murre"}}
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    bad = []
    for s in doc["species"]:
        p = s.get("plate")
        if not isinstance(p, int):
            continue
        words = {w for w in "".join(c if c.isalnum() else " " for c in s["audubon_title"].lower()).split() if len(w) > 2}
        if not (words & slugs[p]) and not (words & aliases.get(p, set())):
            bad.append((s["common"], p, s["audubon_title"], sorted(slugs[p])))
    assert not bad, f"title/slug mismatch (wrong plate number?): {bad}"


def test_kittlitzs_murrelet_not_pinned():
    """Plate 402 shows four alcids per Pitt and NYHS; Commons adds a fifth
    (Kittlitz's Murrelet) that the curatorial sources do not — so it stays
    unpinned rather than risk a wrong bird."""
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    assert not any(s["common"] == "Kittlitz's Murrelet" for s in doc["species"])


def test_plate_50_is_swainsons_not_magnolia():
    """The cached plate-50 scan reads 'Swainson's Warbler' despite the mirror's
    'Black & Yellow Warbler' name (a first-state title). Pinning it to any
    species but Swainson's Warbler would put the wrong bird on the wall."""
    doc = yaml.safe_load(SPECIES_YAML.read_text())
    on_50 = [s["common"] for s in doc["species"] if s.get("plate") == 50]
    assert on_50 in ([], ["Swainson's Warbler"]), f"plate 50 pinned to {on_50}"


# --- The Gould folio (W-702) --------------------------------------------------

GOULD_YAML = SPECIES_YAML.parent / "gould_europe.yaml"

# Each checked by eye against the engraved caption on its scan, and the number
# against the printed General List of Plates.
GOULD_EXPECTED = {
    "House Sparrow": (184, "birdsEuropeIIIGoul", 150),
    "Eurasian Tree Sparrow": (184, "birdsEuropeIIIGoul", 150),
    "European Goldfinch": (196, "birdsEuropeIIIGoul", 198),
    "European Starling": (210, "birdsEuropeIIIGoul", 254),
    "Rock Pigeon": (245, "birdsEuropeIVGoul", 22),
    "Ring-necked Pheasant": (247, "birdsEuropeIVGoul", 30),
    "Mute Swan": (354, "birdsEuropeVGoul", 46),
    "Black-headed Gull": (425, "birdsEuropeVGoul", 330),
}


def _gould():
    return yaml.safe_load(GOULD_YAML.read_text())


def test_gould_pins():
    by_common = {e["common"]: e for e in _gould()["species"]}
    for common, (plate, volume, leaf) in GOULD_EXPECTED.items():
        e = by_common[common]
        assert (e["plate"], e["volume"], e["leaf"]) == (plate, volume, leaf), common


def test_gould_entries_are_whole():
    doc = _gould()
    assert doc["folio"]["title"] == "The Birds of Europe" and "{volume}" in doc["folio"]["scans"]
    for e in doc["species"]:
        assert 1 <= e["plate"] <= doc["folio"]["plates"], e["common"]
        assert e["volume"].startswith("birdsEurope") and e["leaf"] > 0, e["common"]
        assert e.get("rotate", 0) in (0, 90, 180, 270), e["common"]


def test_gould_black_headed_gull_is_his_laughing_gull():
    """Gould's "Black-headed Gull" (plate 427) is today's Mediterranean Gull;
    today's Black-headed Gull is his "Laughing Gull", plate 425; and today's
    (American) Laughing Gull is his "Black-winged Gull", plate 426."""
    by_common = {e["common"]: e for e in _gould()["species"]}
    assert by_common["Black-headed Gull"]["plate"] == 425
    assert by_common["Black-headed Gull"]["scientific"] == "Chroicocephalus ridibundus"
    assert by_common["Mediterranean Gull"]["plate"] == 427
    assert (by_common["Laughing Gull"]["plate"],
            by_common["Laughing Gull"]["scientific"]) == (426, "Leucophaeus atricilla")


def test_gould_pins_each_species_once():
    """The whole folio (slice 3): one plate per species, a species on several
    plates keeping its main one, so the folio never flips between two."""
    species = _gould()["species"]
    assert len(species) >= 390
    sci = [e["scientific"] for e in species]
    assert len(sci) == len(set(sci))
    commons = [e["common"] for e in species]
    assert len(commons) == len(set(commons))


def test_gould_landscape_plates_stand_upright():
    """Plates bound sideways read their caption down the right edge: a
    quarter turn clockwise (PIL 270) stands them up. Nothing else is turned."""
    turned = {e["plate"]: e.get("rotate") for e in _gould()["species"] if e.get("rotate")}
    assert set(turned.values()) == {270}
    assert {247, 354, 425} <= set(turned)
    assert 184 not in turned and 210 not in turned


def test_gould_plates_on_one_sheet_carry_their_figure_numbers():
    """A sheet with more than one species is a composite and each species
    names its own figure, as Gould's caption numbers them."""
    by_plate = {}
    for e in _gould()["species"]:
        by_plate.setdefault(e["plate"], []).append(e)
    for plate, es in by_plate.items():
        # One figure standing for the daughters of a later split (Orphean,
        # Bonelli's, ...) shares its title and is no composite.
        if len({e["gould_title"] for e in es}) > 1:
            assert all(e.get("composite") and e.get("legend") for e in es), plate


# --- Gould's Birds of Great Britain (W-872): a gap-filler behind Europe --------

BRITAIN_YAML = SPECIES_YAML.parent / "gould_britain.yaml"

# (volume, plate, IA volume, leaf), each checked by eye against its engraved
# caption. Numbered per volume, as the book's own Lists of Plates are.
BRITAIN_EXPECTED = {
    "Yellow-browed Warbler": (2, 68, "birdsgreatbrita2goul", 276),
    "Rock Pipit": (3, 10, "birdsgreatbrita3goul", 46),
    "Water Pipit": (3, 11, "birdsgreatbrita3goul", 50),
    "Little Bunting": (3, 25, "birdsgreatbrita3goul", 106),
    "Pallas's Sandgrouse": (4, 11, "birdsgreatbrita4goul", 50),
    "Pink-footed Goose": (5, 3, "birdsgreatbrita5goul", 20),
    "Ross's Gull": (5, 63, "birdsgreatbrita5goul", 260),
}


def _britain():
    return yaml.safe_load(BRITAIN_YAML.read_text())


def test_britain_pins_exactly_the_seven():
    doc = _britain()
    assert doc["folio"]["region"] == "europe" and doc["folio"]["plates_per_volume"]
    by_common = {e["common"]: e for e in doc["species"]}
    assert set(by_common) == set(BRITAIN_EXPECTED)
    for common, (vol, plate, volume, leaf) in BRITAIN_EXPECTED.items():
        e = by_common[common]
        assert (e["volume_no"], e["plate"], e["volume"], e["leaf"]) == (vol, plate, volume, leaf), common
    turned = {e["common"] for e in doc["species"] if e.get("rotate")}
    assert turned == {"Pallas's Sandgrouse", "Pink-footed Goose", "Ross's Gull"}


def test_britain_never_pins_what_europe_does():
    """Both folios are Europe's region, so the index's order between them
    would decide a species they shared. They share none, so it never does:
    Britain only fills what Europe lacks."""
    def names(doc):
        out = set()
        for e in doc["species"]:
            out.add(e["common"].lower())
            out |= {s.lower() for s in [e["scientific"], *e.get("sci_synonyms", [])]}
        return out
    assert not names(_britain()) & names(_gould())


# --- Gould's Birds of Australia (W-870) ---------------------------------------

AUSTRALIA_YAML = SPECIES_YAML.parent / "gould_australia.yaml"

# Plates per volume, from each volume's List of Plates (the Supplement last).
AUSTRALIA_VOLUMES = {1: 36, 2: 104, 3: 97, 4: 104, 5: 92, 6: 82, 7: 85, "Supp.": 81}

# Each read against its engraved caption and Gould's own text.
AUSTRALIA_EXPECTED = {
    "Laughing Kookaburra": (2, 18, "birdsAustraliav2Goul", 80),
    "Superb Fairywren": (3, 18, "birdsAustraliav3Goul", 80),
    "Superb Parrot": (5, 15, "birdsAustraliav5Goul", 68),
    "Regent Parrot": (5, 16, "birdsAustraliav5Goul", 72),
}


def _australia():
    return yaml.safe_load(AUSTRALIA_YAML.read_text())


def _australia_at(volume_no, plate):
    return next((e for e in _australia()["species"]
                 if (e["volume_no"], e["plate"]) == (volume_no, plate)), None)


def test_australia_pins():
    by_common = {e["common"]: e for e in _australia()["species"]}
    for common, (vol, plate, volume, leaf) in AUSTRALIA_EXPECTED.items():
        e = by_common[common]
        assert (e["volume_no"], e["plate"], e["volume"], e["leaf"]) == (vol, plate, volume, leaf), common


def test_australia_entries_are_whole():
    doc = _australia()
    folio = doc["folio"]
    assert folio["region"] == "australia" and folio["plates_per_volume"] is True
    assert folio["plates"] == sum(AUSTRALIA_VOLUMES.values())
    for e in doc["species"]:
        assert 1 <= e["plate"] <= AUSTRALIA_VOLUMES[e["volume_no"]], e["common"]
        assert e["volume"].startswith("birdsAustralia") and e["leaf"] > 0, e["common"]


def test_australia_pins_each_species_once():
    species = _australia()["species"]
    assert len(species) >= 380
    sci = [e["scientific"] for e in species]
    assert len(sci) == len(set(sci))
    commons = [e["common"] for e in species]
    assert len(commons) == len(set(commons))


def test_australia_sideways_plates_stand_upright():
    """A sideways plate reads its caption down the right edge (a quarter turn
    clockwise, PIL 270) or, on a few, up the left (90). All of vol. VII is
    sideways; vols. I-IV are all upright."""
    for e in _australia()["species"]:
        if e["volume_no"] == 7:
            assert e.get("rotate") in (90, 270), e["common"]
        if e["volume_no"] in (1, 2, 3, 4):
            assert "rotate" not in e, e["common"]


def test_australia_no_sheet_is_a_composite():
    """Every sheet shows one species (male, female, young)."""
    leaves = [(e["volume"], e["leaf"]) for e in _australia()["species"]]
    assert len(leaves) == len(set(leaves))
    assert not any(e.get("composite") for e in _australia()["species"])


def test_australia_fold_outs_are_never_pinned():
    """The bowers (IV.8, IV.10) and Supp. 76 are double-page spreads with the
    fold through the art."""
    for vol, plate in ((4, 8), (4, 10), ("Supp.", 76)):
        assert _australia_at(vol, plate) is None


@pytest.mark.parametrize("vol,plate,common,scientific", [
    # Gould's binomial now names another bird: match on the modern name.
    (2, 67, "Rufous Whistler", "Pachycephala rufiventris"),       # his pectoralis
    (2, 91, "Satin Flycatcher", "Myiagra cyanoleuca"),            # his nitida
    (2, 88, "Shining Flycatcher", "Myiagra alecto"),
    (1, 26, "Swamp Harrier", "Circus approximans"),               # his assimilis
    (4, 98, "White-throated Treecreeper", "Cormobates leucophaea"),  # his picumnus
    (4, 93, "Brown Treecreeper", "Climacteris picumnus"),         # his scandens
    (6, 76, "Buff-banded Rail", "Gallirallus philippensis"),      # his Rallus pectoralis
    (6, 77, "Lewin's Rail", "Lewinia pectoralis"),
    (4, 7, "Bassian Thrush", "Zoothera lunulata"),                # not the Mountain Thrush
    (3, 55, "Tasmanian Thornbill", "Acanthiza ewingii"),          # not his diemenensis
])
def test_australia_naming_traps(vol, plate, common, scientific):
    e = _australia_at(vol, plate)
    assert (e["common"], e["scientific"]) == (common, scientific)


def test_folios_are_asked_havell_first_then_as_published():
    """A new folio never takes a species from one a household already sees:
    after Havell, Europe (1832) is asked before Australia (1840), so the
    Eurasian Coot outside the Australia region stays Gould's European plate.
    Britain (1862) shares no species with either."""
    import importlib.util
    script = SPECIES_YAML.parents[1] / "fetch_plates.py"
    spec = importlib.util.spec_from_file_location("fetch_plates", script)
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    order = [f for f, _, _ in fp.load_folios(SPECIES_YAML.parent)]
    assert order == ["havell", "gould_europe", "gould_australia", "gould_britain"]
