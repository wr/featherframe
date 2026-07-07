"""Build a fixture copy of BirdNET-Pi's birds.db for tests and manual runs.

Schema is verbatim from the Nachtzuster fork's createdb.sh, so the reader is
tested against the real column layout, types, and rowid behaviour.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS detections (
  Date DATE,
  Time TIME,
  Sci_Name VARCHAR(100) NOT NULL,
  Com_Name VARCHAR(100) NOT NULL,
  Confidence FLOAT,
  Lat FLOAT,
  Lon FLOAT,
  Cutoff FLOAT,
  Week INT,
  Sens FLOAT,
  Overlap FLOAT,
  File_Name VARCHAR(100) NOT NULL);
CREATE INDEX IF NOT EXISTS "detections_Com_Name" ON "detections" ("Com_Name");
CREATE INDEX IF NOT EXISTS "detections_Sci_Name" ON "detections" ("Sci_Name");
CREATE INDEX IF NOT EXISTS "detections_Date_Time" ON "detections" ("Date" DESC, "Time" DESC);
"""

# (common, scientific)
SPECIES = [
    ("Northern Cardinal", "Cardinalis cardinalis"),
    ("Blue Jay", "Cyanocitta cristata"),
    ("American Robin", "Turdus migratorius"),
    ("Tufted Titmouse", "Baeolophus bicolor"),
    ("House Sparrow", "Passer domesticus"),
    ("European Starling", "Sturnus vulgaris"),
]


def create_birds_db(path: str | Path, rows: list[tuple] | None = None) -> Path:
    """Create a birds.db. `rows` are full 12-tuples; if None, generate a spread."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(CREATE_SQL)
        conn.executemany(
            "INSERT INTO detections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows if rows is not None else _default_rows(),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def make_row(dt: datetime, common: str, scientific: str, confidence: float = 0.85) -> tuple:
    fname = f"{common.replace(' ', '_')}-{int(confidence*100)}-{dt:%Y-%m-%d}.wav"
    return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), scientific, common,
            confidence, 40.0, -75.0, 0.7, dt.isocalendar().week, 1.25, 0.0, fname)


def _default_rows() -> list[tuple]:
    base = datetime(2026, 5, 17, 6, 0, 0)
    rows = []
    # a day's worth: cardinals dominate, then jays, robins, titmice, plus a
    # low-confidence straggler and a house sparrow (for blocklist tests)
    plan = [
        ("Northern Cardinal", "Cardinalis cardinalis", 14, 0.94),
        ("Blue Jay", "Cyanocitta cristata", 9, 0.88),
        ("American Robin", "Turdus migratorius", 6, 0.80),
        ("Tufted Titmouse", "Baeolophus bicolor", 4, 0.76),
        ("House Sparrow", "Passer domesticus", 7, 0.90),
    ]
    t = base
    for common, sci, n, conf in plan:
        for _ in range(n):
            rows.append(make_row(t, common, sci, conf))
            t += timedelta(minutes=7)
    # a below-threshold straggler
    rows.append(make_row(t, "Song Sparrow", "Melospiza melodia", 0.42))
    return rows


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "birds.db"
    p = create_birds_db(out)
    print(f"wrote {p} with default rows")
