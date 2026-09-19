#!/usr/bin/env python3
"""Set the detection source from the command line (W-771).

`install.sh --source birdnet-go --url http://host:8080` calls this so a
BirdNET-Go household never has to see the BirdNET-Pi database default. It
edits the same config the page saves, and changes nothing else in it.

Usage:
    python scripts/set_source.py --source birdnet-go --url http://10.0.0.5:8080
    python scripts/set_source.py --source birdnet-pi [--db ~/BirdNET-Pi/scripts/birds.db]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from featherframe.config import load_config, save_config  # noqa: E402
from featherframe.db import Database  # noqa: E402

# The names a person types -> Config.detection_backend.
SOURCES = {"birdnet-go": "birdnet_go", "birdnet-pi": "custom"}


def apply(db, source: str, url: str = "", db_path: str = "") -> str:
    """Write the source into the stored config; returns a one-line summary."""
    config = load_config(db)
    config.detection_backend = SOURCES[source]
    if source == "birdnet-go":
        if not url.startswith(("http://", "https://")):
            raise ValueError("--url must be BirdNET-Go's address, e.g. http://10.0.0.5:8080")
        config.birdnet_go_url = url
    elif db_path:
        config.birdnet_db_path = db_path
    save_config(db, config)
    config = load_config(db)
    where = config.birdnet_go_url if source == "birdnet-go" else config.birdnet_db_path
    return f"detection source: {source} ({where})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Set Featherframe's detection source.")
    ap.add_argument("--source", required=True, choices=sorted(SOURCES))
    ap.add_argument("--url", default="", help="BirdNET-Go's address (birdnet-go)")
    ap.add_argument("--db", default="", help="path to BirdNET-Pi's birds.db (birdnet-pi)")
    args = ap.parse_args()
    try:
        print(apply(Database(), args.source, args.url, args.db))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
