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
    config.review_species_max = 17
    save_config(db, config)
    out = ss.apply(db, "birdnet-go", url="http://10.0.0.5:8080/")
    assert out == "detection source: birdnet-go (http://10.0.0.5:8080)"
    config = load_config(db)
    assert (config.detection_backend, config.birdnet_go_url) == ("birdnet_go", "http://10.0.0.5:8080")
    assert config.review_species_max == 17


def test_birdnet_go_needs_a_real_url(ss, db):
    with pytest.raises(ValueError):
        ss.apply(db, "birdnet-go", url="10.0.0.5")
    assert load_config(db).detection_backend == "custom"


def test_birdnet_pi_keeps_the_default_database_unless_told(ss, db):
    default = load_config(db).birdnet_db_path
    ss.apply(db, "birdnet-go", url="http://go.local:8080")
    ss.apply(db, "birdnet-pi")
    assert (load_config(db).detection_backend, load_config(db).birdnet_db_path) == ("custom", default)
    ss.apply(db, "birdnet-pi", db_path="/srv/birds.db")
    assert load_config(db).birdnet_db_path == "/srv/birds.db"
