"""scripts/set_source.py: `install.sh --source …` writes the detection source
into the stored config and leaves every other setting alone (W-771)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from featherframe.config import load_config, save_config
from featherframe.db import Database

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set_source.py"


@pytest.fixture(scope="module")
def ss():
    spec = importlib.util.spec_from_file_location("set_source", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "featherframe.db"))


def test_birdnet_go_is_written_and_nothing_else_moves(ss, db):
    config = load_config(db)
    config.collage_species_max = 17
    save_config(db, config)
    out = ss.apply(db, "birdnet-go")
    config = load_config(db)
    # It names the webhook path to add in BirdNET-Go (W-865).
    assert out == f"detection source: birdnet-go (webhook /api/ingest/birdnet-go/{config.ingest_token})"
    assert config.detection_backend == "birdnet_go"
    assert config.collage_species_max == 17


def test_birdnet_pi_keeps_the_default_database_unless_told(ss, db):
    default = load_config(db).birdnet_db_path
    ss.apply(db, "birdnet-go")
    ss.apply(db, "birdnet-pi")
    assert (load_config(db).detection_backend, load_config(db).birdnet_db_path) == ("custom", default)
    ss.apply(db, "birdnet-pi", db_path="/srv/birds.db")
    assert load_config(db).birdnet_db_path == "/srv/birds.db"
