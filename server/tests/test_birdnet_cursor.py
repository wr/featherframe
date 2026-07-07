"""Tests for the BirdNET-Pi read-only ingest + rowid cursor.

This is where field breakage is likely, so the cursor, confidence filtering,
ordering, and soft-fail behaviour are all pinned down against a real-schema
fixture DB.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from featherframe.birdnet import BirdNetDB
from tests._fixtures import create_birds_db, make_row


def _rows(specs):
    """specs: list of (minute_offset, common, sci, confidence)."""
    base = datetime(2026, 5, 17, 6, 0, 0)
    from datetime import timedelta
    return [make_row(base + timedelta(minutes=m), c, s, conf) for (m, c, s, conf) in specs]


def test_available_true(birds_db):
    assert BirdNetDB(birds_db).available() is True


def test_available_false_when_missing(missing_db):
    assert BirdNetDB(missing_db).available() is False


def test_available_false_on_unexpected_schema(tmp_path):
    p = tmp_path / "weird.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE detections (foo TEXT, bar TEXT)")  # wrong columns
    conn.commit(); conn.close()
    assert BirdNetDB(str(p)).available() is False


def test_cursor_new_since_walks_forward(tmp_path):
    p = create_birds_db(tmp_path / "b.db", _rows([
        (0, "Northern Cardinal", "Cardinalis cardinalis", 0.9),
        (7, "Blue Jay", "Cyanocitta cristata", 0.85),
        (14, "American Robin", "Turdus migratorius", 0.8),
    ]))
    db = BirdNetDB(str(p))
    assert db.max_rowid() == 3

    first = db.new_since(0, min_confidence=0.7)
    assert [d.common_name for d in first] == ["Northern Cardinal", "Blue Jay", "American Robin"]
    assert [d.rowid for d in first] == [1, 2, 3]

    # advancing the cursor yields only newer rows
    assert db.new_since(first[-1].rowid) == []
    assert [d.common_name for d in db.new_since(1)] == ["Blue Jay", "American Robin"]


def test_confidence_filter(tmp_path):
    p = create_birds_db(tmp_path / "b.db", _rows([
        (0, "Northern Cardinal", "Cardinalis cardinalis", 0.95),
        (7, "Song Sparrow", "Melospiza melodia", 0.40),   # below threshold
        (14, "Blue Jay", "Cyanocitta cristata", 0.72),
    ]))
    db = BirdNetDB(str(p))
    got = db.new_since(0, min_confidence=0.7)
    assert [d.common_name for d in got] == ["Northern Cardinal", "Blue Jay"]


def test_latest_is_most_recent(tmp_path):
    p = create_birds_db(tmp_path / "b.db", _rows([
        (0, "Northern Cardinal", "Cardinalis cardinalis", 0.9),
        (30, "Blue Jay", "Cyanocitta cristata", 0.9),
        (10, "American Robin", "Turdus migratorius", 0.9),  # inserted later, earlier time
    ]))
    db = BirdNetDB(str(p))
    latest = db.latest(min_confidence=0.7)
    # ordered by Date DESC, Time DESC -> Blue Jay at +30 min is newest
    assert latest.common_name == "Blue Jay"


def test_top_species_today(birds_db):
    db = BirdNetDB(birds_db)
    from datetime import date
    top = db.top_species_today(date(2026, 5, 17), min_confidence=0.7)
    counts = {r["common"]: r["count"] for r in top}
    assert counts["Northern Cardinal"] == 14      # the day's most frequent
    assert counts["Blue Jay"] == 9
    # ordering is by count desc
    assert [r["common"] for r in top][0] == "Northern Cardinal"


def test_all_time_species_count(birds_db):
    # default fixture has 6 distinct species (incl. a below-threshold Song Sparrow)
    assert BirdNetDB(birds_db).all_time_species_count() == 6


def test_species_ordinal_by_first_seen(tmp_path):
    p = create_birds_db(tmp_path / "b.db", _rows([
        (0, "Northern Cardinal", "Cardinalis cardinalis", 0.9),   # first ever
        (7, "Blue Jay", "Cyanocitta cristata", 0.9),              # second
        (14, "American Robin", "Turdus migratorius", 0.9),        # third
    ]))
    db = BirdNetDB(str(p))
    assert db.species_ordinal("Cardinalis cardinalis") == 1
    assert db.species_ordinal("Cyanocitta cristata") == 2
    assert db.species_ordinal("Turdus migratorius") == 3


def test_first_seen_date(tmp_path):
    from datetime import timedelta
    base = datetime(2026, 5, 15, 6, 0)
    rows = [
        make_row(base, "Blue Jay", "Cyanocitta cristata", 0.9),
        make_row(base + timedelta(days=2), "Blue Jay", "Cyanocitta cristata", 0.9),
    ]
    p = create_birds_db(tmp_path / "b.db", rows)
    assert BirdNetDB(str(p)).first_seen_date("Cyanocitta cristata") == "2026-05-15"


def test_soft_fail_returns_safe_defaults(missing_db):
    db = BirdNetDB(missing_db)
    assert db.new_since(0) == []
    assert db.latest() is None
    assert db.max_rowid() == 0
    assert db.top_species_today() == []
    assert db.all_time_species_count() == 0
    assert db.species_ordinal("x") is None


def test_reader_never_locks_writer(birds_db):
    """We open read-only; a concurrent writer must still be able to write."""
    reader = BirdNetDB(birds_db)
    _ = reader.latest()  # opens + closes ro connection
    writer = sqlite3.connect(birds_db)
    writer.execute("INSERT INTO detections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   make_row(datetime(2026, 5, 17, 12, 0), "House Finch",
                            "Haemorhous mexicanus", 0.9))
    writer.commit()
    writer.close()
    # reader sees the new committed row
    assert reader.max_rowid() >= 1
