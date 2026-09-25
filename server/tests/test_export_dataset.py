"""export_dataset.py (W-868): the open dataset agrees with the folios.

Exported offline (no eBird, BirdNET or Wikidata), which is all the pins need.
"""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_dataset.py"


@pytest.fixture(scope="module")
def ex():
    spec = importlib.util.spec_from_file_location("export_dataset", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dataset(ex, tmp_path_factory):
    plates = tmp_path_factory.mktemp("plates")
    (plates / "data.json").write_text(json.dumps(
        [{"plate": n, "name": f"Plate {n}", "download": f"https://example.org/{n}.jpg"} for n in range(1, 436)]))
    out = tmp_path_factory.mktemp("dataset")
    ex.export(out, ex.Taxonomy(None), plates)
    return out


def rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_every_pin_is_in_the_dataset_and_back(ex, dataset):
    assert ex.check(dataset) == []


def test_every_plate_has_a_row(dataset):
    assert [int(r["plate"]) for r in rows(dataset / "havell" / "plates.csv")] == list(range(1, 436))
    gould = {int(r["plate"]) for r in rows(dataset / "gould-europe" / "plates.csv")}
    assert gould == set(range(1, 450))


def test_unidentified_plates_keep_a_reason(dataset):
    for folder in ("havell", "gould-europe"):
        for r in rows(dataset / folder / "species.csv"):
            assert r["scientific"] or r["reason"], r


def test_the_gull_trap_holds(dataset):
    """Gould's 'Black-headed Gull' is today's Mediterranean Gull."""
    sp = {(r["plate"], r["printed_name"]): r for r in rows(dataset / "gould-europe" / "species.csv")}
    assert sp[("427", "Black-headed Gull")]["scientific"] == "Ichthyaetus melanocephalus"
    assert sp[("425", "Laughing Gull")]["scientific"] == "Chroicocephalus ridibundus"


def test_a_split_sends_each_folio_to_its_own_daughter(ex):
    assert ex.EBIRD_NAMES_BY_FOLIO[("havell", "Accipiter gentilis")] == "Astur atricapillus"
    assert ex.EBIRD_NAMES_BY_FOLIO[("gould_europe", "Accipiter gentilis")] == "Astur gentilis"
